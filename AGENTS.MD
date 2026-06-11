# Agent Guidelines

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What is Seizu?

Seizu (星図) is a configuration-driven reporting platform for Neo4j graph data. Three runtimes:
- **FastAPI web app** (`reporting/`) — serves the React frontend and REST API (ASGI, Gunicorn + UvicornWorker).
- **Scheduled query worker** (`reporting/scheduled_queries.py`) — runs Cypher on a schedule with pluggable action plugins (SQS, Slack, log, statsd, temporal); reads from the database, not the YAML config.
- **Temporal worker** (`reporting/temporal_worker.py`) — hosts Temporal workflows/activities started by the `temporal` scheduled query action (see the Temporal Workflows section).

## Running Commands with Docker

Never install bun, node, uv, or Python on the host. Always use `docker compose run --rm <service>`.

**Frontend (`seizu-node`):**
```bash
docker compose run --rm seizu-node bun add <package>
docker compose run --rm seizu-node bun test --watchAll=false              # all tests
docker compose run --rm seizu-node bun test src/path/to.test.tsx --watchAll=false
docker compose run --rm seizu-node bun run type-check
```

**Backend (`seizu`):**
```bash
docker compose run --rm --user root seizu uv sync --frozen --all-groups --all-packages --no-install-workspace
docker compose run --rm seizu uv run --frozen --no-sync pytest tests/unit
docker compose run --rm seizu uv add [--dev] <package>
```
> Always run tests via `uv run --frozen --no-sync` **in the container** so they use the synced Docker env and `uv.lock`. After editing `pyproject.toml`, run `docker compose run --rm seizu uv lock`; commit `pyproject.toml` + `uv.lock` together.

**Docs (Sphinx):** isolated venv under `docs/.venv`, pinned in `docs/requirements.txt`. Sphinx is **not** in the project `pyproject.toml` — don't add it there. `make docs` runs `docs/build.sh` (bootstraps the venv). To change a docs dep, edit `docs/requirements.txt` and re-run `make docs`.

**Linting:** run pre-commit **on the host** (the `seizu` container lacks git): `pre-commit run --all-files` or `make lint`.

**Git:** never push to `master`. Push feature branches and open a PR.

**Makefile shortcuts:**
```
make test / test_unit / test_frontend
make lint
make schema             # regenerate schema/reporting-schema.json + schema/openapi.json
make generate_openapi   # export schema/openapi.json (no running server)
make generate_client LANG=go
make seed_dashboard [ARGS="--force|--dry-run|export"]
make auth_enable / auth_disable
make sqlmodel_enable / sqlmodel_disable
```

## Adjacent Services Configuration

Config for adjacent dev services lives in `.config/`; reference that path in `docker-compose.yml`. `.config/setup.sh` (called by `make up`) copies `.config/dev/` → `.compose/` **only if the destination doesn't exist** — to apply a source change, copy it manually or delete the destination and re-run `make up`. New env vars go in `.env.example`. Gunicorn socket/PID/workdir are `/run/seizu` (tmpfs) to avoid `EINVAL` on macOS bind mounts.

## Architecture

### Frontend (`src/`)

React 19 + TypeScript + Bun + Material-UI + MUI X Charts + `@xyflow/react`.

- `src/config.context.ts` — `ConfigContext` from `GET /api/v1/config` (OIDC + stats config); Cypher resolution is per-report via `Report.queries`.
- `src/authConfig.context.ts` — `AuthConfigContext`; `auth_required` + informational OIDC config; gates API calls until a token exists.
- `src/auth.context.ts` — `AuthContext`; in-memory `accessToken` only.
- `src/api/authClient.ts` — `beginLogin(returnTo)`, `refreshSession()`, `logout()`; thin BFF auth wrappers, send `X-Seizu-Csrf` on mutating calls.
- `src/components/AuthProvider.tsx` — `refreshSession()` on mount; on success stores token + schedules refresh ~30s before expiry; on failure navigates to `/api/v1/auth/login`. Module-level in-flight promise dedupes concurrent refreshes (StrictMode safety).
- `src/hooks/useCypherQuery.ts` — wraps `POST /api/v1/query`; skips if `auth_required` and no token; `saveHistory` forwards `save_history: true`.
- `src/hooks/useQueryHistory.ts` — `GET /api/v1/query-history`; returns `{ loading, error, data, fetchHistory }`.
- `src/hooks/useReportsApi.ts` — `useReportsList/useReport/useDashboardReport/useAllReports/useReportVersionsList/useReportVersion`; `useReportsMutations()` for create/save-version/set-dashboard/delete.
- `src/components/ReportView.tsx` — shared report renderer; resolves panel `cypher` against `report.queries`.
- `src/components/reports/` — panel components (`CypherCount`, `CypherTable`, `CypherBar`, …); `CypherGraph.tsx` is the `@xyflow/react` graph panel (map/path/any format; Graph/Table/Raw tabs).
- `src/pages/QueryConsole.tsx` — ad-hoc console at `/app/query-console` (Ctrl+Enter runs); `QueryConsoleSchemaPanel.tsx` is the collapsible schema browser.
- `src/pages/ChatInterface.tsx` — chat assistant at `/app/chat` (`@ai-sdk/react` `useChat` against `POST /api/v1/chat/stream`); gated on the `chat:use` permission. See the Chat backend section.

