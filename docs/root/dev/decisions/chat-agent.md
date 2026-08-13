# Chat agent decisions (`AGT`)

Decisions about what the chat agent may do, what it is shown, and how a turn is
driven. Cost and context shaping live in [chat context](chat-context.md);
sandbox delegation in [sandbox](sandbox.md).

Primary code: `reporting/services/chat_graph.py`,
`reporting/services/chat_orchestrator.py`, `reporting/services/mcp_runtime.py`,
`reporting/services/headless_chat.py`, `reporting/services/agent_run.py`,
`reporting/services/chat_turns.py`,
`reporting/temporal_workflows/chat_turn.py`.

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
admitting a turn on one **is rejected** (403).

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
`reporting/temporal_workflows/chat_turn.py`, `reporting/routes/chat.py`,
`reporting/services/report_store` (chat turn log), `src/api/chatTransport.ts`

A turn used to *be* the HTTP request: `graph.astream` was iterated inside the
`StreamingResponse` generator. Now a turn is a **Temporal workflow** writing an
append-only log of stream parts, and a request is a **reader** tailing it. The
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

### Interactive chat now requires Temporal

This **reverses the scoping in issue #254**, which asked for the turn to be
detached without adding a dependency, and the first implementation duly ran the
producer as a detached `asyncio.Task` in the web process. What that cost is the
point: a detached task has no identity, so everything a workflow gives for free
had to be rebuilt by hand — a renewable **lease** to prove it was alive, a
process-local **registry** to find it, **crash detection** to notice it was
gone, a **finalizer** for a task cancelled before its coroutine ever ran, and a
first-writer-wins guard so a second cancel could not land inside the first one's
cleanup. Each of those appeared as a review finding, and several of the fixes
produced findings of their own. Meanwhile a restart of `seizu` still ended every
turn it was running.

