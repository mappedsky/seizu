# Chat agent decisions (`AGT`)

Decisions about what the chat agent may do, what it is shown, and how a turn is
driven. Cost and context shaping live in [chat context](chat-context.md);
sandbox delegation in [sandbox](sandbox.md).

Primary code: `reporting/services/chat_graph.py`,
`reporting/services/chat_orchestrator.py`, `reporting/services/mcp_runtime.py`,
`reporting/services/headless_chat.py`, `reporting/services/agent_run.py`.

## AGT-001 — Chat tools fail closed

**Applies to:** `mcp_runtime.list_tools_for_user(chat_safe_only=True)`

Chat sees only built-ins whose `required_permissions` are all in
`_CHAT_SAFE_PERMISSIONS` (read/inspection only), plus mutating built-ins that
carry an action-confirmation resolver.

**Why:** a newly added write or delete tool is hidden from chat by default
rather than exposed by default. The failure mode of the alternative is silent.

**The exceptions are documented at the tool registration**, and there are only
two:

- `reports__create` — creates a new *private* report and modifies nothing
  existing. It still carries a **conditional** resolver for the one case where
  it publishes (filing into a space).
- `sandbox__delegate` — isolation is the control; see
  [SBX-009](sandbox.md).

`reports__clone` is gated **unconditionally**, because whether it publishes
depends on the source's placement, and reading that is the resolver's job rather
than the handler's.

**Don't:** add a third exception without recording it here and at the
registration.

## AGT-002 — Progressive disclosure is a context economy, not an authorization boundary

**Applies to:** `CHAT_LLM_PROGRESSIVE_DISCLOSURE`, `chat_orchestrator._required_action_spec`

RBAC is the boundary: `chat_safe_only` + `chat:tools:call` bound
`_list_chat_tools` and everything a sandbox sub-agent could ever reach. Nothing
below widens that set.

What a model is *shown* is narrower, and under progressive disclosure so is what
the sub-agent may actually call without loading a skill — see
[SBX-004](sandbox.md). That narrowing is context economy and skill curation, not
authorization: it decides what is convenient to reach, never what is permitted.

Because disclosure only decides what a model is *shown*:

- `ChatState.disclosed_tools` carries across turns.
- `planner_node`'s capability context includes what earlier turns unlocked.
- A tool a plan names in `required_action` is **disclosed rather than refused**.
  `_required_action_spec` resolves against the full permitted universe; only a
  tool that does not exist *for that user* is a contract error.

**Why:** refusing was a real cost for no security gain. The planner reads
session memory, where a tool an earlier turn used is recorded by name —
including tools a sandbox sub-agent called. Observed as steps blocked on
`Required tool action cve_analysis__get_recent_cves is not available` for a tool
the previous turn had just used successfully.

Only the explicitly required tool is disclosed this way; `suggested_tools` still
come from the disclosed pool.

## AGT-003 — Message tags exclude messages from context without deleting them

**Applies to:** `reporting/services/chat_messages.py`

`MessageTag` values live in `additional_kwargs["seizu_tags"]` (they round-trip
through the checkpoint serializer). `load_thread_messages` drops `EPHEMERAL` at
the read boundary; broken assistant outputs are tagged `BROKEN` and filtered
from future model context.

**Why:** a failed turn should not poison retries, but the message still has to
exist for history and debugging. This is the reusable mechanism — reach for a
tag rather than a bespoke filter.

## AGT-004 — Thread ids are namespaced server-side

**Applies to:** `chat_graph.namespaced_thread_id`

`user:{user_id}:thread:{client_thread_id}`.

**Why:** clients supply the thread id, so without server-side namespacing one
user can reach another's thread. Everything downstream — checkpoints, the
sandbox resume id, session memory — inherits this isolation, which is what makes
those safe to persist.

## AGT-005 — Report configs are validated at save time

**Applies to:** `CreateVersionRequest.validate_report_config`

Configs are validated against the `Report` schema when saved, and markdown
panels must carry content in `markdown`.

**Why:** without it an agent stores a config that renders empty and gets no
signal. An actionable error at save time is the difference between a retry and a
silently broken report.

## AGT-006 — Headless runs execute as the schedule's creator, with permission-based bypass

**Applies to:** `reporting/authnz/headless.py`, `reporting/services/agent_run.py`

Identity: `resolve_stored_user()` rebuilds the creator's permissions from the
last role claim seen on an authenticated request (`User.role`, synced by
`get_or_create_user`). Archived users hard-stop.