**User identity:**
- `src/hooks/useCurrentUser.ts` → `GET /api/v1/me`; `src/hooks/useUser.ts` → `GET /api/v1/users/<id>`.
- `src/components/UserAvatar.tsx` — `boring-avatars`; `<UserAvatar name={...} />`.
- `src/components/UserDisplay.tsx` — resolves user_id to `display_name ?? preferred_username ?? email ?? "Unknown user"`; `<UserDisplay userId={...} />`.
- Durable identity is `(iss, sub)`; email/display name/username are optional display metadata. **Never** display raw user_ids or `sub` in the UI.

### Backend (`reporting/`)

FastAPI, Python 3.12, Gunicorn + UvicornWorker (ASGI). All I/O is async (neo4j async driver, aioboto3, async SQLAlchemy).

- `reporting/asgi.py` — `application = create_app()`.
- `reporting/app.py` — `create_app()`; `openapi_url="/api/openapi.json"`, `docs_url="/api/docs"`; registers `secure` headers, routers, static, SPA fallback via `lifespan`; hosts the MCP/auth/CSRF ASGI middlewares.
- `reporting/settings.py` — all config via env (Pydantic).
- `reporting/routes/` — `config.py`, `me.py`, `query.py`, `reports.py`, `users.py`, `validate.py`, `scheduled_queries.py`, `roles.py`, `static.py` (healthcheck only).
- `reporting/authnz/__init__.py` — `HTTPBearer` + `Depends(get_current_user)`; `CurrentUser` dataclass (`user`, `jwt_claims`, `permissions`); `sync_user_profile` chains on it; tests override `app.dependency_overrides[get_current_user]`.
- `reporting/services/` — async: `reporting_neo4j.py` (async driver + distributed locking), `report_store/`, `query_validator.py`.
- `reporting/schema/reporting_config.py` — Pydantic models for the YAML config; `report_config.py` — store items + API schemas.

**Async I/O:** Neo4j `AsyncGraphDatabase.driver()` (a sync `GraphDatabase.driver()` is kept only for CyVer validators via `asyncio.to_thread`); DynamoDB/SQS via `aioboto3` async context managers; PostgreSQL via `create_async_engine` + `AsyncSession` (`postgresql+asyncpg://`).

**Query validation** (`reporting/services/query_validator.py`): `async def validate_query()` → `ValidationResult(errors, warnings)`. Errors block; warnings don't. Layers:
1. Syntax + read-only check via `EXPLAIN`.
2. SSRF/admin/procedure guard (regex + allowlist) — blocks `LOAD CSV`, the `USE` clause, admin/catalog commands (`SHOW`, `TERMINATE`, db/user/role/privilege), and `apoc.cypher.*`/`gds.*`/`ai.*`/`genai.*` function namespaces. Every `CALL <procedure>` is allowlisted (side-effect-free schema procedures by default). Extend via `QUERY_VALIDATOR_ALLOWED_PROCEDURES` (exact names or namespace prefixes like `apoc.`); a namespace prefix also drops that namespace's dangerous-function guard.
3. CyVer `SchemaValidator` warnings; 4. CyVer `PropertiesValidator` warnings (both via `asyncio.to_thread`).

### API Endpoints

The route definitions in `reporting/routes/` are the source of truth. Don't maintain an endpoint list here — read the live spec instead: Swagger UI at `/api/docs`, raw spec at `/api/openapi.json`, or regenerate the checked-in copy with `make generate_openapi` (writes `schema/openapi.json`, no running server needed).

Conventions worth knowing up front: all paths are under `/api/v1/*` (except `/healthcheck`, `/api/openapi.json`, `/api/docs`); versioned entities (reports, roles, scheduled-queries, toolsets, tools) expose `GET .../<id>/versions` and `GET .../<id>/versions/<n>`, and reports additionally accept `POST /api/v1/reports/<id>/versions` to save a new version. The browser-only BFF auth routes live under `/api/v1/auth/*` — see the Authentication section.

### CLI (`seizu_cli/`)

Typer app calling the REST API; `python -m seizu_cli`. Global options `--api-url`, `--token`, `--credentials-file`, `--config-file`.

