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
