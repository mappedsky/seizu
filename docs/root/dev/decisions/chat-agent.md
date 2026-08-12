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

**Stop names the turn, not the thread.** The request can be delayed or retried,
and by the time it lands the turn it was aimed at may have finished and the user
started another — a thread-addressed stop would then kill the successor. The
A turn therefore has **two** names, because a client holds them at different
times: `client_token`, which it mints before its send goes out, and `turn_id`,
which rides on the opening frame. Stop can also beat the turn it names into the
store entirely — the user presses it while the create is still in flight — so a
request that finds nothing running records a **tombstone** against the token,
and the create refuses on it *in the same transaction that would create the
turn*. Checking before that write would simply miss the race it exists for. Stop is enabled from the moment a message is
submitted, so without the token the whole window before the first frame did
nothing at all — while the detached producer, tool actions included, carried
on. A client that reconnected to a turn it did not start has only the id.
Either identifies the turn; naming neither is a 422.

**Don't:** let a second local cancel through. The producer clears its own
cancellation before running its terminal cleanup, so a repeat — a retried
request, or the heartbeat seeing the flag the first one set — lands *inside*
that cleanup and leaves the turn recorded as running forever. Cancellation is
first-writer-wins in-process (`_cancelling`), not merely idempotent over HTTP.

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
A turn whose producer is *provably* gone — its local task is done, or its lease
has lapsed — does not count as "did not stop", or a conversation orphaned by a
restart could be neither used nor deleted. A task cancelled before its coroutine
ever ran is the sharper case: none of the terminal cleanup happened, so the
done-callback finalizes the record rather than leaving deletion to wait out a
ten-minute lease.
The claim is re-claimable by design, so the session stays closed and the retry
is a plain repeat; a conversation half-removed from under a live producer cannot
be put back, and no cleanup undoes checkpoint state it recreates afterwards.
`delete_chat_turn` still collects batches **whether or not the header is
there** — a producer that outlived its conversation is the only thing that
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
  reads *past* live entries, and **persists where it got to**, so more pages of
  live entries than one pass can walk delays the ones behind rather than
  starving them. Reaching the end clears the cursor, which is what brings it
  back to entries that were live last time. Queries carry an explicit `Limit`,
  or one turn's completion could pull a megabyte of index and a `GetItem` per
  entry in it. The sweep entry also keeps its own copy of the lease, so a
  plainly-live turn is skipped without reading it (refreshed once, on finish —
  it can only ever be *early*, which costs a confirming read rather than a
  missed collection), and the sweep is paced per process by
  `CHAT_TURN_SWEEP_INTERVAL_SECONDS` rather than run on every completed turn.

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

**Admission and turn creation are one write.** The turn's half of the
retirement handshake ([SBX-011](sandbox.md#sbx-011)) happens *inside*
`create_chat_turn`, not before it. Touching the session first left a window: a
delete could read the fresh timestamp, claim the session, see no running turn
and cascade, all between the two — and the turn was then created against a
conversation that no longer existed. There is no separate touch on this path.

**Still open:** the producer is a detached task in the web process, so a restart
of `seizu` still ends the turns it was running — the client is now told, rather
than silently truncated. Moving the producer to the Temporal worker is the
remaining half, and `chat_turns.start_turn` is the seam for it. Note that a
retry there would be wrong for the same reason it is wrong for scheduled chats
([AGT-007](#agt-007)): a turn is expensive and not idempotent, and retrying
would append a second answer to the same log. The durability on offer is
reconnect and surviving a web-process restart, not automatic retry.
