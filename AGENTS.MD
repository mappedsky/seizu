# Agent Guidelines

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What is Seizu?

Seizu (星図) is a configuration-driven reporting platform for Neo4j graph data. Three runtimes:
- **FastAPI web app** (`reporting/`) — serves the React frontend and REST API (ASGI, Gunicorn + UvicornWorker).
- **Temporal workflow worker** (`reporting/temporal_worker.py`) — owns configurable workflow schedules **and scheduled chats**, and runs ordered stages (parallel activities within each stage) with named outputs and earlier-stage input references. Activity modules include query, SQS, Slack, log, statsd, and code-defined workflows. Temporal Schedules provide durable interval/hourly/daily/monthly and watch-poll triggers. The old scheduled-query worker/API are compatibility aliases only.

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
make build_cartography_worker     # build the cartography sync-worker image
make cartography_contract_test    # verify registry flags against the pinned CLI
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
- `reporting/routes/` — `config.py`, `me.py`, `query.py`, `reports.py`, `users.py`, `validate.py`, `scheduled_queries.py`, `roles.py`, `sync_metadata.py` (distinct SyncMetadata values for watch-scan autocompletes), `static.py` (healthcheck only).
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

- **Transport:** StreamableHTTP via `_MCPMiddleware` (pure ASGI in `app.py`), intercepting `/api/v1/mcp*` before FastAPI routing (avoids a Starlette 1.0.0 `Mount` trailing-slash issue).
- **Auth:** `_MCPAuthMiddleware` validates Bearer tokens via shared `authnz.validate_bearer_token()` (JWKS + optional RFC 7662 introspection); passes through unauthenticated in dev (`DEVELOPMENT_ONLY_REQUIRE_AUTH=false`). Sets two `ContextVar`s per request: `_mcp_permissions` (`frozenset[str]`) and `_mcp_current_user` (`CurrentUser`); handlers attributing writes read the latter.
- **Tool names:** `{group}__{action}` (built-ins) or `{toolset}__{tool}` (user-defined), double underscore.
- **Listing/dispatch:** the actual list/call/prompt logic lives in `reporting/services/mcp_runtime.py` (`list_tools_for_user`, `call_tool_for_user`, `list_prompts_for_user`, `get_prompt_for_user`) so the MCP transport and the chat agent share one implementation. `@server.list_tools()` merges `list_builtin_tools(MCP_ENABLED_BUILTINS)` (filtered by caller permissions) with `report_store.list_enabled_tools()`. `@server.call_tool()` tries `find_builtin()` first, else runs user-defined tools via `reporting_neo4j.run_query()`. Built-ins enforce their own `required_permissions`.
- **Built-ins** live in `reporting/services/mcp_builtins/` (one module per group, registered in `__init__.py`). Each is a `BuiltinTool` dataclass (`base.py`) sharing the REST service layer — no duplicated logic; permissions mirror REST. Groups (all default-on): `graph` (schema, query, validate_query, explain), `reports`, `scheduled_queries`, `skillsets`, `toolsets`, `roles` — full CRUD + version history. To add a group: export `GROUP_DEF: BuiltinGroup` from a new module, append to `_GROUPS` in `__init__.py` and the `settings.MCP_ENABLED_BUILTINS` default.
- **OAuth metadata:** `GET /api/v1/mcp/.well-known/oauth-authorization-server` (RFC 8414) when `MCP_OAUTH_AUTHORIZATION_ENDPOINT` + `MCP_OAUTH_TOKEN_ENDPOINT` are set (also `MCP_OAUTH_ISSUER`); lets clients like Claude Desktop discover the IDP. `.well-known` passes through unauthenticated.

**Toolsets/Tools** are user-managed Cypher-backed tool groupings in the report store with version history; CRUD via REST; Cypher validated (writes blocked) at create/update.

DynamoDB key space: `TOOLSET#{id}` (`#METADATA`, `VERSION#N`), `TOOLSET_LIST/TOOLSET#{id}`, `TOOL#{id}` (`#METADATA`, `VERSION#N`), `TOOL_LIST#{toolset_id}/TOOL#{id}`. SQL tables: `toolsets`, `toolset_versions`, `tools`, `tool_versions`.

### Chat (`reporting/services/chat_graph.py`, `reporting/routes/chat.py`)

A LangGraph-backed chat assistant at `POST /api/v1/chat/stream` (frontend `src/pages/ChatInterface.tsx`, route `/app/chat`), gated by the `CHAT_ENABLED` setting (default false). When off, the chat routes aren't registered, checkpoint storage isn't initialized, and the frontend hides the Chat UI — `GET /api/v1/config` returns `features.chat`, consumed via `src/features.context.ts` (`useFeature('chat')`); add new feature flags the same way (`settings.<X>_ENABLED` → `features.<x>` → `useFeature`). `CHAT_LLM_PROVIDER` is a sentinel: `mock` echoes user input (keyless, deterministic), and any other value routes through **LiteLLM** (`ChatLiteLLM`), so the provider/model surface is whatever LiteLLM supports rather than a fixed allowlist. `CHAT_LLM_MODEL` is the LiteLLM model id (prefer provider-namespaced, e.g. `anthropic/claude-sonnet-4-6`); legacy `CHAT_LLM_PROVIDER` values (`openai`/`anthropic`/`gemini`/`deepseek`) still namespace a bare model name. `CHAT_LLM_BASE_URL` (LiteLLM `api_base`) points chat at a self-hosted LiteLLM proxy or other OpenAI-compatible gateway — the proxy is an opt-in deployment, not a dependency. LiteLLM owns the cross-provider quirks (base_url/api_key handling, DeepSeek-shape endpoints, `reasoning_content` normalization) Seizu used to special-case; reasoning surfaces both as `additional_kwargs["reasoning_content"]` and injected `{"type":"thinking"}` content blocks, both read by `_chunk_reasoning_delta` and stripped from model context by `_strip_reasoning_context`. Real providers use native structured tool calls.