- `main.py` (entry), `config.py` (`SeizuConfig` + `load_config()` reading `~/.config/seizu/seizu.conf`), `client.py` (`SeizuClient`, Bearer auth), `state.py` (`get_client()` lazy factory), `commands/` (`reports`, `scheduled_queries`, `toolsets`, `seed`, `auth`), `auth.py` (keyring or `--credentials-file`; Device Authorization Grant RFC 8628).
- Conf file (`~/.config/seizu/seizu.conf`): `api_url`, `seed_file`. Resolution: CLI flag → env → conf → default.
- Auth flow: `seizu login` → device endpoint → browser approval → CLI polls → token in keyring.

Usage inside the `seizu` container — prefix every command with `uv run --frozen --no-sync python -m seizu_cli --api-url http://seizu:8080`:
```
login
reports list
toolsets list
toolsets tools list <toolset_id>
toolsets tools call <toolset_id> <tool_id> --arg limit=10
seed [--force|--dry-run]
export
```
Prefer `make seed_dashboard [ARGS="--force|--dry-run|export"]`.

### MCP Server (`reporting/services/mcp_server.py`)

Exposed at `/api/v1/mcp` when `MCP_ENABLED=true` (default). LLM agents connect as standard MCP clients.

- **Transport:** StreamableHTTP via `_MCPMiddleware` (pure ASGI in `app.py`), intercepting `/api/v1/mcp*` before FastAPI routing (avoids a Starlette 1.0.0 `Mount` trailing-slash issue).
- **Auth:** `_MCPAuthMiddleware` validates Bearer tokens via shared `authnz.validate_bearer_token()` (JWKS + optional RFC 7662 introspection); passes through unauthenticated in dev (`DEVELOPMENT_ONLY_REQUIRE_AUTH=false`). Sets two `ContextVar`s per request: `_mcp_permissions` (`frozenset[str]`) and `_mcp_current_user` (`CurrentUser`); handlers attributing writes read the latter.
- **Tool names:** `{group}__{action}` (built-ins) or `{toolset}__{tool}` (user-defined), double underscore.
- **Listing/dispatch:** the actual list/call/prompt logic lives in `reporting/services/mcp_runtime.py` (`list_tools_for_user`, `call_tool_for_user`, `list_prompts_for_user`, `get_prompt_for_user`) so the MCP transport and the chat agent share one implementation. `@server.list_tools()` merges `list_builtin_tools(MCP_ENABLED_BUILTINS)` (filtered by caller permissions) with `report_store.list_enabled_tools()`. `@server.call_tool()` tries `find_builtin()` first, else runs user-defined tools via `reporting_neo4j.run_query()`. Built-ins enforce their own `required_permissions`.
- **Built-ins** live in `reporting/services/mcp_builtins/` (one module per group, registered in `__init__.py`). Each is a `BuiltinTool` dataclass (`base.py`) sharing the REST service layer — no duplicated logic; permissions mirror REST. Groups (all default-on): `graph` (schema, query, validate_query, explain), `reports`, `scheduled_queries`, `skillsets`, `toolsets`, `roles` — full CRUD + version history. To add a group: export `GROUP_DEF: BuiltinGroup` from a new module, append to `_GROUPS` in `__init__.py` and the `settings.MCP_ENABLED_BUILTINS` default.
- **OAuth metadata:** `GET /api/v1/mcp/.well-known/oauth-authorization-server` (RFC 8414) when `MCP_OAUTH_AUTHORIZATION_ENDPOINT` + `MCP_OAUTH_TOKEN_ENDPOINT` are set (also `MCP_OAUTH_ISSUER`); lets clients like Claude Desktop discover the IDP. `.well-known` passes through unauthenticated.

**Toolsets/Tools** are user-managed Cypher-backed tool groupings in the report store with version history; CRUD via REST; Cypher validated (writes blocked) at create/update.

DynamoDB key space: `TOOLSET#{id}` (`#METADATA`, `VERSION#N`), `TOOLSET_LIST/TOOLSET#{id}`, `TOOL#{id}` (`#METADATA`, `VERSION#N`), `TOOL_LIST#{toolset_id}/TOOL#{id}`. SQL tables: `toolsets`, `toolset_versions`, `tools`, `tool_versions`.

### Chat (`reporting/services/chat_graph.py`, `reporting/routes/chat.py`)

