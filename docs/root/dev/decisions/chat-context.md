# Chat context and cost decisions (`CTX`)

Decisions about how much the chat agent sends to a model, what it costs, and why
the request is shaped the way it is. For the settings themselves, see the
[chat install docs](../../install/chat.md).

Primary code: `reporting/services/chat_context.py`,
`reporting/services/chat_budget.py`, `reporting/services/chat_graph.py`.

## CTX-001 — Context is budgeted in tokens against the model's own window

**Applies to:** `reporting/services/chat_context.py`

`context_window_tokens` reads `max_input_tokens` from litellm (cached per model);
unknown models fall back to `CHAT_LLM_CONTEXT_WINDOW_FALLBACK_TOKENS`.
`history_token_budget` = min(`CHAT_LLM_CONTEXT_MAX_TOKENS`, window ×
`CHAT_LLM_CONTEXT_WINDOW_SHARE`).

**Why:** the caps used to be characters at an assumed 4 chars/token. Real tool
payloads measure **3.0**, so those caps admitted a third more than intended.
Where something must truncate characters against a token budget, use
`chars_for_tokens`, which calibrates on the text in hand.

**The window is a ceiling, not a target.** A 1M-token model must not silently
multiply per-call cost, which is why `CHAT_LLM_CONTEXT_MAX_TOKENS` still applies.
The fallback is deliberately small: guessing low wastes a window, guessing high
fails the turn.

**Don't:** count tokens without the cache. `count_tokens` is content-hash cached
because a trim pass sizes every message on every call.

## CTX-002 — The whole request is fitted, and overflow is retried once against what was sent

**Applies to:** `_run_llm_tool_turn`, `_fit_messages_to_window` in `chat_graph.py`

`_fit_messages_to_window` subtracts system prompt, tool schemas, reply
allowance, `CHAT_LLM_CONTEXT_SAFETY_MARGIN` and per-message framing from the
window. It runs in `_run_llm_tool_turn` because that is the single scope where
the whole request exists, so every LLM call is covered.

A provider overflow (`chat_context.is_context_overflow`, matched by litellm type
or message **across the exception chain**) is caught once and retried with the
conversation halved **relative to what was sent**, not relative to our
allowance.

**Why:** an overflow *is* our allowance being wrong, so halving the allowance is
no help.

**Don't:** retry after text has streamed — it duplicates output. Don't retry
non-overflow errors at all.

## CTX-003 — Cross-turn history is compacted, not truncated, and deterministically

**Applies to:** `_compact_history`, `HistorySummary` in `chat_graph.py`

The oldest turns are condensed into one block, cut back past the budget in
chunks so the block stays byte-identical between compactions. Measured on 40
simulated turns: 5 compactions, request bounded 2,421–3,348 tokens against a
4,000 budget.

**Why deterministic:** never a summarizing model call. Its output would differ
per run and rewrite the prefix, invalidating the prompt cache for the whole
conversation (see CTX-004).

**Why chunked:** cutting back exactly to the budget re-compacts every turn.

**The block is fenced** with `fenced_within`. Compaction flattens assistant
turns into a *user* message, and an assistant turn carries whatever tool and
graph output it reported on — unfenced, that promotes provider-controlled text
into the instruction role, and *keeps* it there, because the block is
deliberately stable.

**The block gets a reserved share** of the budget (`_SUMMARY_BUDGET_SHARE`).
Without the reserve, a grown summary competed with the history it described and
every turn re-compacted.

**The boundary is a message id** (`covers_through_id`), not a count, because a
count shifts when filtering changes. An **empty id must never be looked up**: it
matches every message that carries none, which silently dropped the first
message of a fresh conversation.

**Still outstanding:** compaction is not append-only, which the original context
plan called for. Rewriting the prefix invalidates the prompt cache for the whole
conversation.

## CTX-004 — Volatile content goes last, never in the system prompt

**Applies to:** `chat_graph.session_memory_message`

Prefix caching matches the longest common prefix, so anything that changes per
turn must not sit at the front of the request.

**Why:** the session digest in the system prompt measured **0% cached against
98%** for an otherwise identical prefix. Carried as a trailing message it leaves
only the newest exchange uncached (93%/74% on a live two-turn conversation).

This is the provider-agnostic half — automatic prefix caches (DeepSeek, OpenAI,
Gemini) need nothing more.

**Don't:** add anything per-turn to a system prompt without checking this first.

## CTX-005 — Anthropic needs explicit cache breakpoints

**Applies to:** `chat_context.with_cache_breakpoints`

Anthropic caches nothing without them (measured: 0 cached over a five-call
turn). Up to three blocks are marked with `cache_control`, **for Anthropic
models only**: the system prompt (tool schemas are ordered ahead of it, so one
mark covers both), the message *before* the session digest (a prefix containing
the digest can never be read back), and the last message (within-turn rolling).