- **Streaming:** the route consumes `graph.astream(..., stream_mode="custom")` and re-emits tokens as a Vercel AI SDK UI Message Stream (SSE: `start`/`text-*`/`finish`, `data: [DONE]`). The node pushes chunks via `get_stream_writer()` as `{"kind": "token", "content": ...}`. The frontend uses `@ai-sdk/react` `useChat`.
- **Message tags (`reporting/services/chat_messages.py`):** `MessageTag` is the reusable mechanism for excluding selected messages from history/LLM context. Tags live in `additional_kwargs["seizu_tags"]` (round-trips through the checkpoint serializer), and `load_thread_messages` calls `drop_tagged(..., EPHEMERAL)` at the read boundary. Broken assistant outputs are tagged `BROKEN` and filtered from future model context so failed turns do not poison retries.
- **Permissions:** `chat:use` (Viewer+) gates the endpoint; `chat:tools:call` and `chat:skills:call` (Editor+) gate tool/skill use; `chat:bypass_permissions` (Editor+) gates the confirmation-bypass mode (UI toggle, headless runs — see Headless Agent Runs). Tool/skill calls also require the *underlying* MCP permission (e.g. `tools:call`, `skills:render`). For chat, `mcp_runtime` is called with `chat_safe_only=True`, which exposes only built-ins whose `required_permissions` are all in `_CHAT_SAFE_PERMISSIONS` (read/inspection only), plus mutating built-ins with an action-confirmation resolver. **Fail-closed:** a new write/delete tool is hidden from chat by default. Report configs are validated against the `Report` schema at save time (`CreateVersionRequest.validate_report_config`; markdown panels must carry content in `markdown`) so agents get an actionable error instead of storing a config that renders empty. The only intentional no-confirmation mutating exception is `reports__create`, because it creates a new private report and does not modify existing resources — and even that carries a resolver that gates the one case where it publishes (filing into a space). `reports__clone` is gated for every call, because whether it publishes depends on the source's placement. Document any future exception at the tool registration.
- **State and limits:** thread id is namespaced server-side as `user:{user_id}:thread:{client_thread_id}` (via `chat_graph.namespaced_thread_id`) so clients can't reach another user's thread. History persists through the backend selected by `CHAT_CHECKPOINT_BACKEND`: DynamoDB (`langgraph-checkpoint-aws`, with optional MinIO/S3 offload for >350KB payloads) or PostgreSQL (`langgraph-checkpoint-postgres`, async pool owned by the app lifespan). `make sqlmodel_enable/disable` switches the report store and chat checkpointer together. The frontend persists the thread id in `localStorage` and rehydrates the conversation on mount via `GET /api/v1/chat/history?thread_id=` (`chat_graph.load_thread_messages` → `graph.aget_state`), so a reload resumes the same conversation. Persisted messages are trimmed by `CHAT_MAX_PERSISTED_MESSAGES`; history responses are bounded by `CHAT_HISTORY_LIMIT`; chat MCP tool output is bounded by `CHAT_TOOL_RESULT_MAX_ROWS` and `CHAT_TOOL_RESULT_MAX_BYTES`. `DynamoDBSaver` is sync boto3 but its async methods offload to a threadpool (`run_in_executor`), so it doesn't block the event loop. Docs: `docs/root/install/chat.md`.

- **Context is budgeted in tokens against the model's own window** (`reporting/services/chat_context.py`): `context_window_tokens` reads `max_input_tokens` from litellm (cached per model; unknown models fall back to `CHAT_LLM_CONTEXT_WINDOW_FALLBACK_TOKENS`, small on purpose — guessing low wastes a window, guessing high fails the turn), and `history_token_budget` = min(`CHAT_LLM_CONTEXT_MAX_TOKENS`, window × `CHAT_LLM_CONTEXT_WINDOW_SHARE`). The window is a **ceiling, not a target**: a 1M-token model must not silently multiply per-call cost. `count_tokens` uses the provider tokenizer, content-hash cached (a trim pass sizes every message, every call). The old caps were characters at an assumed 4 chars/token; real tool payloads measure **3.0**, so they admitted a third more than intended — use `chars_for_tokens` (calibrates on the text in hand) where something must truncate characters against a token budget. `_run_llm_tool_turn` fits the **whole request** (`_fit_messages_to_window`: window − system − tool schemas − reply − `CHAT_LLM_CONTEXT_SAFETY_MARGIN` − per-message framing) — it is the single scope where the whole request exists, so every LLM call is covered — and catches a provider overflow once (`chat_context.is_context_overflow`, matched by litellm type or message across the exception chain), retrying with the conversation halved **relative to what was sent**, not to our allowance: an overflow *is* that allowance being wrong. No retry once text has streamed (it would duplicate), and non-overflow errors are never retried. Cross-turn history is **compacted, not truncated** (`_compact_history`): the oldest turns are condensed into one block, cut back past the budget in chunks so the block stays byte-identical between compactions (40 simulated turns: 5 compactions, request bounded 2,421-3,348 tokens against a 4,000 budget). Deterministic by design -- never a summarizing model call, whose output would differ per run and rewrite the prefix. The block is **fenced with `fenced_within`**: compaction flattens assistant turns into a *user* message, and an assistant turn carries whatever tool and graph output it reported on, so unfenced it promotes provider-controlled text into the instruction role — and keeps it there, because the block is deliberately stable. The block gets a *reserved* share of the budget (`_SUMMARY_BUDGET_SHARE`); without that reserve a grown summary competed with the history it described and every turn recompacted. The boundary is stored as `ChatState.history_summary.covers_through_id` -- a message id, since a count shifts when filtering changes, and an *empty* id must never be looked up (it matches every message that carries none, which silently dropped the first message of a fresh conversation). **Still outstanding:** nothing from the original context plan — any compaction must be **append-only**, since rewriting the prefix invalidates the prompt cache for the whole conversation.

