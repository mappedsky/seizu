# Agent Guidelines

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What is Seizu?

Seizu (星図) is a configuration-driven reporting platform for Neo4j graph data. Three runtimes:
- **FastAPI web app** (`reporting/`) — serves the React frontend and REST API (ASGI, Gunicorn + UvicornWorker).
- **Temporal workflow worker** (`reporting/temporal_worker.py`) — owns configurable workflow schedules, **scheduled chats and interactive chat turns**, and runs ordered stages (parallel activities within each stage) with named outputs and earlier-stage input references. Activity modules include query, SQS, Slack, log, statsd, and code-defined workflows. Temporal Schedules provide durable interval/hourly/daily/monthly and watch-poll triggers. The old scheduled-query worker/API are compatibility aliases only.

## Decision Logs — read before changing these areas

Rationale lives in `docs/root/dev/decisions/`, one log per product area, with a
stable ID per decision (`SBX-004`). This file says *what* the system does; the
logs say *why*, and most entries exist because the obvious alternative was tried
and measured. **Read the relevant log before changing that area**, and cite IDs
rather than restating the reasoning.

| Log | Read before touching |
|-----|----------------------|
| [`chat-context.md`](docs/root/dev/decisions/chat-context.md) (`CTX`) | Context windows, trimming, compaction, prompt caching, token/cost budgeting |
| [`chat-agent.md`](docs/root/dev/decisions/chat-agent.md) (`AGT`) | Chat tool exposure, confirmation gating, progressive disclosure, headless runs, scheduled chats, how a turn is delivered |
| [`sandbox.md`](docs/root/dev/decisions/sandbox.md) (`SBX`) | `sandbox__delegate`, sandbox lifecycle, tool binding, session memory |
| [`spaces.md`](docs/root/dev/decisions/spaces.md) (`SPC`) | Space membership, visibility invariants, the overview pointer |
| [`report-store.md`](docs/root/dev/decisions/report-store.md) (`STO`) | Report record shape, the spaces GSI, SQL migrations |
| [`workflows.md`](docs/root/dev/decisions/workflows.md) (`WF`) | Temporal pipelines, cartography sync, remediation isolation |
| [`query-validation.md`](docs/root/dev/decisions/query-validation.md) (`QV`) | The Cypher validator and its fuzzing corpus |
| [`authentication.md`](docs/root/dev/decisions/authentication.md) (`AUTH`) | Identity resolution, issuer handling, OIDC configuration guards |

When you make a decision worth keeping, add an entry there rather than growing
this file or a docstring — see the log index for the format.

### Rationale belongs in the logs, and nowhere else

The decision logs are the **only** surface that carries *why*. Every other
surface carries *what*, written for whoever reads it next:

| Surface | Carries | Never carries |
|---------|---------|---------------|
| `docs/root/dev/decisions/*.md` | Why the choice was made, what was measured, which alternative failed, what breaks if it is reversed | — |
| Docstrings and code comments | What the code does, what it guarantees, what a caller must not do, non-obvious mechanics | Postmortems, incident history, measurements, "this used to…" |
| `reporting/settings.py` comments, `.env.example` | What the setting controls, its units, how to choose a value, what happens at `0`/empty | The history of the default, benchmark figures, the bug that prompted it |
| `docs/root/install/*.md`, README | What an operator must know to run, configure and troubleshoot it | Design justification, alternatives considered, internal measurements |
| This file (`AGENTS.MD`) | What the system does and where it lives | Extended reasoning — one clause of intent, then the decision ID |

**Cite, don't restate.** Where the reason genuinely helps a reader act, one
sentence plus the ID (`AGT-021`) is the whole allowance; the log holds the rest.
A comment saying what a line must not become is *what*, not why, and belongs in
the code:

```python
# Fitted against the ceiling: the window must hold whatever the model may
# return. What is reserved is a different number (AGT-021).
```
not
```python
# Reserving the ceiling was measured booking 762x the truth on a router
# verdict, and three parallel steps then reserved most of a run's budget
# before emitting a token, which is why this changed.
```

The reason for the split is practical: a docstring is read by someone changing
that function today, and history crowds out the contract they came for, while
the same paragraph in a log is exactly what a reader wants and is kept current
as one entry rather than in five copies.

## Running Commands with Docker

Never install bun, node, uv, or Python on the host. Always use `docker compose run --rm <service>`.

**Frontend (`seizu-node`):**
```bash
docker compose run --rm seizu-node bun add <package>
docker compose run --rm seizu-node bun run test:ci                       # all tests
docker compose run --rm seizu-node bun test src/path/to.test.tsx --watchAll=false
docker compose run --rm seizu-node bun run type-check
```
> Run the **full** suite through `test:ci`, not bare `bun test`. All 64 files
> share one Bun process and per-test wall time grows several times over as the
> run progresses, so the heaviest component tests cross Bun's 5s default and
> fail as timeouts — a different test each run. The script passes
> `--timeout 30000`; `bunfig.toml` has no equivalent setting and a preloaded
> `jest.setTimeout()` does not apply.

**Backend (`seizu`):**
```bash
docker compose run --rm seizu uv run --frozen --no-sync pytest tests/unit
docker compose run --rm seizu uv add [--dev] <package>
docker compose run --rm seizu uv lock          # after editing pyproject.toml by hand
docker compose build seizu                     # REQUIRED after any dependency change
```
> Always run tests via `uv run --frozen --no-sync` **in the container** so they use the synced Docker env and `uv.lock`. Commit `pyproject.toml` + `uv.lock` together.

> **A dependency change needs a rebuild, not a `uv sync`.** The environment lives
> in the image, and `docker compose run --rm` discards the container it synced —
> so `uv sync` appears to succeed, the new package is gone the moment the command
> exits, and the failure surfaces later as a `ModuleNotFoundError` in a test or at
> runtime, far from the change that caused it. After `uv add` (or any edit to
> `pyproject.toml`/`uv.lock`), run **`docker compose build seizu`**, then
> `docker compose up -d --force-recreate` for any service already running.
> `seizu-node` and `seizu-cartography-worker` are the same: rebuild the image
> whose dependencies changed. `make build` does it, `make test_unit` depends on
> it, and it is cached when `pyproject.toml` and `uv.lock` have not moved.

**Docs (Sphinx):** isolated venv under `docs/.venv`, pinned in `docs/requirements.txt`. Sphinx is **not** in the project `pyproject.toml` — don't add it there. `make docs` runs `docs/build.sh` (bootstraps the venv). To change a docs dep, edit `docs/requirements.txt` and re-run `make docs`.

**Linting:** run pre-commit **on the host** (the `seizu` container lacks git): `pre-commit run --all-files` or `make lint`.

**Git:** never push to `master`. Push feature branches and open a PR.

**Makefile shortcuts:**
```
make build              # rebuild the backend image; required after a dependency change
make test / test_unit / test_frontend
make lint
make schema             # regenerate schema/reporting-schema.json + schema/openapi.json
make generate_openapi   # export schema/openapi.json (no running server)
make generate_client LANG=go
make seed_dashboard [ARGS="--force|--dry-run|export"]
make auth_enable / auth_disable
make external_mcp_enable / external_mcp_disable / external_mcp_login
make build_cartography_worker     # build the cartography sync-worker image
make cartography_contract_test    # verify registry flags against the pinned CLI
make lock_proxy_requirements [REQUIREMENTS=... PYTHON_VERSION=... OUTPUT=...]  # re-lock the proxy's deps
make build_proxy_template         # build the E2B template the credential proxy runs on
make chat_harness USER_ID=<id> ARMS="baseline KEY=VALUE" [SAMPLES=4] [TURNS=2]
```

**Measuring chat agent settings:** `scripts/chat_harness.py` runs the same
conversation N times per arm (`TURNS` sets its length; the prompts refer back to
each other, so history genuinely has to be carried) and reports medians and
ranges. Tokens, cost and cache hit rate are summed across *every* turn, not read
off the last one, and `summary_chars` is non-zero when history compaction
engaged — reaching it at all needs a long run or a small
`CHAT_LLM_CONTEXT_MAX_TOKENS`. Use it rather than
comparing single runs — answer quality on an *unchanged* configuration has been
observed to vary several-fold, and single-run comparisons of this system have
repeatedly shown "clean separations" that vanished under more samples. Four
settings have been swept without a distinguishable difference between any of
them. It runs on the host (it recreates the `seizu` service between arms) and is
standard-library only. Note when reading any stream-derived metric: a subagent
detail is re-emitted after every inner tool call carrying the whole children
list, so counts must be de-duplicated on `detail_id` or they inflate
quadratically in the size of a delegation.

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
- `src/pages/ChatInterface.tsx` — chat assistant at `/app/chat` (`@ai-sdk/react` `useChat` over `SeizuChatTransport`, which admits a turn then attaches to it); gated on the `chat:use` permission. See the Chat backend section.

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
- `reporting/routes/` — `config.py`, `me.py`, `query.py`, `reports.py`, `users.py`, `validate.py`, `scheduled_queries.py`, `roles.py`, `sync_metadata.py` (distinct SyncMetadata values for watch-scan autocompletes), `static.py` (healthcheck only).
- `reporting/authnz/__init__.py` — `HTTPBearer` + `Depends(get_current_user)`; `CurrentUser` dataclass (`user`, `jwt_claims`, `permissions`); `sync_user_profile` chains on it; tests override `app.dependency_overrides[get_current_user]`.
- `reporting/services/` — async: `reporting_neo4j.py` (async driver + distributed locking), `report_store/`, `query_validator.py`.
- `reporting/schema/reporting_config.py` — Pydantic models for the YAML config; `report_config.py` — store items + API schemas.

