# Changelog

All notable changes to Seizu are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

**Code-defined workflows are top-level activity types** (#223)
- The generic `workflow` activity sub-type (and the legacy `temporal`
  scheduled-query action) is removed. `cve_repo_report`,
  `cve_dependency_remediation`, and `cartography_sync` are now their own
  activity types in the workflow editor; a new `WorkflowSpec` in
  `WORKFLOW_REGISTRY` automatically becomes one.
- **Breaking on save, migrated on read:** stored definitions using
  `type: workflow` + `parameters.workflow` keep running — they are migrated
  transparently when loaded (including by the Temporal worker) — but new saves
  must use the top-level types. Editing a legacy definition in the UI saves it
  in the canonical shape.
- The cartography activity config is now an ordered **Intel modules** list
  (`module_runs`: `{module, params}` entries executed sequentially, with an
  "Add intel module" button and per-module parameter sub-forms). The
  `modules` and advanced `pipeline` JSON fields are removed (stored configs
  are migrated on read). `create-indexes` and `analysis` are no longer
  injected implicitly — they are ordinary selectable modules; place
  create-indexes before ingestion and analysis after it (migrated configs get
  them materialized explicitly, preserving behavior). Parallel syncs are
  expressed as multiple cartography activities in one workflow stage.
- Operators with a custom `WORKFLOW_ACTIVITY_MODULES` value should drop
  `reporting.scheduled_query_modules.workflow` (or `.temporal`) from it; stale
  entries are skipped with a warning instead of failing worker startup. The
  deprecated legacy scheduled-query worker (`ENABLE_SCHEDULED_QUERIES=true`)
  can no longer execute stored `temporal` actions.
- `GET /api/v1/config` no longer serves `workflow_activity_dependent_schemas`
  (per-workflow fields are inlined into each type's
  `workflow_activity_definitions` entry), and
  `scheduled_query_action_dependent_schemas` is now always empty.

## [3.1.0] - 2026-07-13

The headline of this release is a **LangGraph-backed AI chat assistant** with
MCP tool integration, plus everything built on top of it over the following
seven weeks: recurring **scheduled chats**, **Temporal workflow**
orchestration, a **sandboxed code-execution** tool, and an end-to-end
**automated CVE dependency remediation** pipeline that opens PRs from an
AI coding agent. Also: run-now for scheduled queries/chats, minute-granularity
structured schedules, and live panel previews in the report editor.

This is an additive, opt-in release — no required configuration changes and
no removed functionality. Chat and everything built on it are off by default
(`CHAT_ENABLED=false`); see [Upgrade notes](#upgrade-notes-310) for what to
set to turn it on, and the one API behavior change worth knowing about before
you deploy.

### Added

**Chat assistant** (#173)
- `POST /api/v1/chat/stream` (SSE, Vercel AI SDK UI Message Stream) and
  `GET /api/v1/chat/history` back a new chat UI at `/app/chat`. Multi-provider
  LLM support via LiteLLM (`CHAT_LLM_PROVIDER`/`CHAT_LLM_MODEL`), progressive
  MCP tool/skill disclosure, Markdoc-rendered responses, and a user-approval
  ("confirmation") flow for mutating tool calls, grouped per LLM turn.
- New permissions: `chat:use` (Viewer+), `chat:tools:call` / `chat:skills:call`
  (Editor+) — gate the endpoint and tool/skill execution on top of each tool's
  own underlying permission.
- Gated end-to-end by `CHAT_ENABLED` (default **off**); once enabled,
  `CHAT_LLM_PROVIDER` defaults to keyless `mock` echo mode until a real
  provider is configured.

**Scheduled chats & headless agent runs** (#191, #192)
- Recurring headless agent runs (prompt + trigger, no Cypher) managed from a
  new `/app/scheduled-chats` page, with full version history and a run-session
  viewer. Hourly/daily/monthly schedules and watch-scan triggers.
- Headless runs execute as the schedule's *creator*, under their own RBAC.
  Confirmation bypass is gated by the new `chat:bypass_permissions`
  permission (Editor+) and AUDIT-logged per call.
- New permissions: `chat:schedule` (Editor+, owner-scoped CRUD),
  `chat:schedule:read_all` (Admin, cross-user visibility).
- Dedicated detail pages for both scheduled chats and scheduled queries
  (`/app/scheduled-chats/:id`, `/app/scheduled-queries/:id`), replacing the
  old list-page modal.

**Temporal workflows** (#191, #212)
- New `temporal` scheduled-query action starts a named, deterministic
  Temporal workflow with the query results; `python -m reporting.temporal_worker`
  hosts workflow/activity code. Ships with `cve_repo_report` (per-repo CVE
  assessment chats) and `cve_dependency_remediation` (below).
  `TEMPORAL_ENABLED_WORKFLOWS` allowlists dispatchable workflows.
- Scheduled query detail pages now show past workflow runs with a per-activity
  breakdown (status, attempts, retries, failures, input/result previews);
  payload previews require `scheduled_queries:write`.

**Sandbox delegation** (#198)
- `sandbox__delegate`, a chat-only built-in that lets the agent delegate code
  execution and file operations (`run_python`, `run_bash`, `read_file`,
  `write_file`, `list_files`) to an ephemeral, network-isolated E2B sandbox,
  with the run's own tool calls surfaced live in the chat UI. Gated by
  `SANDBOX_ENABLED` (default **off**) and the new `sandbox:delegate`
  permission (Editor+).

**Automated CVE dependency remediation** (#201, #205, #210)
- The `cve_dependency_remediation` Temporal workflow runs a headless coding
  agent (Claude Code, Codex, or opencode) in ephemeral sandboxes to upgrade a
  vulnerable dependency — including any compatibility code changes — and open
  a PR, with two-sandbox credential isolation so the GitHub token never shares
  a VM with untrusted agent/repo code.
- Post-push, the workflow watches the PR's CI and, on upgrade-caused failures,
  runs bounded fix-mode agent sessions or posts a triage comment for unrelated
  failures.
- Optional fork mode (`REMEDIATION_USE_FORK`) for orgs that don't want the bot
  writing branches directly into target repositories.
- No per-user permission and no single enable flag — reachable only through an
  admin-managed scheduled query's `temporal` action, and only runs when
  `REMEDIATION_GITHUB_TOKEN` + an agent API key are configured.

**Scheduling improvements** (#206, #209)
- **Run now**: `POST /api/v1/scheduled-queries/{id}/run` and
  `POST /api/v1/chat/schedules/{id}/run` trigger an immediate run on the next
  worker poll, even when the job is disabled. Surfaced as a row action, a
  `seizu scheduled-queries run <id>` CLI command, and an MCP builtin.
- **Structured schedules** for scheduled queries, extended to minute
  granularity: the `schedule` field (`interval`/`hourly`/`daily`/`monthly`,
  all UTC) generalizes the scheduled-chats scheduling model. The legacy
  `frequency` field still works but is deprecated; editing a legacy query in
  the UI migrates it to an equivalent schedule on save.

**Report editing** (#195)
- Report edit mode now renders **live panels** reflecting unsaved edits
  (Cypher/params/settings) instead of static skeletons, using the same
  ad-hoc, read-only query path every report editor already has access to.

**Other**
- `GET /api/v1/graph/schema` now also returns index metadata (#173).
- New `GET /api/v1/sync-metadata/values` backs autocomplete for watch-scan
  fields (#191).
- Report authoring skillset seeded for the chat agent (staged
  clone-review-apply-cleanup workflow for report changes) (#200).
- Tagged development release publishing: dev-tagged builds now publish
  matching GHCR images for the full app, server, and CLI (#199).

### Changed

- `POST /api/v1/query/{adhoc,report,history}` now return **400** with
  `{"errors": [...], "warnings": [...]}` for Neo4j `ClientError`s (bad Cypher,
  constraint violations, etc.), instead of the previous **500** with
  `{"error", "code", "details"}`. Other Neo4j errors still return 500
  unchanged (#191).
- Dependabot now only opens PRs for security advisories, not routine version
  bumps (#189).

### Security

- Bumped `cryptography`, `pyjwt`, `python-multipart`, `starlette`, and
  `aiohttp` to patched versions, closing several HIGH-severity advisories
  (SSRF, DoS, algorithm-confusion/forgery) (#194).
- Bumped `langsmith`, `pydantic-settings`, `vite`, `ws`, `markdown-it`, and
  `@babel/core` to patched versions (#197).
- Bumped `joserfc` 1.6.5 → 1.6.8 (#202) and `starlette` 1.0.0 → 1.0.1 (#181).

### Configuration

This release adds a large family of new, all-optional settings for chat,
scheduled chats, Temporal, sandbox delegation, and CVE remediation — see
`.env.example` / `reporting/settings.py` for the full list. The ones worth
knowing before you deploy:

| Variable | Default | Purpose |
|---|---|---|
| `CHAT_ENABLED` | `false` | Registers chat routes/UI/checkpoint storage |
| `CHAT_LLM_PROVIDER` | `mock` | `mock` = keyless echo; any other value routes through LiteLLM |
| `CHAT_SCHEDULES_ENABLED` | `true` | Scheduled chats routes/UI/worker (no-op unless `CHAT_ENABLED=true`) |
| `SANDBOX_ENABLED` | `false` | `sandbox__delegate` chat tool |
| `TEMPORAL_WORKER_ENABLED` | `true` | Gates the separate `seizu-temporal-worker` process only |
| `REMEDIATION_GITHUB_TOKEN` | — | Enables CVE dependency remediation once set (+ an agent API key) |

New docker-compose services (dev only, not profile-gated): `temporal`,
`minio` (+ `minio-create-bucket`), `postgres-create-chat-checkpoint-db`,
`seizu-scheduled-chats`, `seizu-temporal-worker`. `make up` now pulls and
starts these by default.

### Upgrade notes {#upgrade-notes-310}

1. **Chat is off by default.** `CHAT_ENABLED=false` means upgrading changes
   nothing for existing deployments — no new nav item, no new routes, no
   checkpoint storage initialized. To turn it on, set `CHAT_ENABLED=true`;
   `CHAT_LLM_PROVIDER` defaults to keyless, deterministic `mock` echo mode
   until you also configure a real provider, so enabling it is still safe to
   do before wiring up an LLM.
2. **Built-in roles already carry the new chat permissions**, inert until you
   enable the feature: once `CHAT_ENABLED=true`, Viewer gets `chat:use`;
   Editor additionally gets `chat:tools:call`, `chat:skills:call`,
   `chat:bypass_permissions`, `chat:schedule`, and `sandbox:delegate`; Admin
   additionally gets `chat:schedule:read_all`. If you use custom (non-built-in)
   roles, review whether they should include these before enabling chat.
3. **If you parse query-endpoint error responses**, note the shape/status
   change for Neo4j `ClientError`s described above.
4. **SQL backend**: new tables/columns are created/added automatically on
   startup (inline `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` migrations,
   consistent with the existing pattern) — no manual migration step.
5. To enable **CVE dependency remediation**, set `REMEDIATION_GITHUB_TOKEN`
   and an agent API key, then add a `temporal` action (workflow
   `cve_dependency_remediation`) to a watch-scan scheduled query. Everyone
   else is unaffected.

## [3.0.0] - 2026-05-23

The headline of this release is a security-hardening rewrite of the browser
authentication flow: OIDC moves from an in-browser library to a
**backend-for-frontend (BFF)** design where the IDP refresh token never reaches
JavaScript. This is a **breaking change for any deployment with auth enabled** —
see [Upgrade notes](#upgrade-notes-300) before deploying.

### ⚠️ Breaking changes

- **Browser auth is now a backend-for-frontend flow** (#147). The OIDC
  Authorization Code + PKCE exchange happens on the server, not in the browser.
  The IDP refresh token lives only in an AES-256-GCM-encrypted, HttpOnly,
  SameSite=Strict cookie; the access token stays in React state and is never
  persisted. The `oidc-client-ts` browser library has been removed entirely.
- **IDP redirect URI changed.** The SPA-served `/auth/callback` route is gone;
  the backend now handles the redirect at **`/api/v1/auth/callback`**. Update
  the allowed redirect URIs in your identity provider.
- **`SESSION_TOKEN_ENCRYPTION_KEY` is now required when auth is enabled.**
  Without it the backend cannot encrypt the session cookie and will fail to
  start the auth flow.
- **`OIDC_REDIRECT_URI` is no longer hardcoded** in `docker-compose.yml`. The
  backend derives the callback from each request's host, fixing the
  `:3000`-vs-`:8080` dev split.

### Added

- **Encrypted session cookie** carrying `{refresh_token, issued_at,
  absolute_expiry}`. Rolling refreshes can never extend the cookie past the
  IDP's absolute expiry — enforced both by browser `Max-Age` and on decrypt as
  defense-in-depth (#147).
- **Four BFF auth routes:** `GET /api/v1/auth/login`,
  `GET /api/v1/auth/callback`, `POST /api/v1/auth/refresh`,
  `POST /api/v1/auth/logout` (#147).
- **CSRF protection** via a pure-ASGI middleware that requires `X-Seizu-Csrf` on
  mutating requests whenever the session cookie is present. Bearer-only clients
  (CLI, MCP, programmatic) are exempt and cannot downgrade the check (#147).
- **ID-token validation** on the code exchange — signature (via discovery
  JWKS), audience, issuer, and login nonce — gated by `OIDC_VALIDATE_ID_TOKEN`
  (#147).
- **RFC 7662 token introspection fallback** (`OIDC_ENABLE_TOKEN_INTROSPECTION`)
  for opaque (non-JWT) access tokens, shared by the REST resource server and the
  MCP auth middleware (#147).
- **Refresh-token revocation on logout** (`OIDC_REVOKE_REFRESH_TOKEN_ON_LOGOUT`),
  best-effort against the IDP's revocation endpoint (#147).
- **Cross-provider compatibility:** `OIDC_AUTHORIZE_EXTRA_PARAMS` (e.g. Google's
  `access_type=offline,prompt=consent`) so non-`offline_access` providers still
  issue refresh tokens (#147).
- **Shared list-view component library** adopted across all 13 consumers (6 list
  pages, 7 version-history pages, 2 detail dialogs): `RowMenu`,
  `ListPageHeader`, `ListViewState`, `ConfirmDeleteDialog`, and `DetailDialog`
  (+ `DetailSection`/`DetailCodeBlock`), all with tests. Net −1.8k lines of
  copy-pasted markup (#149).

### Changed

- Replaced the hand-rolled OIDC client with **authlib** (`AsyncOAuth2Client`,
  `token_endpoint_auth_method="none"` + PKCE) for RFC 6749/7636/OIDC mechanics
  and IDP-specific quirks (#147).
- `/auth/refresh` is serialized across browser tabs via the Web Locks API, with
  a module-level in-flight dedupe (also fixes a React StrictMode double-refresh
  race against rotating refresh tokens) (#147).
- AES-GCM associated data domain-separates the session and OAuth-state cookies
  that share the encryption key; cookies are scoped to `/api/v1/auth` (#147).
- Split-hostname support: authorize URLs are rewritten to the external origin
  when discovery uses an internal host (Docker dev), with
  `AUTHENTIK_HOST_BROWSER` as the cleaner path for Authentik (#147).
- `AGENTS.MD` compressed ~500→~259 lines, replacing duplicated tables (env vars,
  endpoints, brand colors, fuzzing) with pointers to their canonical sources
  (#149).

### Fixed

- Preserve the `Host` header through the Vite dev proxy (`changeOrigin: false`)
  so the backend derives the correct callback URL in dev (#147).
- OAuth callback errors are no longer reflected; required identity claims are
  guarded and optional profile claims used for display (#147).
- Removed the `NOTICE` reference to Lyft, which never held copyright (#144).

### Configuration

New environment variables (documented in `.env.example` /
`reporting/settings.py`):

| Variable | Default | Purpose |
|---|---|---|
| `SESSION_TOKEN_ENCRYPTION_KEY` | — (**required w/ auth**) | AES-256-GCM key for the session cookie |
| `SESSION_COOKIE_NAME` | `seizu_session` | Session cookie name |
| `SESSION_COOKIE_MAX_AGE_SECONDS` | `64800` | Session cookie lifetime |
| `OIDC_CLIENT_SECRET` | — | For confidential-client IDPs |
| `OIDC_TOKEN_ENDPOINT_AUTH_METHOD` | `none` | Token endpoint auth method |
| `OIDC_REVOCATION_ENDPOINT_AUTH_METHOD` | `none` | Revocation endpoint auth method |
| `OIDC_REVOKE_REFRESH_TOKEN_ON_LOGOUT` | `true` | Revoke refresh token on logout |
| `OIDC_REFRESH_TOKEN_FALLBACK_TTL_SECONDS` | `2592000` | Fallback when IDP omits `refresh_expires_in` |
| `OIDC_AUTHORIZE_EXTRA_PARAMS` | — | Extra authorize params (e.g. Google offline) |
| `OIDC_ENABLE_TOKEN_INTROSPECTION` | `false` | RFC 7662 fallback for opaque tokens |
| `OIDC_INTROSPECTION_ENDPOINT_AUTH_METHOD` | inherits | Introspection endpoint auth method |
| `OIDC_DISCOVERY_CACHE_TTL_SECONDS` | `3600` | Bounds discovery/JWKS staleness |
| `OIDC_VALIDATE_ID_TOKEN` | `true` | Validate ID token on code exchange |

### Upgrade notes {#upgrade-notes-300}

1. **Generate and set `SESSION_TOKEN_ENCRYPTION_KEY`** (required when auth is
   enabled):
   ```bash
   python -c 'import base64,os; print(base64.b64encode(os.urandom(32)).decode())'
   ```
2. **Update your IDP's allowed redirect URIs** to
   `https://<your-host>/api/v1/auth/callback` (the SPA `/auth/callback` is
   removed).
3. **Remove any hardcoded `OIDC_REDIRECT_URI`** — the backend now derives it from
   the request host.
4. For **non-`offline_access` providers** (e.g. Google), set
   `OIDC_AUTHORIZE_EXTRA_PARAMS=access_type=offline,prompt=consent`.
5. For IDPs issuing **opaque access tokens**, set
   `OIDC_ENABLE_TOKEN_INTROSPECTION=true`.
6. Existing users holding the old `path=/` session cookie will briefly see two
   cookies in devtools until they log out and back in; the stale cookie is
   harmless.

## [2.3.0] - 2026-05-20

Dependency and tooling maintenance release.

### Changed

- Bumped frontend dependencies, patched audit vulnerabilities, and fixed the
  Docker build (#141).
- Bumped Python dependencies (python-minor-and-patch group) and docs
  `myst-parser` (#142).
- Bumped CI actions, migrated ESLint to v10, and bumped TypeScript to 6 (#140).
- Bumped `goauthentik/server` 2026.2.2 → 2026.2.3 (#136) and `idna` 3.14 → 3.15
  (#143).

## [2.2.0] - 2026-05-19

### Changed

- Hardened the Cypher validation fuzzing suite — expanded attack-vector
  coverage (write/DDL, disallowed procedures, `USE`, admin/catalog,
  `LOAD CSV` SSRF, APOC/GDS/GenAI functions, unicode/homoglyph) (#126).

## [2.1.0] - 2026-05-14

### Added

- Fuzz coverage for the query validator (#93).
- OpenSSF Scorecard workflow (#92).
- Dependabot configuration (#94).

### Changed

- Replaced and expanded documentation screenshots; updated docs for current
  Seizu workflows (#122, #124).
- Pinned external CI and Docker dependencies; bumped Python base image
  3.12 → 3.14 and numerous CI/docs/frontend dependencies (#115, #96, #118, and
  related Dependabot PRs).

### Fixed

- Surface report-mutation backend errors in the UI (#120).
- Constrained `urllib3` to a patched version (#119).

## [2.0.0] - 2026-05-12

The foundational Seizu platform release — a ground-up rebuild of the runtime,
frontend, storage, auth, and integrations.

### Platform & runtime

- Migrated the backend from Flask/gevent through APIFlask to **FastAPI +
  asyncio** with fully async I/O (#28, #31).
- Migrated to **Pydantic v2, Python 3.12**, and `myst-parser` (#4).
- Switched Python tooling to **uv** and packaged Seizu (#90).
- Added configurable timeouts for all outbound calls and FastAPI requests (#75).

### Frontend

- Migrated the frontend from **JavaScript to TypeScript** (#6), from **yarn to
  bun** (#5), and from **webpack/CRA to Vite** (#15).
- Replaced Nivo with **MUI X Charts** and added a graph panel (#21).
- Editable reports UI with create/edit/delete and **version history** (#19, #20).
- Report panel editor improvements: per-panel heights via `react-grid-layout`,
  multi-threshold colors, row reordering, move-panel-between-rows, collapsible
  rows, optional headers, and type-representative skeletons (#66, #67, #70, #71).
- WYSIWYG markdown editor for panels and skill templates; markdown rendering via
  **Markdoc** (#45, #57, #62, #85).
- Query console with history, dedicated history/schema endpoints, and graph
  panel UX improvements (#22, #37, #58, #84).
- Clone reports (#59), report-level private/public access (#60), and pinned
  reports with sidebar filtering (#40).

### Storage & configuration

- Store report/dashboard configs in the database with a **pluggable backend**
  (DynamoDB + SQLModel) (#14).
- Track scheduled query results and moved distributed locking to the report
  store (#42).
- Scheduled query management with version history, schema-driven action forms,
  and a statsd action plugin (#26, #61).

### Auth, RBAC & identity

- Generic **OIDC JWT auth** with an Authentik dev identity provider (#10).
- **RBAC**: permissions, built-in roles, user-defined roles, and JWT-claim
  resolution, with permission-gated UI (#38, #39).
- Persistent user store with JIT provisioning and user identity display (#24).
- Role management UI and logout menu (#56).

### MCP, CLI & integrations

- **MCP server** with user-defined toolsets and tools, plus built-in tool groups
  for managing Seizu (#33, #51).
- User-defined MCP **skills** (#53).
- **seizu CLI** with auth, OS keyring support, pip packaging, toolset/tool
  management, and seed/export (#29, #34).

### Security

- Added the `/api/v1/query` endpoint with Cypher validation; pass params through
  validation and execution and drop cypher-guard (#8, #13).
- Hardened Cypher validation and Neo4j dev security (#54).
- Migrated frontend queries to the backend API, removing direct Neo4j auth (#9).
- CSP nonce support for MUI styles and tightened CSP (#43, #44).

### Branding & docs

- Space-themed brand refresh: logos, design tokens, and chart palette (#48).
- Space-themed docs splash page; switched Sphinx to the Shibuya theme and
  isolated docs deps (#46, #47).

## [1.0.0] - 2022-06-08

Initial release of the original reporting tool that Seizu was built from —
Dockerized build, GitHub Container Registry publishing, and quickstart docs.

[3.0.0]: https://github.com/mappedsky/seizu/compare/v2.3.0...v3.0.0
[2.3.0]: https://github.com/mappedsky/seizu/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/mappedsky/seizu/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/mappedsky/seizu/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/mappedsky/seizu/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/mappedsky/seizu/releases/tag/v1.0.0