- **Volatile prompt content goes last, never in the system prompt** (`chat_graph.session_memory_message`). Prefix caching matches the longest common prefix, so a system prompt that changes per turn invalidates the whole request: the session digest there measured **0% cached against 98%** for an otherwise identical prefix. Carried as a trailing message it leaves only the newest exchange uncached (93%/74% on a live two-turn conversation). This is the provider-agnostic half — automatic prefix caches (DeepSeek, OpenAI, Gemini) need nothing more. Keep it in mind before adding anything per-turn to a system prompt. **Anthropic caches nothing without explicit breakpoints** (measured: 0 cached over a five-call turn), so `chat_context.with_cache_breakpoints` marks up to three blocks with `cache_control` for Anthropic models only — system prompt (tool schemas are ordered ahead of it, so one mark covers both), the message *before* the session digest (a prefix containing the digest can never be read back), and the last message (within-turn rolling). Non-Anthropic requests are returned untouched; reshaping them into content blocks would risk a provider transformer for no gain. Live two-turn measurement: turn 1 writes ~11,000 and reads 0 (cold, writes cost 1.25x), turn 2 reads 16,467 (56%) and writes 1,801. **What the prefix contains matters more than the marks:** `_worker_system_prompt()` takes no step argument and must stay that way — it embedded the step goal/criteria/required action, so every step had a different prefix head and none could read another's (measured: second step read 0 of 2,963). Step-specific text lives in `_worker_user_message` via `_step_contract`, still fenced. Tool-list churn used to invalidate too (Anthropic orders tools *ahead* of the system prompt: 3 → 11 tools made the next call read 0). Fixed by honouring skills' `tools_required` **up front, scoped and bounded**. `chat_orchestrator._step_declared_tool_names` takes only the skills *this step names* (`required_action` / `suggested_tools`) — unioning every enabled skill's declaration is the catalogue, not the need, and took a single-agent turn from 1 bound tool (343 tokens) to 43 (4,666). The single-agent path has no such signal, so it still discloses on render. `mcp_runtime.declared_tool_names(prompts, only=…)` reads declarations off the prompt `_meta` of the listing the turn already made (no second store read; `_meta`, not `meta`, or the alias shadows it), and `chat_graph.skill_declared_tool_names` bounds the result by `CHAT_LLM_DISCLOSE_SKILL_TOOLS_MAX_TOKENS`, measured in schema tokens rather than tool count. After the change a turn held one tool list across all four calls, each reading the previous prefix.

- **`CHAT_LLM_CACHE_DIAGNOSTICS`** (default off) answers the question a cache miss never does: *which* part moved. `chat_context.diagnose_cache_divergence` fingerprints model/system/tools/messages as hashes per call and logs the earliest divergence with the tokens behind it (`tools_changed, ~4000 tokens…`). Scoped by key (thread+phase; **per delegation** for the sandbox, whose delegations all open with the same system prompt) *and* by lineage (the opening message) — a plan reuses step ids across turns, so without both, turn 2's `worker:s2` was diffed against turn 1's unrelated one and every first call reported a divergence. Anthropic's own beta is unreachable through LiteLLM 1.87.0 — it builds `anthropic-beta` from feature detection and drops caller `extra_headers`/`headers` (verified on the wire), so the `diagnostics` body param arrives unauthorised and the API **rejects the call outright**; do not wire it up without fixing the header first. Reach for this instead of hand-adding hash logging, which is how both cache bugs in this area were found.

- **Budgeting is cache-aware** (`reporting/services/chat_budget.py`): `usage_from_message` reads `input_token_details.cache_read`/`cache_creation` off the response and `usage_cost_usd` forwards them to litellm, which treats `prompt_tokens` as the **total** and subtracts the cached portions (so pass the total, not the difference; clamp the details to it or a provider over-report negative-prices the call). Committed cost is exact. **Reservations are not discounted** by the observed hit rate: that ratio spans every model and phase, so a cache-heavy sandbox phase discounted a cold planner call on another model (reproduced 6.6x under-reserved), and more fundamentally a cache hit is never guaranteed — a ceiling that assumes one is not a ceiling. `project_cost_usd` reserves the uncached price and the ledger self-corrects on commit. Tokens are still counted whole (a cached token occupies the context window); only the price differs, and `cache_read_tokens` is in the ledger and per phase.

### Configuration System

Reports + scheduled queries are stored in DynamoDB (default) or PostgreSQL (`sqlmodel`). The YAML config (`.config/dev/seizu/reporting-dashboard.yaml`, mounted as `/reporting-dashboard.yaml`) is a seed source.

YAML top-level: `queries` (named Cypher for `scheduled_queries` refs only; not served to frontend), `dashboard` (key of default report), `reports` (each with its own `queries` dict), `scheduled_queries` (**list** of SQS/Slack/log defs), `toolsets` (dict → `ToolsetDef`, each with a `tools` dict → `ToolDef`: `name`, `description`, `cypher`, `parameters`, `enabled`).