A LangGraph-backed chat assistant at `POST /api/v1/chat/stream` (frontend `src/pages/ChatInterface.tsx`, route `/app/chat`), gated by the `CHAT_ENABLED` setting (default true). When off, the chat routes aren't registered, checkpoint storage isn't initialized, and the frontend hides the Chat UI — `GET /api/v1/config` returns `features.chat`, consumed via `src/features.context.ts` (`useFeature('chat')`); add new feature flags the same way (`settings.<X>_ENABLED` → `features.<x>` → `useFeature`). `CHAT_LLM_PROVIDER` is a sentinel: `mock` echoes user input (keyless, deterministic), and any other value routes through **LiteLLM** (`ChatLiteLLM`), so the provider/model surface is whatever LiteLLM supports rather than a fixed allowlist. `CHAT_LLM_MODEL` is the LiteLLM model id (prefer provider-namespaced, e.g. `anthropic/claude-sonnet-4-6`); legacy `CHAT_LLM_PROVIDER` values (`openai`/`anthropic`/`gemini`/`deepseek`) still namespace a bare model name. `CHAT_LLM_BASE_URL` (LiteLLM `api_base`) points chat at a self-hosted LiteLLM proxy or other OpenAI-compatible gateway — the proxy is an opt-in deployment, not a dependency. LiteLLM owns the cross-provider quirks (base_url/api_key handling, DeepSeek-shape endpoints, `reasoning_content` normalization) Seizu used to special-case; reasoning surfaces both as `additional_kwargs["reasoning_content"]` and injected `{"type":"thinking"}` content blocks, both read by `_chunk_reasoning_delta` and stripped from model context by `_strip_reasoning_context`. Real providers use native structured tool calls.

- **Streaming:** the route consumes `graph.astream(..., stream_mode="custom")` and re-emits tokens as a Vercel AI SDK UI Message Stream (SSE: `start`/`text-*`/`finish`, `data: [DONE]`). The node pushes chunks via `get_stream_writer()` as `{"kind": "token", "content": ...}`. The frontend uses `@ai-sdk/react` `useChat`.
- **Message tags (`reporting/services/chat_messages.py`):** `MessageTag` is the reusable mechanism for excluding selected messages from history/LLM context. Tags live in `additional_kwargs["seizu_tags"]` (round-trips through the checkpoint serializer), and `load_thread_messages` calls `drop_tagged(..., EPHEMERAL)` at the read boundary. Broken assistant outputs are tagged `BROKEN` and filtered from future model context so failed turns do not poison retries.
- **Permissions:** `chat:use` (Viewer+) gates the endpoint; `chat:tools:call` and `chat:skills:call` (Admin only) gate tool/skill use; `chat:bypass_permissions` (Editor+) gates the confirmation-bypass mode (UI toggle, headless runs — see Headless Agent Runs). Tool/skill calls also require the *underlying* MCP permission (e.g. `tools:call`, `skills:render`). For chat, `mcp_runtime` is called with `chat_safe_only=True`, which exposes only built-ins whose `required_permissions` are all in `_CHAT_SAFE_PERMISSIONS` (read/inspection only), plus mutating built-ins with an action-confirmation resolver. **Fail-closed:** a new write/delete tool is hidden from chat by default. The only intentional no-confirmation mutating exceptions are `reports__create` and `reports__clone`, because they create new private reports and do not modify existing resources; document any future exception at the tool registration.
- **State and limits:** thread id is namespaced server-side as `user:{user_id}:thread:{client_thread_id}` (via `chat_graph.namespaced_thread_id`) so clients can't reach another user's thread. History persists through the backend selected by `CHAT_CHECKPOINT_BACKEND`: DynamoDB (`langgraph-checkpoint-aws`, with optional MinIO/S3 offload for >350KB payloads) or PostgreSQL (`langgraph-checkpoint-postgres`, async pool owned by the app lifespan). `make sqlmodel_enable/disable` switches the report store and chat checkpointer together. The frontend persists the thread id in `localStorage` and rehydrates the conversation on mount via `GET /api/v1/chat/history?thread_id=` (`chat_graph.load_thread_messages` → `graph.aget_state`), so a reload resumes the same conversation. Persisted messages are trimmed by `CHAT_MAX_PERSISTED_MESSAGES`; history responses are bounded by `CHAT_HISTORY_LIMIT`; chat MCP tool output is bounded by `CHAT_TOOL_RESULT_MAX_ROWS` and `CHAT_TOOL_RESULT_MAX_BYTES`. `DynamoDBSaver` is sync boto3 but its async methods offload to a threadpool (`run_in_executor`), so it doesn't block the event loop.

### Configuration System

Reports + scheduled queries are stored in DynamoDB (default) or PostgreSQL (`sqlmodel`). The YAML config (`.config/dev/seizu/reporting-dashboard.yaml`, mounted as `/reporting-dashboard.yaml`) is a seed source.

YAML top-level: `queries` (named Cypher for `scheduled_queries` refs only; not served to frontend), `dashboard` (key of default report), `reports` (each with its own `queries` dict), `scheduled_queries` (**list** of SQS/Slack/log defs), `toolsets` (dict → `ToolsetDef`, each with a `tools` dict → `ToolDef`: `name`, `description`, `cypher`, `parameters`, `enabled`).

