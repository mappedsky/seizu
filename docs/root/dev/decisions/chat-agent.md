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
explicit `timeout = 300` originally masked the first; only detaching the turn
fixes the second and makes a web-worker restart recoverable. With production
now owned by Temporal, keeping a wedged web worker alive for five minutes has no
chat benefit. The bundled Gunicorn watchdog therefore follows
`API_REQUEST_TIMEOUT` (60 seconds by default). A healthy `UvicornWorker` keeps
notifying Gunicorn during a long-lived stream, so chat duration does not set
this value.

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
renewable lease, and a turn survives a web-process restart. The price is that
`CHAT_ENABLED` now implies a reachable Temporal server, the same as scheduled
chats ([AGT-007](#agt-007)). In Docker Compose the web service therefore waits
for Temporal's health check before starting; it need not wait for the worker,
because Temporal durably queues an admitted workflow until a worker polls it.

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
the permission cap stored in the admitted command. `resolve_stored_user` reads
the last seen role claim, which can be staler *or broader* than the live token.

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
collision could mean any of them and the store had to guess. The store now
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
(`status = 'running'`) in PostgreSQL. Under read-committed two requests can both
observe no running turn and both commit, leaving two producers interleaving two
answers into one conversation.

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

**A cancellation buffer comes off the top of that remainder.** Timing a
workflow out does not stop its activity there and then — the cancellation
reaches it on its next heartbeat — so handing it the claim's full remainder
still lets a producer run past the instant a successor can be admitted. Too
little left for both the work and the stopping means the turn is not started at
all, and is **closed** rather than left at `running`: a turn with no producer is
one a client attaches to and waits on until the tail deadline, for an answer
that is never coming. That case is stored with terminal status `expired` and
has its own admission outcome (`expired` → 503), so every repeat of the same
idempotency key remains retryable rather than becoming an `existing` empty log.
That 503 carries `X-Seizu-Chat-Admission: expired`: unlike an ambiguous store
503, the old key is definitively spent, so the transport retries the same
logical message once under a fresh key instead of attempting to repair it.

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
is still the active unexpired turn for the thread before producing, because a
lapsed claim is exactly the case the bound is protecting against.

**The admitted command is immutable.** The turn record stores the message,
continuation/confirmation fields, bypass flag, permission cap, and timeout.
Every first handoff or later repair dispatches that stored command; the retrying
request is only a lookup by its required idempotency key. This keeps one durable
source of truth, makes repair safe across request or permission changes, and
removes a parallel request-fingerprint protocol. A client must mint a key per
logical send and reuse it for ambiguous retries.

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
permanently rather than late. The store truncates a page at the first gap.

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
lost claim, or a turn that does not stop within the internal stop deadline.
The claim is re-claimable by design, so the session stays closed and the retry
is a plain repeat; a conversation half-removed from under a live producer cannot
be put back.

### Expiry and sweeping

`expires_at` on a **running** turn is a claim on the thread, and admission
retires a lapsed one. It is therefore derived from the turn's own timeout
(`CHAT_TURN_TIMEOUT_SECONDS` plus an internal safety margin, in the shared
`chat_turn_lease_expiry`), **not** from the replay retention window. On finish
the record is re-stamped with `CHAT_TURN_RETENTION_SECONDS`, which is a replay
deadline rather than a claim.

This replaced a **renewable** lease heartbeated by the producer. Renewal existed
only because a detached task had no other way to prove it was alive, and it
brought three separate correctness rules with it (moving the record and the
active pointer in one transaction, re-checking expiry in the takeover update as
well as the read above it, and a sweep that could not assume creation order was
expiry order). With the workflow bounding the turn, a fixed lease derived from
that bound is sound and all three disappear.

**Deletion is transactional.** Event batches and their turn row are removed in
one PostgreSQL transaction. The idempotency key is constrained on that same turn
row, so deleting the row cannot leave a separate key that resolves to nothing
or permanently blocks a retry.

**Note:** expired logs are swept at the end of turns, not by a scheduler. A log
belongs to a *turn*, so `delete_chat_session`'s cascade is not enough. The
PostgreSQL `expires_at` index lets a bounded ordered query read only expired
turns, with no scan cursor or renewal path.

**Heartbeat on a timer, never on output.** A turn is quietest exactly when it is
slowest — a model call or a tool can run for minutes producing no chunk — so
heartbeating from the stream loop times out *healthy* turns. The damage is not
just a failed turn: the fallback finalizer marks it failed and frees the thread
while the original activity is still running and still writing the same
checkpoint, which is two producers on one conversation. The activity ticks
independently (`_CHAT_TURN_HEARTBEAT_INTERVAL_SECONDS`); `start_to_close_timeout`
is what bounds a turn that genuinely runs too long.

**A terminal write is conditional on the turn still being `running`.** Two
writers reach `finish_chat_turn` for one turn: the turn closing itself, and the
workflow's fallback closing it after the activity timed out. A turn that timed
out *spuriously* is still alive, so an unconditional write lets the late one
replace a recorded outcome — including its `last_seq`, which is exactly what a
reader uses to know it has seen the whole answer. First writer wins, enforced by
`WHERE status = 'running'` in the update itself.

The loser is handed **what is recorded**, not what it asked for, so it can tell
it lost; `None` keeps meaning "no such turn". `produce_turn` reports the store's
status rather than its own, so the workflow result and the log a reader sees
cannot disagree.

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
- **Completion is identified by the turn that ended**, not by the thread on
  screen and not by "the stream currently being read". Several conversations can
  stream at once, so the latter is not a single thing either — an earlier
  thread finishing after a later one starts would be attributed to the later
  one. `clearFinishedTurn(turnId)` takes the id off the finished message; a turn
  that ended without announcing one is left alone, which keeps it stoppable.
  The producer therefore carries `turn_id` in the opening frame's message
  metadata; defining the TypeScript field without emitting it leaves every
  completion unidentified.
- **Unresolved threads are a set.** One value means a second ambiguous send in
  another conversation hides the recovery the first still needs.
- **Reconnect prefers a turn this client already holds** over asking `/active`,
  which answers 204 once the turn has finished — losing a response nobody has
  rendered yet. That is exactly the case after a retry resolves to a turn that
  completed while the connection was down.
- **An unresolved send is recoverable from the UI**, or the preserved key is
  unreachable and the repair path is theatre — typing the message again mints a
  new key and admits a second turn. The banner offers Retry, which replays the
  stored body under the stored key. First send and retry share one admission
  path so they cannot drift.
- **Don't** hold that state only in the transport. It lives on plain objects
  React cannot observe, so the button never renders and the feature is invisible
  in the product while testing green against the transport directly
  (`onUnresolvedChange` mirrors it into state).
- **Recovery is rendered from unresolved state, not SDK error state.** Changing
  threads recreates the SDK chat and its transient error, while the transport's
  unresolved key deliberately survives. Nesting Retry under `error` therefore
  hides the only route back to the turn after navigating away and back.
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
- **When you scope one piece of state to a thread, check its siblings.** Mapping
  `pending` by thread while leaving `streamingThread` global reproduced the same
  bug one field over, and the test missed it by having the *later* thread finish
  — the order a single slot handles correctly. Order the actors so the earlier
  one finishes last.
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

## AGT-019 — What a call may spend is derived from the model, never a constant

**Applies to:** `reporting/services/chat_models.py`, `chat_graph.build_chat_model`
/ `get_chat_model`, `chat_context.max_output_tokens`,
`chat_orchestrator._structured_invoke`; `CHAT_LLM_MAX_TOKENS`,
`CHAT_LLM_MAX_OUTPUT_TOKENS_CAP`, `CHAT_LLM_*_REASONING_EFFORT`,
`CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS`

A `ModelSpec` — model id, output ceiling, reasoning effort — is resolved once per
call and passed down. The ceiling is `min(cap, the model's own
max_output_tokens)` read from litellm, not a configured constant.

**Why: a constant here fails silently and catastrophically.** On a reasoning
model the thinking and the answer come out of **one** allowance, so a ceiling
that is too low does not produce a shorter answer — it produces *no* answer, and
nothing distinguishes that from a model that could not satisfy the request.
Measured on `deepseek-v4-pro` at the old `CHAT_LLM_MAX_TOKENS=4096`: **every**
planner call returned `chars=0, finish_reason=length` twice, and `planner_node`
fell back to a single step carrying the user's whole request.

That fallback is invisible everywhere downstream — identical in the stream, the
checkpoint and the harness (`steps_total: 1`), with `run_errors` the only record
— and it **disables the orchestrator's parallelism entirely**, because
parallelism operates on
independent plan *steps* and there was only ever one. So a feature can be
correct, tested, and completely unreachable. Measured, 3 samples per arm:

| `max_tokens` | real plans | widest batch |
|---|---|---|
| 4,096 | **0/3** | 1 |
| 16,384 | 3/3 | 4 |
| 32,768 | 3/3 | 4 |

The planner emits **5,798–13,058 output tokens** per call on this model, so
4,096 could never have worked: the plan and the reasoning that produces it do
not fit.

**A constant is wrong in both directions at once.** The models we run report
ceilings from 16,384 (`gpt-4o`) to 393,216 (`deepseek-v4-pro`). A constant large
enough for the second is *refused outright* by the first — providers reject an
over-ceiling request rather than reducing it. Only derivation is right for both,
and it needs no per-model configuration.

**`0` means derive.** `CHAT_LLM_MAX_TOKENS` and
`CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS` both default to `0` and are still honoured
when set — and still clamped to the provider's ceiling.

**Don't:** reintroduce a default output size at a call site.
`_structured_invoke` defaulted to `1,024`, which is why the router and verifier
sat one hard question away from the same silent failure the planner hit.

### `reasoning_effort` is the intent; the provider's own parameter is what ships

`max_tokens` is genuinely portable — litellm renames it to
`max_completion_tokens` for OpenAI on its own. **Effort is not.** Its mapping is
lossy on half the providers we run: every level collapses to
`thinking: {"type": "enabled"}` on DeepSeek and `{"type": "adaptive"}` on
Anthropic, so a "high" and a "minimal" arrive identical. That is why sweeping
effort measured nothing on DeepSeek — there was no dial attached.

Graded control does exist on all four, so `chat_models.reasoning_kwargs` renders
the level into whatever that provider grades on:

| Provider | Rendered from `low` |
|---|---|
| OpenAI | `reasoning_effort="low"` (native) |
| Gemini | `reasoning_effort="low"` → `thinkingBudget` (native) |
| Anthropic | `thinking={"type":"enabled","budget_tokens":N}` |
| DeepSeek | `extra_body={"reasoning_effort":"low"}` |

`budget_tokens` is a **share of the call's own ceiling**, not a constant, so one
profile means the same thing on a 16k model and a 393k one — floored at
Anthropic's 1024 minimum and capped at half the ceiling, because thinking and the
answer come out of the same allowance.

This is provider-specific code, which the previous revision of this entry argued
against. The argument was wrong: keeping only the portable parameter did not
avoid provider knowledge, it silently discarded control. The knowledge lives in
the **one resolver that already knows the model**, so no call site learns a
provider name.

### Two generation parameters are refused, and neither says so

**OpenAI reasoning models reject `temperature` outright.** With
`litellm.drop_params` False that raises, so `CHAT_LLM_TEMPERATURE=0.2` fails
*every call* on `gpt-5` and `o3`. There is no capability flag for it —
`get_supported_openai_params` reports `temperature` as supported for `gpt-5`,
which then refuses it — so `chat_models` asks litellm's parameter transform
directly and caches the answer.

**Anthropic fixes temperature at 1 once extended thinking is on**, and litellm
does not strip a different value. `temperature_for` therefore returns `1.0` for
that combination and `None` where a temperature may not be sent at all.

Both are the same shape as the ceiling bug: a flat generation constant applied to
models that do not all accept it.

### Only the router has a measured default, and only because of its shape

`CHAT_LLM_ROUTER_REASONING_EFFORT` defaults to `none`. Measured across 21 cases
on `deepseek-v4-pro`: routing stayed **21/21 correct** while median output
**halved**, 78 → 37 tokens, on every turn.

**The planner looks like a far bigger win and is not takeable on that evidence.**
With reasoning off it used **7.7x fewer tokens** (8,439 → 1,093) and was 12x
faster, at the same plan *width*. But reading the plans showed they are not
equivalent: with reasoning the planner produced a shared up-front fetch step that
the four per-CVE steps depended on; without it, four independent steps each
re-derived that data. Width is a structural metric and hid this entirely.

The asymmetry is the point, and it generalizes. **A stage whose output is a
single value can be measured by a cheap probe; a stage whose output is the
structure of everything downstream cannot.** The router's reasoning can change
nothing but one label. The planner's changes what every worker then does, so
only an end-to-end run can price it — a saving at one call that adds work to
dozens is not a saving.

**Measured end to end, the planner saving reverses.** Three samples per arm on
the same conversation: turning planner reasoning off cost **more** overall --
median 1,107k tokens against 846k, and $0.329 against $0.271 -- despite saving
7,300 tokens at the planner call. Exactly the mechanism the plans predicted: four
steps re-deriving the data the shared prefetch would have fetched once, and the
workers are where the tokens are. (Ranges overlap at n=3, so read this as
directionally confirmed rather than settled; the mechanism is what makes it
credible, not the sample size.)

It *is* faster -- 684s against 1,019s -- because those four steps start
immediately instead of serializing behind the prefetch. So it is a real
cost-versus-latency trade, and a deployment that wants responsiveness over spend
can take it deliberately. It is not a free win, which is how the probe made it
look.

**Don't:** convert the per-stage rationale table into defaults. It is where to
look, not what to set.

### Effort keys on stage, not only role

A worker's ReAct loop is deciding what to do next; its summary pass is writing
down what the step already established, and **every "reasoning ate the
allowance" failure in this codebase is in the latter**. Both used to resolve
through `get_chat_model("worker", …)`, so the distinction could not be
expressed. `worker_summary` and `worker_summary_retry` are now stages that run
on the worker's model with their own effort, inheriting the worker's when unset.

**It must go through `model_kwargs`.** `ChatLiteLLM` does not declare
`reasoning_effort`, so passing it as a constructor argument is silently
swallowed — no attribute, absent from `model_kwargs`, absent from
`_default_params`. It shipped that way first, and every measurement taken
against it was measuring nothing.

`litellm.drop_params` is `False`, so an unsupported parameter **raises** rather
than being dropped. That makes the `supports_reasoning` gate in
`chat_models.resolve` load-bearing rather than tidy.

Effort is **per role** because the stages want opposite things in principle:
reasoning is what decomposition and judgment (planner, verifier) are for, while
classification (router) and transcription (worker summaries, synthesis) only
lose answer allowance to it.

**Every per-role default is empty, because on this deployment the knob does
nothing measurable.** LiteLLM collapses `minimal`/`low`/`medium`/`high` to a
single value on DeepSeek (`thinking: {"type": "enabled"}`) and Anthropic
(`{"type": "adaptive"}`) — only OpenAI and Gemini are genuinely graded. Measured
on DeepSeek with the parameter actually reaching the wire: the router routes
21/21 correctly at every level with ~70 output tokens either way, and the
planner produces a width-4 plan 3/3 at every level. Turning reasoning *off*
(`none`) did not help either — it produced **more** output (11k vs 6.5k median),
because the model writes its thinking into the visible answer instead.

So the recommended-values table in the install docs is guidance for graded
providers, not a measured win. Do not ship it as a default without measuring on
the provider in question.

### Reasoning has to survive back into the next request

A tool-calling assistant message must be replayed with its reasoning intact, and
the shape differs: DeepSeek needs `reasoning_content`, Anthropic extended
thinking needs the **signed `thinking_blocks`**, and a tool-use turn replayed
without them is rejected. `_strip_reasoning_context` flattens list content to
plain text, which would destroy Anthropic's blocks, so both shapes are preserved
in `additional_kwargs` — where litellm reads them
(`prompt_templates/factory.py`) and where the flattening cannot reach.

**Anthropic + reasoning + tool loops is unverified.** This deployment runs
DeepSeek, so that path is written to what litellm reads rather than to an
observed failure. Verify it against a real Anthropic key before recommending
reasoning on Anthropic.

### The spec is the cache key, and it travels

`build_chat_model` is memoized on the **spec**, not on `(role, economy)`. That
matters ahead of user-selected models: a role-keyed cache would hand one user's
chosen model to another in the same process.

For the same reason a spec **travels** to wherever the call actually happens
rather than being re-resolved there. A caller that re-resolves at the far end
reads that process's settings and can produce a different model than the one the
turn was admitted with — the failure `permission_cap` travelling already
prevents ([AGT-006](#agt-006)). The worker's summary passes take their model as
an argument for exactly this reason.

**Measuring this needs `scripts/plan_probe.py`**, which runs the planner alone —
one LLM call, nothing executed — and reports the widest independent batch plus
whether the plan was the fallback. A full harness turn costs ~553s and ~$0.20 to
learn the same integer.

## AGT-009 — Answer-only plan steps require complete evidence

**Applies to:** `chat_orchestrator._PLANNER_PROMPT`

The planner may reuse facts established earlier, but an answer-only step is
valid only when those facts satisfy the step's success criteria. A prior answer
mentioning the subject is not evidence for a missing property. If a request asks
to determine, verify, investigate, cross-check, or trace something and the
conversation identifies an evidence gap, the plan must gather that evidence
with an available tool/skill or explicitly say the determination cannot be
made.

Attack-path and internet-exposure work illustrates the distinction: selecting
CVEs from a prior ranked list may be answer-only, while claiming reachability
when the prior result contains no deployment or network data may not. Use a
direct graph tool for a bounded lookup and `sandbox__delegate` for iterative
exploration. Do not manufacture tool activity merely for display; tool and
subagent details represent actions that actually ran.

**Why:** an attack-path follow-up asked which previously ranked CVEs were
accessible from the internet. The prior result contained vulnerability and
repository facts, but no deployment endpoints or network-exposure metadata.
The planner nevertheless made both worker steps answer-only, recorded the
missing evidence as an assumption, and then presented an accessibility
conclusion without making an action call. The execution trace was accurate—the
absence of tool/subagent rows reflected that no evidence gathering occurred—but
the answer overstated what the available evidence could establish.

## AGT-010 — External MCP confirmation uses annotations with a local override

**Applies to:** `reporting/services/external_mcp.py`,
`mcp_runtime.list_tools_for_user(chat_safe_only=True)`

External MCP tools are agent-only capabilities, namespaced as
`ext__<proxy>__<tool>`. Confirmation policy is evaluated per tool. An exact
match in `MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS` always requires
confirmation. Otherwise an explicit `readOnlyHint:true` does not; a complete
mutating profile of `destructiveHint:false`, `idempotentHint:true`, and
`openWorldHint:false` also does not. An explicit mutation or risk hint requires
confirmation. Missing or incomplete guidance falls back to the proxy's
`require_confirmation` value, which defaults true. The same confirmation bypass
permission and audit path used by built-ins applies to external calls. The
autonomous sandbox subagent receives only the individual external tools that
this policy classifies as confirmation-free.

The client creates a fresh transport and header dictionary for every discovery
or call. It does not pool an authenticated connection across users. Detached
interactive turns and headless runs use a service credential plus target-user
delegation because the browser bearer token is deliberately not persisted in a
Temporal turn command (AGT-008).

**Why:** the MCP protocol provides standard behavioral hints at the individual
tool boundary, which is more precise than treating an entire proxy as mutating.
They remain advisory, so Seizu accepts them only from an operator-configured
proxy, keeps an exact local force-confirm list for known-sensitive tools, and
uses a fail-closed fallback by default when annotations do not establish a
clear profile. Per-operation connections prevent one worker's pooled headers
from turning a subsequent user's call into a confused-deputy request.

The web tool catalog surfaces configured proxies and their dynamically
discovered tools as read-only synthetic toolsets. This is observability and a
skill-authoring aid, not federation: external tools remain absent from Seizu's
own MCP `tools/list`, and the catalog REST routes do not execute them. When a
proxy returns an OAuth challenge, its catalog contains the synthetic
`seizu_authenticate` tool until credentials are available. Catalog parameter
metadata preserves the external JSON Schema property names verbatim (including
names such as `perPage`); the lower-snake-case rule remains limited to
Seizu-authored Cypher tool definitions.

## AGT-011 — An unfinished plan is discarded unless the next turn resumes it

**Applies to:** `chat_orchestrator.router_node`, `_forced_route`,
`_abandoned_plan_reset`, `reporting/temporal_workflows/activities.finalize_chat_turn`

`plan`/`step_results` round-trip through the checkpoint so an orchestrated run
can survive a turn boundary, but only `synthesizer_node` clears them. A plan is
therefore meant to outlive its turn in exactly one case: the run stopped at
`confirmation_pause` and the next turn carries the approval (or a
continue-the-answer request). Both arrive as a marked `HumanMessage`.

`router_node` — the graph's single entry point — clears an unfinished plan when
the incoming turn is **not** one of those, and `_forced_route` pins a turn to
the orchestrated path only for a genuine resume. A new user message therefore
always gets a new plan.

**Why:** a user stopped an investigation of one repository mid-run and asked for
a different one. Cancelling a turn cancels the graph task wherever it is, so the
dispatcher's last checkpointed write — steps at `pending`/`ran` — stayed in the
thread state. On the next message `_has_pending_plan` forced the orchestrated
route and the planner kept the stale plan, so the agent resumed the *abandoned*
repository and never read what had just been asked. The same hole is open to any
turn that does not reach synthesis: a crashed worker, a timeout. Clearing at the
entry point covers all of them with one rule, rather than asking each producer
to unwind state it was cancelled out of.

**A cancellation is recorded as a cancellation, whichever writer wins.** Stopping
a turn reaches both finalizers at once — Temporal cancels the activity and the
workflow immediately schedules `finalize_chat_turn` — and the fallback usually
wins by a second or two because the activity is still publishing its closing
frames. It wrote a blanket `failed`, so a user-initiated stop surfaced as an
error and the activity's own `canceled` lost the first-writer-wins race. The
fallback now reads `cancel_requested` from the turn, which is set before either
writer runs, so the two agree instead of racing over the outcome.

## AGT-014 — A step that made calls never reports nothing, and neither does a run

**The synthesizer gets the same treatment as the step summary.** A run whose two
steps had both *passed* still opened with "could not produce a final summary"
and handed the user raw step output: the synthesizer call ran, spent its
allowance and returned no text. From the user's seat that is a failed answer
whatever the internals say. An empty synthesis is now retried once, asking for
the answer and nothing else, before the fallback renders step output.

Its output allowance is no longer capped at a concision ceiling either. 2,048
tokens is enough for the answer but not for a reasoning model to think *and*
answer, and the observed result was a blank one — concision achieved by saying
nothing. Length is the prompt's job; the allowance only has to leave room to
answer at all.

### The step-level half

**Applies to:** the summary pass in `chat_orchestrator._run_worker_step`

When the summary pass returns nothing, a narrower retry asks for three things
only — what was established, what was unfinished, what is still unknown — since
that is far smaller to produce than a full summary and a model that spent its
allowance thinking has a better chance at it. If that is also empty, the step
reports its *state* deterministically: goal, completion condition, how many
calls across how many tools, an explicit "still unknown", and only then the
evidence. A raw dump was the first version of this and is not a report — it
leaves the verifier and synthesizer to work out what the step was for, and an
absent finding reads like a negative one unless something says otherwise.

When a step's summary pass returns no text, its result is rendered from the
calls it made and what they returned, rather than left empty.

**Why:** the summary pass is a step's last chance to say what it found, and it
can come back empty — refused by the budget, or a reasoning model spending its
whole output allowance without emitting text. Observed on a step that had made
90 successful calls: `output=0, partial_output=0`, which fails verification for
"Step produced no output", is retried from scratch, and loses the work. The
allowance is also no longer a constant: `chat_context.max_output_tokens` returns
the smaller of `CHAT_LLM_MAX_TOKENS` and what the model reports it accepts, and
every call site that used to pick a number now goes through it. A hardcoded
1,024 bore no relation to what that model could have given, and asking *above* a
provider's ceiling is refused outright rather than quietly reduced — so the
clamp matters in both directions. The synthesizer keeps its deliberate concision
ceiling as `min(model_limit, 2048)`; structured calls (router, planner,
verifier) are clamped once inside `_structured_invoke`, where the model is
chosen.

The fallback is still the load-bearing half: it does not depend on knowing why
the model went quiet.

## AGT-017 — Stop useless work; do not ration all work

**Applies to:** `_step_thresholds`, `_looks_stuck` / `_note_call_signature` in
`_run_worker_step`, `_prepare_retries`, `_stuck_notice` in `sandbox.py`;
`CHAT_ORCHESTRATOR_STEP_SHARE_HARD_MULTIPLE`,
`CHAT_ORCHESTRATOR_STUCK_CALL_WINDOW`, `SANDBOX_STUCK_REPEAT_LIMIT`

A token ceiling cannot tell a run that is looping from one that is working: both
spend. So the ceiling is no longer where a long investigation ends, and the
looping is detected as itself.

**The per-step share is a signal, not the execution cut.** Its purpose is that
no step starves its siblings — a scheduling concern — and at a hard multiple of
1.0 it was also what ended the step. Measured across four consecutive
CVE-reachability runs, **every one stopped on the step share** while the run
budget sat ~80% unspent and the cost budget at ~16%. The default multiple is now
3.0: crossing the share still degrades the step and tells it to converge; what
changes is that a step with no sibling contending may use what the run can
actually spend.

This re-breaks a tie that a three-arm sweep had left open. The sweep found no
*quality* difference between multiples, so the tie went to sibling protection;
it did not measure the case that matters here, which is a plan with one
genuinely large step.

**Three loop detectors, at the level each loop happens.**

- *Within a step:* a full window of tool calls
  (`CHAT_ORCHESTRATOR_STUCK_CALL_WINDOW`, default 8) with no call the step had
  not already made. The step stops, keeps what it gathered, still runs its
  summary pass, and is marked terminal — a step that has run out of new calls to
  make will run out again. A full window is required so ordinary repetition
  (polling, re-reading a file just written) does not trip it.
- *Across attempts:* a rejection the step has already been given once and not
  addressed is terminal. Three of four attempts in one measured run were the same
  verdict restated, and they cost the rest of the run's budget.
- *Inside a delegation:* consecutive already-answered calls
  (`SANDBOX_STUCK_REPEAT_LIMIT`, default 3) escalate from the per-call note to
  an instruction to stop and report. The per-call note says one call was
  pointless; it does not say the task is.

**Why this direction, and not a tighter cap:** a cap hit while answering a hard
question does not save the tokens it appears to. The work is re-done in the next
turn or the next session, from a cold context, and the failed run is a total
loss on top. Cheap detection of *useless* work is what makes an expensive
*useful* run affordable; a cost ceiling (`CHAT_RUN_COST_BUDGET_USD`) remains the
outer guard against genuine runaway, and is the one an operator should set.

## AGT-016 — The planner does not supply identifiers the request did not

**Applies to:** `_PLANNER_PROMPT`, the `repo_cve_reachability` skill

A plan step must not name a repository, organization, account or host the
request did not give it. Identifiers in this graph are whatever was scanned, and
a familiar-looking name is the trap: the resource is very unlikely to be the
upstream project it shares a name with. A bare name stays bare in the goal and
is resolved against the graph at execution time.

**Why:** asked about "the confidant repository", the planner wrote
`lyft/confidant` into both step goals from its own knowledge. The findings step
correctly resolved `mappedsky/confidant` and was then *failed by the verifier*
for not reporting on `lyft/confidant` — the invented identifier had become the
thing the step was judged against. Worse, the reachability step believed it:
**all 100 of its GitHub reads went to `lyft/confidant`**, an unrelated public
repository, so it was judging one codebase's recorded vulnerabilities against
another codebase's source. That failure mode produces confident, cited,
wrong-target verdicts, which is worse than producing nothing.

The skill carries the same guard where the calls are actually made: if the repo
it was handed disagrees with the one the findings step resolved, it uses the
resolved one and says so, and it refuses to read a repository the graph has no
record of.

## AGT-015 — What a retry is told, and what it is then judged on

**Applies to:** `_worker_user_message` (the resume block), the required-action
guard in `_run_worker_step`, `_dependency_context`, the verifier prompt in
`_verify_step`

Four rules, each from the same observed run: a reachability step produced a
correct, cited review on its first attempt and was retried three times until the
budget was gone.

**A "cannot be determined" that names its missing evidence is a finding.** The
verifier failed a review of 19 CVEs because one was `Undetermined` — a verdict
the skill defines, and requires evidence for. [AGT-009](#agt-009) already allows
a plan to "explicitly say the determination cannot be made"; the verifier now
applies that to part of a result as well as the whole, and still fails anything
left silently unaddressed or asserted beyond its evidence.

**A rejected attempt is told it was rejected.** The resume block said "ran out of
budget before finishing … do not re-gather what is already here" for *every*
carry. Told that, a worker whose result had been rejected reasonably skipped the
work the rejection asked for — including its required skill.

**A step's contract is satisfied once, not once per attempt.** Having skipped the
skill, the retry was failed for not calling it: the guard runs per attempt while
`required_action` is a property of the step. It is now remembered on the step,
so a later attempt is not failed for a contract an earlier one met. A first
attempt that skips its required action still fails.

**A dependency gets a budgeted share, not a fixed 2,000 characters.** The
19-finding list reached the dependent step truncated; the worker said so, and
the verifier held the incomplete coverage against it. Split across the step's
dependencies (`CHAT_ORCHESTRATOR_DEPENDENCY_CONTEXT_MAX_CHARS`, default 16,000),
and a slice now says it is one — silent truncation is how a step comes to report
missing coverage without knowing what it is missing.

## AGT-013 — A retry carries what the attempt fetched, not only what it wrote

**Applies to:** `chat_orchestrator._prepare_retries`, `_worker_user_message`

A failed step's retry resumes from `partial_output` when the worker wrote one,
and otherwise from a bounded digest of the calls it made and what they returned.

**Why:** the two conditions were mutually exclusive in practice. A worker cut at
its step ceiling never gets to write a partial summary — that is precisely what
produces `Step produced no output.`, which is what fails verification and sends
the step back for a retry. So the carry-forward path existed for a case that
could not reach it, and the retry re-gathered from scratch. Observed on a
reachability step: `output=0, partial_output=0, budget_capped=True`, and the
second attempt re-fetched files the first had already read.

**An interrupted attempt also leaves its full trace in the sandbox.** The
digest above is bounded by what fits in a prompt, which is the wrong shape for
what a step that made ninety calls has to hand on, so `_persist_step_record`
writes the whole trace to a file and records it as a receipt — the machinery
that already tells a delegation about result files then tells it about this
([SBX-008](sandbox.md)). Best-effort, and only into a sandbox that is already
open: it is a convenience for the next attempt, never a reason to open one or to
fail a step that has otherwise finished.

`tool_details` is thin for a *delegating* step — it records the delegations, not
the sub-agent's calls — so this helps a directly-working step most. The
sandbox layer has its own carry: the session digest and receipts already tell a
later delegation what is on disk ([SBX-008](sandbox.md)), which is why the
observed retry repeated 13 of 99 calls rather than all of them.

## AGT-012 — Running out of budget must not delete what the run already found

**Applies to:** `chat_orchestrator._dispatch_batch` (the degraded/finalizing
sweeps), `_budget_stop_result`, `_step_evidence`, `_rendered_step_status`

When the run budget enters finalization the dispatcher marks every unfinished
step `skipped`, which is what stops the retry loop. It must **annotate** the
step's existing result rather than replace it: the stub it used to write
(`output: ""`, `tools_used: []`, no `tool_details`) deleted the findings the
step had already gathered.

A step with retained findings is never *labelled* "skipped" either. `skipped`
is the routing status; as a label above real evidence it tells the reader to
discount it, which is the same failure one layer up.

**Why:** a CVE-exploitability run made 33 tool calls, read the repository's
manifests, lockfile and source, spent 302,679 input tokens — and answered "the
step was skipped and produced no output or supporting evidence". Nothing
hallucinated: the worker is killed at its share of the run budget *before* it
writes its summary, so the result carried evidence and an empty `output`; the
verifier failed it for having no summary; the retry pass met it at `failed` and
the sweep overwrote it. `_synthesis_context` forwards `tool_details` precisely
so a missing summary cannot take a step's findings down with it
([AGT-009](#agt-009) is the same concern from the planner's side), and the stub
deleted that safeguard's input. The checkpoint shows it exactly: `step_results`
went 110,930 bytes → 390 bytes → the answer. Replaying the real state through
the fixed path hands the synthesizer 12.5k characters of evidence instead of
"(no output)".

**Identical tool results are charged once.** A worker that re-runs a tool with
the same arguments records the result again, and an equal split of the evidence
budget then pays repeatedly for one fact while genuinely new evidence falls off
the end — 33 recorded calls, 25 distinct, on the run above.

**Don't:** treat "the budget ended the run" as "the run found nothing". The
terminal status is `budget_exhausted` and the answer must be the partial one the
evidence supports, with the limit stated.
