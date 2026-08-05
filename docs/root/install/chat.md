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
- New write/delete tools are **hidden from chat by default** (fail closed) until they are explicitly given a confirmation flow. The only no-confirmation mutating exception is `reports__create`, which creates a new private report and cannot modify existing resources — and it still asks for confirmation in the one case where the new report is public (filing it into a space). `reports__clone` asks every time, since whether the copy is public depends on where the source is filed.

Users holding `chat:bypass_permissions` see a **Bypass confirmations** toggle (off by default) that lets the agent execute confirmation-gated actions without pausing. Every bypassed execution is audit-logged, and the user's normal RBAC permissions still apply. The same permission controls whether headless runs (scheduled chats, Temporal workflows) may bypass confirmations — without it, mutating tools fail closed for the run.

## Sessions and history

Conversations are grouped into sessions listed in the chat sidebar; sessions can be created, renamed, and deleted. The active thread id is kept in browser `localStorage`, and reloading the page rehydrates the conversation from the server. Thread ids are namespaced server-side per user, so one user can never reach another user's thread.

Assistant turns include an expandable details section showing thinking and tool calls (arguments and output). Replies cut off by the output-token limit are auto-continued server-side and stitched into one response (bounded by `CHAT_LLM_MAX_CONTINUATIONS`); a manual **Continue response** action covers the rest.

Sessions created by scheduled chats are excluded from the sidebar and are read-only; see [scheduled chats](chat-schedules.html).

## Orchestration and run budgets

For multi-step requests, chat can route a turn through a plan → dispatch → verify orchestration instead of the single-agent path. A cheap router classifies each turn; simple turns take the direct path with no extra LLM call, while complex ones get a planner, scoped sub-agent workers (run in parallel when steps are independent), and a verify gate with bounded retry. This is on by default and controlled by the `CHAT_ORCHESTRATOR_*` settings below.

Every run — interactive or scheduled — is governed by a shared budget ledger tracking tokens, estimated USD cost (when LiteLLM knows the model price), and LLM call count. `CHAT_RUN_RESERVE_PERCENT` holds back part of the budget so final summaries and synthesis can produce an explicit partial result instead of stopping mid-plan; after the soft limit, eligible read-only work switches to `CHAT_LLM_ECONOMY_MODEL` when one is configured. Run outcomes distinguish `success`, `partial`, `budget_exhausted`, `blocked`, and `failure`.

### Fitting the model's context window

Context caps are **tokens**, counted with the provider's own tokenizer, and the
model's window is read from litellm's model database rather than configured.

Both halves of that mattered. Measured against real tool payloads the conversion
is 3.0 characters per token, not the 4 the code assumed — structured graph data
tokenizes worse than prose — so the old 120,000-*character* history cap admitted
about a third more tokens than intended, and worst exactly when payloads were
largest. And one fixed cap applied to every model is simultaneously unsafe and
wasteful: ~40,000 tokens of history overflows a 32k model's entire window on
history alone, and is 4% of a million-token model's.

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

**The whole request is budgeted, not just history.** Immediately before each
call, the conversation is trimmed to `window − system prompt − tool schemas −
reply − safety margin`. That check lives at the single point where the whole
request exists in one scope, so it covers every LLM call — the chat loop,
orchestrator workers, synthesis and continuations alike. What is shed is
condensed into a digest rather than dropped. `CHAT_LLM_CONTEXT_SAFETY_MARGIN`
(5%) plus a per-message framing allowance covers what we cannot see: providers
frame each message with tokens we never count, and a tokenizer resolved by name
can differ from the one the endpoint runs. Both make our count an
under-estimate, and under-counting is the direction that fails a call.

**An overflow is recovered, not fatal.** If a provider rejects a call for
exceeding its window anyway, the turn is retried once with the conversation
halved *relative to what was actually sent* — an overflow means our estimate of
the window was wrong, so halving our own allowance could leave the request
exactly as oversized. The retry is skipped once any text has streamed, since it
would duplicate what the user already saw, and errors that a smaller context
cannot fix (rate limits, bad keys) are never mistaken for overflow.

One limit remains outside this, deliberately: **cross-turn history is truncated,
not compacted.** The oldest turns are dropped rather than summarized. The
within-turn loop *does* condense what it sheds into a deterministic digest.
Extending that across turns has to stay append-only, because rewriting the
prefix invalidates the provider's prompt cache for the whole conversation — see
below.

### Prompt caching and cost

An agent loop re-sends a growing prefix on every call, and providers serve most
of it from their prompt cache at a fraction of the input price. The ledger reads
that accounting back out of the response (`input_token_details.cache_read` /
`cache_creation`) and prices each portion at its own rate.

This is worth a lot. A measured DeepSeek call re-sending a 4,016-token prefix
reported 3,968 of those tokens as cache reads: priced as fresh input it was
charged at $0.001794, and its real cost was $0.000083 — **21.7× overstated**. An
uncached call prices identically either way.

**Volatile content goes last.** Prompt caching matches the longest common
*prefix*, so anything that changes between turns invalidates everything after
it. The session digest grows every turn, and it used to sit in the system
prompt — the very first thing sent. Measured against an otherwise identical
request, that was the difference between **98% of input served from cache and
0%**. It is now carried as the last message instead, so the only uncached part
of a follow-up turn is the newest exchange and the digest itself. A live
two-turn conversation with session memory reports 93% and 74% cached.

This ordering is the **provider-agnostic** half of caching, and it has to come
first: automatic prefix caches (DeepSeek, OpenAI, Gemini) need nothing else, and
an explicit-breakpoint cache cannot benefit from a breakpoint unless the prefix
ahead of it is stable to begin with.