**Report storage** (`reporting/services/report_store/`): `base.py` (`ReportStore` ABC), `dynamodb.py` (single-table, composite PK/SK, Snowflake IDs via `SNOWFLAKE_MACHINE_ID`), `sql.py` (SQLModel/SQLAlchemy, 5 tables, non-secret `SQL_DATABASE_URL` plus `SQL_DATABASE_USER`/`SQL_DATABASE_PASSWORD`), `__init__.py` (`get_store()` factory, env `REPORT_STORE_BACKEND`, default `dynamodb`). Import `from reporting.services import report_store` — backend is transparent. Switch backends: `make sqlmodel_enable/disable` then `make down && make up`.

DynamoDB key design: per report `#METADATA`/`#LATEST`/`VERSION#<n>`; per scheduled query `SQ#<id>` + `SCHEDULED_QUERY_LIST`; default dashboard `#DASHBOARD/#POINTER`.

**SQL schema changes:** only small inline compatibility migrations exist in `sql.py` today — introduce a real framework (e.g. Alembic) before the next non-trivial change rather than adding more piecemeal ones. SQLite dev DBs created before nullable user emails keep the old `NOT NULL`; recreate the DB to test emailless users on SQLite.

**Seeding:** existing reports/queries unchanged unless content differs or `--force`. `cypher` fields resolve against the top-level `queries` dict, else literal. Keep the `Report` TS interface in `src/config.context.ts` in sync with the Pydantic `Report` in `reporting/schema/reporting_config.py`.

### Cartography Data Sync