`ChatTurnWorkflow` replaces all of it. It exists, it is addressable by a name
derived from the turn id (`workflow_id_for`), and Temporal is what guarantees it
reaches an end — so stopping a turn needs no stored handle, liveness needs no
lease, and a turn survives a web-process restart. The price is that
`CHAT_ENABLED` now implies a reachable Temporal server, the same as scheduled
chats ([AGT-007](#agt-007)).

**Don't:** add a retry policy. `maximum_attempts=1`, for the same reason as
AGT-007 — a turn is expensive and not idempotent, and a retry both re-bills it
and appends a second answer to the same log. What running here buys is that the
turn survives its request and always reaches an end, not that it is repeated.

**Two paths reach a terminal state, deliberately.** The activity finalizes its
own turn, including under cancellation — it is ordinary Python and owns the log
it has been writing. The workflow finalizes only when the activity never got to:
it died with its worker, or timed out. Between them a turn cannot sit at
"running" forever, which is what a reader waits on and what keeps the thread
from admitting another.

**Identity is intersected, never unioned.** An interactive turn's `CurrentUser`
comes from a live JWT and is not serializable, so the activity rebuilds it with
`resolve_stored_user` ([AGT-006](#agt-006)) and **intersects** the result with
the permissions carried in the invocation. `resolve_stored_user` reads the last
seen role claim, which can be staler *or broader* than the live token.

### Admission is its own request

`POST /chat/threads/{thread_id}/turns` answers with a `turn_id` before anything
streams; `GET /chat/turns/{turn_id}/stream` reads it. The client therefore holds
the id from the moment it sends.

**Why this is a separate request and not the head of the stream.** Folding the
two together meant every command about a turn had to be expressed against a
resource that might not exist yet. That produced, in order: a second identity
(`client_token`) for the window before the first frame; a cancel route that
accepted *either* name and 422'd on neither; a stop that could beat its own turn
into the store, and so had to **create** a turn already canceled to claim the
token; and a uniqueness constraint per `(thread, client_token)` to settle the
race between that create and the real one. Every one of those is deleted by
answering admission first. **Seven rounds of review findings on this feature
trace to that single coupling** — if a command here is hard to express, check
whether the resource it names exists yet before adding a mechanism.

`idempotency_key` makes admission repeatable: asking again resolves to the turn
already made rather than starting a second one, so a lost response is fixed by
retrying the same request.

**Admission returns an outcome, not an exception.** `ChatTurnAdmission.outcome`
is one of `created` / `existing` / `busy` / `retired`. It used to raise one of
four errors inferred from *whichever constraint rejected the write*, so a single
collision could mean any of them and the store had to guess — including, in one
version, reporting DynamoDB throttling as "this thread is busy". The store now
re-reads and says which it was.

**A store failure during admission is a 503, never an assumption.** A failed
write means we do not know whether the conversation is being torn down;
refusing costs a retry, guessing costs the conversation.

**Admission and the session touch are one write.** The turn's half of the
retirement handshake ([SBX-011](sandbox.md#sbx-011)) happens *inside*
`admit_chat_turn`. Touching the session first left a window: a delete could read
the fresh timestamp, claim the session, see no running turn and cascade, all
between the two — and the turn was then created against a conversation that no
longer existed.

**Don't:** enforce one-running-turn with a read above the insert. The *store*
says a thread has at most one running turn: a partial unique index
(`status = 'running'`) in SQL, a conditional write in DynamoDB. Under
read-committed two requests can both observe no running turn and both commit,
leaving two producers interleaving two answers into one conversation.

**The handoff is repairable, because the store commits first.** Admission writes
the turn and *then* starts the workflow, so a failure in between leaves a turn
recorded as running with nothing producing it: unreadable, and holding the
thread against a successor until its claim lapses. So `start_turn` ensures the
workflow for **`existing` as well as `created`**, and treats
`WorkflowAlreadyStartedError` as the outcome it wanted. The workflow id is
derived from the turn id, which is what makes ensuring it twice a no-op rather
than a second producer.

**Don't:** ensure it unconditionally. A repeat can arrive after the turn it
names has finished, and a closed workflow's id is reusable — ensuring it then
would run the whole turn again and bill a second answer into the same log. The
guard is `status == "running"`.

**Temporal deduplicates, not the status check above it.** That check is a read
followed by an act, so the turn can finish in between — and under the default
`ALLOW_DUPLICATE` a *closed* workflow's id is free again, so the repair would
start the finished turn over. `REJECT_DUPLICATE` makes the id itself the guard:
a turn's workflow can exist exactly once, ever.

**The workflow is bounded by what is left of the claim, not by a fresh
duration.** A duration cannot express the invariant: the claim is an *instant*
fixed at admission, while a timeout starts whenever the workflow is created — so
a handoff repaired minutes later restarts the clock and the workflow can run past
a claim a successor has already taken. `chat_turn_execution_bound_seconds` takes
the turn's `expires_at` and returns the remainder; a turn whose claim has already
lapsed is not started at all.

**Both deadlines come from one helper.** The bound adds *half* the lease margin
where the lease adds all of it, so it is smaller by construction rather than by
coincidence. Hard-coding it (`timeout + 60`) while the lease used the
configurable margin left the invariant true only for the settings the test
happened to run under — a test that looked like it protected the property and
did not.

**The execution bound covers queue time, not just the activity.** A
`start_to_close_timeout` starts when a worker accepts the task, so a workflow
queued through a worker outage can begin *after* the turn's claim on the thread
has lapsed and a successor has been admitted. `execution_timeout` bounds the
whole thing and is kept strictly under the lease, with a test asserting that
relationship so a settings change cannot quietly reopen it. Belt and braces on
the worker side: the activity re-checks that its turn is still `running` **and**
still holds the thread pointer before producing, because a lapsed claim is
exactly the case the bound is protecting against.

**One resolver, both paths.** The hash comparison runs on the lookup *before*
the write and on the re-read *after* a lost race
(`resolve_chat_turn_for_key`). Comparing only on the first means two requests
sharing a key but not a body can have the loser resolve to the winner's turn —
and then hand it the wrong work, since the loser may be the one that starts the
workflow.

**The permission cap is part of the fingerprint.** A repair dispatches from the
*retrying* request, so without this a turn admitted before a role was widened
could be started afterwards with the wider cap — precisely what the cap exists
to prevent ([AGT-006](#agt-006)). The cost is deliberate and worth knowing: a
turn is **not repairable across a permission change**, becoming unrecoverable
rather than running above its admitted authority. Persisting the whole
invocation would let such repairs succeed instead; that is a schema change, and
a different decision.

**A key names one request.** The key alone says "this is a repeat", not a repeat
*of what* — and it resolves to a turn that may already be running, whose body is
what the producer executes. So a `request_hash` (`chat_turn_request_hash`, over
every field that reaches the invocation) is stored with the turn and compared on
admission; a repeat carrying the same key with a different body is `mismatched`
→ **409**, never resolved. Nullable and compared only when both sides have one,
so turns admitted before the fingerprint existed keep resolving.

**Cancellation does reach the fallback finalizer.** Worth recording because it
reads like a bug: the workflow catches `Exception`, and Temporal cancellation is
often described as arriving as `asyncio.CancelledError`, which is not one. In
this path it does not — `execute_activity` raises `ActivityError` (wrapping
temporalio's own `CancelledError`), which *is* an `Exception`, whether the
cancel lands while the activity is running or while it is still scheduled.
`chat_turn_test.py` pins both cases, so a future change that lets a cancelled
turn stay `running` fails there rather than in production.

### The event log

**The producer renders the parts; the reader only replays them.** The log holds
the exact JSON the live stream sent, so the first delivery and every replay are
byte-identical and there is no second rendering path that can drift. `message_id`
and `text_id` live on the turn record for the same reason — a replay that minted
fresh ids would read to the client as a *second* assistant message, not the same
one being rebuilt.

**A reader stops on two conditions, not one:** a terminal status *and* a cursor
that has reached `last_seq`. Status alone races the visibility of the final
batches and cuts the answer off mid-sentence, which is why `finish_chat_turn`
takes `last_seq` rather than deriving it (that would mean rewriting the metadata
item on every flush).

**Don't:** advance a reader's cursor past a gap in `seq`. Store propagation is
per item, so a poll can return 5 and 7 without 6; taking the gap loses 6
permanently rather than late. Both backends truncate a page at the first gap,
and the DynamoDB reads are `ConsistentRead=True` on top of that.

**Only the stream route is exempt from the request timeout.** The exemption is
matched on the path's shape (`/api/v1/chat/turns/{id}/stream`) because the id is
in the path — exempting the whole `/turns/` subtree would silently drop the
deadline from admission and cancellation too.

### Stopping

Disconnecting no longer stops anything, so `POST /chat/turns/{turn_id}/cancel`
puts that back. It **only marks**: it sets a flag on the record and cancels the
workflow. The flag is what reaches a turn running on a worker that never saw the
request, and cancelling the workflow is what interrupts a turn blocked mid-call.

**Don't:** make the turn notice a stop only between chunks. It reads the flag on
its heartbeat and cancels its own work, because a turn is most likely to be
stopped precisely while it is blocked on a slow model call or tool — where no
chunk arrives for as long as the call takes, and where letting the call finish
first means its side effects happen anyway.

**Stop names the turn, not the thread.** The request can be delayed or retried,
and by the time it lands the turn it was aimed at may have finished and the user
started another — a thread-addressed stop would then kill the successor. The
client learns the id from admission, or, after a reload, from
`GET /chat/threads/{thread_id}/turns/active` (204 when the thread is idle, which
the AI SDK maps to "nothing to resume").

**Deleting a conversation closes it first, then stops the turn, then cascades.**
Cancelling alone is not enough: the cancelled turn releases its mutex when it
stops, and another tab can start a successor before the cascade runs. The gate
is the reaper's own claim (`claim_chat_session_for_retirement`,
[SBX-011](sandbox.md#sbx-011)), which makes admission fail atomically — one
mechanism for "this conversation is going away", not a second one.

The record is deleted **last**, after the checkpoint and sandbox, by the same
`delete_session_state` the reaper uses. The route used to do the opposite and
swallow a cleanup failure behind a 204: the record is what makes a thread
findable, so that left the transcript stored forever with nothing to retry from
and nothing saying so.

Every uncertainty on that path is a **503**, never a delete: a failed cancel, a
lost claim, or a turn that does not stop within `CHAT_TURN_STOP_WAIT_SECONDS`.
The claim is re-claimable by design, so the session stays closed and the retry
is a plain repeat; a conversation half-removed from under a live producer cannot
be put back.

### Expiry and sweeping

`expires_at` on a **running** turn is a claim on the thread, and admission
retires a lapsed one. It is therefore derived from the turn's own timeout
(`CHAT_TURN_TIMEOUT_SECONDS + CHAT_TURN_LEASE_MARGIN_SECONDS`, in the shared
`chat_turn_lease_expiry`), **not** from the replay retention window — which is
much shorter than a turn may legitimately run, so using it let one send retire a
turn that was merely slow and put two producers on one conversation. On finish
the record is re-stamped with `CHAT_TURN_RETENTION_SECONDS`, which is a replay
deadline rather than a claim.

This replaced a **renewable** lease heartbeated by the producer. Renewal existed
only because a detached task had no other way to prove it was alive, and it
brought three separate correctness rules with it (moving the record and the
active pointer in one transaction, re-checking expiry in the takeover update as
well as the read above it, and a sweep that could not assume creation order was
expiry order). With the workflow bounding the turn, a fixed lease derived from
that bound is sound and all three disappear.

**A turn's header and its thread pointer terminalize together.** Two writes
wedge the thread: terminalizing the header first and releasing the pointer
second means a transient failure on the second leaves every retry seeing a
terminal header, returning early, and never reaching the pointer — so nothing
new can be admitted until the lease lapses. **Don't** collapse the fallback
though: the two conditions mean different things, and when only the *pointer's*
fails a successor already owns the thread while this turn still has to reach a
terminal state, or a reader waits on it forever. The header is retried alone.

**Deletion order is load-bearing.** Event batches go first, in a batch write —
there can be hundreds, far past a transaction's limit, and a half-deleted log is
unreachable without its header anyway. The header then goes *with* every index
naming it in one small transaction. Orphaning the idempotency-key item is the
case that motivates this: it names a missing turn, and its
`attribute_not_exists` condition then refuses that key forever while resolving
to nothing — a request that can be neither admitted nor repaired.

**Note:** expired logs are swept at the end of each turn, not by a scheduler. A
log belongs to a *turn*, so `delete_chat_session`'s cascade is not enough — the
turns of a conversation nobody deletes would accumulate. The DynamoDB sweep
still reads *past* live entries and persists where it got to, because its index
is keyed by `created_at`: a long-running turn sits at the head of the partition,
and a pass that stopped there would re-read the same entries every time while
everything behind them accumulated. Queries carry an explicit `Limit`, and the
sweep is paced per process by `CHAT_TURN_SWEEP_INTERVAL_SECONDS`.

**Heartbeat on a timer, never on output.** A turn is quietest exactly when it is
slowest — a model call or a tool can run for minutes producing no chunk — so
heartbeating from the stream loop times out *healthy* turns. The damage is not
just a failed turn: the fallback finalizer marks it failed and frees the thread
while the original activity is still running and still writing the same
checkpoint, which is two producers on one conversation. The activity ticks
independently (`_CHAT_TURN_HEARTBEAT_INTERVAL_SECONDS`); `start_to_close_timeout`
is what bounds a turn that genuinely runs too long.

**Terminal writes are conditional on the turn still being `running`, in both
stores.** Two writers reach `finish_chat_turn` for one turn: the turn closing
itself, and the workflow's fallback closing it after the activity timed out. A
turn that timed out *spuriously* is still alive, so an unconditional write lets
the late one replace a recorded outcome — including its `last_seq`, which is
exactly what a reader uses to know it has seen the whole answer. First writer
wins, enforced by the write itself: `WHERE status = 'running'` in SQL, and
`#s = :running` in the DynamoDB condition expression.

The loser is handed **what is recorded**, not what it asked for, so it can tell
it lost; `None` keeps meaning "no such turn". `produce_turn` reports the store's
status rather than its own, so the workflow result and the log a reader sees
cannot disagree. And a loser must **not** go on to release the thread pointer:
by then it may belong to a successor, and clearing it would let a third turn
start alongside.

### The client holds a pending send

Stop is live from `submitted`, which is *before* admission answers, so there is a
window where the user can ask to stop a turn the client cannot yet name.
Aborting the admission request does not close that window — the server may
already have admitted and started the turn, which then runs with nobody watching
it. The transport therefore keeps one `PendingSend` per logical message
(idempotency key, turn id once known, and a `stopRequested` flag), and admission
is deliberately **not** given the abort signal. A stop with no id yet is
recorded and applied the instant there is one.

Three rules fall out of that object, and each was a live defect without it:

- **The key is minted per logical message, not per attempt.** A fresh key per
  attempt puts the server's idempotency promise out of reach: a retry admits a
  *second* turn instead of resolving to the one a lost response already made.
- **Ambiguous admissions are retried by the transport, with that key.** A 503 or
  a dropped connection means the turn may well have been admitted, and the
  server's repair path is reachable *only* by asking again with the same key —
  waiting for the user to resend does not work, because their next message gets
  a new id and therefore a new key, which admits nothing and is told the thread
  is busy. This retries an idempotent *request*; the turn still runs at most
  once ([AGT-007](#agt-007)). A 409 or 404 is a decision, not ambiguity, and is
  never retried.
- **Pending state is a map keyed by thread, not one slot.** The transport
  outlives any one conversation and the sidebar can switch mid-turn, so a single
  slot means whichever thread acted last owns it and the others silently lose
  Stop. Reattaching in an idle conversation must clear only its own entry.
- **Completion clears the thread whose *stream* ended**, which is not the thread
  on screen: a callback runs after the user may have switched away, and clearing
  on the selected thread disarms Stop for the turn they are watching. The
  transport records which stream it is reading (`clearFinishedStream`), because
  the caller cannot know.
- **An unresolved send is recoverable from the UI**, or the preserved key is
  unreachable and the repair path is theatre — typing the message again mints a
  new key and admits a second turn. The banner offers Retry, which replays the
  stored body **verbatim** under the stored key; a recomposed body would not
  match the fingerprint and would be refused. First send and retry share one
  admission path so they cannot drift.
- **Don't** hold that state only in the transport. It lives on plain objects
  React cannot observe, so the button never renders and the feature is invisible
  in the product while testing green against the transport directly
  (`onUnresolvedChange` mirrors it into state).
- **The pending send is scoped to its thread.** The transport outlives any one
  conversation and the sidebar can switch sessions mid-turn, so a turn finishing
  in a thread the user has navigated away from must not clear the pending state
  of the one they are watching — that silently disarms Stop for the turn that is
  actually running.
- **Admission carries its own deadline.** It deliberately ignores the SDK's
  abort signal, so without one a response that never arrives pins the send
  forever: the retry loop cannot advance and a stop asked for meanwhile is never
  delivered, because it is waiting on a turn id. A timeout is ambiguous in
  exactly the way a 503 is, and retries with the same key.
- **"Unknown" is not "failed".** A send whose attempts all ended ambiguously
  keeps its key (`unresolved`), because the turn may exist server-side and the
  key is the only route back to it; a 409 or 404 is a decision and spends it.
  `onFinish` fires for errored sends too, so clearing pending state there
  unconditionally strands exactly the turn the repair path exists to recover.
- **A deferred stop reports through a callback, not a return value.** A stop
  asked for before admission answers is carried out later, by which point
  `requestStop` has returned and throwing from the send would be swallowed by
  the SDK as an expected abort — so the exact race the deferral exists for would
  be the one whose failure is invisible. (A promise settled later was tried
  first and is worse: it deadlocks any caller that awaits it before releasing
  the step that settles it, and strands unsettled on an admission failure.)
- **A finished turn stops being the pending one.** Otherwise a Stop pressed
  during the *next* send — before that one is admitted — cancels the turn that
  already ended and silently does nothing to the live one.
- **A refused cancel raises.** The reader stops either way, so an unchecked
  401/403/5xx looks exactly like success while the turn keeps generating and
  running the actions it had queued.

### Testing notes

- A concurrency test here needs **real connections**. The SQL store's fixture
  uses `StaticPool`, which hands every session the *same* connection, so two
  "concurrent" sessions interleave inside one transaction and the race cannot
  occur — a broken read-then-write passes, with both callers reporting success
  even though only one row lands.
  `test_two_concurrent_admissions_cannot_both_win` builds its own file-backed
  engine for that reason.
- These races are easy to write tests *around* rather than *for*. Several tests
  here passed against the broken code before being fixed, so **check a new one
  fails against the version without the fix**.
- The route tests stand in for Temporal by running the activity inline
  (`_fake_temporal`), so admission, the producer and the reader are exercised
  together without a worker. The fake records workflow starts in a **list**, not
  a dict keyed by workflow id — a second start against the same id is precisely
  the bug worth catching, and a dict silently overwrites it. (That mistake made
  the first version of the "don't re-run a finished turn" test pass with its own
  fix removed.)
- CI's `unit` job blocks network, which catches unmocked store and Temporal
  calls that pass locally because a real Temporal and Postgres are there to fall
  into. A locally green backend suite does not prove the mocks are complete.
- **Drive client behaviour through the path a user takes.** Two defects here
  were masked by tests that called the transport directly: a `clearPending`
  scoped by an argument the test supplied (so it never exercised the callback
  that reads the *wrong* thread), and a Retry that worked in the transport while
  never rendering a button. If a test supplies the value the bug is about, it
  cannot see the bug.
- **A resumed SSE stream cannot be fed to the AI SDK mid-message.**
  `createStreamingUIMessageState` initialises `activeTextParts: {}` even when it
  reuses the existing assistant message, and `text-delta` throws when that map
  has no entry — the `text-start` was before the cursor and is not re-sent.
  Resuming was implemented and reverted for this reason; replaying from the
  first frame is what works. Note that no test here can catch it: the
  environment's `fetch` has no streaming body, so `processResponseStream` is
  stubbed and the real parser never sees a resumed stream.
- In the frontend suite, `jest.mock` is applied at call time by Bun rather than
  hoisted, so it **cannot** replace the superclass of a class that has already
  been evaluated — mocking `ai` does not change what `SeizuChatTransport`
  extends. Stub the instance method instead.