**Async I/O:** Neo4j `AsyncGraphDatabase.driver()` (a sync `GraphDatabase.driver()` is kept only for CyVer validators via `asyncio.to_thread`); AWS integrations use boto3 clients; PostgreSQL uses `create_async_engine` + `AsyncSession` (`postgresql+asyncpg://`).

**Query validation** (`reporting/services/query_validator.py`): `async def validate_query()` → `ValidationResult(errors, warnings)`. Errors block; warnings don't. Layers:
1. Syntax + read-only check via `EXPLAIN`.
2. SSRF/admin/procedure guard (regex + allowlist) — blocks `LOAD CSV`, the `USE` clause, admin/catalog commands (`SHOW`, `TERMINATE`, db/user/role/privilege), and `apoc.cypher.*`/`gds.*`/`ai.*`/`genai.*` function namespaces. Every `CALL <procedure>` is allowlisted (side-effect-free schema procedures by default). Extend via `QUERY_VALIDATOR_ALLOWED_PROCEDURES` (exact names or namespace prefixes like `apoc.`); a namespace prefix also drops that namespace's dangerous-function guard.
3. CyVer `SchemaValidator` warnings; 4. CyVer `PropertiesValidator` warnings (both via `asyncio.to_thread`).

### API Endpoints

The route definitions in `reporting/routes/` are the source of truth. Don't maintain an endpoint list here — read the live spec instead: Swagger UI at `/api/docs`, raw spec at `/api/openapi.json`, or regenerate the checked-in copy with `make generate_openapi` (writes `schema/openapi.json`, no running server needed).

Conventions worth knowing up front: all paths are under `/api/v1/*` (except `/healthcheck`, `/api/openapi.json`, `/api/docs`); versioned entities (reports, roles, workflows, toolsets, tools) expose `GET .../<id>/versions` and `GET .../<id>/versions/<n>`, and reports additionally accept `POST /api/v1/reports/<id>/versions` to save a new version. The browser-only BFF auth routes live under `/api/v1/auth/*` — see the Authentication section.

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