`nostart` profile; entrypoint is already `cartography` (don't prefix it): `make sync_cve / sync_aws / sync_github / sync_crowdstrike / sync_k8s`.

### Scheduled Query Action Plugins

In `reporting/scheduled_query_modules/`; implement `ModuleInterface`. Required: `action_name()`, `setup()`, `handle_results(scheduled_query_id, action, results)`, `action_config_schema()` → `List[ActionConfigFieldDef]` (types: `string`, `text`, `number`, `boolean`, `string_list`, `select`; optional `warning` renders an alert above the field, and a **required boolean must be `True`** to validate — used for explicit acknowledgements). Optional `required_permission()` gates who may *configure* the action (enforced in `validate_action_configs`, hidden in the UI; served as `scheduled_query_action_permissions`). Schemas served via `GET /api/v1/config` as `scheduled_query_action_schemas`.

### Headless Agent Runs & Confirmation Bypass

`reporting/services/headless_chat.py:run_headless_chat()` drives a full chat-agent turn for a stored user (session persists as their `ChatSessionItem`, visible in their chat UI). Used by the `agent_chat` action and Temporal activities. Security model (don't weaken without explicit sign-off):
- **Identity:** headless runs execute as the scheduled query's *creator*. `reporting/authnz/headless.py:resolve_stored_user()` rebuilds their permissions from the last role claim seen on an authenticated request (`User.role`, synced by `get_or_create_user`); archived users hard-stop.
- **Confirmation bypass is permission-based:** `chat:bypass_permissions` (Editor+). `mcp_runtime.call_tool_for_chat(bypass_confirmations=True)` re-checks the permission and AUDIT-logs every bypassed execution; without the permission the call is blocked. Headless runs without bypass keep the normal confirmation flow (mutating tools fail closed for the run). Creator RBAC and `chat_safe_only` always apply.
- The same permission gates the chat UI "Bypass confirmations" toggle (`ChatStreamRequest.bypass_confirmations`, 403 without it; default off) and configuring the `agent_chat` action.
- Headless turns get a system-prompt addendum (`_HEADLESS_PROMPT_ADDENDUM` in chat_graph) telling the model nobody can answer: don't ask for confirmation, follow the skills, summarize blocks instead of retrying.

### Temporal Workflows

The `temporal` action module (`reporting/scheduled_query_modules/temporal.py`) starts a named workflow (from `WORKFLOW_REGISTRY` in `reporting/temporal_workflows/__init__.py`) with the query results; `reporting/temporal_worker.py` (`python -m reporting.temporal_worker`) executes them. Workflow/activity code lives in `reporting/temporal_workflows/` — workflows are deterministic (dataclasses + pure helpers from `shared.py` only); **all I/O belongs in `activities.py`**, and AI sessions go through `headless_chat.run_headless_chat` (see above).

Settings: `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`, `TEMPORAL_WORKER_ENABLED`, `TEMPORAL_WORKFLOW_MAX_RESULT_ROWS`, `TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS`; `AGENT_CHAT_MAX_RESULT_ROWS`, `AGENT_CHAT_TIMEOUT_SECONDS`. Docs: `docs/root/install/temporal-workflows.md`.

### Docker Services

| Service | Role |
|---------|------|
| `seizu` | FastAPI backend + static frontend |
| `seizu-node` | Bun dev server |
| `seizu-scheduled-queries` | Scheduled query worker |
| `seizu-temporal-worker` | Temporal workflow worker |
| `temporal` | Temporal dev server (`server start-dev`, in-memory; UI `:8233`) |
| `neo4j` | Graph database |
| `dynamodb` | DynamoDB Local (default backend) |
| `minio` (+ `minio-create-bucket`) | S3-compatible store for chat checkpoint offload |
| `postgres` | PostgreSQL (`sqlmodel` profile) |
| `sqs` | ElasticMQ (local SQS) |
| `authentik-*` | OIDC provider (`auth` profile) |
| `cartography` | Graph data sync (`nostart` profile) |

Dev URLs: frontend `:3000`, backend `:8080`, Authentik `:9000`, Temporal UI `:8233`.

**Dockerfile stages:** `base` (Python + uv + `pyproject.toml`/`uv.lock`, no deps) → `dev` (`uv sync --frozen --all-groups --all-packages --no-install-workspace`; used by compose `target: dev`) ; `nodebuilder` (`oven/bun`, builds frontend) ; `production` (`uv sync --frozen --no-dev --package seizu --no-install-workspace` + built frontend; `publish.yml` builds this, so pytest/pre-commit never ship).

**Reset volumes:** `docker compose down && docker volume rm seizu_neo4j_data seizu_dynamodb_data && docker compose up && make seed_dashboard`.

### Authentication

Two mechanisms resolve to the same `CurrentUser`:

1. **Cookie-based BFF** (browser SPA). Backend owns the OIDC Authorization Code + PKCE exchange. The IDP refresh token lives only in an AES-256-GCM-encrypted, HttpOnly, Secure, SameSite=Strict cookie (`seizu_session`); the access token stays in React state, never persisted. SPA refreshes via `POST /api/v1/auth/refresh` (cookie-authenticated). The session and `seizu_oauth_state` cookies share `SESSION_TOKEN_ENCRYPTION_KEY` but are domain-separated by AES-GCM associated data (`seizu-session-v1` / `seizu-oauth-state-v1`).
2. **Bearer JWT** (CLI, MCP, programmatic). Validated by `authnz.validate_bearer_token()`: `PyJWKClient` JWKS check first, then RFC 7662 introspection fallback when `OIDC_ENABLE_TOKEN_INTROSPECTION` is set and the token isn't a verifiable JWT (for opaque tokens). Shared by `get_current_user` (REST) and the MCP middleware.

`get_current_user` consumes only the Bearer path; the cookie is consumed only by the four `/api/v1/auth/*` routes. `_CSRFMiddleware` requires `X-Seizu-Csrf: 1` on mutating methods whenever the session cookie is present; Bearer-only requests are exempt.

**Auth-flow endpoints** (browser-only): `login` generates PKCE verifier + state + nonce into a 5-min `seizu_oauth_state` cookie (SameSite=Lax to survive the redirect back), returns the authorize URL (with `nonce` + `OIDC_AUTHORIZE_EXTRA_PARAMS`). `callback` verifies state, exchanges the code, validates the ID token (gated by `OIDC_VALIDATE_ID_TOKEN`), sets the session cookie, 302s to path-only `return_to`. `refresh` rolls Max-Age (capped at the IDP refresh token's absolute expiry); 401 + clears cookie on IDP rejection. `logout` clears the cookie and (gated by `OIDC_REVOKE_REFRESH_TOKEN_ON_LOGOUT`) best-effort-revokes the refresh token.

**Env vars** are defined and documented inline in `reporting/settings.py` (each with its default, purpose, and examples) and listed with comments in `.env.example` — that's the source of truth; don't duplicate the list here. The families: `JWKS_URL` / `JWT_*` (token validation), `OIDC_*` (BFF flow, discovery, introspection, scope, revocation), `SESSION_*` (cookie encryption + lifetime; `SESSION_TOKEN_ENCRYPTION_KEY` is required when auth is on), plus `DEVELOPMENT_ONLY_REQUIRE_AUTH` and `TALISMAN_FORCE_HTTPS` (both default-on; set `false` in dev).

### RBAC

Each authenticated request resolves a role from the JWT → `frozenset[str]` permissions, enforced at routes via `require_permission(*perms)` (factory in `reporting/authnz/__init__.py`). MCP enforces `tools:call` / `query:execute` via the `ContextVar` set in `_MCPAuthMiddleware`.

- **Built-in roles** (hierarchical Admin ⊇ Editor ⊇ Viewer) in `reporting/authnz/permissions.py` — no store I/O. **User-defined roles** live in the report store (version history); resolved via one `get_role_by_name`.
- Key files: `permissions.py` (`Permission` enum, `BUILTIN_ROLES`, `ALL_PERMISSIONS`, `resolve_permissions()`), `authnz/__init__.py` (`CurrentUser.permissions`, `require_permission()`, resolution), `routes/roles.py` (CRUD).
- Env: `RBAC_ROLE_CLAIM` (default `seizu_role`), `RBAC_DEFAULT_ROLE` (default `seizu-viewer`; `""` denies access).
- **Dev mode** (`DEVELOPMENT_ONLY_REQUIRE_AUTH=false`): `ALL_PERMISSIONS` on every request — no enforcement.

**Local Authentik:** `make auth_enable && make down && make up` (first run ~2 min; revert with `make auth_disable && make down && make up`). Fully automated via `.config/dev/authentik/blueprints/seizu.yaml`. Device code flow needs `flow_device_code` on the **Brand** (not the OAuth2Provider). Redirect URIs: `http://localhost:{3000,8080}/auth/callback`. Dev creds: `akadmin/devpassword`, `developer/devpassword`.

### Testing Patterns

- **Backend:** pytest + pytest-mock + pytest-asyncio (`asyncio_mode = auto`) in `tests/unit/reporting/`; all tests `async def`. Use `httpx.AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")` for routes; override auth via `app.dependency_overrides[get_current_user] = lambda: CurrentUser(...)`; mock async calls with `AsyncMock`. Reference: `tests/unit/reporting/routes/query_test.py`.
- **Frontend:** React Testing Library + Jest (Bun) in `__tests__/` next to components.

### Query Validator Fuzzing

Three layers: (1) **EXPLAIN write check** — `query_type != 'r'` blocks writes/schema; (2) **SSRF/admin/procedure guard** — `_scan_for_dangerous_constructs()` scans original, comment-stripped, and unicode-decoded forms for `LOAD CSV` (`_LOAD_CSV_RE`), `USE` (`_USE_CLAUSE_RE`), admin/catalog (`_ADMIN_COMMAND_RE`), and `apoc.cypher.*`/`gds.*`/`ai.*`/`genai.*` (`_DANGEROUS_FUNCTION_NAMESPACES`); every `CALL <procedure>` matched against `_DEFAULT_ALLOWED_PROCEDURES` + `QUERY_VALIDATOR_ALLOWED_PROCEDURES`; (3) **CyVer warnings** — never block.

Run the suite:
```bash
docker compose run --rm seizu uv run --frozen --no-sync pytest tests/unit/reporting/services/query_validator_test.py -v
```
APOC deep testing (read-classified but can make outbound HTTP): `make apoc_enable && make down && make up` … `make apoc_disable && make down && make up`.

**Attack vectors** (one row per technique tried, blocked or not — write queries, DDL/schema, disallowed procedures, `USE`, admin/catalog, `LOAD CSV` SSRF, APOC/GDS/GenAI functions, unicode/homoglyph, …) are catalogued exhaustively in `tests/data/query-fuzzing.csv` (`Technique, Cypher, Result, Blocked_By, Notes`) with the matching assertions in `query_validator_test.py`. Read those rather than a summary table here, and append a row for every new vector you try.

**Two-sided comment check:** scan the comment-stripped form to catch `CALL /* x */ apoc.`; scan the original to catch `//` inside string literals (e.g. URLs) where the stripper would hide a following `CALL apoc.`.

**Adding cases:** confirm whether Neo4j executes the vector (logs: `ExternalResourceFailed` / write side effects) → add a CSV row → add a unit test (mock `_get_async_neo4j_client`/`_get_sync_neo4j_client`, return summary with `query_type='r'` + no notifications, assert `result.has_errors`) → if not blocked, fix `query_validator.py` first, then prove with the test.

### Typing

Strict mypy (`check_untyped_defs`, `disallow_untyped_defs`). Annotate all functions; fix type errors instead of `# type: ignore` (and remove existing ones you touch); use `typing.cast` only for known-safe invariants.

## Branding

Deep-space observatory: dark navy canvas, starlight-blue primary accent, amber "ember" warm accent. Mark = dashed-orbit ring around a six-node constellation with one amber anchor star; wordmark = **Manrope ExtraBold (800)** at tight tracking. Tagline: "A star chart for your security graph." Don't introduce other brand colors or fonts.

**Colors:** the hex values live in `src/theme/brand.ts` (`brand` tokens + `chartPalette`/`chartColorsFor`) — that's the source of truth; import from there, don't hardcode or invent shades. Core tokens are Space (dark bg), Starlight (primary accent), Ember (warm accent), Paper (light surface), each with an AAA-contrast `*Dark` variant for light surfaces; the extended constellation accents (Aurora/Pulsar/Flare/Beacon) form the ordered 6-color chart palette consumed by `CypherBar`/`CypherPie`. Extend `brand.ts` rather than adding one-off colors. Starlight opacity rungs `0.04/0.08/0.12/0.25` via `rgba(143,180,255,α)`. (Docs CSS mirrors these as `--brand-*` custom props in `docs/root/_templates/splash.html`.)

**Typography:** Wordmark = Manrope 800 (`letter-spacing: -0.04..-0.05em`, logo only). Display = Instrument Serif 400 (docs splash may use EB Garamond italic). UI/body = Inter (web app) / IBM Plex Sans (docs splash) — don't mix within a surface. Mono = JetBrains Mono. All from Google Fonts; preconnect `fonts.googleapis.com` + `fonts.gstatic.com` before the stylesheet.

**Logo assets** live in two synced copies: `public/static/images/` (served at `/static/images/`) and top-level `images/` (`docs/root/images` is a symlink; README links `/images/...`). Files: `favicon.svg` (tab/PWA, orbit + amber core), `logo-horizontal-{white,black}.svg` (full lockup dark/light), `logo-mark{,-light}.svg` (mark-only dark/light). `public/favicon.png` is a legacy fallback (regenerating it is a TODO). Mark geometry: `viewBox="0 0 140 140"`, orbit `r=54` at `(70,70)`, ticks at 12/3/6/9, nodes `(40,55)`,`(62,42)`,`(86,52)`,`(100,78)`,`(68,78)`,`(50,98)` with amber anchor at `(62,42)`; sub-24px drops the constellation.

**Where tokens live today:** app — `src/components/Logo.tsx`, `public/favicon.svg`, `public/manifest.json` (`theme_color: #0a0f22`), `index.html` (MUI theme doesn't inherit these yet). Docs — `docs/conf.py` (`html_logo`/`html_favicon`/Shibuya logos); `docs/root/_templates/splash.html` defines `--brand-*` custom props (reuse them). README — `/images/logo-horizontal-{black,white}.svg` with `#gh-{light,dark}-mode-only`.

## Frontend Style Guide

List-view conventions live in shared components — compose these rather than re-implementing the markup. `Toolsets.tsx` is the canonical list page; `ToolDetailDialog.tsx` the canonical detail dialog; `ToolsetHistory.tsx` the canonical version-history page.

- **`src/components/ListTable.tsx`** — the table (search, filters, pagination, column resizing, empty state, truncation). Exports `ListTableColumn`/`ListTableFilterGroup` and cell `sx` helpers (`listTableSecondaryCellSx`, `listTableMonoCellSx`, `listTableActionColumnSx`, `listTableTruncateSx`, …).
- **`src/components/ListPageHeader.tsx`** — top bar: an h1 `title` (string or node) + optional right-aligned `action` (e.g. a `New …` button).
- **`src/components/ListViewState.tsx`** — loading/error gate (centered spinner / error-icon row) wrapping the table.
- **`src/components/RowMenu.tsx`** — per-row three-dot overflow menu. Pass a declarative `RowMenuAction[]` (`label`, `icon`, `onClick`, `disabled`, `tooltip`, `destructive`, `dividerBefore`); it owns the trigger/anchor/min-width, the divider-fenced destructive action in error color, and disabled-item tooltips.
- **`src/components/ConfirmDeleteDialog.tsx`** — `maxWidth="xs"` delete confirmation (body as children with the `<strong>` name; `deleting` spinner; `error` alert).
- **`src/components/DetailDialog.tsx`** — read-only detail dialog (title + optional `secondary` version label + close button), with exported `DetailSection` (labeled block) and `DetailCodeBlock` (monospace `action.hover` pre).

**Conventions not yet componentized:**
- **Clickable names:** `sx={{ cursor: 'pointer', '&:hover': { textDecoration: 'underline' } }}` on `<Typography>` (not `<Link>`) when click opens a dialog.
- **Create dialogs:** `maxWidth="sm" fullWidth`; `<CircularProgress size={20} />` in the submit button while in-flight; errors in `<Alert severity="error">` atop `<DialogContent>`.
- **Version-history pages:** route `<entity>/:id/history`; back `<Button startIcon={<ArrowBackIcon />}>`; title `<HistoryIcon color="action" />` + `<Typography variant="h1">Version history – {name}</Typography>`; columns Version/Saved/Created By/Comment/actions; current version bold + `"current"` in `color="primary"`; a single `RowMenu` Restore action, disabled on current, `comment: \`Restored from version ${version}\``; Helmet `History – {name} | Seizu`.
- **Column names + cells:** Latest Update / Updated By / Created By / Version / Status; dates `new Date(v).toLocaleString()`; users `<UserDisplay userId={...} />`.

## Keeping Supporting Files Up to Date

When changing the codebase, update as needed: **`Makefile`** (new targets), **`.github/workflows/`** (CI), **`Dockerfile`/`docker-compose.yml`** (services, files, env), **`.pre-commit-config.yaml`** (linting), **`pyproject.toml` + `uv.lock`** (commit together; `uv lock` after edits), **`docs/`** (user-facing changes — grep for removed items), **`schema/reporting-schema.json`** (`make schema` after Pydantic changes), **`.env.example`** (env vars), **`AGENTS.MD`** (conventions/tooling changes).