**Report storage** (`reporting/services/report_store/`): `base.py` (`ReportStore` ABC), `dynamodb.py` (single-table, composite PK/SK, Snowflake IDs via `SNOWFLAKE_MACHINE_ID`), `sql.py` (SQLModel/SQLAlchemy, 5 tables, non-secret `SQL_DATABASE_URL` plus `SQL_DATABASE_USER`/`SQL_DATABASE_PASSWORD`), `__init__.py` (`get_store()` factory, env `REPORT_STORE_BACKEND`, default `dynamodb`). Import `from reporting.services import report_store` — backend is transparent. Switch backends: `make sqlmodel_enable/disable` then `make down && make up`.

DynamoDB GSI: `space_reports_index` (`space_id` hash + `SK` range, `ALL` projection) backs `list_space_reports` and the `delete_space` emptiness check. Sparse, and queried with `begins_with(SK, "REPORT#")` because the report `#METADATA` copy and sub-space items also carry `space_id`. **Optional by design** — production tables are IaC-managed (`DYNAMODB_CREATE_TABLE` defaults false), so `_query_space_reports_via_index` returns `None` on a missing index and callers fall back to filtering the whole `REPORT_LIST` partition; the availability probe is cached per process. Keying on the existing `space_id` attribute rather than a dedicated one is what makes the index need no backfill.

DynamoDB key design: per report `#METADATA`/`#LATEST`/`VERSION#<n>`; per scheduled query `SQ#<id>` + `SCHEDULED_QUERY_LIST`; default dashboard `#DASHBOARD/#POINTER`; per space `SPACE#<id>/#METADATA` + `SPACE_LIST/SPACE#<id>`; per sub-space `SUBSPACE#<id>/#METADATA` + `SUBSPACE_LIST#<space_id>/SUBSPACE#<id>`.

**The `REPORT_LIST` item is a full duplicate of a report's `#METADATA` item, not a pointer**, and `list_reports`/`list_space_reports` read only the duplicate. Both copies are always built from one dict — `_report_record()` + `_report_record_items()` in `dynamodb.py`, with the field list in `_REPORT_RECORD_FIELDS`. Add new report fields there, never to one copy at a time; a field written to `#METADATA` but not to `REPORT_LIST` fails silently.

