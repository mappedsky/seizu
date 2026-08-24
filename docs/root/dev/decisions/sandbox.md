# Sandbox decisions (`SBX`)

Decisions behind `sandbox__delegate` and the sandbox session lifecycle. For how
to configure and operate the sandbox, see the
[sandbox install docs](../../install/sandbox.md).

Primary code: `reporting/services/mcp_builtins/sandbox.py`,
`reporting/services/sandbox_session.py`, `reporting/services/sandbox_backend.py`,
`reporting/services/episodic_memory.py`.

## SBX-001 — The backend is a five-operation protocol, not an E2B client

**Applies to:** `reporting/services/sandbox_backend.py`

`SandboxBackend` defines `run_python`, `run_bash`, `read_file`, `write_file`,
`list_files`, plus `sandbox_id`. `_E2BSandboxBackend` is the only
implementation today and is the only place that knows E2B response shapes.
`_open_backend` is the single swap point for another provider.

**Why:** fixed tool names and descriptions mean the inner agent behaves the same
across providers, and tests need no E2B package.

**Don't:** widen the protocol to expose provider features. A sixth operation is
a sixth thing every future backend must implement.

Account-wide operations that belong to no open sandbox — `kill_sandbox` and
`list_paused_sandboxes`, which the reaper ([SBX-011](#sbx-011)) runs on — live
in the same module for the same reason, and return the provider-agnostic
`SandboxSnapshot` rather than a provider response.

**Note:** `@runtime_checkable` `isinstance` reads members with `getattr_static`,
so a `MagicMock` standing in for a backend must have **every** member explicitly
assigned — an auto-created attribute does not satisfy the check.

## SBX-002 — Oversized tool results are routed to a file by size, never by the model's choice

**Applies to:** `_invoke` in `reporting/services/mcp_builtins/sandbox.py`

When a tool result exceeds the byte or row bound, it is written into the sandbox
filesystem and the agent receives a receipt (`status: too_large_to_return`,
path, row count, columns, sample) instead.

**Why:** an earlier version exposed `save_to_path` as an argument and let the
agent decide. It then used it for all 233 calls of a measured run — including
schema lookups it needed to read — read none of the files back, and re-queried
instead, at 4.4x the sandbox spend. Routing on size removes the decision: a file
appears only where the alternative was a truncated result, so reading it is
strictly better than what the agent would otherwise have had, and where a result
fits, nothing changes.

Triggering on bytes alone was also tried and was worse. With the fetch raised to
the file bounds, the row cap is the only thing keeping an inline result small in
row terms; dropping it let multi-thousand-row results return in full. A measured
turn went from 124 inner calls and a complete answer to 880 calls, 791 of them
queries, and a 581-character answer with both steps failing.

**Don't:** re-expose the path as a tool argument, and don't drop the row bound
from the oversize test.

## SBX-003 — The sub-agent is bound a narrow tool set, not the catalogue

**Applies to:** `_bound_tool_names` in `reporting/services/mcp_builtins/sandbox.py`

Bound = the read-only graph core (`SANDBOX_CORE_TOOLS`) + what the conversation
disclosed (`chat_graph.current_disclosed_tools()`) + what the delegating call
named in `tools`. All three are unions: **naming `tools` widens only**.

**Naming `tools` used to replace the disclosed set, and that was wrong.** A
sub-agent runs on the same context as the step that spawned it, so a tool that
step already unlocked — a skill's `tools_required`, most often — is not
something the sub-agent should have to rediscover. Replacing made the more
specific instruction the *less* capable one: a delegation under a skill that
declared the GitHub tools, which then named a tool of its own, lost the skill's
tools and spent the step's budget hunting for them through
`find_seizu_skills`/`load_seizu_skill`. The context-economy argument below is
unaffected — the catalogue is still not in scope, because "disclosed" is what
this conversation actually unlocked, not what exists.

**The core is configurable, and bypasses disclosure but not RBAC.**
`_core_tool_names()` reads `SANDBOX_CORE_TOOLS` per call and the result is
intersected with the caller's permitted tools, so a role without
`query:execute` gets none of it. It defaults non-empty because "fetch some
data" is what a sub-agent is *for*, and the harness measured the core covering
every delegation across four samples with no sample needing discovery — putting
the most ordinary operation behind a round trip inverts that. A deployment that
wants graph access gated can narrow the list or empty it, which routes even
Cypher through a skill or through the delegating model naming `tools`.

**Why:** the inner agent used to be handed every chat-safe tool — 58 in a
measured deployment, ~3,800 tokens of schema re-sent on *every* inner LLM call,
about a fifth of a delegation's spend, two thirds of it management CRUD a
data-processing sub-agent never uses. The bound set is now ~830–1,100 tokens. It
also leaked: a tool name the sub-agent saw reached the session memory the
planner reads, which then required a tool the worker had not disclosed, and the
step was refused.

Harness, 4 samples before/after (cross-build, bundling session memory and the
disclosure fixes): median sandbox tokens 181k → 48k with non-overlapping ranges,
budget exhaustion 4/4 → 0/4, failed steps 4/5 → 0/8, median answer 1,233 → 2,339
characters.

**Don't:** treat this as an authorization boundary — see [AGT-002](chat-agent.md).
RBAC (`chat_safe_only` + `exclude_confirmation_gated`) bounds what `_invoke` can
reach, and that is unchanged by any of the above.

**Don't:** conclude from a thrashing sub-agent that binding is broken without
checking that the tools *exist* for that user first. External MCP tools vanish
from discovery and are replaced by `ext__<proxy>__seizu_authenticate` when the
proxy 401s — an expired login looks exactly like a binding bug from the trace,
and a Temporal turn holds no browser token to renew it with ([AGT-010](chat-agent.md)).

## SBX-014 — The sub-agent trims what it re-sends

**Applies to:** the `pre_model_hook` on `create_react_agent` in `_delegate`

The delegation's inner loop bounds its input with the same
`_trim_inner_loop_messages` the single-agent path and the orchestrator's worker
use, sized by `chat_context.history_token_budget`.

**Why:** it was the only loop in the system without this. Every inner call
re-sent the whole accumulated exchange — system prompt, every tool call, every
result — so cost grew with the square of the work. Measured on a reachability
step: **20 calls, 1,500,173 tokens, ~75,000 per call**, with the loop detectors
silent because none of it was repetition. It was the same evidence paid for
again on every turn.

Returned as `llm_input_messages`, which bounds the model's input without
touching graph state, so the final answer is still extracted from the full
history.

**The same hook delivers the wrap-up signal.** `_budget_note` is composed once,
when the delegation starts, so a delegation that begins with room and then runs
for thirty calls was never told it had crossed its share -- it worked until it
was cut, which is exactly how an unreported result is lost. `_live_budget_note`
re-reads the scope before every inner call and rides last in the input, silent
until the soft limit is reached. Last, because it changes every call and
anything that changes invalidates the cached prefix after it -- the same rule
`chat_graph.session_memory_message` states for the session digest. The trimmer sheds the oldest exchanges into a deterministic digest
rather than deleting them, so a shed tool result is still represented rather
than inviting the sub-agent to fetch it again.

**Note the interaction with [SBX-013](#sbx-013):** the cumulative inline budget
reduces what *arrives* in the context; this reduces what is *re-sent*. Neither
substitutes for the other, and the second is the larger effect on a long
delegation.

## SBX-013 — A delegation's inline results are budgeted cumulatively

**Applies to:** `_invoke` in `reporting/services/mcp_builtins/sandbox.py`,
`SANDBOX_INLINE_RESULT_BUDGET_TOKENS`

Three triggers decide whether a tool result is returned inline or written to a
file, checked cheapest first: more rows than `CHAT_TOOL_RESULT_MAX_ROWS`, more
bytes than `SANDBOX_MAX_OUTPUT_BYTES`, or a **cumulative** budget —
`SANDBOX_INLINE_RESULT_BUDGET_TOKENS` (default 60,000) — spent by everything the
delegation has already returned inline. Past it, the rest goes to disk whatever
its individual size. Set 0 to keep only the per-call triggers.

**Why:** the per-call triggers are sized for one big row set, which is what a
Cypher query produces, and they fire perfectly there. They cannot catch the
shape that actually exhausts a step: a reachability review made **90 GitHub
calls of a few KB each — every one under the per-call trigger — put 1.1M tokens
through one sub-agent's context, and wrote exactly one receipt.** No per-call
threshold at a sane value catches 90 × 3KB; only a cumulative one does.

**In tokens, not bytes**, because what is being protected is a context window,
and the bytes-per-token ratio swings about twofold between prose and
punctuation-dense JSON — a lockfile being the second kind. `count_tokens` is a
local tokenizer with a content-hash cache; no model is threaded down to the call
site, so it uses the chars-per-token fallback, which is accurate enough for a
threshold. Bytes remain the outer safety cap: the guard has to hold before
anything would want to tokenize a 10MB payload.

**This was only safe once receipts described their contents** ([SBX-012](#)).
Lowering the trigger with a receipt that could return a bare path would have
traded a context problem for a blindness problem.

## SBX-012 — A call that cannot come out differently is not made twice

**Applies to:** `_invoke`, `_drop_unset_arguments`, `_unchanging_outcome`,
`_repeat_note`, `_file_result_receipt` in
`reporting/services/mcp_builtins/sandbox.py`

Two fixes to the same observed failure — a sub-agent re-issuing one call until
its step's budget was gone.

**The call was invalid, and ours.** The generated args model gives every
optional parameter a `None` default, so a parameter the sub-agent never supplied
reached the wire as an explicit null. An MCP server is entitled to reject that:
GitHub answers `parameter sort is not of type string, is <nil>`, and the call
then fails identically however often it is retried. `_drop_unset_arguments`
removes `None` values before dispatch — absent is what "not supplied" means on
the wire. Only `None`; `0`, `False` and `""` are supplied values.

**An identical repeat of a settled call is answered from what it returned.**
Narrowly: a call the server refused *on grounds that will not change*, or one
that authoritatively returned nothing. "Later" is not "never" — a first cut
treated every error as settled and suppressed GitHub's `429 try again in 6.9s`,
permanently losing two searches the sub-agent was right to repeat, so a
transient marker (429, 5xx, rate limit, timeout, unavailable, connection) sends
it back to the tool. A false match there only means the call runs again, which
is the behaviour that existed before the guard. Everything ambiguous runs again, because a sub-agent that repeats a
call is usually right to — polling, or retrying something transient. The reply
carries the original result *and* says the call was already made and why
repeating cannot help, so the sub-agent has something to act on rather than a
new error to retry.

**A receipt describes shape, not data.** For row-shaped results it carries the
key the rows live under, then one line per column — type(s) and a bounded
example (`"epss": "float|null = 0.0068"`) — rather than two whole sample rows.
The sub-agent reads a receipt to write code against the file, and for that it
needs names, types, and the *format* of a value: that a severity is `"high"` and
not `"HIGH"`, that a timestamp is `"2026-05-13T16:16:57.303000000"` and not an
epoch. Types are unioned across the sampled rows, so a sometimes-null column
says so instead of depending on which row was looked at first.

Two sample rows carried format too, but at a cost scaling with row width, and
described only the columns those two rows happened to contain: on a 15-column
vulnerability row most of the budget went to advisory prose and URLs. Measured
on that row, the profile is **31% smaller** and covers every column.
`rows_at` and the access path in `next_step` exist because the sub-agent was
observed writing `d.get("results") or d.get("rows") or []` — one wrong guess
away from processing nothing at all. Rows that are not objects keep a sample:
there are no columns to profile.

**Receipts preview documents, not only row-shaped JSON.** `_result_rows` finds a
list in a query result but not in a fetched file — that arrives as prose plus a
resource object — so an oversized document used to come back as nothing but a
path, and the only way to see what it held was to spend a `run_python`. The
receipt now falls back to a bounded head, using the same
`SANDBOX_PREVIEW_MAX_BYTES` budget `preview_file` uses, so it can never become a
way of pulling the file back into context.

**Why it matters beyond the tokens:** the retry loop is invisible in an answer.
The run that surfaced it reported a reachability step that "stopped early",
having spent 718k tokens, and nothing in that output said the same rejected call
had been made over and over.

## SBX-004 — Discovery follows `CHAT_LLM_PROGRESSIVE_DISCLOSURE`

**Applies to:** `_discovery_tools`, `_skill_discovery_tools` in `sandbox.py`

How the sub-agent reaches tools outside its bound set is decided by the same
setting that governs the planner:

| Setting | Discovery tools | `call_seizu_tool` may run |
|---|---|---|
| `false` | `find_seizu_tools`, `call_seizu_tool` | anything reachable |
| `true` (default) | `find_seizu_skills`, `load_seizu_skill`, `call_seizu_tool` | only what a loaded skill declared |

**Why:** free-text tool search reaches anything RBAC permits by string match,
which is the disclosure model the setting exists to switch off. An inner agent
that browses the catalogue while the outer one is kept to skills is two
disclosure models in one system, and the mismatch is how an undisclosed tool
name reached session memory (see SBX-003). A skill's `tools_required` is its
author naming the tools for a workflow, and the instructions arrive with them.

All routes share the same `_invoke`, so whatever is reached gets identical
result bounds, oversized-result handling and receipts.

**What narrowing costs differs by mode, and the two should not be conflated:**

- **Disclosure off** — a round trip, not a capability. Everything RBAC permits
  is still reachable through `find_seizu_tools`.
- **Disclosure on** — a round trip *and*, where skill coverage is incomplete, a
  capability. A tool no skill declares cannot be reached by the sub-agent on its
  own initiative. That is the deliberate trade, not an oversight.

On one measured deployment, skills reach 30 of the 58 tools a sub-agent could
otherwise browse. Of the 28 lost, roughly twenty are management CRUD (roles,
spaces, scheduled queries, workflows, toolsets) that a data sub-agent has no
reason to touch; the rest are answerable with `graph__query`. Where no skill
covers the need, the delegating model naming the tool in `tools` is the intended
route — the same limitation the planner has. So the loss is bounded by how the
deployment's skills are authored, and is recoverable by the caller, but it is a
real loss rather than a deferred cost.

**Do not overstate what this gates.** With the default `SANDBOX_CORE_TOOLS`
(SBX-003), raw Cypher is bound to every delegation, and anything the gated read
tools do `graph__query` can also do. So skill gating narrows *convenience and
curation*, not reach: a sub-agent denied `cve_analysis__count_cves_by_severity`
writes the equivalent Cypher instead. The "30 of 58" figure describes which
tools are conveniently reachable, not how much capability is withheld. A
deployment that wants graph access genuinely restricted must use RBAC
(`query:execute`) or empty `SANDBOX_CORE_TOOLS`; progressive disclosure alone
will not do it, and was never an authorization boundary
([AGT-002](chat-agent.md)).

**Don't:** add a free-text tool search back under progressive disclosure, and
don't bind discovery tools when no skills exist — two tools that can never find
anything are pure schema cost.

**Note:** a skill may declare no tools at all (1 of 10 on a measured
deployment). `load_seizu_skill` says so explicitly, because otherwise the
sub-agent discovers it one failed call at a time.

## SBX-005 — One sandbox per conversation, suspended between turns

**Applies to:** `reporting/services/sandbox_session.py`

A `SandboxSession` is made ambient by `chat_agent_node` (single-agent path) and
`dispatcher_node` (orchestrated path), **turn-level, not step-level**. It opens
lazily on the first delegation, then suspends (E2B `pause()`) and resumes next
turn via `resume_sandbox_id`, with the id carried in `ChatState.sandbox_id`.

**Why turn-level:** `asyncio.gather` copies the context but not the object, so
every step of a batch shares one sandbox while keeping its own `EpisodeLog`.
Per-step sessions meant parallel sub-agents could not see each other's files.

**Why suspend rather than destroy:** data fetched in one turn disappeared with
the sandbox, so the next turn re-fetched it.

**Suspension keeps the memory snapshot (`keep_memory=True`), and this is a
security trade accepted deliberately.** Untrusted processes from one turn
survive into the next, bounded to a single user's thread, still
network-isolated and still holding no credentials.

The filesystem-only alternative was tried first, for exactly the isolation that
sentence gives up, and **it does not work**: the code interpreter is itself a
process, so every resumed sandbox came back with port 49999 closed and
`run_python` failing 502 (`"The sandbox is running but port is not open"`) for
the rest of the turn. Reproduced in bare SDK code with no Seizu involved, and
five ways of restarting the service by hand — bare relaunch, explicit
port/ip, as root, `HOME=/root`, explicit `--config` — all failed the same way:
jupyter starts, but E2B's `/execute` extension never loads, so the port either
stays shut or answers 404.

This shipped broken. Because the failure is silent (the agent works around a
dead interpreter and still answers), it survived four instrumented harness runs
before being noticed, and it is the likely source of a large share of the
retries, flailing and token blowups seen in those runs.

**Planned way out:** persist result files to an object store and go back to cold
boots, which is faster and more reliable than resuming a VM. That requires an
S3-compatible bucket, so `keep_memory=True` stays as the fallback for
deployments that do not have one — which is why the trade is documented rather
than treated as temporary.

**Don't:** "tidy" this back to `keep_memory=False` for isolation. A test pins
the value and says why.

**Don't:** assume retention is solved by the session lifecycle.
`SANDBOX_SESSION_TIMEOUT_SECONDS` bounds a *running* sandbox, not a paused one,
and a memory snapshot costs more provider-side storage than a disk-only one. The
sandbox of a thread that is abandoned rather than deleted is reclaimed only when
the *thread* is retired by the sweep in [SBX-011](#sbx-011) — a scheduled pass
on a multi-day threshold, not a guarantee the lifecycle itself provides.

## SBX-016 — The conversation's sandbox is reachable without a sub-agent

**Applies to:** `sandbox.py::_direct_tools`, `_direct_backend`,
`_handle_write_file` / `_handle_read_file` / `_handle_list_files` /
`_handle_run_python`; `SANDBOX_DIRECT_TOOLS_ENABLED`

`sandbox__delegate` runs a whole agent loop inside one tool call. That is right
for exploration and wasteful for one operation: a measured turn spent **569
seconds across 43 sub-agent calls** at 13.2s each, where reading a file is one
round trip. The sandbox tools themselves ran at 739ms.

So the chat agent gets four of them directly — **prime** (`write_file`),
**inspect** (`read_file`, `list_files`) and **compute** (`run_python`). Shell
work stays delegation-only: it is the exploratory shape delegation exists for.

**They grant nothing `sandbox__delegate` does not already grant.** It can run
arbitrary code in the same sandbox, so these share its permission and its
reasoning about confirmation ([SBX-009](#sbx-009)): isolation is the control.
`chat_only` for the same reason it is on delegate — a sandbox belongs to a
conversation, so the tools have no meaning on the MCP endpoint.

**Writing records a receipt**, which is the point rather than a detail. A file
the outer agent primes is then advertised to every later delegation through the
existing manifest ([SBX-008](#sbx-008)), so data can be prepared for work that
has not been delegated yet, and a sub-agent written later still finds it.

**There is no private-sandbox fallback.** `_direct_backend` returns a reason
instead: a sandbox nobody holds the id of would take the file with it when the
call ended, which is the opposite of what these are for. A step that *attaches*
rather than owns cannot open one ([SBX-015](#sbx-015)), and says so.

**The cost is schema.** Four more always-disclosed tools sit in the outer agent's
context on every call; without `always_disclosed` a skill would have to unlock
them and the capability would not be there when the model wanted it.
`SANDBOX_DIRECT_TOOLS_ENABLED` turns them off without disabling delegation,
which `MCP_ENABLED_BUILTINS` cannot do at group granularity.

**Not done:** routing between the two. Which shape a task is remains the model's
call, as the `tools` narrowing on delegate already assumes.

## SBX-015 — A distributed plan step attaches to the conversation's sandbox; it never owns one

**Applies to:** `sandbox_session.attach_sandbox_session`,
`SandboxSession(attach=True)`, `open_backend(create_if_missing=, detach_on_exit=)`,
`chat_orchestrator._shared_sandbox_id`

[SBX-005](#sbx-005) gives a conversation exactly one sandbox and relies on
`asyncio.gather` copying the context but not the session object, so every step of
a batch shares one disk. Once a step runs as its own Temporal activity
([AGT-018](chat-agent.md#agt-018)) that no longer follows from anything: the step
is a different process, possibly on a different machine.

**One sandbox per conversation is kept, by splitting ownership from use.** The
coordinating turn still opens the sandbox, still suspends it, and is still the
only thing that writes `ChatState.sandbox_id`. A distributed step *attaches*: it
connects to the running sandbox by id, and on exit neither pauses nor kills it —
`detach_on_exit`. Parallel steps therefore go on sharing one disk exactly as they
do in-process, and a step's receipts keep naming a sandbox that is still there
(SBX-008).

**An attaching session must not create one** (`create_if_missing=False`). The
failure it prevents is not a crash: a step that quietly created its own sandbox
would give the conversation a second one that nobody holds the id for, so nothing
suspends it, nothing reaps it (SBX-011 finds sandboxes through the session that
owns them), and every file the step wrote is invisible to its siblings and to the
next turn. A step that cannot attach runs without a sandbox instead — the same
position every step is in when `SANDBOX_ENABLED` is false.

**So the coordinator opens it eagerly before a distributed batch**, where SBX-005
opens on first delegation. A worker cannot open the shared sandbox itself, and
deciding in advance which steps will delegate is not possible: `sandbox__delegate`
is always disclosed, so any step may. The cost is a sandbox opened for a batch
whose steps turn out never to delegate, bounded to multi-step orchestrated turns
— which are the shape that delegates. A failure to open is not a failure of the
batch.

**Don't:** give distributed steps a sandbox each and copy artifacts between them.
It was the obvious alternative and it is worse in three ways at once: N sandboxes
per turn instead of one, a step that cannot see what a sibling wrote, and the
conversation's accumulated files unreachable from the step that most needs them.

## SBX-010 — Result files live under `/home/user`, never `/tmp`

**Applies to:** `_RESULT_DIR` in `reporting/services/mcp_builtins/sandbox.py`

**Measured:** a file written to `/tmp` is gone after a pause/resume; the same
file under `/home/user` is intact.

`_RESULT_DIR` was `/tmp/seizu_results`, so every oversized-result file was
destroyed at the turn boundary while the session ledger went on advertising its
path to the next turn. The cross-turn half of [SBX-002](#sbx-002) and
[SBX-008](#sbx-008) — the entire reason receipts persist — could never have
worked, and the symptom in the logs is
`not found: lstat /tmp/seizu_results`.

**Don't:** put anything under `/tmp` that is meant to outlive a turn, and don't
rely on `keep_memory=True` to save it — the path has to be right on its own, or
the fallback-to-cold-boot plan above reintroduces the bug.

## SBX-006 — Only terminal resume failures create a replacement sandbox

**Applies to:** `open_backend`, `_terminal_resume_errors` in `sandbox_backend.py`

A failed resume creates a fresh sandbox **only** for terminal errors
(`SandboxNotFoundException`, `NotFoundException`). A timeout, rate limit or auth
failure propagates.

**Why:** replacing on a transient error leaves the old sandbox paused and alive
while its id is overwritten — paid for and unreachable. The differing
`sandbox_id` is how callers learn the old files are gone, so it must mean the
old sandbox is actually gone.

**Related:** `sandbox_id` is written to state **whenever a sandbox was opened**,
including the empty string. The state reducer overwrites rather than merges, so
omitting the key does not clear it: a killed sandbox stayed in the checkpoint,
later turns retried a dead resume, and — worse — the session digest advertised
receipts under an id that no longer existed. `SandboxTeardown(opened,
suspended_id)` exists to make that distinction expressible; a turn that opened
nothing must leave the stored id alone.

Testing note: the tests that existed while this bug was live asserted the close
call returned `None`, which was *already true*. The return value was right and
the state was wrong — assert on persisted checkpoint state, not on the return.

## SBX-007 — Error paths abandon the session; they do not destroy it

**Applies to:** `abandon_sandbox_session()` in `sandbox_session.py`

On a failed turn, a **resumed** sandbox is kept and a freshly **created** one is
destroyed.

**Why:** a resumed sandbox's id is already in the checkpoint from an earlier
successful turn, so failing changes nothing about finding it again — and
destroying it would cost a long conversation every turn's accumulated work over
one broken turn. A sandbox created by the turn that raised has its id nowhere,
so pausing it would strand it.

**Don't:** collapse this into a single "always pause" or "always kill" path.

## SBX-008 — Session memory carries receipts, scoped to their own sandbox

**Applies to:** `reporting/services/episodic_memory.py`

`EpisodeLog` is per-step; `SessionLedger` is the cross-turn carry, holding
episodes plus **receipts** — files an oversized result was written to, recorded
with the delegated task as their purpose. Receipts render **only for their own
`sandbox_id`**.

**Why:** without a record of the file, the next turn re-runs the query that
produced it, which is the cost SBX-002 exists to avoid. Scoping to the sandbox
id means a replacement sandbox silently stops advertising files that no longer
exist.

Sub-agents get `EpisodeLog.recall(sandbox_id=...)` built *after* the backend
resolves; the top-level agent gets the shorter `session_digest()`, because it is
the model that decides whether to delegate at all. Both are fenced as untrusted.

## SBX-009 — Isolation is the safety mechanism, so there is no confirmation gate

**Applies to:** the `sandbox__delegate` registration in `sandbox.py`

`chat_safe_without_confirmation=True` and `chat_only=True`.

**Why:** the sandbox is network-isolated from Seizu's data stores and holds no
credentials, so there is nothing for a user to approve. `chat_only` keeps it off
the MCP server endpoint entirely. Confirmation-gated mutating tools are excluded
from the sub-agent's reach: it runs to completion inside one outer tool call and
cannot drive the interactive confirmation round-trip, so those stay with the
outer agent where the user can approve them.

Persistence (SBX-005) widens the blast radius deliberately: untrusted code's
*output* lives for a conversation rather than a turn, bounded to one user's
thread, since the resume id lives in that thread's already user-namespaced
checkpoint.

**Don't:** grant the sub-agent a confirmation-gated tool on the grounds that the
runtime would catch it. It fails closed there as a backstop, not as the control.

## SBX-011 — The session is what gets reaped; the sandbox goes with it

**Applies to:** `reporting/services/session_reaper.py`,
`reporting/services/session_reaper_schedule.py`,
`reporting/temporal_workflows/session_reap.py`, the account-wide helpers in
`sandbox_backend.py`

**Measured, on one developer's account:** 59 suspended sandboxes, the oldest six
days old, every one of them a chat thread abandoned rather than deleted. The
leak is not theoretical and it does not plateau.

A chat thread's sandbox is destroyed when the thread is deleted (SBX-005), and
nothing deletes an abandoned thread. `SANDBOX_SESSION_TIMEOUT_SECONDS` bounds a
*running* sandbox, not a paused one, so nothing covered this case.

**The session is the unit of retirement, not the sandbox.** A sandbox belongs to
its thread for as long as the thread exists, so a sweep that reaped sandboxes on
their own age would leave live conversations whose accumulated files had
silently vanished — recoverable (a resume that fails terminally creates a fresh
sandbox, SBX-006) but a real loss the user never asked for and cannot see
coming. So the sweep retires *sessions* idle past
`CHAT_SESSION_REAP_IDLE_SECONDS`, and the sandbox goes with the session through
the same `delete_thread_state` path a user's own delete takes.

**This deletes chat history**, and that is the deliberate consequence of tying
the two lifetimes together: a resource whose owner may never come back cannot be
reclaimed without retiring the thing that owns it. Idle time is measured from
`updated_at` — last activity, not creation — so an active conversation is never
at risk however old it is.

**Which is why it ships off.** `CHAT_SESSION_REAP_ENABLED` defaults to false:
retention is a policy an operator chooses, and an upgrade that quietly began
deleting transcripts would be the worst possible way to learn this feature
exists. The cost is that the leak this fixes persists until someone turns it on,
which is the right trade — a deployment that never notices its paused sandboxes
loses money, while one that never notices its retention policy loses data. Note
the ordering hazard when enabling: the first sweep collects everything already
past the threshold, so the window has to be set before the switch.

**Idle time comes from Seizu's store, not from the provider.** `updated_at` on
the session record is authoritative and precise. An earlier design inferred
idleness from the provider's `started_at`/`end_at`, because sandbox metadata
cannot be amended after creation and there is nowhere to stamp a last-used time
— but a chat session already records exactly that, in a place that is ours.
Sandbox metadata is now used only for facts true for the sandbox's whole life:
who owns it, what it is for, and which thread it serves.

**Two passes, because the failure modes differ:**

| Pass | Input | Reaps |
|---|---|---|
| Idle sessions | the store, `updated_at < cutoff` | the session, its checkpoint, its sandbox |
| Orphan sandboxes | the provider's paused listing | sandboxes whose thread has no session |

The orphan pass is why a *provider* listing is involved at all: a deleted thread
whose kill failed, a run that died before its session record was written, a
database restored from a backup. Anything derived from Seizu's own records can
only find what Seizu still remembers, which is exactly the set that was never
the problem. An orphan must also be older than `SANDBOX_SESSION_TIMEOUT_SECONDS`
before it counts — a session record is written by a different call than the
sandbox it serves, so a sandbox seconds old can genuinely have no session yet.

**Ownership is per deployment, not per product.** `seizu_managed` carries
`SEIZU_DEPLOYMENT_ID`, and only an exact match is an ownership claim. "Some
Seizu created it" is not one: production and staging on a shared provider
account would otherwise reap each other. The listing is filtered by that tag
**provider-side**, which also stops a busy shared account from spending the
page cap on sandboxes that were never reapable. Deployments that leave the id
unset share one `default` bucket — set it whenever the credentials are shared.

**Singleton by Temporal Schedule, not by process.** Worker replicas are
ordinary, and a timer inside each of them would list the whole account N times
and race over every deletion. A Schedule with a fixed id and
`ScheduleOverlapPolicy.SKIP` gives one sweep at a time across every replica, and
a sweep that outruns its interval is skipped rather than stacked. Every replica
reconciles the same schedule at startup, which is idempotent. The cost is that a
deployment running no Temporal worker does not reap;
`SANDBOX_SESSION_PERSIST=false` is the alternative there.

**A returning user wins, decided by one conditional write.** A listing is a
snapshot, and re-reading before an unconditional delete only narrows the window
— the owner can pass the re-read, start streaming, and have the sweep delete the
session out from under the turn. So retirement pivots on a **claim**:
`claim_chat_session_for_retirement` sets `retiring_at` conditioned on the
`updated_at` the sweep listed. The stream route awaits its own activity write
before any graph work, and that write is conditioned on `attribute_not_exists`
/`IS NULL` for `retiring_at`. Exactly one of the two commits:

- user first → the claim's condition fails → the session is kept;
- claim first → the turn's write fails → the route refuses with *"This
  conversation has been retired"*.

The condition lives in the **write**, not in a read above it, because a claim
does not move `updated_at` and would otherwise slip between a read and its
write. The activity write is awaited rather than fired into a background task
for the same reason: a task that has not run yet protects nothing.

Sandboxes get the weaker treatment they can afford: a paused-state re-check
immediately before the kill, which narrows but does not close. That asymmetry is
deliberate — losing a sandbox costs a turn's cached files, losing a session
costs the conversation.

**Retirement deletes the session record last.** It is the only thing that makes
a thread findable, so deleting it first turns a failed checkpoint deletion into
a transcript stored forever with nothing left to retry from — worst on
PostgreSQL checkpoints, which have no TTL to catch it later. Last, it is its own
tombstone: a pass that dies part-way leaves a claimed, still-idle session that
the next sweep re-claims and finishes, and every step is idempotent. A claim is
therefore conditioned only on `updated_at`, never on being unclaimed, so it can
be re-taken. The interactive delete route keeps the opposite order for the
opposite reason: it should vanish from the UI at once, and a user watching it
can retry.

**Finding idle sessions is a bounded indexed query.** PostgreSQL has a composite
`(origin, updated_at)` index, added by migration `0006` and declared on the
model so fresh databases get it from `create_all`. Each pass selects at most
`_MAX_SESSIONS_PER_PASS` interactive sessions older than the cutoff, oldest
first. Bounded work prevents one sweep monopolizing the worker; anything beyond
the limit remains the oldest population and is picked up by the next
non-overlapping pass.

**The sweep is gated on its own settings only.** Not on `CHAT_ENABLED`, not on
`SANDBOX_ENABLED`: a deployment that turns either off still holds everything it
created while they were on. That also removes a failure mode that shipped once —
`CHAT_ENABLED` was not in the Temporal worker's compose environment, so the
worker read chat as disabled and deleted its own schedule.

**Checkpoint retention follows session retirement.** Superseded per-turn
checkpoints accumulate until a thread is retired, and that is accepted. The
saver exposes no retention knob, retirement is opt-in, and a periodically
active thread may never become idle enough to retire. Each checkpoint is still
bounded by `CHAT_MAX_PERSISTED_MESSAGES`, and the whole thread is reclaimed with
its session. If this needs paying down, use a pruning pass over *superseded*
checkpoints that preserves the latest; never expire the current checkpoint
independently of the session, which would leave a listed conversation opening
without its history.

**Don't:** reap a sandbox whose session is still alive, however old the sandbox
is. **Don't:** reap untagged or foreign-tagged sandboxes by default. **Don't:**
read `CHAT_SESSION_REAP_IDLE_SECONDS=0` as "retire immediately" — it means off,
because immediately would mean every session there is. **Don't:** move the sweep
back into a worker-local loop; the Schedule is the singleton mechanism.
**Don't:** delete the session record before its checkpoint, or let a schedule
failure propagate out of worker startup — both were review findings, and both
trade a housekeeping sweep for something much larger.

## SBX-017 — Plugin code runs only from an immutable sandbox materialization

**Applies to:** `materialize_plugin_skill`, `sandbox__run_script`

A selected plugin skill is materialized beneath `/home/user/seizu_plugins` at a
path containing its revision and package digest. The digest marker is written
last. Scripts execute there through an argv-array subprocess launched inside the
sandbox; the Seizu web and Temporal worker processes never execute package code.

**Why:** package scripts are untrusted code. Reusing the conversation sandbox
keeps their files available to the rest of the skill while preserving SBX-009's
isolation boundary. Revision-addressed paths prevent a draft or later publish
from changing the code midway through a turn.

## SBX-018 — Rendering a skill never provisions a sandbox

**Applies to:** `mcp_runtime._get_prompt_core`, `materialize_plugin_skill`

Rendering an Agent Skill substitutes arguments into its template. It attaches the
package's files to the answer when two things hold: the skill ships `scripts/`,
and the caller holds `sandbox:delegate`. It then materializes them, opening the
conversation's sandbox if that is what it takes.

**Why:** `chat_agent_node` makes a sandbox session ambient for every turn, and the
session opens its sandbox lazily on first use — so materializing during a render
made *rendering* the trigger. A user with `chat:skills:call` and no
`sandbox:delegate` could provision a billable sandbox by loading a skill, which
inverts the permission and pays for a VM to hold a template nobody will run.

**A third condition was tried and reverted.** Requiring the sandbox to be *already
open* (`only_if_open`) looked like the conservative choice and broke the feature:
a turn opens its sandbox on first use, which happens *after* the skill renders,
so `verify_packaged_assets` was handed instructions addressing a materialized
path that was never written. It read nothing, never reached
`sandbox__run_script`, and reported itself unverified — caught by a live run, not
by tests. The two conditions above are the whole guard, and they already cover
what this decision is for: a skill with nothing to run, or a caller who could not
run it, still never provisions anything.

**Don't:** materialize for a skill without scripts, or for a caller lacking
`sandbox:delegate`. Those are the cases where a render would be paying for a VM
to hold a template nobody can use.
