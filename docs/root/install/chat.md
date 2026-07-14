# Chat Assistant

## Purpose

The chat assistant is an LLM agent built into the web app at `/app/chat`. It answers questions about your graph by calling the same tools exposed by the [MCP server](mcp-toolsets.html) — running Cypher, inspecting the schema, reading reports and scheduled queries, rendering [skills](mcp-skillsets.html) — and can create or update resources with your explicit confirmation. Conversations stream token-by-token, persist across reloads, and are organized into named sessions in a sidebar.

The assistant also powers the headless features documented separately: [scheduled chats](chat-schedules.html), agent sessions started by [Temporal workflows](temporal-workflows.html), and [sandbox delegation](sandbox.html).

## Enabling chat

Chat is off by default. Set `CHAT_ENABLED=true` to register the chat API routes, initialize checkpoint storage, and show the Chat UI (the frontend discovers it via `GET /api/v1/config` → `features.chat`).

The default provider is `mock`, which just echoes input — deterministic and keyless, useful for development but unable to call tools. For real use, pick a model through **LiteLLM**: set `CHAT_LLM_MODEL` to a provider-namespaced model id and supply the provider's API key. The supported provider/model surface is whatever LiteLLM supports rather than a fixed allowlist.

```shell
CHAT_ENABLED=true
CHAT_LLM_PROVIDER=litellm
CHAT_LLM_MODEL=anthropic/claude-sonnet-4-6
ANTHROPIC_API_KEY=sk-ant-...
```

API keys resolve in order: `CHAT_LLM_API_KEY`, then the standard provider env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`), then LiteLLM's own per-provider environment lookup. Seizu fails fast at startup if a real provider is selected without a model.

`CHAT_LLM_BASE_URL` points chat at a self-hosted LiteLLM proxy or another OpenAI-compatible gateway. Legacy `CHAT_LLM_PROVIDER` values (`openai`, `anthropic`, `gemini`, `deepseek`) still work and namespace a bare `CHAT_LLM_MODEL`.

Chat history requires checkpoint storage (DynamoDB by default, PostgreSQL optional); the `CHAT_CHECKPOINT_*` variables are documented in the [backend configuration](backend.html) under *Chat checkpoint storage*.

## Permissions

| Permission | Built-in role | Grants |
|------------|---------------|--------|
| `chat:use` | `seizu-viewer` and above | Access to the chat endpoint and UI. |
| `chat:tools:call` | `seizu-editor` and above | Letting the agent call tools during a turn. |
| `chat:skills:call` | `seizu-editor` and above | Letting the agent render skills during a turn. |
| `chat:bypass_permissions` | `seizu-editor` and above | The **Bypass confirmations** toggle and headless confirmation bypass (see below). |

Tool and skill calls also require the *underlying* MCP permission (for example `tools:call` or `skills:render`) — chat never grants access the user's role doesn't already have.

## Tool access and action confirmations

Chat exposes a deliberately narrower tool surface than the MCP server:

- **Read and inspection tools** (schema, query, validate, listing reports/toolsets/skillsets/scheduled queries/users/roles) are available directly.
- **Mutating tools** (creating or updating reports, scheduled queries, roles, and so on) pause the turn and render an in-chat **confirmation card**; the action runs only after you approve it. Approvals and denials expire after `ACTION_CONFIRMATION_TTL_SECONDS`.
- New write/delete tools are **hidden from chat by default** (fail closed) until they are explicitly given a confirmation flow. The only no-confirmation mutating exceptions are `reports__create` and `reports__clone`, which create new private reports and cannot modify existing resources.

Users holding `chat:bypass_permissions` see a **Bypass confirmations** toggle (off by default) that lets the agent execute confirmation-gated actions without pausing. Every bypassed execution is audit-logged, and the user's normal RBAC permissions still apply. The same permission controls whether headless runs (scheduled chats, Temporal workflows) may bypass confirmations — without it, mutating tools fail closed for the run.

## Sessions and history

Conversations are grouped into sessions listed in the chat sidebar; sessions can be created, renamed, and deleted. The active thread id is kept in browser `localStorage`, and reloading the page rehydrates the conversation from the server. Thread ids are namespaced server-side per user, so one user can never reach another user's thread.

Assistant turns include an expandable details section showing thinking and tool calls (arguments and output). Replies cut off by the output-token limit are auto-continued server-side and stitched into one response (bounded by `CHAT_LLM_MAX_CONTINUATIONS`); a manual **Continue response** action covers the rest.

Sessions created by scheduled chats are excluded from the sidebar and are read-only; see [scheduled chats](chat-schedules.html).

## Orchestration and run budgets

For multi-step requests, chat can route a turn through a plan → dispatch → verify orchestration instead of the single-agent path. A cheap router classifies each turn; simple turns take the direct path with no extra LLM call, while complex ones get a planner, scoped sub-agent workers (run in parallel when steps are independent), and a verify gate with bounded retry. This is on by default and controlled by the `CHAT_ORCHESTRATOR_*` settings below.

Every run — interactive or scheduled — is governed by a shared budget ledger tracking tokens, estimated USD cost (when LiteLLM knows the model price), and LLM call count. `CHAT_RUN_RESERVE_PERCENT` holds back part of the budget so final summaries and synthesis can produce an explicit partial result instead of stopping mid-plan; after the soft limit, eligible read-only work switches to `CHAT_LLM_ECONOMY_MODEL` when one is configured. Run outcomes distinguish `success`, `partial`, `budget_exhausted`, `blocked`, and `failure`.

## Configuration

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_ENABLED` | `false` | Master switch: gates the chat routes, checkpoint storage, and the Chat UI. |
| `CHAT_LLM_PROVIDER` | `mock` | `mock` echoes input (no tools); any other value routes through LiteLLM. Legacy values (`openai`, `anthropic`, `gemini`, `deepseek`) namespace a bare model name. |
| `CHAT_LLM_MODEL` | `""` | LiteLLM model id, preferably provider-namespaced (e.g. `anthropic/claude-sonnet-4-6`). Required for any real provider. |
| `CHAT_LLM_API_KEY` | `""` | Optional API key override passed to LiteLLM; falls back to the standard provider env vars. |
| `CHAT_LLM_BASE_URL` | `""` | Optional OpenAI-compatible base URL (LiteLLM `api_base`) for a self-hosted proxy or gateway. |
| `CHAT_LLM_TEMPERATURE` | `0.2` | Sampling temperature. |
| `CHAT_LLM_MAX_TOKENS` | `4096` | Per-call output token cap. |
| `CHAT_LLM_TIMEOUT_SECONDS` | `60` | Per-call provider timeout. |
| `CHAT_LLM_MAX_RETRIES` | `2` | Provider retry count. |