Confirmation bypass is gated by `chat:bypass_permissions` (Editor+).
`call_tool_for_chat(bypass_confirmations=True)` re-checks the permission and
AUDIT-logs every bypassed execution. Without it, headless runs keep the normal
confirmation flow, so mutating tools fail closed for the run. Creator RBAC and
`chat_safe_only` always apply.

The same permission gates the chat UI's "Bypass confirmations" toggle
(`ChatStreamRequest.bypass_confirmations`, 403 without it, default off).

Headless turns get a system-prompt addendum (`_HEADLESS_PROMPT_ADDENDUM`)
telling the model nobody can answer: don't ask for confirmation, follow the
skills, summarize blocks instead of retrying.

**Don't:** weaken any of this without explicit sign-off. A headless run is an
unattended agent holding a real user's permissions.

## AGT-007 — Scheduled chats run on Temporal, and their sessions are not chat sessions

**Applies to:** `reporting/services/chat_schedules.py`,
`reporting/temporal_workflows/scheduled_chat.py`

Execution is Temporal-only; the polling worker is gone. Run sessions are created
with `origin="scheduled"` + `scheduled_chat_id`, excluded from
`list_chat_sessions`, listed via `list_scheduled_chat_sessions`, and
`POST /api/v1/chat/stream` **rejects them** (403).

**Why the rejection:** a scheduled run's transcript is a record of what an
unattended agent did. Allowing a user to continue that thread interactively
would blur which turns were unattended.

Schedules are per-user (`created_by` owner; list/get/update/delete are
owner-scoped, 404 otherwise). `chat:schedule:read_all` (Admin) unlocks *viewing*
every user's schedules; mutations stay owner-only.

Overlap is `ScheduleOverlapPolicy.SKIP` with no mutex, unlike
`ConfiguredWorkflow`, and runs use `maximum_attempts=1`. Disabling **pauses** the
Schedule rather than deleting it. Monthly specs over-fire on days 28–31, and
`load_scheduled_chat` drops the non-matching firings via `schedule_due`.
`POST /chat/schedules/<id>/run` is keyed on `run_requested_at` so a recovering
reconcile pass is idempotent.

## AGT-008 — An interactive turn is detached from the connection watching it

**Applies to:** `reporting/services/chat_turns.py`,
`reporting/routes/chat.py`, `reporting/services/report_store` (chat turn log)

A turn used to *be* the HTTP request: `graph.astream` was iterated inside the
`StreamingResponse` generator. Now a turn is a **producer** writing an
append-only log of stream parts, and the request is a **reader** tailing it. The
two share only a turn id, so a client can disconnect and reattach.

**Why:** two failures come from the old shape, and both are structural.
`gunicorn.conf` set no `timeout`, so the 30s default applied — and under
`UvicornWorker` that is a heartbeat watchdog, so a loop blocked past it gets the
worker `SIGABRT`ed mid-response and the client receives a **truncated body
rather than an error** (observed three times under the harness, as
`IncompleteRead` after ~470 bytes). Separately, a dropped connection **destroyed
the turn**: Starlette cancels a `StreamingResponse` generator on
`http.disconnect`, so closing the tab killed minutes of work outright. An
explicit `timeout = 300` fixes the first; only detaching the turn fixes the
second.

**The producer renders the parts; the reader only replays them.** The log holds
the exact JSON the live stream sent, so the first delivery and every replay are
byte-identical and there is no second rendering path that can drift. It is also
what lets `POST /chat/stream` and the reconnecting
`GET /chat/stream/{thread_id}` share one reader. `message_id` and `text_id` live
on the turn record for the same reason — a replay that minted fresh ids would
read to the client as a *second* assistant message, not the same one being
rebuilt.

**A reader stops on two conditions, not one:** a terminal status *and* a cursor
that has reached `last_seq`. Status alone races the visibility of the final
batches and cuts the answer off mid-sentence, which is why `finish_chat_turn`
takes `last_seq` rather than deriving it (that would mean rewriting the metadata
item on every flush).

**Don't:** advance a reader's cursor past a gap in `seq`. Store propagation is
per item, so a poll can return 5 and 7 without 6; taking the gap loses 6
permanently rather than late. Both backends truncate a page at the first gap,
and the DynamoDB reads are `ConsistentRead=True` on top of that.

**Stopping is now an explicit request, because disconnecting no longer stops
anything.** That cancellation used to be free — Starlette cancelled the
generator, which cancelled the graph — and detaching the turn took it away, so
`POST /chat/stream/{thread_id}/cancel` puts it back. It sets a flag on the
record rather than signalling the task: with several workers the request
usually lands somewhere other than the producer, so the record is the only
channel that reaches it.