- **Transport:** StreamableHTTP via `_MCPMiddleware` (pure ASGI in `app.py`), intercepting `/api/v1/mcp*` before FastAPI routing (avoids a Starlette 1.0.0 `Mount` trailing-slash issue). The SDK is **`mcp` 2.x**, which serves the 2026-07-28 revision and every earlier one from the same `Server` (1.x is security-fix-only, so don't pin back under `<2`). Both eras end up stateless — 2026-07-28 requests carry their own protocol version and need no handshake, and `stateless=True` on `StreamableHTTPSessionManager` covers the older handshake path.
- **Auth:** `_MCPAuthMiddleware` validates Bearer tokens via shared `authnz.validate_bearer_token()` (JWKS + optional RFC 7662 introspection); passes through unauthenticated in dev (`DEVELOPMENT_ONLY_REQUIRE_AUTH=false`). Sets two `ContextVar`s per request: `_mcp_permissions` (`frozenset[str]`) and `_mcp_current_user` (`CurrentUser`); handlers attributing writes read the latter.
- **Tool names:** `{group}__{action}` (built-ins) or `{toolset}__{tool}` (user-defined), double underscore.
- **Listing/dispatch:** the actual list/call/prompt logic lives in `reporting/services/mcp_runtime.py` (`list_tools_for_user`, `call_tool_for_user`, `list_prompts_for_user`, `get_prompt_for_user`) so the MCP transport and the chat agent share one implementation. Handlers are plain `async def (ctx, params)` functions passed to `Server(...)` as `on_list_tools` / `on_call_tool` / `on_list_prompts` / `on_get_prompt` (mcp 2.x dropped the `@server.list_tools()` decorators and the `request_handlers` dict) and return the full result model — `ListToolsResult`, `CallToolResult`, … — rather than a bare list. They ignore `ctx`: the caller comes from the `ContextVar`s the auth middleware sets, which the SDK's per-request task inherits. `list_tools` merges `list_builtin_tools(MCP_ENABLED_BUILTINS)` (filtered by caller permissions) with `report_store.list_enabled_tools()`. `call_tool` tries `find_builtin()` first, else runs user-defined tools via `reporting_neo4j.run_query()`. Built-ins enforce their own `required_permissions`. Protocol types are snake_case on the Python side and aliased to the camelCase wire form (`Tool.input_schema` ↔ `inputSchema`). Tests reach handlers through `tests/unit/reporting/services/mcp_dispatch.py` rather than the SDK internals.
- **Built-ins** live in `reporting/services/mcp_builtins/` (one module per group, registered in `__init__.py`). Each is a `BuiltinTool` dataclass (`base.py`) sharing the REST service layer — no duplicated logic; permissions mirror REST. Groups (all default-on): `graph` (schema, query, validate_query, explain), `reports`, `scheduled_queries`, `skillsets`, `toolsets`, `roles` — full CRUD + version history. To add a group: export `GROUP_DEF: BuiltinGroup` from a new module, append to `_GROUPS` in `__init__.py` and the `settings.MCP_ENABLED_BUILTINS` default.
- **OAuth metadata:** `GET /api/v1/mcp/.well-known/oauth-authorization-server` (RFC 8414) when `MCP_OAUTH_AUTHORIZATION_ENDPOINT` + `MCP_OAUTH_TOKEN_ENDPOINT` are set (also `MCP_OAUTH_ISSUER`); lets clients like Claude Desktop discover the IDP. `.well-known` passes through unauthenticated.

**Toolsets/Tools** are user-managed Cypher-backed tool groupings in the report store with version history; CRUD via REST; Cypher validated (writes blocked) at create/update.

PostgreSQL tables: `toolsets`, `toolset_versions`, `tools`, `tool_versions`.

### External MCP Proxies (`reporting/services/external_mcp.py`)

`MCP_EXTERNAL_ENABLED` gates both the agent capability and the optional local
Compose profile (`make external_mcp_enable` / `make external_mcp_disable`). The
local `external-mcp` profile wires Seizu straight to the PAT adapter — one
service identity, read-only, no credential to expire; the obot OAuth proxy is
the separate `external-mcp-oauth` profile, for exercising the RFC 9728 challenge
path, and is the only thing `make external_mcp_login` is for.
`MCP_EXTERNAL_PROXIES` is a JSON array of SSE or Streamable HTTP proxy
definitions used by the chat agent. External tools are dynamically discovered,
namespaced as `ext__<proxy>__<tool>`, and deliberately not re-exported from
Seizu's own MCP endpoint. The `/app/toolsets` catalog exposes configured proxies
and their per-user discovery results as read-only synthetic toolsets. Each
discovery/call creates a new transport with
headers derived for exactly one `CurrentUser`; never pool these transports or
their delegated identity headers across users. Bearer/M2M secrets are named by
`token_env`, not embedded in JSON. A call an upstream refuses as rate-limited is retried after the
delay the refusal itself names, bounded by `MCP_EXTERNAL_RATE_LIMIT_*` (AGT-029). The web and Temporal worker must receive the
same proxy config and token env vars.

Confirmation is decided per tool: the local
`MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS` exact-name list wins, then MCP
`readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` annotations,
then the proxy's `require_confirmation` fallback (default true). Bypass still
requires `chat:bypass_permissions` and is audit-logged. RFC 9728 401 challenges
surface as a structured authentication block, and M2M 401s have a distinct
`ExternalMCPTokenExpired` exception before chat normalization. Interactive
Temporal turns do not persist the browser bearer token, so use M2M plus a target
user header (or trusted header delegation) for detached/headless execution.
Decisions: [`AGT-010`](docs/root/dev/decisions/chat-agent.md). Docs:
`docs/root/install/external-mcp.md`.

### Chat (`reporting/services/chat_graph.py`, `reporting/routes/chat.py`)

A LangGraph-backed chat assistant at `/api/v1/chat/*` (frontend `src/pages/ChatInterface.tsx`, route `/app/chat`), gated by the `CHAT_ENABLED` setting (default false). When off, the chat routes aren't registered, checkpoint storage isn't initialized, and the frontend hides the Chat UI — `GET /api/v1/config` returns `features.chat`, consumed via `src/features.context.ts` (`useFeature('chat')`); add new feature flags the same way (`settings.<X>_ENABLED` → `features.<x>` → `useFeature`). `CHAT_LLM_PROVIDER` is a sentinel: `mock` echoes user input (keyless, deterministic), and any other value routes through **LiteLLM** (`ChatLiteLLM`), so the provider/model surface is whatever LiteLLM supports rather than a fixed allowlist. `CHAT_LLM_MODEL` is the LiteLLM model id (prefer provider-namespaced, e.g. `anthropic/claude-sonnet-4-6`); legacy `CHAT_LLM_PROVIDER` values (`openai`/`anthropic`/`gemini`/`deepseek`) still namespace a bare model name. `CHAT_LLM_BASE_URL` (LiteLLM `api_base`) points chat at a self-hosted LiteLLM proxy or other OpenAI-compatible gateway — the proxy is an opt-in deployment, not a dependency. LiteLLM owns the cross-provider quirks (base_url/api_key handling, DeepSeek-shape endpoints, `reasoning_content` normalization) Seizu used to special-case; reasoning surfaces both as `additional_kwargs["reasoning_content"]` and injected `{"type":"thinking"}` content blocks, both read by `_chunk_reasoning_delta` and stripped from model context by `_strip_reasoning_context`. Real providers use native structured tool calls.

- **Turns run on Temporal; chat is a client (`reporting/services/chat_turns.py`, `reporting/temporal_workflows/chat_turn.py`):** sending is admit-then-attach. `POST /api/v1/chat/threads/{thread_id}/turns` atomically reserves the thread, stores an immutable command (work, permission cap, timeout) under a required idempotency key, and starts `seizu_chat_turn`; `GET /api/v1/chat/turns/{turn_id}/stream` tails its append-only event log. A retry only resolves the stored command and ensures the same workflow — it never dispatches the retry body.
  The activity writes already-rendered Vercel AI SDK stream parts in `seq`-numbered batches, so live delivery and replay are byte-identical. `maximum_attempts=1`: retrying an expensive, non-idempotent turn would bill twice and append a second answer. Identity is rebuilt worker-side and intersected with the stored permission cap, never unioned. `CHAT_ENABLED` therefore implies a reachable Temporal server.
  `GET /api/v1/chat/threads/{thread_id}/turns/active` reattaches after reload; `POST /api/v1/chat/turns/{turn_id}/cancel` marks and cancels the workflow. Disconnecting does not stop a turn. Session deletion takes the SBX-011 retirement claim, stops the turn, fails closed on uncertainty, and removes the session record last.
  One running turn per thread is enforced by the store. A running turn's `expires_at` is a claim derived from `CHAT_TURN_TIMEOUT_SECONDS` plus an internal safety margin; finish re-stamps it with `CHAT_TURN_RETENTION_SECONDS`. Frontend transport lives in `src/api/chatTransport.ts`. **Read [AGT-008](docs/root/dev/decisions/chat-agent.md) before changing admission, streaming, the log, reader termination, exclusivity, or timing.**
- **What a call may spend is derived, not configured (`reporting/services/chat_models.py`):** a `ModelSpec` (model id, output ceiling, reasoning effort) is resolved once per call and passed down; `build_chat_model` is memoized on the **spec**, not on `(role, economy)`. The ceiling is `min(CHAT_LLM_MAX_OUTPUT_TOKENS_CAP, the model's own max_output_tokens)` from litellm — a constant is wrong in both directions (models report 16,384 to 393,216, and an over-ceiling request is refused outright). `CHAT_LLM_MAX_TOKENS` and `CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS` default to `0` = derive. On reasoning models thinking and answer share one allowance, so a low ceiling removes the answer rather than shortening it: at the old 4096 the planner returned `chars=0, finish_reason=length` every time and fell back to a one-step plan, silently disabling all orchestrator parallelism. `reasoning_effort` is the one portable knob (LiteLLM maps it to Anthropic/DeepSeek `thinking`, Gemini `thinkingConfig`, OpenAI `reasoning_effort`); never send `thinking` directly, and it must go through **`model_kwargs`** — `ChatLiteLLM` does not declare it, so a constructor argument is silently swallowed. `litellm.drop_params` is False, so the `supports_reasoning` gate is load-bearing. Effort keys on **stage** (`worker_summary` runs on the worker's model with its own budget) and is **rendered per provider** by `chat_models.reasoning_kwargs` — `reasoning_effort` for OpenAI/Gemini, `thinking.budget_tokens` (a share of the ceiling) for Anthropic, `extra_body` for DeepSeek — because LiteLLM's own mapping collapses every level to one value on the latter two. A stage's reasoning tokens (`output_token_details.reasoning`, a subset of `output_tokens`) and the effort actually on the wire (`chat_models.applied_reasoning_effort`, read off `model_kwargs` rather than re-resolved) are on every model-call span, and the planner and synthesizer are graded `low` rather than left to the provider — a provider's levels are not all distinct (DeepSeek collapses medium/high/xhigh into its default), so measure reasoning tokens rather than trusting the name (AGT-033). `chat_models.temperature_for` omits temperature where a model refuses it (OpenAI reasoning models raise) and forces 1.0 for Anthropic thinking. All effort settings default to empty. A tool-calling message must be replayed with `reasoning_content` (DeepSeek) *and* signed `thinking_blocks` (Anthropic) intact — `_strip_reasoning_context` flattens content, so both live in `additional_kwargs`. Measure plan shape with `scripts/plan_probe.py`, not a full harness turn. **Read [AGT-019](docs/root/dev/decisions/chat-agent.md).**
- **A plan's independent steps are distributed (`reporting/temporal_workflows/chat_step_fanout.py`, `reporting/services/chat_step_worker.py`):** an orchestrated *interactive* turn hands each dispatch batch to a `seizu_chat_step_fanout` workflow that schedules one `run_chat_worker_step` activity per step, so steps are placed across the fleet, time out and fail independently, and are separately visible and cancellable. The turn itself stays one activity — routing, planning, verification and synthesis are sequential and synthesis stays single-producer. Consequences that are load-bearing: the store now **allocates** `seq` (`append_chat_turn_events(turn_id, parts_json) -> int | None`) because a turn has several producers and the reader truncates at the first gap; each step gets a **pre-allocated budget grant** (`chat_budget.grant_ledger` / `BudgetController.absorb`) that is a hard cut rather than a share of a live ledger; oversized step results spill to `chat_turn_payloads` rather than travelling through Temporal history; identity is rebuilt worker-side and intersected with the admitted cap, never carried; and distributed workers **attach** to the conversation's sandbox without owning it ([SBX-015](docs/root/dev/decisions/sandbox.md)), which is why the coordinator opens it eagerly before a distributed batch. Headless runs are never distributed (`build_turn_config` carries `turn_id`; empty is the switch). `CHAT_ORCHESTRATOR_MAX_PARALLEL` bounds a turn, `TEMPORAL_MAX_CONCURRENT_ACTIVITIES` bounds the cluster. **Read [AGT-018](docs/root/dev/decisions/chat-agent.md) before changing the fan-out, sequencing, grants, payload carriage, or the fallback rule.**
- **The plan is a validated DAG (`chat_orchestrator._plan_problems`, `_init_plan`, `_replan_invalid_graph`):** `depends_on` is the edge set, and the contract is stated in the planner's *schema* (field description + `_Plan` docstring, both of which travel as JSON Schema) as well as the prompt: unique ids, no self-edges, no references outside the plan, no cycles, nothing left waiting on one. An invalid graph is **replanned once**, shown its own edges and the problems; only if that is also invalid does `_init_plan` repair — dangling/self edges dropped, duplicate ids renamed rather than dropped, cycles cut by freeing their earliest member. `_truncate_plan` removes the edges `CHAT_ORCHESTRATOR_MAX_STEPS` orphans, so our own cut never reaches validation. Diagnostics go to `run_errors` and the plan detail's body; `_fail_unreachable_steps` fails a step that can never run instead of leaving it pending. Budget slicing keys on schedule depth, not outstanding-step count (`_remaining_waves` / `_budget_divisor`). **Read [AGT-020](docs/root/dev/decisions/chat-agent.md) before changing plan validation, repair, or the budget divisor.**
- **A step maps over what an earlier step discovered (`chat_orchestrator._expand_mapped_steps`):** `_PlannedStep.map_over` names a dependency; when it passes, the dispatcher extracts items from its output (`_MapItems`), and the step is replaced by one child per item while the parent takes the status `expanded` and never runs. Children are ordinary steps, so the ready set, the fan-out and the retry cycle carry them unchanged — this is the only place graph structure is produced at run time. Load-bearing details: child ids come from `_child_step_id` (item slug + digest, **never** position, or AGT-018's idempotency key moves); `CHAT_ORCHESTRATOR_MAX_EXPANSION` bounds the list where it arrives and the cut is reported in `run_errors`; a step with no items or a failed extraction clears `map_over` and runs as written; `_dependency_satisfied` makes a dependent of an `expanded` step wait for every child; a `map_over` whose source is already `expanded` chains **1:1** over that source's children, with no extraction call and a per-item edge; and `expanded` is excluded from the outstanding set in `_remaining_waves`, from `_terminal_status`/`_terminal_errors`, and from the synthesis context. **Read [AGT-023](docs/root/dev/decisions/chat-agent.md) before changing expansion, child ids, or what `expanded` means.**
- **Fan-out is for divergent work, and a batch is seeded with what all of it wants (`_seed_shared_schema`):** the planner is told to fetch in bulk when the per-item work is the same call with a different argument, and to set `map_over` only when each item needs its own investigation — paired with `map_reason`, which also rides the `chat expand step` span. Separately, the steps of a batch start together, so a receipt written by one reaches a sibling only after the batch returns: the graph schema is therefore fetched once before a distributed batch and its receipt recorded before the ledger is serialized for the workers. Carrying a dependency's files to a dependent step is **not** the fix and was reverted — `session_digest` and `EpisodeLog.recall` already do it. **Read [AGT-027](docs/root/dev/decisions/chat-agent.md).**
- **The call ceiling is derived from the plan (`chat_budget.derived_call_ceiling`, `set_planned_steps`):** `CHAT_RUN_MAX_LLM_CALLS` defaults to `0` = derive (`8 + CHAT_RUN_LLM_CALLS_PER_STEP x steps`), recomputed by `_refresh_remaining_estimate` wherever the plan changes — including expansion. It is a loop guard, not a spend limit (cost and tokens bound spend, loop detection catches loops), so a fixed count really bounds *plan size*: 64 was set when a plan was at most eight steps. `set_planned_steps` **only raises**, or a shrinking plan would finalize a run retroactively. The ceiling **only ever fires**: it is out of the soft-limit calculation (degrading a run on call count downgraded its model at 17.6% of spend) and takes the batch-width bound rather than the schedule divisor, unless neither cost nor tokens is budgeted. **Read [AGT-024](docs/root/dev/decisions/chat-agent.md) and [AGT-030](docs/root/dev/decisions/chat-agent.md).**
- **Tracing (`reporting/services/telemetry.py`):** OTLP spans for the turn, each dispatch batch, each plan step, each expansion and every model call, carrying the tokens/cost/stop-reason the run already computes. Off unless `TELEMETRY_OTLP_ENDPOINT` is set; any collector works and LangSmith is one of them. The **Temporal interceptor on client and worker** is what makes it useful — a turn spans three processes, and without propagated context the spans are unrelated trees. Three rules: content (prompts, results, tool output, exception *messages*) is excluded unless `TELEMETRY_RECORD_CONTENT`; nothing here is load-bearing (a span is a no-op when off, `configure()` swallows its failures, budget decisions read `chat_budget`); and span attributes are not metric labels — step ids are content-derived and high-cardinality. A sandbox sub-agent's calls are traced at the budget reservation in `_ToolMessageNormalizingModel.ainvoke`, not in `_run_llm_tool_turn` — they never reach it, and without that span most of a delegating turn is missing from the trace (AGT-028). Tool calls are traced at `mcp_runtime._guarded` and the sub-agent's tool wrapper, carrying an `outcome` of `ok`/`error`/block-reason — a tool that *ran* and returned a refusal is otherwise invisible, and external rate limits differ enough between runs to invalidate a cost comparison (AGT-029). **Read [AGT-026](docs/root/dev/decisions/chat-agent.md).**
- **Only the dimension that bounds the run is fair-shared (`_cost_is_primary`, `_grant_for`, `_step_thresholds`):** the schedule divisor ([AGT-020](docs/root/dev/decisions/chat-agent.md)) shares what is left between the steps still to run; the batch width is what keeps concurrent grants disjoint. When cost is the budget, tokens keep **only** the batch-width bound — slicing a backstop by schedule turned a 2M-token ceiling into 66k per step on a 17-step expanded plan and cut every later step short with 87% of the cost unspent. Do **not** grant the whole remainder instead: grants must stay disjoint (AGT-018) because cost never binds on a model LiteLLM cannot price. Related: a step-level stop carries `budget_step_share` so `_terminal_status` reports `partial` rather than `budget_exhausted`, and `_dimension_share`/`grant_ledger` withhold the finalization reserve on cost and calls as the token path always did. **Read [AGT-025](docs/root/dev/decisions/chat-agent.md).**
- **Message tags (`reporting/services/chat_messages.py`):** `MessageTag` is the reusable mechanism for excluding selected messages from history/LLM context. Tags live in `additional_kwargs["seizu_tags"]` (round-trips through the checkpoint serializer), and `load_thread_messages` calls `drop_tagged(..., EPHEMERAL)` at the read boundary. Broken assistant outputs are tagged `BROKEN` and filtered from future model context so failed turns do not poison retries. The same file stamps each turn's time (`stamp_created_at`/`created_at`, also in `additional_kwargs`), applied by the `add_timestamped_messages` reducer on `ChatState.messages` — one choke point, so no message-producing path can forget — and only for ids new to the state, since nodes routinely return the whole list and pre-timestamp history must not be back-dated. Surfaced as `metadata.created_at` on `/chat/history`.
- **Permissions:** `chat:use` (Viewer+) gates the endpoint; `chat:tools:call` and `chat:skills:call` (Editor+) gate tool/skill use; `chat:bypass_permissions` (Editor+) gates confirmation bypass. Tool/skill calls also require the *underlying* MCP permission (e.g. `tools:call`, `skills:render`). Chat calls `mcp_runtime` with `chat_safe_only=True` and **fails closed** — a new write/delete tool is hidden from chat by default. Report configs are validated at save time (`CreateVersionRequest.validate_report_config`). The intentional no-confirmation exceptions and their conditional resolvers are **[AGT-001](docs/root/dev/decisions/chat-agent.md)**; document any new one there and at the tool registration.
- **State and limits:** thread id is namespaced server-side as `user:{user_id}:thread:{client_thread_id}` (via `chat_graph.namespaced_thread_id`) so clients can't reach another user's thread. History persists through `langgraph-checkpoint-postgres`, using an async pool owned by the app lifespan and independently configurable `CHAT_CHECKPOINT_DATABASE_*` credentials. The frontend persists the thread id in `localStorage` and rehydrates the conversation on mount via `GET /api/v1/chat/history?thread_id=` (`chat_graph.load_thread_messages` → `graph.aget_state`), so a reload resumes the same conversation. Persisted messages are trimmed by `CHAT_MAX_PERSISTED_MESSAGES`; history responses are bounded by `CHAT_HISTORY_LIMIT`; chat MCP tool output is bounded by `CHAT_TOOL_RESULT_MAX_ROWS` and `CHAT_TOOL_RESULT_MAX_BYTES`. Docs: `docs/root/install/chat.md`.

- **Context, caching and cost** — see [`CTX`](docs/root/dev/decisions/chat-context.md). Context is budgeted in **tokens** against the model's own window (`chat_context.py`): `history_token_budget` = min(`CHAT_LLM_CONTEXT_MAX_TOKENS`, window × `CHAT_LLM_CONTEXT_WINDOW_SHARE`), and `_run_llm_tool_turn` fits the whole request via `_fit_messages_to_window`, retrying a provider overflow once. Cross-turn history is **compacted, not truncated** (`_compact_history`), deterministically and into a fenced block with a reserved budget share. Volatile content (the session digest) goes **last, never in the system prompt**; Anthropic additionally needs explicit `cache_control` breakpoints (`with_cache_breakpoints`). Budgeting is cache-aware on commit but reserves the **uncached** price (`chat_budget.py`). `CHAT_LLM_CACHE_DIAGNOSTICS` reports which part of a prefix moved. **CTX-001 through CTX-009 record what was measured and which alternatives failed — read them before changing any of this; several of these rules look arbitrary and are not.**
- **A reservation is an authorization, not a prediction (`chat_budget.BudgetController`):** `projected_output_tokens` sizes a call's reserved output from what its phase family has been observed to emit (EWMA x `CHAT_BUDGET_OUTPUT_ESTIMATE_SAFETY`, floored at 256, capped by the model's ceiling, seeded by `CHAT_BUDGET_OUTPUT_ESTIMATE_TOKENS`) — never `max_output_tokens`. The context window is still fitted against the *ceiling*; only the reservation shrinks. `_authorize_locked` separates **committed** overrun (raises and finalizes) from **in-flight** overrun (returns a reason; `reserve` waits up to `CHAT_BUDGET_CONTENTION_WAIT_SECONDS` and re-authorizes against actuals), so contention throttles concurrency instead of ending the run. The LLM-call dimension never waits — a commit raises `llm_calls` by exactly what it removes from in-flight. `CHAT_RUN_COST_BUDGET_USD` defaults to $2.00 and is the intended runaway guard; `CHAT_RUN_TOKEN_BUDGET` defaults to `0` = **derive** (`chat_budget.derived_token_ceiling`) — no token ceiling when the model is priced, `CHAT_RUN_UNPRICED_TOKEN_BUDGET` when litellm cannot price it (`ModelCapability.priced`), since a fixed backstop is 8x too tight on a cheap model and 5x too loose on a frontier one and so can never let cost bind. Measure a phase's reserved-vs-actual with `scripts/budget_probe.py`. **Read [AGT-021](docs/root/dev/decisions/chat-agent.md) before changing reservation sizing, the authorize/wait split, or the budget defaults.**
- **A run is budgeted in cost, not tokens (`CHAT_RUN_COST_BUDGET_USD`):** it bounds the run *and* each plan step's share of it; `CHAT_RUN_TOKEN_BUDGET` (default 2,000,000) is the backstop for a model LiteLLM cannot price. A budget scope carries a cost ceiling beside its token pair, and `scope_exhausted`/`scope_soft_limit_reached` are true when **either** binds — without that, raising the token ceiling would have removed step-level fair-share entirely. `_step_thresholds` returns `_StepThresholds` (both dimensions) and gives **no** token bound when tokens are unbudgeted, rather than falling back to the complexity floor; the floor applies only when neither dimension is set. `_dimension_share` takes the wave-aware divisor, so cost and call grants slice a bottleneck like tokens do. **Read [AGT-022](docs/root/dev/decisions/chat-agent.md) before changing what a run or a step is budgeted in.**

### Configuration System

Reports + scheduled queries are stored in PostgreSQL. The YAML config (`.config/dev/seizu/reporting-dashboard.yaml`, mounted as `/reporting-dashboard.yaml`) is a seed source.

YAML top-level: `queries` (named Cypher for `scheduled_queries` refs only; not served to frontend), `dashboard` (key of default report), `reports` (each with its own `queries` dict), `scheduled_queries` (**list** of SQS/Slack/log defs), `toolsets` (dict → `ToolsetDef`, each with a `tools` dict → `ToolDef`: `name`, `description`, `cypher`, `parameters`, `enabled`).

**Report storage** (`reporting/services/report_store/`): `base.py` is the persistence contract/test seam, `sql.py` is the sole implementation, and `__init__.py` preserves the module-level service API used by callers. Configure PostgreSQL with non-secret `SQL_DATABASE_URL` plus `SQL_DATABASE_USER`/`SQL_DATABASE_PASSWORD`. Removed backend selectors and DynamoDB persistence settings are startup errors so an incomplete migration cannot open an empty database.

**SQL schema changes:** Alembic is the sole startup schema owner. `SQLModelReportStore.initialize()` runs `reporting/services/report_store/migrations.py`, which upgrades to head under a pg advisory lock. Add a revision in `reporting/migrations/versions/`; `0005_spaces.py` is the current template. **Every operation must be inspector-guarded** and verified against both a fresh volume and an upgraded database ([`STO-004`](docs/root/dev/decisions/report-store.md)). Unit tests may inject SQLite engines as an isolated test seam, but runtime configuration accepts PostgreSQL only.

### Spaces

Spaces group reports; sub-spaces group reports within a space. Flat records — **no version history, no access scope** — so there are no `/versions` endpoints and `list_spaces` is unfiltered; report-level visibility still filters what `list_space_reports` returns. Schemas in `reporting/schema/space_config.py`, routes in `reporting/routes/spaces.py`, cross-entity validation in `reporting/services/spaces.py` (`resolve_report_space`, `resolve_overview_report`, and the `SPACE_MEMBER_ACCESS` guards, shared so REST and the MCP report builtins can't diverge). Reports carry nullable `space_id`/`subspace_id`; membership is unversioned parent metadata written via `update_report_space` with **replace semantics**. **Decisions: [`SPC`](docs/root/dev/decisions/spaces.md).**

Invariants worth knowing before you touch this: `subspace_id` requires `space_id` and must belong to it (400 via `resolve_report_space`, deliberately not a pydantic validator, which would yield 422). **A report filed in a space is public** — filing a draft is a 409, unpublishing a member is a 409, creating into a space publishes, and all three are enforced **in the store**, not just the routes (SPC-002). Membership never enters a version item or `config`, or restoring a version would relocate the report (SPC-003). **The space overview is a nullable pointer** resolved lazily by `GET /spaces/{id}/tree` and blanked everywhere else (SPC-004). `delete_space` returns `SpaceDeleteResult` rather than a bool; only reports block a delete, sub-spaces cascade, and no report is ever deleted with a space (SPC-005). Permissions `spaces:read` (Viewer) / `spaces:write` / `spaces:delete` (Editor, matching reports); sub-spaces and the overview pointer reuse them.

The **`spaces` MCP builtin group** mirrors the routes one-for-one (no `*_versions` tools — spaces are flat), calling the same shared helpers in `services/spaces.py` so REST and MCP cannot diverge. Filing a report is `spaces__set_report_space`, in the `spaces` group (disabling the group drops the whole feature) but keeping the route's `reports:write` permission. **Every mutating tool in the group is confirmation-gated with no exception, and a test asserts it** (SPC-006).

**YAML round-trip:** top-level `spaces:` (`SpaceDef`/`SubspaceDef` in `seizu_schema/reporting_config.py`, lower_snake_case keys) plus `Report.space`/`Report.subspace`. YAML keys are local handles — spaces and sub-spaces are matched **by name** on seed, like reports, since ids are server-generated. The three cross-references (report→space, report→sub-space, space overview→report filed in it) are validated by a `ReportingConfig` model validator at load, so a typo fails before any write. `space`/`subspace` are seed metadata like `pinned`: applied via `PUT /reports/{id}/space` after the version save and stripped from the stored config (`exclude={"pinned", "space", "subspace"}`), or restoring a version would relocate the report. Seed order is spaces → reports → membership → overview. **An omitted `space` never unfiles a report** (the `pinned` rule), and seeding deletes nothing. `seizu export` emits all of it and reuses existing keys. Docs: `docs/root/install/spaces.md`.

**Seeding:** existing reports/queries unchanged unless content differs or `--force`. `cypher` fields resolve against the top-level `queries` dict, else literal. Keep the `Report` TS interface in `src/config.context.ts` in sync with the Pydantic `Report` in `reporting/schema/reporting_config.py`.

### Cartography Data Sync

Manual runs: `nostart` profile; entrypoint is already `cartography` (don't prefix it): `make sync_cve / sync_aws / sync_github / sync_crowdstrike / sync_k8s`. Scheduled runs go through the `cartography_sync` Temporal workflow (see Temporal Workflows) executed by the dedicated `seizu-cartography-worker` service — a separate image (`Dockerfile.cartography`: upstream cartography pinned by tag+digest + the top-level `cartography_sync/` package's thin activity worker; after bumping the pin run `make cartography_contract_test`) on its own task queue, holding only cartography intel credentials.

`cartography_sync/registry.py` is the **security boundary** — per-module typed flag allowlists, fixed credential env-var names/paths, argv-list exec (never a shell), scrubbed subprocess env — and the activity re-validates params, re-enforces `CARTOGRAPHY_ENABLED_MODULES`, and rebuilds argv worker-side. Each module run is a `cartography_module` child workflow whose fixed workflow ID is the per-module mutex; `temporal_runs.py` folds child-workflow events into the runs UI. The sync worker must **not** import `reporting.*` (that pulls pydantic settings) — it reads plain env vars (`CARTOGRAPHY_NEO4J_URI`, `CARTOGRAPHY_TASK_QUEUE`, …); `registry`/`shared` stay stdlib-only, `activities`/`worker` may use temporalio. **Decisions: [`WF-003`, `WF-004`](docs/root/dev/decisions/workflows.md) — read before changing the registry or module scheduling.** Docs: `docs/root/install/cartography-sync.md`.

### Workflow Activity Plugins

In `reporting/scheduled_query_modules/`; implement `ModuleInterface`. Modules are configured by `WORKFLOW_ACTIVITY_MODULES` and execute as Temporal activities in the order stored by the top-level configurable workflow. Required: `action_name()`, `setup()`, `handle_results(scheduled_query_id, action, results)`, `action_config_schema()` → `List[ActionConfigFieldDef]` (types: `string`, `text`, `number`, `boolean`, `string_list`, `select`, `parameters`, `module_runs`; optional `warning` renders an alert above the field, and a **required boolean must be `True`** to validate — used for explicit acknowledgements). Code-defined workflows are **not** activity modules: they are top-level activity types from `WORKFLOW_REGISTRY` (the former `workflow`/`temporal` dispatcher module is removed; stale `WORKFLOW_ACTIVITY_MODULES` entries naming it are skipped with a warning). Schemas are served via `GET /api/v1/config` as `workflow_activity_schemas`; scheduled-query keys remain compatibility aliases.

### Headless Agent Runs & Confirmation Bypass

`reporting/services/headless_chat.py:run_headless_chat()` drives a full chat-agent turn for a stored user (session persists as their `ChatSessionItem`, visible in their chat UI). Callers go through `reporting/services/agent_run.py:run_agent_session()`, which wraps it with identity resolution, optional server-side skill rendering, the untrusted-evidence boundary, and run-status normalization — shared by scheduled chats and the `agent_chat` workflow module.

**Security model — [`AGT-006`](docs/root/dev/decisions/chat-agent.md); don't weaken without explicit sign-off:**
- **Identity:** headless runs execute as the schedule/scheduled-query *creator*, permissions rebuilt by `reporting/authnz/headless.py:resolve_stored_user()`; archived users hard-stop.
- **Confirmation bypass is permission-based:** `chat:bypass_permissions` (Editor+), re-checked in `call_tool_for_chat` and AUDIT-logged per bypassed execution. Without it, mutating tools fail closed for the run. Creator RBAC and `chat_safe_only` always apply.
- The same permission gates the chat UI "Bypass confirmations" toggle (`ChatStreamRequest.bypass_confirmations`, 403 without it; default off).
- Headless turns get a system-prompt addendum (`_HEADLESS_PROMPT_ADDENDUM` in chat_graph) telling the model nobody can answer: don't ask for confirmation, follow the skills, summarize blocks instead of retrying.

### Scheduled Chats

Recurring headless agent runs (prompt + trigger, no Cypher) managed from the top-level Scheduled Chats page (`/app/scheduled-chats`, with `/:id/history` for versions); `chat:schedule` (Editor+) gates the CRUD routes (`/api/v1/chat/schedules` + `/versions` + `/sessions`, registered when `CHAT_ENABLED` + `CHAT_SCHEDULES_ENABLED`) and the UI (`features.chat_schedules` → `useFeature('chat_schedules')`). Schedules are per-user (`created_by` owner; list/get/update/delete are owner-scoped, 404 otherwise) with full version history (`ScheduledChatItem`/`ScheduledChatVersion` in `reporting/schema/chat.py`; both store backends + lock/record methods mirror scheduled queries). Run sessions are created with `ChatSessionItem.origin="scheduled"` + `scheduled_chat_id`: excluded from `list_chat_sessions`, listed via `list_scheduled_chat_sessions`, viewable read-only from the page's runs dialog (transcripts via `GET .../sessions/{thread_id}/history`, resolved under the owner's namespace), and admitting a turn on them is rejected (403). `chat:schedule:read_all` (Admin) unlocks viewing every user's schedules (`?all=true` / `?user_id=` on the list, plus read access to get/versions/sessions/history); mutations stay owner-only. Execution runs on **Temporal** (the polling worker is gone; Temporal is now required for scheduled chats). `reporting/services/chat_schedules.py` binds the shared reconciler (`reporting/services/schedule_reconciler.py`, kind-parameterized and also used by `workflow_schedules.py`) to chat records, reconciled by `seizu-temporal-worker`'s loop; `reporting/temporal_workflows/scheduled_chat.py` holds `seizu_scheduled_chat` + `seizu_scheduled_chat_watch_poll`, whose activities (`load_scheduled_chat`, `run_scheduled_chat_session`, `record_scheduled_chat_run_result`, `check_scheduled_chat_watch`) live in `activities.py`. `ChatScheduleSpec` triggers (hourly interval / daily days-of-week+hour / monthly days-of-month, all UTC) map to Temporal interval/calendar specs; monthly over-fires days 28-31 and `load_scheduled_chat` drops the non-matching firings with `schedule_due` (`reporting/services/schedule_spec.py`). `watch_scans` becomes a poll workflow reusing `check_watch_scan_triggered`. Overlap is `ScheduleOverlapPolicy.SKIP` (no mutex — unlike `ConfiguredWorkflow`), runs use `maximum_attempts=1`, and disabling pauses the Schedule rather than deleting it. Records carry `schedule_sync_status`/`schedule_sync_error`/`schedule_synced_at` (migration `0004_chat_schedule_sync_fields`). `POST /api/v1/chat/schedules/<id>/run` (owner-only) records `run_requested_at` and starts the run immediately, keyed on that timestamp so a recovering reconcile pass is idempotent (runs even when disabled). The agent session itself goes through `reporting/services/agent_run.py:run_agent_session` — shared with the `agent_chat` workflow module. Settings: `CHAT_SCHEDULES_ENABLED`, `CHAT_SCHEDULE_TIMEOUT_SECONDS`. Docs: `docs/root/install/chat-schedules.md`.

### Sandbox Delegation

`reporting/services/mcp_builtins/sandbox.py` — a `chat_only=True` builtin that lets the chat agent delegate code execution and file operations to an isolated ephemeral sandbox. **Decisions: [`SBX`](docs/root/dev/decisions/sandbox.md) — read it before changing the lifecycle, what is bound, or what triggers a result file.**

**`SandboxBackend` protocol** — the five-operation stable interface (`run_python`, `run_bash`, `read_file`, `write_file`, `list_files`) plus `sandbox_id` (the resume handle). `_E2BSandboxBackend` is the only implementation and the only place that knows E2B internals; `_open_backend` is the single swap point for another provider. `_build_sandbox_tools(backend)` wraps the five methods as LangChain `StructuredTool`s. Gotcha: `@runtime_checkable` `isinstance` reads members with `getattr_static`, so a `MagicMock` standing in for a backend must have **every** member explicitly assigned.

**Lifecycle — one sandbox per conversation** (`reporting/services/sandbox_session.py`). Made ambient (ContextVar) by `chat_agent_node` and `dispatcher_node`, **turn-level, not step-level**. Opened lazily on the first delegation, then suspended (E2B `pause(keep_memory=True)`) and resumed next turn via `resume_sandbox_id`, with the id in `ChatState.sandbox_id`. **Memory is kept deliberately** — filesystem-only suspension leaves the code interpreter dead on resume, so untrusted processes surviving a turn is an accepted trade (SBX-005). Result files live under `/home/user`, never `/tmp`, which does not survive a resume (SBX-010). Only **terminal** resume errors create a replacement; a timeout/rate-limit/auth failure propagates. Closing returns `SandboxTeardown(opened, suspended_id)` and callers write `sandbox_id` **whenever `opened`**, empty string included — the reducer overwrites rather than merges, so omitting the key leaves a dead id in the checkpoint. Error paths must call `abandon_sandbox_session()`, which keeps a *resumed* sandbox and destroys a freshly *created* one. `delete_thread_messages` kills the thread's sandbox first and never blocks on it. Rationale: SBX-005 – SBX-007.

**Reaping** (`reporting/services/session_reaper.py`): `SANDBOX_SESSION_TIMEOUT_SECONDS` bounds a *running* sandbox, so a thread the user abandons rather than deletes leaves a paused one behind. **The session is the unit, not the sandbox** — the sweep retires sessions idle past `CHAT_SESSION_REAP_IDLE_SECONDS` (30 days from `updated_at`; **this deletes chat history, so `CHAT_SESSION_REAP_ENABLED` defaults to false**) and removes the sandbox through `chat_graph.delete_thread_state`. A second pass kills old orphan sandboxes whose thread has no session. Idle time comes from `report_store.list_idle_chat_sessions`; PostgreSQL serves it through the `(origin, updated_at)` index from migration `0006`. Ownership is per deployment via `SEIZU_DEPLOYMENT_ID`, with exact matching unless `SANDBOX_REAP_UNTAGGED`. A singleton Temporal Schedule runs the sweep. A returning user wins through `claim_chat_session_for_retirement`, and the session record is deleted **last** so a partial sweep remains retryable. The sweep is gated on its own settings, not `CHAT_ENABLED` or `SANDBOX_ENABLED`. Rationale: SBX-011.

**What the sub-agent is bound** (`_bound_tool_names`): the read-only graph core (`SANDBOX_CORE_TOOLS`, configurable, intersected with RBAC so it never widens access — emptying it routes even Cypher through a skill) + what the conversation disclosed (`chat_graph.current_disclosed_tools()`, ambient per turn and per worker step) + what the delegating call named in `tools`. Naming `tools` **narrows** as well as widens.

**How it reaches anything else follows `CHAT_LLM_PROGRESSIVE_DISCLOSURE`.** Off: `find_seizu_tools` / `call_seizu_tool`. On (default): `find_seizu_skills` / `load_seizu_skill`, with `call_seizu_tool` limited to what a loaded skill's `tools_required` declared (`_skill_discovery_tools`). The turn's skill listing arrives ambiently (`chat_graph.set_available_skills`, set beside `set_disclosed_tools` in both chat paths) — the one-listing-per-turn rule. No skills authored → no discovery tools at all. All routes share `_invoke`, so bounds, result files and receipts are identical. RBAC is untouched: `chat_safe_only` + `exclude_confirmation_gated` bound what `_invoke` can reach. Rationale and measurements: SBX-003, SBX-004; and note that **progressive disclosure is not an authorization boundary** — [AGT-002](docs/root/dev/decisions/chat-agent.md).

**Session memory** (`reporting/services/episodic_memory.py`). `EpisodeLog` is per-step; `SessionLedger` is the cross-turn carry, seeded from `ChatState.session_memory` and written back by whichever node ran the delegations. It holds episodes plus **receipts** (files an oversized result was written to), rendered **only for their own `sandbox_id`**. Sub-agents get `EpisodeLog.recall(sandbox_id=...)` built after the backend resolves; the top-level agent gets the shorter `session_digest()`. Both fenced as untrusted. Rationale: SBX-008.

**Security model**: network-isolated from Seizu's data stores and credentials, holds none of its own, so no confirmation gate (`chat_safe_without_confirmation=True`) and `chat_only=True` keeps it off the MCP endpoint. Persistence widens the blast radius deliberately, bounded to one user's thread. Rationale: SBX-009.

**Direct sandbox use** (`_direct_tools`, SBX-016): the chat agent also gets `sandbox__write_file` / `read_file` / `list_files` / `run_python` without spawning a sub-agent — one round trip where a delegation pays for a whole agent loop. They share `sandbox:delegate` and its confirmation reasoning because delegate can already run arbitrary code in the same sandbox. A written file **records a receipt**, so data primed by the outer agent is advertised to delegations that do not exist yet. `SANDBOX_DIRECT_TOOLS_ENABLED` turns them off without disabling delegation.

**Permission**: `sandbox:delegate` (Editor+). Enabled via `SANDBOX_ENABLED=true`. Settings: `SANDBOX_API_KEY`, `SANDBOX_DOMAIN`, `SANDBOX_TIMEOUT_SECONDS`, `SANDBOX_CORE_TOOLS`, `SANDBOX_MAX_OUTPUT_BYTES`, `SANDBOX_LLM_MODEL`, `SANDBOX_SESSION_TIMEOUT_SECONDS`, `SANDBOX_SESSION_PERSIST`, `CHAT_SESSION_MEMORY_*`. Docs: `docs/root/install/sandbox.md`.

**Sandbox coding-agent** (`reporting/services/sandbox_agent.py`) — reusable, provider-agnostic machinery for running a headless coding-agent CLI in a sandbox: the `PROVIDERS` registry (`claude` default, `codex`, `opencode`), credential resolution (static key / `SANDBOX_AGENT_API_KEY_COMMAND` mint / credential proxy), agent-env + run-script builders, and secret masking. Config is the generic `SANDBOX_AGENT_*` family. `sandbox_remediation` is the first consumer.

**Sandbox remediation** (`reporting/services/sandbox_remediation.py`) — not a chat tool: the `cve_dependency_remediation` Temporal workflow drives the sandbox directly, composing `sandbox_agent` with the git/GitHub flow against a cloned repo. **Two sandboxes** so the GH token never shares a VM with untrusted code, with per-command env isolation (`run_bash_streaming(envs=...)`); after a fresh push the workflow watches the PR's CI worker-side and may run bounded fix-mode sessions. opencode is multi-provider: `SANDBOX_AGENT_MODEL` = `provider/model` selects the key env and reuses the chat assistant's global `*_API_KEY`. `REMEDIATION_USE_FORK` pushes to a bot-owned fork (`github_checks.ensure_fork`, `REMEDIATION_FORK_ORG`) so the token needs no write access to target repos. `REMEDIATION_GITHUB_HOST` supports GHE; `REMEDIATION_GH_VERSION`/`REMEDIATION_GH_SHA256` pin the gh version + tarball digest; `SANDBOX_AGENT_TEMPLATE` selects an E2B prebuilt image. The credential-proxy sandbox is provisioned one of two ways: from `SANDBOX_AGENT_CREDENTIAL_PROXY_TEMPLATE` (any image with a working litellm proxy — `make build_proxy_template` builds one; used **as built**, runs install and check nothing), or with no template by installing a hash-locked requirement file (`SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE`, default `reporting/services/sandbox_proxy_requirements.txt`, regenerated by `make lock_proxy_requirements`; its header records the python/arch it was resolved for and the install refuses a sandbox that does not match). **The isolation model, the credential options, the unverified LiteLLM↔CLI wire and the proxy pinning are [`WF-005` – `WF-009`](docs/root/dev/decisions/workflows.md) — read them before touching credential handling.**

**Testing**: patch `_open_backend` with an `_open_backend_ctx(fake_backend)` that yields a mock satisfying `SandboxBackend`. Tests are fully backend-agnostic — no E2B package needed.

### Temporal Workflows

`ConfiguredWorkflow` (`reporting/temporal_workflows/configured_workflow.py`) is the top-level, user-configurable pipeline. Its ordered stages execute sequentially; activities in a stage execute in parallel, publish globally unique named outputs, and may consume only outputs from earlier stages. Query is an ordinary activity and exposes an optional referenced value as the reserved `$input` Cypher parameter. Each code-defined workflow registered in `WORKFLOW_REGISTRY` (`reporting/temporal_workflows/__init__.py`) is its own top-level activity type; the activity starts it as an awaited child workflow. Stored activities using the removed `workflow` sub-type (`parameters.workflow` selecting the child) are migrated to the top-level type on read (`workflows.normalized_stages`); new saves reject `type: workflow`. Workflow/activity code lives in `reporting/temporal_workflows/` — workflows are deterministic (dataclasses + pure helpers from `shared.py` only); **all I/O belongs in `activities.py`**, and AI sessions go through `headless_chat.run_headless_chat` (see above). Built-ins: `agent_chat` (general-purpose AI activity — operator-supplied prompt, optional input rows passed as untrusted evidence, session summary as its named output; `requires_rows=False`), `cve_repo_report`, `cve_dependency_remediation`, and `cartography_sync` (config = an ordered `module_runs` list — `{module, params}` entries run sequentially, one stage each; create-indexes/analysis are ordinary selectable modules). `TEMPORAL_ENABLED_WORKFLOWS` allowlists which registered workflows appear as activity types and may be dispatched.

A `WorkflowSpec` may declare per-workflow activity config: `config_fields` (the activity type's `ActionConfigFieldDef`s, served via `GET /api/v1/config` in `workflow_activity_schemas`/`workflow_activity_definitions` and rendered in `WorkflowDialog`; row-consuming specs additionally get generic `max_rows`/`query_return_attribute` fields), `config_validator` (run on save by `workflows.validate_definition`), and `requires_rows=False` for workflows that consume no input reference (dispatched without rows; the activity's full `parameters` reach the `input_factory` via `WorkflowInputContext.action_config`).

Settings: `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`, `TEMPORAL_WORKER_ENABLED`, `TEMPORAL_WORKFLOW_MAX_RESULT_ROWS`, `TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS`; `AGENT_CHAT_MAX_RESULT_ROWS`, `AGENT_CHAT_TIMEOUT_SECONDS`; `CARTOGRAPHY_TASK_QUEUE`, `CARTOGRAPHY_ENABLED_MODULES`, `CARTOGRAPHY_MODULE_TIMEOUT_SECONDS`, `CARTOGRAPHY_MODULE_WAIT_SECONDS`, `CARTOGRAPHY_SYNC_RETRY_ATTEMPTS`. Docs: `docs/root/install/temporal-workflows.md`, `docs/root/install/cartography-sync.md`.

### Docker Services

| Service | Role |
|---------|------|
| `seizu` | FastAPI backend + static frontend |
| `seizu-node` | Bun dev server |
| `seizu-temporal-worker` | Workflow + scheduled-chat scheduler, Temporal workflow/activity worker, session reaper |
| `seizu-cartography-worker` | Cartography sync activity worker (own image + task queue) |
| `temporal` | Temporal dev server (`server start-dev`, in-memory; UI `:8233`) |
| `neo4j` | Graph database |
| `postgres` | PostgreSQL application store + chat checkpoint databases |
| `postgres-create-chat-checkpoint-db` | Idempotent local checkpoint-database provisioning |
| `sqs` | ElasticMQ (local SQS) |
| `authentik-*` | OIDC provider (`auth` profile) |
| `cartography` | Graph data sync (`nostart` profile) |

Dev URLs: frontend `:3000`, backend `:8080`, Authentik `:9000`, Temporal UI `:8233`.

**Dockerfile stages:** `base` (Python + uv + `pyproject.toml`/`uv.lock`, no deps) → `dev` (`uv sync --frozen --all-groups --all-packages --no-install-workspace`; used by compose `target: dev`) ; `nodebuilder` (`oven/bun`, builds frontend) ; `production` (`uv sync --frozen --no-dev --package seizu --no-install-workspace` + built frontend; `publish.yml` builds this, so pytest/pre-commit never ship).

**Reset volumes:** `docker compose down && docker volume rm seizu_neo4j_data seizu_postgres_data && docker compose up && make seed_dashboard`.

### Authentication

Two mechanisms resolve to the same `CurrentUser`:

1. **Cookie-based BFF** (browser SPA). Backend owns the OIDC Authorization Code + PKCE exchange. The IDP refresh token lives only in an AES-256-GCM-encrypted, HttpOnly, Secure, SameSite=Strict cookie (`seizu_session`); the access token stays in React state, never persisted. SPA refreshes via `POST /api/v1/auth/refresh` (cookie-authenticated). The session and `seizu_oauth_state` cookies share `SESSION_TOKEN_ENCRYPTION_KEY` but are domain-separated by AES-GCM associated data (`seizu-session-v1` / `seizu-oauth-state-v1`).
2. **Bearer JWT** (CLI, MCP, programmatic). Validated by `authnz.validate_bearer_token()`: `PyJWKClient` JWKS check first, then RFC 7662 introspection fallback when `OIDC_ENABLE_TOKEN_INTROSPECTION` is set and the token isn't a verifiable JWT (for opaque tokens). Shared by `get_current_user` (REST) and the MCP middleware.

`get_current_user` consumes only the Bearer path; the cookie is consumed only by the four `/api/v1/auth/*` routes. `_CSRFMiddleware` requires `X-Seizu-Csrf: 1` on mutating methods whenever the session cookie is present; Bearer-only requests are exempt.

**Durable identity is `(iss, sub)` exactly as the token carries it.** The two mechanisms above can reach the IDP through *different* hostnames (`OIDC_INTERNAL_AUTHORITY` vs `OIDC_AUTHORITY`), and an IDP that derives its issuer from the request host then forks one person into two user records — every owner-scoped surface diverges silently. `oauth_client.verify_issuer_consistency()` (called from the app lifespan) compares the two discovery documents and logs a mismatch, fatal only under `OIDC_REQUIRE_CONSISTENT_ISSUER`. **Never normalize or alias `iss` on the way to `get_or_create_user`** — [`AUTH-001`](docs/root/dev/decisions/authentication.md).

**Auth-flow endpoints** (browser-only): `login` generates PKCE verifier + state + nonce into a 5-min `seizu_oauth_state` cookie (SameSite=Lax to survive the redirect back), returns the authorize URL (with `nonce` + `OIDC_AUTHORIZE_EXTRA_PARAMS`). `callback` verifies state, exchanges the code, validates the ID token (gated by `OIDC_VALIDATE_ID_TOKEN`), sets the session cookie, 302s to path-only `return_to`. `refresh` rolls Max-Age (capped at the IDP refresh token's absolute expiry); 401 + clears cookie on IDP rejection. `logout` clears the cookie and (gated by `OIDC_REVOKE_REFRESH_TOKEN_ON_LOGOUT`) best-effort-revokes the refresh token.

**Env vars** are defined and documented inline in `reporting/settings.py` (each with its default, purpose, and examples) and listed with comments in `.env.example` — that's the source of truth; don't duplicate the list here. The families: `JWKS_URL` / `JWT_*` (token validation), `OIDC_*` (BFF flow, discovery, introspection, scope, revocation), `SESSION_*` (cookie encryption + lifetime; `SESSION_TOKEN_ENCRYPTION_KEY` is required when auth is on), plus `DEVELOPMENT_ONLY_REQUIRE_AUTH` and `TALISMAN_FORCE_HTTPS` (both default-on; set `false` in dev).

### RBAC

Each authenticated request resolves a role from the JWT → `frozenset[str]` permissions, enforced at routes via `require_permission(*perms)` (factory in `reporting/authnz/__init__.py`). MCP enforces `tools:call` / `query:execute` via the `ContextVar` set in `_MCPAuthMiddleware`.

- **Built-in roles** (hierarchical Admin ⊇ Editor ⊇ Viewer) in `reporting/authnz/permissions.py` — no store I/O. **User-defined roles** live in the report store (version history); resolved via one `get_role_by_name`.
- Key files: `permissions.py` (`Permission` enum, `BUILTIN_ROLES`, `ALL_PERMISSIONS`, `resolve_permissions()`), `authnz/__init__.py` (`CurrentUser.permissions`, `require_permission()`, resolution), `routes/roles.py` (CRUD).
- Env: `RBAC_ROLE_CLAIM` (default `seizu_role`), `RBAC_DEFAULT_ROLE` (default `seizu-viewer`; `""` denies access).
- **Dev mode** (`DEVELOPMENT_ONLY_REQUIRE_AUTH=false`): `ALL_PERMISSIONS` on every request — no enforcement.

**Local Authentik:** `make auth_enable && make down && make up` (first run ~2 min; revert with `make auth_disable && make down && make up`). Fully automated via `.config/dev/authentik/blueprints/seizu.yaml`. Device code flow needs `flow_device_code` on the **Brand** (not the OAuth2Provider). Redirect URIs: `http://localhost:{3000,8080}/auth/callback`. Dev creds: `akadmin/devpassword`, `developer/devpassword`. **Everything reaches Authentik as `localhost:9000`**, the backend included, via `scripts/dev_oidc_loopback.py`, which the `seizu` container starts alongside gunicorn (in-process, not a sidecar — a sidecar sharing the netns is stranded by `--force-recreate seizu`) — Authentik derives `iss` from the request `Host`, so a second hostname would fork one login into two users ([`AUTH-001`](docs/root/dev/decisions/authentication.md)). Don't reintroduce `OIDC_INTERNAL_AUTHORITY` here.

### Testing Patterns

- **Backend:** pytest + pytest-mock + pytest-asyncio (`asyncio_mode = auto`) in `tests/unit/reporting/`; all tests `async def`. Use `httpx.AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")` for routes; override auth via `app.dependency_overrides[get_current_user] = lambda: CurrentUser(...)`; mock async calls with `AsyncMock`. Reference: `tests/unit/reporting/routes/query_test.py`.
- **Frontend:** React Testing Library + Jest (Bun) in `__tests__/` next to components.
  - **`jest.mock('<module>', factory)` leaks across test files.** The whole suite shares one Bun process, so a module-factory mock registered by one file replaces that module for the others too — and a factory that omits exports the other files need breaks them with errors that point at the *innocent* file. This has already bitten `ReportPane`, `useReportsApi`, and `useSpacesApi`. Symptom to recognise: a spec passes alone and fails in `test:ci` (or alongside a sibling), with a "not a function"/missing-element failure in the file you didn't touch.
  - **Default to `jest.spyOn(module, 'export')` in `beforeEach` with `mockRestore()` in `afterEach`** — it is scoped and reversible. `src/pages/__tests__/SpaceDetail.test.tsx` and `ReportsList.test.tsx` show the pattern. Reserve `jest.mock` for modules whose mock is identical everywhere (e.g. `UserDisplay`) or that only one file touches, and when mocking a shared module keep the factory exhaustive.
  - **Spies leak across files too**, and the failure is nastier: a spy installed by one file can outlive it, and a later file's `jest.clearAllMocks()` then strips the return value, so the spied export returns `undefined` and the component blows up on a null destructure — a stack trace pointing at product code, not at mocking. So **any suite that relies on a hook's real implementation must still stub its siblings explicitly** rather than trusting them to be unmocked. `SpaceDetailFetch.test.tsx` exercises the real `useSpaceTree` and stubs `useSpaceMutations`/`useReportsMutations` for exactly this reason. Symptom: passes locally, fails in CI (or vice versa) because file order differs.

### Query Validator Fuzzing

Three layers: (1) **EXPLAIN write check** — `query_type != 'r'` blocks writes/schema; (2) **SSRF/admin/procedure guard** — `_scan_for_dangerous_constructs()` scans original, comment-stripped, and unicode-decoded forms for `LOAD CSV` (`_LOAD_CSV_RE`), `USE` (`_USE_CLAUSE_RE`), admin/catalog (`_ADMIN_COMMAND_RE`), and `apoc.cypher.*`/`gds.*`/`ai.*`/`genai.*` (`_DANGEROUS_FUNCTION_NAMESPACES`); every `CALL <procedure>` matched against `_DEFAULT_ALLOWED_PROCEDURES` + `QUERY_VALIDATOR_ALLOWED_PROCEDURES`; (3) **CyVer warnings** — never block.

Run the suite:
```bash
docker compose run --rm seizu uv run --frozen --no-sync pytest tests/unit/reporting/services/query_validator_test.py -v
```
APOC deep testing (read-classified but can make outbound HTTP): `make apoc_enable && make down && make up` … `make apoc_disable && make down && make up`.

**Attack vectors** (one row per technique tried, blocked or not) are catalogued exhaustively in `tests/data/query-fuzzing.csv` (`Technique, Cypher, Result, Blocked_By, Notes`) with matching assertions in `query_validator_test.py`. Read those rather than a summary table here, and **append a row for every new vector you try** — including ones already blocked.

**Adding cases:** confirm whether Neo4j executes the vector (logs: `ExternalResourceFailed` / write side effects) → add a CSV row → add a unit test (mock `_get_async_neo4j_client`/`_get_sync_neo4j_client`, return summary with `query_type='r'` + no notifications, assert `result.has_errors`) → if not blocked, fix `query_validator.py` first, then prove with the test.

**Decisions: [`QV`](docs/root/dev/decisions/query-validation.md)** — the allowlist-not-denylist choice, the three scanned forms, and the two-sided comment check each have a bypass if reversed.

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

When changing the codebase, update as needed: **`Makefile`** (new targets), **`.github/workflows/`** (CI), **`Dockerfile`/`docker-compose.yml`** (services, files, env), **`.pre-commit-config.yaml`** (linting), **`pyproject.toml` + `uv.lock`** (commit together; `uv lock` after edits, then `docker compose build`), **`docs/`** (user-facing changes — grep for removed items), **`schema/reporting-schema.json`** (`make schema` after Pydantic changes), **`.env.example`** (env vars), **`AGENTS.MD`** (conventions/tooling changes).