**Explicit breakpoints** cover the other half. Anthropic caches nothing at all
without them — measured at 0 cached tokens across a five-call turn — so when the
model is an Anthropic one, Seizu marks up to three blocks with
`cache_control`, in order of what they are worth:

1. **The system prompt** — the largest stable block, and Anthropic orders tool
   schemas ahead of it, so this one mark covers both.
2. **The message before the session digest** — the digest differs every turn, so
   a prefix containing it can never be read back.
3. **The last message** — within a turn the tool loop calls repeatedly with a
   growing list, so each call reads the previous prefix and writes only its
   delta.

Providers with automatic caching are left untouched: reshaping their messages
into content blocks would risk a provider transformer for no gain. A system
prompt below `CHAT_LLM_PROMPT_CACHE_MIN_TOKENS` is left unmarked, since the
provider will not cache a prefix that short. Set
`CHAT_LLM_PROMPT_CACHE_ENABLED=false` to disable.

Measured on a live two-turn Anthropic conversation: turn 1 writes ~11,000 tokens
to cache and reads none (cold, and cache writes carry a 1.25× premium); turn 2
reads 16,467 — 56% of its input — and writes 1,801.

**What the prefix is made of matters more than the breakpoints.** Instrumenting
a real turn showed two separate causes of lost hits, only one of which was worth
fixing:

- *A per-step system prompt.* The orchestrator's worker prompt embedded the
  step's goal, success criteria and required action, so every step had a
  different system prompt — the head of the cached prefix — and no step could
  read another's. Two steps of one turn: the second read 0 of its 2,963 input
  tokens against an almost identical prefix the first had just written. The
  worker system prompt is now identical for every step of every turn, with
  everything step-specific in the user message.
- *A tool list that grows mid-turn.* Anthropic orders tool schemas **ahead** of
  the system prompt, so when a rendered skill unlocks tools (3 → 11 in a
  measured turn) the prefix behind them is invalidated: the next call read 0,
  and the one after — same tool list — read 4,853. This is inherent to
  progressive disclosure and is left alone deliberately. Binding every tool up
  front would cache better and cost far more per call, since the full chat tool
  surface is an order of magnitude larger than what a turn actually unlocks.
  The cost is bounded by how many times disclosure fires in a turn.

Tool ordering is deterministic (the listing order, filtered), so the churn is
genuine growth rather than instability.

Two more consequences worth knowing:

- **Reservations are projected, not assumed.** A reservation decides whether a
  call is *allowed*, so overpricing it does not merely misreport — it refuses
  work that would have fit. Each is priced using the cache hit rate the run has
  actually observed so far. The first call of a run has no history and is a
  cache miss by definition, so it is projected at the full rate.
- **Tokens are still counted whole.** `CHAT_RUN_TOKEN_BUDGET` counts a cached
  token like any other: it still occupies the context window and still costs
  something. Only the price differs. `cache_read_tokens` appears in the run
  ledger (and per phase) so the saving is visible.

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
| `CHAT_RUN_TOKEN_BUDGET` | `400000` | Per-run token budget; `0` disables this dimension. Includes sandbox sub-agent spend, which is typically 70-85% of a delegating turn — lower it if the sandbox is disabled or rarely used. |
| `CHAT_RUN_COST_BUDGET_USD` | `0` | Per-run estimated-cost budget in USD; `0` disables this dimension. Cache-aware: input the provider served from its prompt cache is priced at the cache rate, and a reservation is projected using the hit rate the run has actually observed. See [Prompt caching and cost](#prompt-caching-and-cost). |
| `CHAT_RUN_RESERVE_PERCENT` | `20` | Portion of the budget reserved for final summaries and synthesis. |
| `CHAT_RUN_SOFT_LIMIT_PERCENT` | `75` | Threshold after which eligible work switches to the economy model. |
| `CHAT_RUN_MAX_LLM_CALLS` | `64` | Emergency ceiling on LLM calls per run. |
| `CHAT_EPISODIC_RECALL_MAX_CHARS` | `4000` | Results carried from earlier sandbox delegations into the next one's prompt, so each fresh sub-agent does not re-derive what the last one found; `0` disables. |
| `CHAT_EPISODIC_MAX_ENTRIES` | `20` | Delegation results retained before the oldest are shed. |
| `CHAT_SESSION_MEMORY_MAX_ENTRIES` | `30` | The same carry one scope out: sub-agent results kept across the *turns* of a conversation, so a follow-up turn does not re-run the previous turn's work. Stored in the thread's checkpoint. |
| `CHAT_SESSION_MEMORY_MAX_RECEIPTS` | `40` | Files earlier turns left in the (persistent) sandbox that later turns are told about, so they read the data instead of fetching it again. See [Sandbox delegation](sandbox.md). |
| `CHAT_SESSION_MEMORY_DIGEST_MAX_CHARS` | `2000` | Budget for that material in the *top-level* agent's prompt (planner, worker, single-agent loop), which is where a re-fetch would otherwise be planned; `0` disables the digest without disabling the carry. |
| `CHAT_ORCHESTRATOR_STEP_BUDGET_OVERRUN` | `12.0` | Floor on a step's token ceiling, as a multiple of the planner's per-step estimate. The ceiling is normally a share of what the run has left. |
| `CHAT_ORCHESTRATOR_STEP_SHARE_HARD_MULTIPLE` | `1.0` | How far past its fair share a step may go before being stopped rather than only degraded and asked to converge. `1.0` makes the share a hard cut. |
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