### Turn behavior

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_LLM_SYSTEM_PROMPT` | `""` | Full system prompt override. Empty uses Seizu's built-in security-dashboard prompt. |
| `CHAT_LLM_PROGRESSIVE_DISCLOSURE` | `true` | Show the model skills first and let rendered skills disclose which tools to use; `false` presents all chat-safe tools and skills up front. |
| `CHAT_LLM_MAX_AUTO_ACTIONS` | `12` | Maximum tool/skill calls the agent executes in one assistant turn. |
| `CHAT_LLM_MAX_PARALLEL_TOOL_CALLS` | `4` | Maximum tool calls run concurrently in one batch. |
| `CHAT_LLM_MAX_CONTINUATIONS` | `2` | Auto-continuation attempts when a reply is cut off by the token limit; `0` disables (leaving the manual **Continue response** button). |
| `CHAT_LLM_MAX_RESPONSE_CHARS` | `60000` | Hard ceiling on a stitched auto-continued response; `0` disables. |
| `CHAT_LLM_CONTEXT_MAX_MESSAGES` | `80` | Maximum prior messages sent to the LLM (checkpoints may retain more for UI history). |
| `CHAT_LLM_CONTEXT_MAX_CHARS` | `120000` | Maximum prior-message characters sent to the LLM. |

### Orchestrator

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_ORCHESTRATOR_ENABLED` | `true` | Route complex turns through plan → dispatch → verify; when off, every turn takes the single-agent path. |
| `CHAT_ORCHESTRATOR_MAX_STEPS` | `8` | Maximum steps the planner may emit for one turn. |
| `CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS` | `4096` | Planner generation budget, kept separate so thinking models have room to emit the structured plan. |
| `CHAT_ORCHESTRATOR_MAX_ITERATIONS` | `3` | Verify-driven retry cycles before synthesizing an answer from the steps that passed. |
| `CHAT_ORCHESTRATOR_MAX_PARALLEL` | `3` | Independent steps dispatched concurrently in one batch. |
| `CHAT_ORCHESTRATOR_WORKER_MAX_ACTIONS` | `24` | Per-step action-count guard, used only when all shared budget dimensions are disabled. |

### Run budgets

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_RUN_TOKEN_BUDGET` | `120000` | Per-run token budget; `0` disables this dimension. |
| `CHAT_RUN_COST_BUDGET_USD` | `0` | Per-run estimated-cost budget in USD; `0` disables this dimension. |
| `CHAT_RUN_RESERVE_PERCENT` | `20` | Portion of the budget reserved for final summaries and synthesis. |
| `CHAT_RUN_SOFT_LIMIT_PERCENT` | `75` | Threshold after which eligible work switches to the economy model. |
| `CHAT_RUN_MAX_LLM_CALLS` | `64` | Emergency ceiling on LLM calls per run. |
| `CHAT_LLM_PLANNER_MODEL` | `""` | Optional planner model override; empty inherits `CHAT_LLM_MODEL`. |
| `CHAT_LLM_WORKER_MODEL` | `""` | Optional worker model override. |
| `CHAT_LLM_VERIFIER_MODEL` | `""` | Optional verifier model override. |
| `CHAT_LLM_SYNTHESIZER_MODEL` | `""` | Optional synthesizer model override. |
| `CHAT_LLM_ECONOMY_MODEL` | `""` | Model used for eligible read-only work after the soft budget limit. |

### History and tool-result limits

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_MAX_PERSISTED_MESSAGES` | `200` | Maximum persisted messages per thread; older turns are trimmed from checkpoint state. |
| `CHAT_HISTORY_LIMIT` | `100` | Default number of messages returned by `GET /api/v1/chat/history`. |
| `CHAT_TOOL_RESULT_MAX_ROWS` | `100` | Maximum rows returned to chat from one tool call (normal MCP calls are unaffected). |
| `CHAT_TOOL_RESULT_MAX_BYTES` | `200000` | Maximum serialized bytes returned to chat from one tool call. |
| `ACTION_CONFIRMATION_TTL_SECONDS` | `1800` | Lifetime of an approved or denied mutating-action confirmation. |

Checkpoint storage (`CHAT_CHECKPOINT_*`) is documented in the [backend configuration](backend.html).

## Related features

- [Scheduled chats](chat-schedules.html) — run the agent headlessly on a recurring schedule.
- [Sandbox delegation](sandbox.html) — let the agent run code in an isolated ephemeral sandbox.
- [Temporal workflows](temporal-workflows.html) — durable workflows whose AI sessions run through the same headless chat machinery.
- [MCP toolsets](mcp-toolsets.html) and [MCP skillsets](mcp-skillsets.html) — the user-defined tools and skills the agent can use.
