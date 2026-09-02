# Chat Assistant

## Purpose

The chat assistant is an LLM agent built into the web app at `/app/chat`. It answers questions about your graph by calling the same tools exposed by the [MCP server](mcp-toolsets.html) — running Cypher, inspecting the schema, reading reports and scheduled queries, rendering [skills](agent-plugins.html) — and can create or update resources with your explicit confirmation. Conversations stream token-by-token, persist across reloads, and are organized into named sessions in a sidebar.

The assistant also powers the headless features documented separately: [scheduled chats](chat-schedules.html), agent sessions started by [workflows](built-in-workflows.html), and [sandbox delegation](sandbox.html).

## Enabling chat

Most of this page is reference material: orchestration, run budgets, context
windows, prompt caching. Almost none of it is required reading, and almost every
setting it documents is optional tuning with a working default. A useful chat
deployment needs four things:

1. **`CHAT_ENABLED=true`**, plus PostgreSQL checkpoint storage for history —
   the `CHAT_CHECKPOINT_DATABASE_*` variables in
   [backend configuration](backend.html).
2. **A real provider, model, and API key**: set `CHAT_LLM_PROVIDER=litellm`,
   then either set `CHAT_LLM_MODEL` as the environment base model or configure
   an enabled default model profile. Supply the provider keys needed by every
   model in that configuration. Seizu refuses to start when neither source
   supplies a model.