Live two-turn measurement: turn 1 writes ~11,000 and reads 0 (cold; writes cost
1.25x), turn 2 reads 16,467 (56%) and writes 1,801.

**Don't:** reshape non-Anthropic requests into content blocks — it risks a
provider transformer for no gain.

## CTX-006 — What the prefix contains matters more than the marks

**Applies to:** `chat_orchestrator._worker_system_prompt`, `_step_contract`,
`_step_declared_tool_names`

`_worker_system_prompt()` **takes no step argument and must stay that way.** It
used to embed the step goal, criteria and required action, so every step had a
different prefix head and none could read another's — measured: the second step
read 0 of 2,963 tokens. Step-specific text lives in `_worker_user_message` via
`_step_contract`, still fenced.

Tool-list churn invalidated the prefix the same way. Anthropic orders tools
*ahead* of the system prompt, so going from 3 to 11 tools made the next call
read 0. Fixed by honouring skills' `tools_required` **up front, scoped and
bounded**:

- `_step_declared_tool_names` takes only the skills *this step names*
  (`required_action` / `suggested_tools`). Unioning every enabled skill's
  declaration is the catalogue, not the need — it took a single-agent turn from
  1 bound tool (343 tokens) to 43 (4,666).
- `mcp_runtime.declared_tool_names(prompts, only=…)` reads declarations off the
  prompt `_meta` of the listing the turn already made — no second store read.
  Use `_meta`, not `meta`, or the alias shadows it.
- `chat_graph.skill_declared_tool_names` bounds the result by
  `CHAT_LLM_DISCLOSE_SKILL_TOOLS_MAX_TOKENS`, measured in schema tokens rather
  than tool count.

After the change a turn held one tool list across all four calls, each reading
the previous prefix. The single-agent path has no signal for which skills a turn
will use, so it still discloses on render.

## CTX-007 — Cache diagnostics are ours, because Anthropic's are unreachable

**Applies to:** `chat_context.diagnose_cache_divergence`, `CHAT_LLM_CACHE_DIAGNOSTICS`

Fingerprints model/system/tools/messages as hashes per call and logs the
earliest divergence with the tokens behind it (`tools_changed, ~4000 tokens…`).
Off by default.

**Why not Anthropic's beta:** unreachable through LiteLLM 1.87.0. It builds
`anthropic-beta` from feature detection and drops caller
`extra_headers`/`headers` (verified on the wire), so the `diagnostics` body
param arrives unauthorised and the API **rejects the call outright**. Do not
wire it up without fixing the header first.

**Scoping is load-bearing.** Keyed by thread+phase — **per delegation** for the
sandbox, whose delegations all open with the same system prompt — *and* by
lineage (the opening message). A plan reuses step ids across turns, so without
both, turn 2's `worker:s2` was diffed against turn 1's unrelated one and every
first call reported a divergence.

**Do:** reach for this instead of hand-adding hash logging. Both cache bugs in
this area were found with it.

## CTX-008 — Budgeting is cache-aware, but reservations are not discounted

**Applies to:** `reporting/services/chat_budget.py`

`usage_from_message` reads `input_token_details.cache_read`/`cache_creation` off
the response; `usage_cost_usd` forwards them to litellm, which treats
`prompt_tokens` as the **total** and subtracts the cached portions. Pass the
total, not the difference, and clamp the details to it — a provider over-report
otherwise negative-prices the call. Committed cost is exact.

**Reservations use the uncached price.** `project_cost_usd` does not apply the
observed hit rate.

**Why:** that ratio spans every model and phase, so a cache-heavy sandbox phase
discounted a cold planner call on another model (reproduced 6.6x
under-reserved). More fundamentally, a cache hit is never guaranteed, and a
ceiling that assumes one is not a ceiling. The ledger self-corrects on commit.

**Tokens are counted whole** — a cached token still occupies the context window.
Only the price differs. `cache_read_tokens` is in the ledger and per phase.

## CTX-009 — Measure with the harness, never single runs

**Applies to:** `scripts/chat_harness.py`

Answer quality on an *unchanged* configuration varies several-fold. Single-run
comparisons of this system have repeatedly shown "clean separations" that
vanished under more samples; four settings have been swept without a
distinguishable difference between any of them.

The harness runs the same conversation N times per arm and reports medians and
ranges. Tokens, cost and cache hit rate are summed across *every* turn, not read
off the last one.

**Traps:**

- A subagent detail is re-emitted after every inner tool call carrying the whole
  children list, so any stream-derived count must be de-duplicated on
  `detail_id` or it inflates quadratically in the size of a delegation.
- Do not edit code while a run is in flight — the dev server reloads and the run
  is contaminated.
- `summary_chars` is non-zero only when history compaction engaged, which needs
  a long run or a small `CHAT_LLM_CONTEXT_MAX_TOKENS`.