**SQL schema changes:** Alembic is the sole startup schema owner. `SQLModelReportStore.initialize()` runs `reporting/services/report_store/migrations.py`, which upgrades to head (with a pg advisory lock so concurrent workers don't race). Add a revision in `reporting/migrations/versions/`; `0005_spaces.py` is the current template. **Every operation must be inspector-guarded**: the baseline `0001` calls `SQLModel.metadata.create_all` on a fresh database, so later revisions run against a schema that already has whatever they are adding. Verify against both a fresh volume and an upgraded database. SQLite dev DBs created before nullable user emails keep the old `NOT NULL`; recreate the DB to test emailless users on SQLite.

### Spaces

Spaces group reports; sub-spaces group reports within a space. Flat records — **no version history, no access scope** — so there are no `/versions` endpoints and `list_spaces` is unfiltered; report-level visibility still filters what `list_space_reports` returns. Schemas in `reporting/schema/space_config.py`, routes in `reporting/routes/spaces.py`, cross-entity validation in `reporting/services/spaces.py` (`resolve_report_space`, `resolve_overview_report`, and the `SPACE_MEMBER_ACCESS` guards, shared so REST and the MCP report builtins can't diverge). Reports carry nullable `space_id`/`subspace_id`; membership is unversioned parent metadata, denormalized onto `ReportVersion` and never written into a version item or into `config` (otherwise restoring a version would relocate the report). Moves go through `update_report_space` (a metadata mutation like `update_report_visibility`, not a version save) with **replace semantics** — omitting `subspace_id` clears it, which is what makes "moving to another space drops the sub-space" fall out for free.

Invariants: `subspace_id` requires `space_id` and must belong to it (400 via `resolve_report_space`, deliberately not a pydantic validator, which would yield 422). **A report filed in a space is public** (`SPACE_MEMBER_ACCESS`): filing a draft is a 409, unpublishing a member is a 409, and creating a report into a space publishes it. All three writes that can break it are enforced **in the stores** — `require_public_space_member` on `create_report`, a `ConditionExpression` on the DynamoDB `#METADATA` put, `SELECT ... FOR UPDATE` on SQL (only a first-item `ConditionalCheckFailed` becomes the 409; other cancellation reasons propagate) — because the route-level check alone loses a concurrent unpublish/file race; both raise `SpaceConflictError` (defined in `schema/space_config.py` so stores and services can share it) which routes map to 409. In chat, `reports__create` keeps its no-confirmation exception but carries a **conditional** resolver that fires when the new report would land in a space; `reports__clone` is gated **unconditionally**, because whether it publishes depends on the source's placement and the resolver's read of that is not the handler's. `mcp_runtime` asks the resolver before the no-source fail-closed branch, so a declining resolver still means "not a gated call". That is what keeps a space from holding a report nobody else can see — which would both block the space's deletion invisibly and leak the report's ID through the overview pointer. **The space overview is a nullable pointer** (`space.overview_report_id`) at one of the space's own reports, set via `PUT /spaces/{id}/overview` — the target stays an ordinary report with no protections, and the pointer is **resolved lazily** by `GET /spaces/{id}/tree` (blanked when the target is deleted, moved out, or invisible to the caller), which is what removes the need for any invariant on the report. Only the tree and `PUT /spaces/{id}/overview` (which echoes what the caller just set) expose the pointer; `GET /spaces`, `GET /spaces/{id}` and `PUT /spaces/{id}` blank it, because outside the tree there is no report list to resolve it against. `delete_space` returns `SpaceDeleteResult` — a deliberate break from the `bool` delete convention — because "no such space" (404) and "still has reports" (409) differ, and emptiness is evaluated **without** visibility filtering (belt and braces: members are public anyway). Only reports block the delete; sub-spaces cascade with the space, and **no report is ever deleted with a space**. `delete_subspace` writes nothing to member reports: their dangling `subspace_id` is normalized to `None` by the tree endpoint, which beats an unbounded non-transactional fan-out. Permissions `spaces:read` (Viewer) / `spaces:write` / `spaces:delete` (Editor, matching reports); sub-spaces and the overview pointer reuse them.

The **`spaces` MCP builtin group** mirrors the routes one-for-one (no `*_versions` tools — spaces are flat). Both call the shared helpers in `services/spaces.py` — the duplicate-name guards (`find_duplicate_space_name`/`find_duplicate_subspace_name`) and the lazy resolvers (`without_overview`, `with_resolved_overview`, `with_resolved_subspace`) live there so REST and MCP cannot diverge on which responses carry the pointer. Filing a report is `spaces__set_report_space`, in the `spaces` group (disabling the group drops the whole feature) but keeping the route's `reports:write` permission. **Every mutating tool in the group is confirmation-gated, with no `chat_safe_without_confirmation` exception**, because a space is a shared globally visible container and filing publishes; a test asserts this so a new tool can't quietly opt out.

**YAML round-trip:** top-level `spaces:` (`SpaceDef`/`SubspaceDef` in `seizu_schema/reporting_config.py`, lower_snake_case keys) plus `Report.space`/`Report.subspace`. YAML keys are local handles — spaces and sub-spaces are matched **by name** on seed, like reports, since ids are server-generated. The three cross-references (report→space, report→sub-space, space overview→report filed in it) are validated by a `ReportingConfig` model validator at load, so a typo fails before any write. `space`/`subspace` are seed metadata like `pinned`: applied via `PUT /reports/{id}/space` after the version save and stripped from the stored config (`exclude={"pinned", "space", "subspace"}`), or restoring a version would relocate the report. Seed order is spaces → reports → membership → overview. **An omitted `space` never unfiles a report** (the `pinned` rule), and seeding deletes nothing. `seizu export` emits all of it and reuses existing keys. Docs: `docs/root/install/spaces.md`.

**Seeding:** existing reports/queries unchanged unless content differs or `--force`. `cypher` fields resolve against the top-level `queries` dict, else literal. Keep the `Report` TS interface in `src/config.context.ts` in sync with the Pydantic `Report` in `reporting/schema/reporting_config.py`.

### Cartography Data Sync

Manual runs: `nostart` profile; entrypoint is already `cartography` (don't prefix it): `make sync_cve / sync_aws / sync_github / sync_crowdstrike / sync_k8s`. Scheduled runs go through the `cartography_sync` Temporal workflow (see Temporal Workflows) executed by the dedicated `seizu-cartography-worker` service — a separate image (`Dockerfile.cartography`: upstream cartography pinned by tag+digest + the top-level `cartography_sync/` package's thin activity worker; after bumping the pin run `make cartography_contract_test`) on its own task queue, holding only cartography intel credentials. `cartography_sync/registry.py` is the security boundary: per-module typed flag allowlists, fixed credential env-var names/paths, argv-list exec (never a shell), scrubbed subprocess env; the activity re-validates params, re-enforces `CARTOGRAPHY_ENABLED_MODULES`, and rebuilds argv worker-side, so forged Temporal payloads can't escape the allowlist. Each subprocess runs exactly one `--selected-modules` stage — `create-indexes` and `analysis` are ordinary selectable modules users place explicitly (nothing is injected implicitly), and each module run is a `cartography_module` child workflow whose fixed workflow ID (`seizu-cartography-module:{module}`) is the per-module mutex (concurrent same-module syncs race on cartography's update tags; the pipeline waits up to `CARTOGRAPHY_MODULE_WAIT_SECONDS`, and `temporal_runs.py` folds child-workflow events into the runs UI). The sync worker must not import `reporting.*` (that pulls pydantic settings) — it reads plain env vars (`CARTOGRAPHY_NEO4J_URI`, `CARTOGRAPHY_TASK_QUEUE`, …); `registry`/`shared` stay stdlib-only, `activities`/`worker` may use temporalio. Docs: `docs/root/install/cartography-sync.md`.

### Workflow Activity Plugins

In `reporting/scheduled_query_modules/`; implement `ModuleInterface`. Modules are configured by `WORKFLOW_ACTIVITY_MODULES` and execute as Temporal activities in the order stored by the top-level configurable workflow. Required: `action_name()`, `setup()`, `handle_results(scheduled_query_id, action, results)`, `action_config_schema()` → `List[ActionConfigFieldDef]` (types: `string`, `text`, `number`, `boolean`, `string_list`, `select`, `parameters`, `module_runs`; optional `warning` renders an alert above the field, and a **required boolean must be `True`** to validate — used for explicit acknowledgements). Code-defined workflows are **not** activity modules: they are top-level activity types from `WORKFLOW_REGISTRY` (the former `workflow`/`temporal` dispatcher module is removed; stale `WORKFLOW_ACTIVITY_MODULES` entries naming it are skipped with a warning). Schemas are served via `GET /api/v1/config` as `workflow_activity_schemas`; scheduled-query keys remain compatibility aliases.

### Headless Agent Runs & Confirmation Bypass

`reporting/services/headless_chat.py:run_headless_chat()` drives a full chat-agent turn for a stored user (session persists as their `ChatSessionItem`, visible in their chat UI). Callers go through `reporting/services/agent_run.py:run_agent_session()`, which wraps it with identity resolution, optional server-side skill rendering, the untrusted-evidence boundary, and run-status normalization — shared by scheduled chats and the `agent_chat` workflow module. Security model (don't weaken without explicit sign-off):
- **Identity:** headless runs execute as the schedule/scheduled-query *creator*. `reporting/authnz/headless.py:resolve_stored_user()` rebuilds their permissions from the last role claim seen on an authenticated request (`User.role`, synced by `get_or_create_user`); archived users hard-stop.
- **Confirmation bypass is permission-based:** `chat:bypass_permissions` (Editor+). `mcp_runtime.call_tool_for_chat(bypass_confirmations=True)` re-checks the permission and AUDIT-logs every bypassed execution; without the permission the call is blocked. Headless runs without bypass keep the normal confirmation flow (mutating tools fail closed for the run). Creator RBAC and `chat_safe_only` always apply.
- The same permission gates the chat UI "Bypass confirmations" toggle (`ChatStreamRequest.bypass_confirmations`, 403 without it; default off).
- Headless turns get a system-prompt addendum (`_HEADLESS_PROMPT_ADDENDUM` in chat_graph) telling the model nobody can answer: don't ask for confirmation, follow the skills, summarize blocks instead of retrying.

### Scheduled Chats

Recurring headless agent runs (prompt + trigger, no Cypher) managed from the top-level Scheduled Chats page (`/app/scheduled-chats`, with `/:id/history` for versions); `chat:schedule` (Editor+) gates the CRUD routes (`/api/v1/chat/schedules` + `/versions` + `/sessions`, registered when `CHAT_ENABLED` + `CHAT_SCHEDULES_ENABLED`) and the UI (`features.chat_schedules` → `useFeature('chat_schedules')`). Schedules are per-user (`created_by` owner; list/get/update/delete are owner-scoped, 404 otherwise) with full version history (`ScheduledChatItem`/`ScheduledChatVersion` in `reporting/schema/chat.py`; both store backends + lock/record methods mirror scheduled queries). Run sessions are created with `ChatSessionItem.origin="scheduled"` + `scheduled_chat_id`: excluded from `list_chat_sessions`, listed via `list_scheduled_chat_sessions`, viewable read-only from the page's runs dialog (transcripts via `GET .../sessions/{thread_id}/history`, resolved under the owner's namespace), and `POST /api/v1/chat/stream` rejects them (403). `chat:schedule:read_all` (Admin) unlocks viewing every user's schedules (`?all=true` / `?user_id=` on the list, plus read access to get/versions/sessions/history); mutations stay owner-only. Execution runs on **Temporal** (the polling worker is gone; Temporal is now required for scheduled chats). `reporting/services/chat_schedules.py` binds the shared reconciler (`reporting/services/schedule_reconciler.py`, kind-parameterized and also used by `workflow_schedules.py`) to chat records, reconciled by `seizu-temporal-worker`'s loop; `reporting/temporal_workflows/scheduled_chat.py` holds `seizu_scheduled_chat` + `seizu_scheduled_chat_watch_poll`, whose activities (`load_scheduled_chat`, `run_scheduled_chat_session`, `record_scheduled_chat_run_result`, `check_scheduled_chat_watch`) live in `activities.py`. `ChatScheduleSpec` triggers (hourly interval / daily days-of-week+hour / monthly days-of-month, all UTC) map to Temporal interval/calendar specs; monthly over-fires days 28-31 and `load_scheduled_chat` drops the non-matching firings with `schedule_due` (`reporting/services/schedule_spec.py`). `watch_scans` becomes a poll workflow reusing `check_watch_scan_triggered`. Overlap is `ScheduleOverlapPolicy.SKIP` (no mutex — unlike `ConfiguredWorkflow`), runs use `maximum_attempts=1`, and disabling pauses the Schedule rather than deleting it. Records carry `schedule_sync_status`/`schedule_sync_error`/`schedule_synced_at` (migration `0004_chat_schedule_sync_fields`). `POST /api/v1/chat/schedules/<id>/run` (owner-only) records `run_requested_at` and starts the run immediately, keyed on that timestamp so a recovering reconcile pass is idempotent (runs even when disabled). The agent session itself goes through `reporting/services/agent_run.py:run_agent_session` — shared with the `agent_chat` workflow module. Settings: `CHAT_SCHEDULES_ENABLED`, `CHAT_SCHEDULE_TIMEOUT_SECONDS`. Docs: `docs/root/install/chat-schedules.md`.

### Sandbox Delegation

`reporting/services/mcp_builtins/sandbox.py` — a `chat_only=True` builtin that lets the chat agent delegate code execution and file operations to an isolated ephemeral sandbox.

**`SandboxBackend` protocol** — the five-operation stable interface (`run_python`, `run_bash`, `read_file`, `write_file`, `list_files`) that every sandbox provider must implement, plus `sandbox_id` (the resume handle). Fixed names/descriptions mean the inner agent behaves consistently across providers. Use `@runtime_checkable` — `isinstance(mock, SandboxBackend)` works in tests, but the check reads members with `getattr_static`, so a `MagicMock` must have **every** member explicitly assigned; an auto-attribute does not count.

**`_E2BSandboxBackend`** — the built-in implementation wrapping `e2b_code_interpreter.AsyncSandbox`. Contains all E2B-specific logic (response shape, bytes decoding, etc.). Nothing outside this class knows about E2B internals.

**`_open_backend(*, api_key, domain)`** — async context manager; the single extension point for swapping providers. Opens an `_E2BSandboxBackend` today. To add a new backend: implement `SandboxBackend`, select it here (e.g. by a new `SANDBOX_BACKEND` setting), yield it. No other code changes.

**`_build_sandbox_tools(backend)`** — wraps the five `SandboxBackend` methods as LangChain `StructuredTool`s for the `create_react_agent` inner subagent. Takes `SandboxBackend`, not `Any`.

**Lifecycle — one sandbox per conversation** (`reporting/services/sandbox_session.py`). A `SandboxSession` is made ambient (ContextVar) by `chat_agent_node` on the single-agent path and by `dispatcher_node` on the orchestrated one — **turn-level, not step-level**: `asyncio.gather` copies the context but not the object, so every step of a batch shares one sandbox while keeping its own `EpisodeLog`. Opened lazily on the first delegation, then **suspended** (`open_backend(suspend_on_exit=...)` → E2B `pause()`) and resumed next turn via `resume_sandbox_id` (→ `AsyncSandbox.connect`), with the id carried in `ChatState.sandbox_id`. A failed resume is only "not an error" for **terminal** errors (`SandboxNotFoundException`/`NotFoundException`): a fresh sandbox is created and the differing `sandbox_id` is how callers learn the old files are gone. A timeout, rate limit or auth failure **propagates** — replacing on those leaves the old sandbox paused and alive while its id is overwritten, i.e. paid for and unreachable. `on_teardown` reports whether the pause actually succeeded, because a failed pause falls back to a kill and returning that id would checkpoint a dead sandbox. **`SANDBOX_SESSION_PERSIST` defaults off** until a TTL/reaper exists. `suspend_on_exit` accepts a callable because the keep-or-destroy decision is only known at exit — **error paths must call `abandon_sandbox_session()`**, which keeps a *resumed* sandbox and destroys a freshly *created* one. The distinction is load-bearing: a resumed sandbox's id is already in the checkpoint from an earlier successful turn, so failing changes nothing about finding it again, and destroying it would cost a long conversation every turn's accumulated work over one broken turn — while a sandbox created by the turn that raised has its id nowhere, so pausing it strands it. Deleting a thread destroys its sandbox and never blocks on that succeeding (orphans logged at warning, with the id). **No time-based reaper exists** — `SANDBOX_SESSION_TIMEOUT_SECONDS` bounds a *running* sandbox — so threads users abandon rather than delete accumulate paused sandboxes until provider retention. `delete_thread_messages` kills the thread's sandbox first. `SANDBOX_SESSION_PERSIST=false` restores a sandbox per turn.

**What the sub-agent is bound** (`_bound_tool_names`): the read-only graph core (`_CORE_TOOL_NAMES`) + what the conversation disclosed (`chat_graph.current_disclosed_tools()`, an ambient contextvar set per turn and per worker step) + what the delegating call named in `tools`. Naming `tools` **narrows** as well as widens — the caller that knows the task gets that plus the core, not the disclosed set too. Everything else the user may reach stays reachable via `find_seizu_tools` / `call_seizu_tool`, which share the same `_invoke` (same bounds, same oversized-result file, same receipt), so narrowing costs a round trip, not a capability. Binding all 58 chat-safe tools cost ~3,800 tokens of schema on **every** inner LLM call (~a fifth of a delegation's spend, two thirds of it management CRUD); the bound set is now ~830-1,100. Harness, 4 samples before/after (cross-build, and bundling session memory + the disclosure fixes): median sandbox tokens 181k → 48k with non-overlapping ranges, budget exhaustion 4/4 → 0/4, failed steps 4/5 → 0/8, median answer 1,233 → 2,339 chars; no sample needed `find_seizu_tools`. RBAC is untouched by any of this — `chat_safe_only` + `exclude_confirmation_gated` still bound what `_invoke` can reach.

**Progressive disclosure is not an authorization boundary** (`CHAT_LLM_PROGRESSIVE_DISCLOSURE`). RBAC is — `chat_safe_only` + `chat:tools:call` bound `_list_chat_tools`, and the sandbox sub-agent already gets that whole set regardless of what the outer model has been shown. Disclosure only decides what a model is *shown*, so: `ChatState.disclosed_tools` carries across turns, `planner_node`'s capability context includes what earlier turns unlocked, and a tool a plan names in `required_action` is **disclosed rather than refused** (`_required_action_spec` resolves against the full permitted universe; only a tool that does not exist for the user is a contract error). Refusing was a real cost — the planner reads session memory, where a tool an earlier turn used is recorded by name — for no security gain. Only the explicitly required tool is disclosed this way; `suggested_tools` still come from the disclosed pool.

**Session memory** (`reporting/services/episodic_memory.py`). `EpisodeLog` is per-step (unchanged); `SessionLedger` is the cross-turn carry, seeded from `ChatState.session_memory` and written back by whichever node ran the delegations. It holds episodes plus **receipts** — files an oversized tool result was written to, recorded by `_record_receipt` in `sandbox.py` with the delegated task as their purpose. Receipts render **only for their own `sandbox_id`**, so a replacement sandbox silently stops advertising files that no longer exist. Sub-agents get `EpisodeLog.recall(sandbox_id=...)` built *after* the backend is resolved; the top-level agent (planner, worker, single-agent loop) gets the shorter `session_digest()` under `chat_graph.SESSION_MEMORY_PREAMBLE`, because it is the model that decides whether to delegate at all. Both fenced as untrusted.

**Security model**: the sandbox is network-isolated from Seizu's data stores and credentials, and holds no credentials of its own. No confirmation gate (`chat_safe_without_confirmation=True`; same rationale as `reports__create`). `chat_only=True` keeps the tool off the MCP server endpoint. Persistence widens the blast radius deliberately: untrusted code lives for a conversation rather than a turn, bounded to one user's thread (the resume id lives in that thread's checkpoint, which is already user-namespaced).

**Permission**: `sandbox:delegate` (Editor+). Enabled via `SANDBOX_ENABLED=true`. Settings: `SANDBOX_API_KEY`, `SANDBOX_DOMAIN`, `SANDBOX_TIMEOUT_SECONDS`, `SANDBOX_MAX_OUTPUT_BYTES`, `SANDBOX_LLM_MODEL`, `SANDBOX_SESSION_TIMEOUT_SECONDS`, `SANDBOX_SESSION_PERSIST`, `CHAT_SESSION_MEMORY_*`. Docs: `docs/root/install/sandbox.md`.

**Sandbox coding-agent** (`reporting/services/sandbox_agent.py`) — the reusable, provider-agnostic machinery for running a headless coding-agent CLI in a sandbox: the `PROVIDERS` registry (`claude` default, `codex`, `opencode`), credential resolution (static key / `SANDBOX_AGENT_API_KEY_COMMAND` mint / credential proxy), agent-env + run-script builders, and secret masking. Config is the generic `SANDBOX_AGENT_*` family. Any workflow (or a future tool) composes it; `sandbox_remediation` is the first consumer.

**Sandbox remediation** (`reporting/services/sandbox_remediation.py`) — not a chat tool: the `cve_dependency_remediation` Temporal workflow drives the sandbox directly, composing `sandbox_agent` (provider/credentials) with the git/GitHub flow (clone, branch, diff, push, PR) against a cloned repo. opencode is multi-provider: `SANDBOX_AGENT_MODEL` = `provider/model` (e.g. `deepseek/deepseek-v4-pro`) selects the key env + reuses the chat assistant's global `*_API_KEY` (e.g. `DEEPSEEK_API_KEY`), enabling DeepSeek etc. **Two sandboxes** so the GH token never shares a VM with untrusted code, with per-command env isolation (`run_bash_streaming(envs=...)`). *Agent sandbox:* install (no secrets) → clone/branch (GH token via `gh auth setup-git`, none on disk/URL, pre-agent) → guard (skip with status `skipped` if an open PR exists) → agent run (provider key only — **never the GH token**) → extract the change as a base64 git diff (no token). *Fresh push sandbox* (never ran the agent/npm/tests): apply the patch to a clean clone, commit, push + `gh pr create` (GH token) — so an agent-planted hook/PATH-shadow can't reach the token. Branch is version-keyed (`seizu/dependency-update/{eco}-{pkg}-{version}`, hash fallback) so same-fix re-runs converge and later different-version fixes get distinct PRs. `REMEDIATION_USE_FORK` pushes the branch to a bot-owned fork instead (ensured on demand via `github_checks.ensure_fork`; `REMEDIATION_FORK_ORG` picks the owning org) and opens the PR cross-repo — the token then needs no write access to the target repos; fix runs clone/push the PR branch on the fork. After a fresh push the workflow **watches the PR's CI** (durable timers + the read-only worker-side `reporting/services/github_checks.py`, which holds the GH token safely — no untrusted code runs in the worker): polls every `REMEDIATION_CI_POLL_SECONDS` up to `REMEDIATION_CI_MAX_WAIT_SECONDS` (0 disables), ignoring checks queued past `REMEDIATION_CI_QUEUED_STUCK_SECONDS` (never-scheduled runners) and cancelled/stale runs; on settled failures it runs ≤`REMEDIATION_CI_FIX_MAX_ATTEMPTS` fix-mode agent sessions (`sandbox_remediation.run_ci_fix`: same two-sandbox isolation, checks out the existing PR branch, extracts only new commits, fast-forward push — no force, no `gh pr create`) where the agent fixes upgrade-caused failures or writes a PR-comment file explaining unrelated ones (posted worker-side via the API through a fixed sanitized template — block-quoted, @-mentions/slash-commands neutralized, length-capped — never verbatim; the agent never gets credentials). Outcome lands in `DependencyRemediationResult.ci_status`/`ci_detail`. No per-user permission and no enable flag: configured (`REMEDIATION_GITHUB_TOKEN` + agent key) = enabled; scheduled queries are admin-managed, and `scheduled_queries:write` is re-checked per run. `SANDBOX_AGENT_API_KEY_COMMAND` mints short-lived per-run agent keys (recommended — the agent key IS exposed to untrusted repo code; a static key warns); `SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED` (all providers; opencode needs `SANDBOX_AGENT_MODEL` to derive the LiteLLM namespace) instead runs a LiteLLM proxy in a *third* sandbox holding the real key and hands the agent only the proxy's ephemeral master key (per-key `/key/generate` needs a DB we don't run; the config sets an in-memory `max_budget` cap instead) that dies at teardown; that proxy sandbox stays private (`allow_public_traffic=false`), reached via E2B's traffic-access token the CLI sends as a custom header — per `SubagentProvider.proxy_transport` (claude=`ANTHROPIC_CUSTOM_HEADERS` env; codex=`~/.codex/config.toml` `env_http_headers`; opencode=an `@ai-sdk/openai-compatible` provider config). LiteLLM↔CLI wire is unverified (`make remediation_smoke SMOKE_PROXY=1` probes it — smoke-test before prod); `REMEDIATION_GITHUB_HOST` supports GHE; `REMEDIATION_GH_VERSION`/`REMEDIATION_GH_SHA256` pin the gh version + tarball digest; `SANDBOX_AGENT_TEMPLATE` selects an E2B prebuilt image. Keeping CVE ids out of PRs is prompt-only (they're public). Workflow-supplied repo/branch values are regex-validated and reach scripts only via env vars; PR review is the gate.

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
| `seizu-temporal-worker` | Workflow + scheduled-chat scheduler and Temporal workflow/activity worker |
| `seizu-cartography-worker` | Cartography sync activity worker (own image + task queue) |
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