3. **Permissions** for the people who should have chat, and for what the agent
   may do on their behalf — see [Permissions](#permissions).
4. **A model profile**, if you want more than one model. Skip this and every
   turn uses the `CHAT_LLM_*` settings above. Add one from
   [Model profiles](#model-profiles) to give users a choice of model or
   reasoning level, an economy fallback, or a per-profile cost cap.

Two optional capabilities are worth knowing about up front, because both are off
by default and both noticeably change what the agent can do:

- **[The sandbox](sandbox.html)** (`SANDBOX_ENABLED=true`) lets the agent run
  code in an isolated VM, and is what makes skills that ship `scripts/` runnable.
- **[External MCP](external-mcp.html)** (`MCP_EXTERNAL_ENABLED=true`) gives the
  agent tools from other MCP servers through a configured proxy.

Chat is off by default. Set `CHAT_ENABLED=true` to register the chat API routes, initialize checkpoint storage, and show the Chat UI (the frontend discovers it via `GET /api/v1/config` → `features.chat`).

The default provider is `mock`, which just echoes input — deterministic and keyless, useful for development but unable to call tools. For real use, set `CHAT_LLM_PROVIDER=litellm`, then set `CHAT_LLM_MODEL` to a provider-namespaced model id or configure an enabled default model profile, and supply the required provider keys. The supported provider/model surface is whatever LiteLLM supports rather than a fixed allowlist.

```shell
CHAT_ENABLED=true
CHAT_LLM_PROVIDER=litellm
CHAT_LLM_MODEL=anthropic/claude-sonnet-4-6
ANTHROPIC_API_KEY=sk-ant-...
```

API keys resolve in order: `CHAT_LLM_API_KEY`, then the standard provider env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`), then LiteLLM's own per-provider environment lookup. Seizu fails fast at startup if a real provider is selected without either `CHAT_LLM_MODEL` or an enabled default profile.

`CHAT_LLM_BASE_URL` points chat at a self-hosted LiteLLM proxy or another OpenAI-compatible gateway. Legacy `CHAT_LLM_PROVIDER` values (`openai`, `anthropic`, `gemini`, `deepseek`) still work and namespace a bare `CHAT_LLM_MODEL`.

Chat history requires PostgreSQL checkpoint storage; the
`CHAT_CHECKPOINT_DATABASE_*` variables are documented in the
[backend configuration](backend.html) under *Chat checkpoint storage*.

## Permissions

| Permission | Built-in role | Grants |
|------------|---------------|--------|
| `chat:use` | `seizu-viewer` and above | Access to the chat endpoint and UI. |
| `chat:tools:call` | `seizu-editor` and above | Letting the agent call tools during a turn. |
| `chat:skills:call` | `seizu-editor` and above | Letting the agent render skills during a turn. |
| `chat:bypass_permissions` | `seizu-editor` and above | The **Bypass confirmations** toggle and headless confirmation bypass (see below). |

Tool and skill calls also require the *underlying* MCP permission (for example `tools:call` or `skills:render`) — chat never grants access the user's role doesn't already have.

## Model profiles

Admins manage model profiles from **Model Profiles** in the app sidebar. A
profile names a base primary model and default user reasoning level, one economy
fallback model and reasoning level, optional primary model and reasoning
overrides for individual chat stages, the reasoning levels users may select,
and a per-run USD cost cap. A stage whose
reasoning is **Inherit base** uses the user's selected level; selecting an
explicit value fixes that stage to the admin's value. Each save creates a
version. `model_profiles:read`,
`model_profiles:write`, and `model_profiles:delete` are granted to the built-in
Admin role.

Every user with `chat:use` selects one of the levels the profile's admin made
available; new profiles offer `low`, `medium`, and `high` by default. The full
admin vocabulary is LiteLLM's `default`, `none`, `minimal`, `low`, `medium`,
`high`, and `xhigh`. The first admitted turn locks that conversation to the
profile, while the reasoning level remains changeable between turns. The selector
then shows only that profile and its allowed levels. The selection affects
stages whose reasoning inherits the base; fixed stage overrides and the economy
fallback retain their configured reasoning. Start a new conversation to use
another profile. If a locked conversation's profile is later disabled or
deleted, start a new conversation; Seizu does not substitute another profile. The full resolved
choice is captured when a turn is admitted, so editing a profile does not alter
a running turn.

The first enabled profile becomes the default. When profiles exist, exactly one
enabled profile must be the default. Seizu does not install built-in profiles:
until an admin creates one, chat continues to use the `CHAT_LLM_*` environment
settings. A profile has one primary base model. Direct assistant calls use that
base, and every other runtime stage inherits it unless that stage has an
override. There is no separate assistant setting in a profile.

A profile's cost cap is bounded by `CHAT_RUN_COST_BUDGET_USD`: when both are
positive, the lower value applies. Set the global value to the deployment-wide
hard ceiling and use profiles for smaller per-choice limits. The Model Profiles
page warns when a profile requests more than this ceiling; the profile remains
valid, but turns use the lower global value.

## Tool access and action confirmations

Chat exposes a deliberately narrower tool surface than the MCP server:

- **Read and inspection tools** (schema, query, validate, listing reports/toolsets/skillsets/scheduled queries/users/roles) are available directly.
- **Mutating tools** (creating or updating reports, scheduled queries, roles, and so on) pause the turn and render an in-chat **confirmation card**; the action runs only after you approve it. Approvals and denials expire after `ACTION_CONFIRMATION_TTL_SECONDS`.
- New write/delete tools are **hidden from chat by default** (fail closed) until they are explicitly given a confirmation flow. The only no-confirmation mutating exception is `reports__create`, which creates a new private report and cannot modify existing resources — and it still asks for confirmation in the one case where the new report is public (filing it into a space). `reports__clone` asks every time, since whether the copy is public depends on where the source is filed.

Users holding `chat:bypass_permissions` see a **Bypass confirmations** toggle (off by default) that lets the agent execute confirmation-gated actions without pausing. Every bypassed execution is audit-logged, and the user's normal RBAC permissions still apply. The same permission controls whether headless runs (scheduled chats, Temporal workflows) may bypass confirmations — without it, mutating tools fail closed for the run.

## Sessions and history

Conversations are grouped into sessions listed in the chat sidebar; sessions can be renamed and deleted. Opening `/app/chat` — and **New session** — shows a question prompt rather than a conversation: the session is created when you ask something, so an abandoned visit leaves no empty session behind and the sidebar lists only questions that were actually asked. A session's own URL (`/app/chat/<thread id>`) opens it directly and reloading rehydrates it from the server. Thread ids are namespaced server-side per user, so one user can never reach another user's thread.

Every turn is timestamped when it is persisted. Assistant replies show the time beside their copy button; hovering your own message reveals its time and a copy button of its own. Messages persisted before timestamps were recorded simply show no time.

Each assistant turn opens with a details block showing its thinking and tool calls (arguments and output). It is open by default and moves only when you click it; thinking appears while the model is still reasoning and is expanded, while a tool call starts collapsed. An orchestrated turn nests each step's thinking, tool calls and verification under that step. A plan step's thinking is shown live only — a reloaded turn replays its plan, calls and results, not the reasoning behind them — and a model that returns structured output natively exposes no thinking to show. Replies cut off by the output-token limit are auto-continued server-side and stitched into one response (bounded by `CHAT_LLM_MAX_CONTINUATIONS`); a manual **Continue response** action covers the rest.

Sessions created by scheduled chats are excluded from the sidebar and are read-only; see [scheduled chats](chat-schedules.html).

## Turns outlive the connection watching them

A turn does not run inside its HTTP request. Sending a message admits a **turn**
— a Temporal workflow on `seizu-temporal-worker` — which writes to a short-lived
**turn event log**; the request is a reader over that log, so closing
the tab, losing the network, or navigating away neither stops the turn nor loses
what it has already produced. Coming back replays the turn from its first token
and then follows it live — the browser reattaches automatically on load, and
after a dropped connection.

**Stop** ends the turn on the server, not just the stream. That is a separate
request, because closing the connection no longer stops anything: the turn would
carry on generating and could still run the actions it had queued. It takes
effect immediately, including while the turn is blocked on a slow model call or
tool.

Deleting a conversation closes it to new turns, stops the one running, and only
then removes anything. If the turn cannot be stopped, the delete **fails with a
503 and changes nothing** — the conversation stays closed, so retrying is safe
and is a plain repeat.

Two things this does *not* recover:

- **A failed turn is not retried.** An agent turn is expensive and not
  idempotent, so a failure is reported rather than repeated.
- **A stopped turn stays stopped.** Cancelling is a decision, not a fault, so
  nothing restarts it.

A restart of the `seizu` web process no longer ends a turn: the turn runs as a
Temporal workflow on `seizu-temporal-worker`, and the browser reattaches to it
when the page comes back.

Turn logs are deleted `CHAT_TURN_RETENTION_SECONDS` after the turn finishes, and
immediately when the session is deleted. Expired logs are collected at the end
of each turn, in small batches — there is no scheduler to run. They are not
conversation history: that lives in the checkpoint and is served by
`/api/v1/chat/history`.

Because each turn is a workflow, **interactive chat requires a reachable
Temporal server and a running `seizu-temporal-worker`**.

### Gunicorn worker timeout

Seizu's bundled Gunicorn configuration reads `API_REQUEST_TIMEOUT` for its
worker watchdog, matching the FastAPI request deadline (60 seconds by default).
Under `UvicornWorker` this is a heartbeat watchdog rather than a per-request
deadline: a healthy long-lived chat stream continues to notify Gunicorn and is
not cut short by this value.

The client can reconnect and replay the durable event log. If
you supply your own Gunicorn configuration, choose its watchdog for web-worker
health rather than for the maximum duration of a chat turn.

## Orchestration and run budgets

For multi-step requests, chat can route a turn through a `plan → dispatch → verify` orchestration instead of the single-agent path. A cheap router classifies each turn; simple turns take the direct path with no extra LLM call, while complex ones get a planner, scoped sub-agent workers (run in parallel when steps are independent), and a verify gate with bounded retry. This is on by default and controlled by the `CHAT_ORCHESTRATOR_*` settings below.

The plan is a **directed acyclic graph**: each step lists the steps whose output
it needs, and runs as soon as all of them have passed. The graph is validated as
it is produced — unique ids, no self-references, no references to steps that do
not exist, no cycles. An invalid graph is sent back to the planner once; if the
second attempt is also invalid the graph is repaired and the repair is reported
in the run's errors and beside the plan in the UI. A step that can never run is
recorded as failed, naming the dependency that stopped short.

Each step's share of the run budget is divided by the remaining dispatcher
passes rather than by the number of steps left, so a step that runs alone at a
bottleneck gets a whole pass's share.

**A step can fan out over what an earlier step found.** Work that has to be done
once per discovered thing — each CVE in a list, each repository in an
organization — is planned as a single step that maps over the step producing the
list. When that step finishes, it is replaced by one step per item, and those
run in parallel like any other independent steps. A step whose collection comes
back empty runs once, as written.

`CHAT_ORCHESTRATOR_MAX_EXPANSION` (default 8) bounds how many steps one such
step may become. A larger collection is cut to that many and the run reports the
coverage it did not have; `0` turns expansion off.

Every run — interactive or scheduled — is governed by a shared budget ledger tracking tokens, estimated USD cost (when LiteLLM knows the model price), and LLM call count. `CHAT_RUN_RESERVE_PERCENT` holds back part of the budget so final summaries and synthesis can produce an explicit partial result instead of stopping mid-plan; after the soft limit, eligible read-only work switches to `CHAT_LLM_ECONOMY_MODEL` when one is configured. Run outcomes distinguish `success`, `partial`, `budget_exhausted`, `blocked`, and `failure`.

**The call ceiling follows the plan.** `CHAT_RUN_MAX_LLM_CALLS` is an emergency
loop guard rather than a spend limit. It defaults to being derived from the
plan's size, including after a step expands; set a positive value to pin it.

**A run is budgeted in cost.** `CHAT_RUN_COST_BUDGET_USD` (default $2.00 per
run) is the limit to tune: it bounds the run, and a share of it bounds each plan
step. `CHAT_RUN_TOKEN_BUDGET` defaults to being **derived** — a priced model
needs no token ceiling, since cost already bounds the run, and a model LiteLLM
cannot price falls back to `CHAT_RUN_UNPRICED_TOKEN_BUDGET`. Set a positive
value to bound runs by tokens instead. Each step is bounded in whichever
dimension applies, and whichever binds first stops it.

**A step's share comes out of the budget the run is bounded by.** With a cost
budget set, each step gets a share of the *cost*; the token ceiling only bounds
concurrent steps against each other. A step that uses up its own share is
reported as a partial run rather than as the run running out of budget.

**Concurrency throttles itself rather than ending the run.** A call is
authorized against what the run has committed plus what is reserved by calls
that have not returned. When only the reserved part leaves no room, the call
waits for a reservation to settle — up to `CHAT_BUDGET_CONTENTION_WAIT_SECONDS`
— instead of failing; only committed spend ends a run. Reservations are sized
from what each kind of call has been observed to emit, so a phase's first call
uses `CHAT_BUDGET_OUTPUT_ESTIMATE_TOKENS` and later ones track reality.

### What a call may spend is derived from the model

Output ceilings are not configured per model. Seizu reads the model's own
`max_output_tokens` from litellm and caps it with
`CHAT_LLM_MAX_OUTPUT_TOKENS_CAP`, so a deployment does not maintain a limit per
model and never asks for more than a provider accepts.

`CHAT_LLM_MAX_TOKENS=0` and `CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS=0` mean
"derive". Set either to pin a value; a pinned value is still clamped to the
model's ceiling.

```{note}
On reasoning models the thinking and the answer share one output allowance, so a
ceiling set too low yields an empty response rather than a shorter one. Prefer
the derived value unless you have a reason to pin.
```

`CHAT_LLM_REASONING_EFFORT` bounds how much of that allowance a model may spend
thinking: `default`, `none`, `minimal`, `low`, `medium`, `high`, or `xhigh`;
empty also uses the provider's default. This is LiteLLM's fixed vocabulary.
Seizu renders it into each provider's native parameter —
`reasoning_effort` for OpenAI and Gemini, `thinking.budget_tokens` for Anthropic
(a share of the call's ceiling), `extra_body` for DeepSeek — and never sends it
to a model that does not support reasoning.

Per-stage overrides take precedence over the global value, and an empty stage
inherits it:

| Setting | Default | Stage |
|---------|---------|-------|
| `CHAT_LLM_ROUTER_REASONING_EFFORT` | `none` | routing classification, once per turn |
| `CHAT_LLM_PLANNER_REASONING_EFFORT` | inherit | plan decomposition |
| `CHAT_LLM_WORKER_REASONING_EFFORT` | inherit | plan step execution |
| `CHAT_LLM_WORKER_SUMMARY_REASONING_EFFORT` | inherit worker | a step's summary passes |
| `CHAT_LLM_VERIFIER_REASONING_EFFORT` | inherit | step verification |
| `CHAT_LLM_SYNTHESIZER_REASONING_EFFORT` | inherit | the final answer |

Effort levels are graded natively on OpenAI and Gemini. On DeepSeek and Anthropic
the practical distinction is `none` against any other value.

To measure the effect on your own provider before changing anything:

```bash
# plan shape, one LLM call
docker compose exec -T seizu uv run --frozen --no-sync \
    python -m scripts.plan_probe --repeat 3 "your request here"

# routing accuracy, output tokens and latency per effort level
docker compose exec -T seizu uv run --frozen --no-sync \
    python -m scripts.reasoning_sweep --stage router --efforts "" none low

# what each phase reserves against what it actually emits
docker compose exec -T seizu uv run --frozen --no-sync \
    python -m scripts.budget_probe
```

The worker, verifier and synthesizer need real step results to judge, so measure
those with `make chat_harness` arms rather than these probes.

### Independent steps run across the worker fleet

Within an orchestrated turn, a batch of plan steps with no dependency on one
another is scheduled as one Temporal activity per step. Steps run on any
`seizu-temporal-worker` replica, and each is bounded by
`CHAT_ORCHESTRATOR_DISTRIBUTED_STEP_TIMEOUT_SECONDS`.

A step that fails, or whose worker dies, is recorded as a failed step; the plan
continues and the answer is synthesized from the steps that completed.

Each batch appears in the Temporal UI as a `seizu-chat-fanout:` workflow named
after the turn, with one activity per step. Progress streams live: a distributed
step writes its step and tool details into the same turn event log, so the UI
shows the fan-out as it happens. The final answer has a single producer.

**Budget.** Each step is allocated a fixed slice of the run budget before the
batch starts, and cannot exceed it. Plan fewer, larger steps for work that needs
a bigger allowance.

**Concurrency is bounded twice.** `CHAT_ORCHESTRATOR_MAX_PARALLEL` bounds one
turn; `TEMPORAL_MAX_CONCURRENT_ACTIVITIES` bounds each worker process, and so the
cluster. Size the second for what your model provider, Neo4j, MCP proxies and
sandbox account can take at once — Temporal queues the overflow rather than
dropping it.

**Sandbox.** The turn opens the conversation's sandbox and distributed steps
attach to it, so parallel steps share one disk and files from earlier turns
remain available.

Scheduled chats and other headless runs are not distributed. Set
`CHAT_ORCHESTRATOR_DISTRIBUTED_ENABLED=false` to run every batch inside the
turn's own process.

### Fitting the model's context window

Context caps are **tokens**, counted with the provider's own tokenizer, and the
model's window is read from litellm's model database rather than configured.

The window is a **ceiling, not a target**. `CHAT_LLM_CONTEXT_MAX_TOKENS` remains
the "how much history is useful and affordable" knob and the window only clamps
it down, so pointing Seizu at a large-context model does not silently multiply
the cost of every call:

| model | window | history budget |
|---|---|---|
| `deepseek/deepseek-v4-pro` | 1,000,000 | 40,000 (configured cap) |
| `anthropic/claude-sonnet-4-5` | 200,000 | 40,000 (configured cap) |
| `deepseek/deepseek-chat` | 131,072 | 40,000 (configured cap) |
| unknown / self-hosted | 32,768 (assumed) | 16,384 (clamped by share) |

**The whole request is budgeted, not just history.** Before each call the
conversation is trimmed to `window − system prompt − tool schemas − reply −
safety margin`, covering every LLM call: the chat loop, orchestrator workers,
synthesis and continuations alike. `CHAT_LLM_CONTEXT_SAFETY_MARGIN` (5%) plus a
per-message framing allowance covers tokens we cannot see — providers frame each
message, and a tokenizer resolved by name can differ from the one the endpoint
runs. If a provider rejects a call anyway, the turn is retried once with a
halved conversation; a retry is skipped once text has streamed.

**Long conversations are compacted, not truncated.** When history no longer
fits, the oldest turns are condensed into a single block rather than dropped.
The block is deterministic (never a model call) and is rebuilt in chunks, so it
stays byte-identical for many turns at a stretch — which is what keeps a long
conversation cacheable.

The block is bounded by `CHAT_LLM_HISTORY_SUMMARY_MAX_TOKENS` and by a reserved
share of the history budget, so this is **not unlimited memory**: as it fills,
the oldest lines are shed. Set `CHAT_LLM_HISTORY_COMPACTION=false` to go back to
dropping the oldest turns.

```{note}
Why tokens rather than characters, why the retry halves what was *sent*, why the
block is deterministic and reserved, and the measurements behind each — see
[CTX-001 through CTX-003](../dev/decisions/chat-context.md).
```

### Prompt caching and cost

An agent loop re-sends a growing prefix on every call, and providers serve most
of it from their prompt cache at a fraction of the input price. The ledger reads
that accounting back out of the response (`input_token_details.cache_read` /
`cache_creation`) and prices each portion at its own rate.

Two things make Seizu's requests cacheable, and both are automatic:

- **Volatile content goes last.** Prompt caching matches the longest common
  *prefix*, so the session digest is carried as the final message rather than in
  the system prompt. This is the provider-agnostic half — automatic prefix
  caches (DeepSeek, OpenAI, Gemini) need nothing else.
- **Explicit breakpoints** for Anthropic, which caches nothing without them.
  Seizu marks up to three blocks with `cache_control`: the system prompt (tool
  schemas are ordered ahead of it, so one mark covers both), the message before
  the session digest, and the last message. Providers with automatic caching are
  left untouched. A system prompt below `CHAT_LLM_PROMPT_CACHE_MIN_TOKENS` is
  left unmarked. Set `CHAT_LLM_PROMPT_CACHE_ENABLED=false` to disable.

Two consequences worth knowing when reading the ledger:

- **Reservations use the uncached price**, because a cache hit is never
  guaranteed. Committed cost stays exact, so the ledger self-corrects the moment
  a call returns.
- **Tokens are counted whole.** `CHAT_RUN_TOKEN_BUDGET` counts a cached token
  like any other — it still occupies the context window. Only the price
  differs. `cache_read_tokens` appears in the run ledger and per phase.

```{note}
The measurements behind the ordering and the breakpoints, and why reservations
are not discounted by the observed hit rate, are
[CTX-004, CTX-005 and CTX-008](../dev/decisions/chat-context.md).
```

### Diagnosing a cache miss

`usage.cache_read_input_tokens` tells you the cache missed; it never tells you
*why*. Set `CHAT_LLM_CACHE_DIAGNOSTICS=true` and each LLM call is fingerprinted
— model, system prompt, tools, and each message, as hashes — and compared with
the previous call of the same kind. When the prefix moves, the log names the
component and estimates the tokens behind it:

```
cache diagnostic [user:…:thread:…:worker:s1]: tools_changed, ~4000 tokens behind the divergence
```

The answer is always one of `model_changed`, `system_changed`, `tools_changed`,
`messages_changed`, or `messages_truncated` (history rewritten rather than
appended to). Only the earliest divergence is reported; later ones hide behind
it. Fingerprints are hashes only — never prompt content — bounded in number, and
process-local.

**Leave it off in production:** it token-counts every component of every call.
Why this exists rather than Anthropic's own beta, and how comparisons are
scoped, is [CTX-007](../dev/decisions/chat-context.md).

### Disclosing what skills declare

A skill's `tools_required` is its author stating exactly which tools the
workflow uses, so those tools are disclosed from the start of a step rather than
when the skill renders — a tool list that grows mid-turn invalidates the cached
prefix behind it.

The disclosure is **scoped** to the skills a step names (`required_action` /
`suggested_tools`) rather than to every enabled skill, and **bounded** by
`CHAT_LLM_DISCLOSE_SKILL_TOOLS_MAX_TOKENS` of tool schema, above which tools are
disclosed on render as before. The bound is in schema tokens rather than tool
count, since that is what occupies the prefix. The single-agent path has no
signal for which skills a turn will use, so it always discloses on render.

Declarations ride on the skill listing the turn already makes, so this adds no
store read. Names of tools that no longer exist, or that the user cannot reach,
drop out — the live listing is the authority. Set
`CHAT_LLM_DISCLOSE_SKILL_TOOLS=false` to disclose only on render.

## Configuration

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_ENABLED` | `false` | Master switch: gates the chat routes, checkpoint storage, and the Chat UI. |
| `CHAT_LLM_PROVIDER` | `mock` | `mock` echoes input (no tools); any other value routes through LiteLLM. Legacy values (`openai`, `anthropic`, `gemini`, `deepseek`) namespace a bare model name. |
| `CHAT_LLM_MODEL` | `""` | Environment base model and fallback, preferably provider-namespaced (e.g. `anthropic/claude-sonnet-4-6`). Required for a real provider only when no enabled default model profile exists. |
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
| `CHAT_LLM_PROGRESSIVE_DISCLOSURE` | `true` | Show the model skills first and let rendered skills disclose which tools to use; `false` presents all chat-safe tools and skills up front. Disclosure decides what the model is *shown* — RBAC decides what it may call — so it carries across turns, the planner sees what earlier turns unlocked, and a tool a plan explicitly requires is disclosed rather than refused. |
| `CHAT_LLM_MAX_AUTO_ACTIONS` | `12` | Maximum tool/skill calls the agent executes in one assistant turn. |
| `CHAT_LLM_MAX_PARALLEL_TOOL_CALLS` | `4` | Maximum tool calls run concurrently in one batch. |
| `CHAT_LLM_MAX_CONTINUATIONS` | `2` | Auto-continuation attempts when a reply is cut off by the token limit; `0` disables (leaving the manual **Continue response** button). |
| `CHAT_LLM_MAX_RESPONSE_CHARS` | `60000` | Hard ceiling on a stitched auto-continued response; `0` disables. |
| `CHAT_LLM_CONTEXT_MAX_MESSAGES` | `80` | Maximum prior messages sent to the LLM (checkpoints may retain more for UI history). |
| `CHAT_LLM_CONTEXT_MAX_TOKENS` | `40000` | Maximum prior-conversation **tokens** sent to the LLM, counted with the provider's tokenizer. `0` means "whatever the window allows". See [Fitting the model's context window](#fitting-the-models-context-window). |
| `CHAT_LLM_CONTEXT_WINDOW_SHARE` | `0.5` | Share of the model's input window history may occupy; the rest is for the system prompt, tool schemas, this turn's tool results and the reply. The effective budget is the smaller of this and `CHAT_LLM_CONTEXT_MAX_TOKENS`. |
| `CHAT_LLM_CONTEXT_WINDOW_TOKENS` | `0` | Override the model's input window instead of reading it from litellm's model database. `0` derives it. |
| `CHAT_LLM_CONTEXT_WINDOW_FALLBACK_TOKENS` | `32768` | Window assumed for a model litellm cannot identify (typically self-hosted). Small on purpose. |
| `CHAT_LLM_CONTEXT_SAFETY_MARGIN` | `0.05` | Fraction of the window held back when sizing a call, covering provider message framing and tokenizer differences we cannot observe. |
| `CHAT_LLM_PROMPT_CACHE_ENABLED` | `true` | Emit explicit `cache_control` breakpoints for providers that need them (Anthropic). Providers with automatic prefix caching are unaffected. |
| `CHAT_LLM_PROMPT_CACHE_MIN_TOKENS` | `1024` | Shortest system prompt worth marking; below this the provider will not cache the prefix. |
| `CHAT_LLM_DISCLOSE_SKILL_TOOLS` | `true` | Disclose the tools declared by the skills a plan step names, from the start of the step, instead of only once the skill renders. |
| `CHAT_LLM_HISTORY_COMPACTION` | `true` | Condense the oldest turns of a long conversation instead of dropping them. |
| `CHAT_LLM_HISTORY_COMPACTION_TARGET` | `0.5` | How far back a compaction cuts, as a fraction of the space available to history. Lower compacts less often and keeps less; at 1.0 it would compact almost every turn. |
| `CHAT_LLM_HISTORY_SUMMARY_MAX_TOKENS` | `1500` | Ceiling on the condensed block, also capped at a quarter of the history budget. |
| `CHAT_LLM_CACHE_DIAGNOSTICS` | `false` | Log which request component changed since the previous call of the same kind. A debugging aid; see [Diagnosing a cache miss](#diagnosing-a-cache-miss). |
| `CHAT_LLM_DISCLOSE_SKILL_TOOLS_MAX_TOKENS` | `6000` | Skip that up-front disclosure when the declared tools' schemas exceed this, so a skill declaring a great many tools does not turn into binding them all on every call. |

### Orchestrator

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_ORCHESTRATOR_ENABLED` | `true` | Route complex turns through plan → dispatch → verify; when off, every turn takes the single-agent path. |
| `CHAT_ORCHESTRATOR_MAX_STEPS` | `8` | Maximum steps the planner may emit for one turn. Steps past it are dropped, along with any dependency pointing into them. |
| `CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS` | `4096` | Planner generation budget, kept separate so thinking models have room to emit the structured plan. |
| `CHAT_ORCHESTRATOR_MAX_ITERATIONS` | `3` | Verify-driven retry cycles before synthesizing an answer from the steps that passed. |
| `TELEMETRY_ENABLED` | `true` | Master switch; tracing still needs an endpoint to do anything. |
| `TELEMETRY_OTLP_ENDPOINT` | `""` | OTLP/HTTP traces endpoint. Empty disables tracing. |
| `TELEMETRY_OTLP_HEADERS` | `""` | Comma-separated `k=v` headers for the collector, e.g. an API key. |
| `TELEMETRY_SERVICE_NAME` | `seizu` | `service.name` on exported spans. |
| `TELEMETRY_RECORD_CONTENT` | `false` | Include prompts, results and tool output in spans. Off by default: it exports graph data. |
| `CHAT_ORCHESTRATOR_MAX_EXPANSION` | `8` | Maximum steps one step may expand into when it maps over items an earlier step discovered; `0` disables expansion. |
| `CHAT_ORCHESTRATOR_MAX_PARALLEL` | `8` | Independent steps dispatched concurrently in one batch. Matched to `CHAT_ORCHESTRATOR_MAX_EXPANSION`, so an expanded step's children run in one batch rather than several. |
| `CHAT_ORCHESTRATOR_WORKER_MAX_ACTIONS` | `24` | Per-step action-count guard, used only when all shared budget dimensions are disabled. |
| `CHAT_ORCHESTRATOR_DISTRIBUTED_ENABLED` | `true` | Schedule each independent step of a batch as its own Temporal activity. Interactive turns only. |
| `CHAT_ORCHESTRATOR_DISTRIBUTED_MIN_STEPS` | `2` | Batches smaller than this run inside the turn's own process. |
| `CHAT_ORCHESTRATOR_DISTRIBUTED_STEP_TIMEOUT_SECONDS` | `600` | How long one distributed step may run. Keep well under `CHAT_TURN_TIMEOUT_SECONDS`. |
| `CHAT_ORCHESTRATOR_DISTRIBUTED_INLINE_MAX_BYTES` | `262144` | Step results larger than this are stored and passed by reference instead of through Temporal history. |
| `TEMPORAL_MAX_CONCURRENT_ACTIVITIES` | `100` | Activity slots per worker process — the cluster-wide bound on concurrent distributed steps. |

### Run budgets

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_RUN_TOKEN_BUDGET` | `0` | Per-run token ceiling. `0` derives it: none at all when a cost budget is set and the model is priced, otherwise `CHAT_RUN_UNPRICED_TOKEN_BUDGET`. A positive value bounds runs by tokens instead. Includes sandbox sub-agent spend, typically 70-85% of a delegating turn. |
| `CHAT_RUN_UNPRICED_TOKEN_BUDGET` | `2000000` | The backstop used when a cost budget is set but LiteLLM cannot price the model, so cost can never accrue. |
| `CHAT_RUN_COST_BUDGET_USD` | `2.0` | Per-run estimated-cost budget in USD — the limit a run is meant to be tuned on, bounding both the run and each step's share of it; `0` disables this dimension (do that for a gateway or custom model whose pricing LiteLLM does not know). Cache-aware: input the provider served from its prompt cache is priced at the cache rate. See [Prompt caching and cost](#prompt-caching-and-cost). |
| `CHAT_RUN_RESERVE_PERCENT` | `20` | Portion of the budget reserved for final summaries and synthesis. |
| `CHAT_RUN_SOFT_LIMIT_PERCENT` | `75` | Threshold after which eligible work switches to the economy model. |
| `CHAT_RUN_MAX_LLM_CALLS` | `0` | Emergency ceiling on LLM calls per run. `0` derives it from the plan (`8 + CHAT_RUN_LLM_CALLS_PER_STEP` per step, recomputed as the plan grows); a positive value pins it. |
| `CHAT_RUN_LLM_CALLS_PER_STEP` | `24` | Calls one plan step may make, for that derivation. Set this and `CHAT_RUN_MAX_LLM_CALLS` both to `0` to disable the dimension. |
| `CHAT_BUDGET_OUTPUT_ESTIMATE_TOKENS` | `4096` | What a call is assumed to emit when its budget is reserved, before any call of its kind has returned. After that, the observed figure is used. |
| `CHAT_BUDGET_OUTPUT_ESTIMATE_SAFETY` | `1.5` | Headroom over the observed average when reserving. |
| `CHAT_BUDGET_CONTENTION_WAIT_SECONDS` | `30` | How long a call waits for in-flight reservations to settle when the budget has room but the room is spoken for; `0` fails fast instead. |
| `CHAT_EPISODIC_RECALL_MAX_CHARS` | `4000` | Results carried from earlier sandbox delegations into the next one's prompt, so each fresh sub-agent does not re-derive what the last one found; `0` disables. |
| `CHAT_EPISODIC_MAX_ENTRIES` | `20` | Delegation results retained before the oldest are shed. |
| `CHAT_SESSION_MEMORY_MAX_ENTRIES` | `30` | The same carry one scope out: sub-agent results kept across the *turns* of a conversation, so a follow-up turn does not re-run the previous turn's work. Stored in the thread's checkpoint. |
| `CHAT_SESSION_MEMORY_MAX_RECEIPTS` | `40` | Files earlier turns left in the (persistent) sandbox that later turns are told about, so they read the data instead of fetching it again. See [Sandbox delegation](sandbox.md). |
| `CHAT_SESSION_MEMORY_DIGEST_MAX_CHARS` | `2000` | Budget for that material in the *top-level* agent's prompt (planner, worker, single-agent loop), which is where a re-fetch would otherwise be planned; `0` disables the digest without disabling the carry. |
| `CHAT_ORCHESTRATOR_STEP_BUDGET_OVERRUN` | `12.0` | Floor on a step's token ceiling, as a multiple of the planner's per-step estimate. The ceiling is normally a share of what the run has left. |
| `CHAT_ORCHESTRATOR_STEP_SHARE_HARD_MULTIPLE` | `1.0` | How far past its fair share a step may go before being stopped rather than only degraded and asked to converge. `1.0` makes the share a hard cut. |
| `CHAT_LLM_PLANNER_MODEL` | `""` | Optional planner model override; empty inherits `CHAT_LLM_MODEL`. |
| `CHAT_LLM_ROUTER_MODEL` | `""` | Optional router model override; empty inherits `CHAT_LLM_PLANNER_MODEL`, then `CHAT_LLM_MODEL`. |
| `CHAT_LLM_WORKER_MODEL` | `""` | Optional worker model override. |
| `CHAT_LLM_WORKER_SUMMARY_MODEL` | `""` | Optional worker-summary model override; empty inherits `CHAT_LLM_WORKER_MODEL`, then `CHAT_LLM_MODEL`. |
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
| `CHAT_TURN_RETENTION_SECONDS` | `600` | How long a finished turn stays replayable — the window a client has to reconnect. Not conversation history. |
| `CHAT_TURN_STREAM_LATENCY_MS` | `200` | Target delay for flushing produced parts and polling their log. Polling backs off automatically while a turn is quiet. |
| `CHAT_TURN_TIMEOUT_SECONDS` | `900` | How long one turn may run before its workflow gives up. A turn that hits this is recorded as failed rather than left running. |
| `CHAT_SESSION_REAP_ENABLED` | `false` | Retire sessions nobody has come back to. **Deletes chat history.** |
| `CHAT_SESSION_REAP_IDLE_SECONDS` | `2592000` (30d) | How long a session may sit untouched before it is retired, measured from its last update. `0` disables reaping. |
| `CHAT_SESSION_REAP_INTERVAL_SECONDS` | `3600` | Time between sweeps. |

```{warning}
**Session retirement is off by default, and turning it on deletes chat
history** — transcripts included, along with the suspended sandbox each session
holds. Set `CHAT_SESSION_REAP_IDLE_SECONDS` to your retention policy *before*
enabling it: the first sweep collects everything already past the threshold, so
a default 30-day window applied to a year-old deployment retires a great deal at
once.

A session is never retired while it is in use: a sweep claims it first, and a
turn that starts in the same moment either wins the claim or is refused with
"This conversation has been retired". The sweep runs as a Temporal Schedule on
`seizu-temporal-worker`, so a deployment without that worker never reaps
whatever this is set to — see
[cleaning up idle conversations](sandbox.html#cleaning-up-idle-conversations)
for why the session and its sandbox are retired together.
```

Checkpoint storage (`CHAT_CHECKPOINT_*`) is documented in the [backend configuration](backend.html).

## Related features

- [Scheduled chats](chat-schedules.html) — run the agent headlessly on a recurring schedule.
- [Sandbox delegation](sandbox.html) — let the agent run code in an isolated ephemeral sandbox.
- [Built-in workflows](built-in-workflows.html) — durable workflows whose AI sessions run through the same headless chat machinery.
- [MCP toolsets](mcp-toolsets.html) and [Agent Plugins](agent-plugins.html) — the user-defined tools and skills the agent can use.
- [External MCP proxies](external-mcp.html) — connect the agent to proxied third-party MCP servers with per-user or M2M identity delegation.