**Don't:** make the producer notice a stop only between chunks. It reads the
flag on its heartbeat and **cancels its own task**, because a turn is most
likely to be stopped precisely while it is blocked on a slow model call or
tool — where no chunk arrives for as long as the call takes, and where letting
the call finish first means its side effects happen anyway. The publisher
captures `asyncio.current_task()` in `__aenter__`, which is the producer's task,
so the cross-worker path behaves like the same-worker one rather than degrading
into a wait.

**Deleting a conversation cancels, then waits.** Cascading while the producer
still runs lets it append batches and recreate checkpoint state behind the
delete. A failed cancel is a 503 rather than a best-effort log — without knowing
the turn stopped, deleting is the race this exists to avoid. The wait is bounded
(`CHAT_TURN_STOP_WAIT_SECONDS`) and falling through it is safe, because
`delete_chat_turn` collects batches **whether or not the header is still
there**: a producer that outlived its conversation is the only thing that
creates headerless batches, so gating that cleanup on the header skipped the
only rows worth collecting.

**`expires_at` is a renewable lease, not a lifetime.** It is heartbeated by the
running producer, independently of token output, because a turn is quietest
exactly when it is slowest. Fixed at creation it would lapse mid-turn on any
turn longer than the retention window, and then: reconnect reports nothing to
attach to, a second producer may start on the same thread, and the sweep may
delete the log still being written.

Three things follow from expiry being mutable, and each was wrong when it was
merely additive:

- **Renewal moves the record and the pointer together**, in one transaction.
  Separately, a successor can take the pointer between the two writes and the
  old producer carries on believing it holds the thread. **A failed renewal
  stops the producer** rather than being ignored.
- **Taking over an expired lease re-checks expiry in the update**, not just in
  the read above it. The producer can renew in between, and retiring a live
  turn puts a second producer on the thread.
- **The DynamoDB sweep cannot assume creation order is expiry order.** Its index
  is keyed by `created_at`, so a long-running turn that keeps renewing sits at
  the head of the partition forever; a pass that stopped there would re-read and
  skip the same entries every time while everything behind them accumulated. It
  reads *past* live entries instead, bounded by pages.

**Don't:** enforce one-running-turn with a read above the insert. A thread has
at most one running turn, and the *store* is what says so: a partial unique
index (`status = 'running'`) in SQL, a conditional write in DynamoDB. Under
read-committed two requests can both observe no running turn and both commit,
leaving two producers interleaving two answers into one conversation. The loser
is told to reconnect; the one case it retries instead is a blocker whose lease
has **expired**, whose producer is gone and which it retires first.

**Testing note:** a concurrency test here needs real connections. The SQL
store's test fixture uses `StaticPool`, which hands every session the *same*
connection, so two "concurrent" sessions interleave inside one transaction and
this race cannot occur — a broken read-then-write passes, with both callers
reporting success even though only one row lands.
`test_two_concurrent_creates_cannot_both_win` builds its own file-backed engine
for that reason.

**Testing note:** these races are easy to write tests *around* rather than
*for*, and three tests here passed against the broken code before being fixed.
Check a new one fails against the version without the fix — in particular, a
lease renewal injected before the blocking row is read is caught by the read
guard and never reaches the update guard it was meant to exercise.

**Note:** expired logs are swept at the end of each turn, not by a scheduler. A
log belongs to a *turn*, so `delete_chat_session`'s cascade is not enough — the
turns of a conversation nobody deletes would accumulate. Hanging the sweep off
the producer rate-limits it to chat traffic, keeps it off the request, and keeps
it working in deployments that run no Temporal worker (unlike session
retirement, [SBX-011](sandbox.md#sbx-011)).

**Note:** the retirement handshake is unchanged and still comes first.
`touch_chat_session` is awaited *before* the turn is created, not beside it, for
the reason in [SBX-011](sandbox.md#sbx-011): a turn that started before that
write landed could be running against state being torn down.

**Still open:** the producer is a detached task in the web process, so a restart
of `seizu` still ends the turns it was running — the client is now told, rather
than silently truncated. Moving the producer to the Temporal worker is the
remaining half, and `chat_turns.start_turn` is the seam for it. Note that a
retry there would be wrong for the same reason it is wrong for scheduled chats
([AGT-007](#agt-007)): a turn is expensive and not idempotent, and retrying
would append a second answer to the same log. The durability on offer is
reconnect and surviving a web-process restart, not automatic retry.
