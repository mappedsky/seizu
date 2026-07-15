# Scheduled Chats

## Purpose

Scheduled chats run the chat agent on a recurring schedule with a prompt you write — no Cypher required, since the agent uses its chat tools to query the graph itself. Each run records a chat session whose full transcript (tool calls included) is reviewable from the Scheduled Chats page afterwards.

Typical uses: a daily digest of new critical CVEs, a weekly security posture summary, or a recurring check that files findings into a report.

## Managing scheduled chats

Schedules live in the **Scheduled Chats** page, linked from the app sidebar. Clicking a schedule's name opens its **detail view**: panels describing the schedule (status, trigger, owner, version, recent errors, and the prompt) alongside its runs, with an Edit button and an actions menu (history, delete) in the header. Schedules are personal: you only see and manage your own.

> **Permissions:** managing scheduled chats requires the `chat:schedule` permission (`seizu-editor` and above). The page and sidebar link are hidden without it, and the API rejects requests. Holders of `chat:schedule:read_all` (`seizu-admin`) additionally get a **Show all users** toggle that lists every user's schedules with an owner column and a per-user facet filter in the table toolbar (the same filter UI as the reports list), plus read access to their runs, transcripts, and version history — mutations always stay owner-only.

Every save creates a new configuration version; the history page (`/app/scheduled-chats/<id>/history`) lists versions with author and comment, and lets you restore an older one.

A schedule has:

| Field | Description |
|-------|-------------|
| name | Display name; also used as the chat session title prefix for each run. |
| prompt | Instructions for the agent. It runs headlessly as you, with your permissions, and can use chat tools to query the graph, render skills, and (with `chat:bypass_permissions`) create or update resources. |
| trigger | A **schedule** or **watch scans** (run when matching Cartography `SyncMetadata` records update — same semantics as scheduled query `watch_scans`; the fields autocomplete from the values present in the graph). |
| enabled | Whether the worker runs this schedule. |

Schedules repeat **hourly**, **daily**, or **monthly** (all times UTC):

- **Hourly** — every N hours, anchored to the last run; a new hourly schedule runs immediately.
- **Daily** — on selected days of the week at a chosen hour. A new schedule waits for its first selected occurrence (creating "Mondays at 09:00" on a Tuesday first runs the following Monday).
- **Monthly** — on selected days of the month (1–31) at 00:00. Months that don't have a selected day run on their **last day** instead (31 → Apr 30, Feb 28/29); the form warns when you select day 29, 30, or 31. Days that collapse to the same date (e.g. 30 and 31 in April) run once, not twice.

The list shows each schedule's trigger and the status of its last run; run errors are recorded on the schedule (last five).

### Running a schedule on demand

Choose **Run now** from a schedule's **⋮** menu — on a list row or on the detail view (owner only) — to request an immediate run without waiting for the trigger. The worker picks the request up on its next poll (`CHAT_SCHEDULES_POLL_SECONDS`) and runs the schedule as usual — **even if it is disabled**, so you can test a schedule before enabling it. The same is available via `POST /api/v1/chat/schedules/<id>/run`.

## Run sessions

Each run creates a chat session owned by the schedule's creator, but these sessions are kept out of the chat sidebar's session list so scheduled runs don't crowd out interactive conversations. The detail view lists a schedule's runs as collapsed entries; expanding one shows the full transcript — the prompt, the assistant's response, and an expandable **Details** section with the run's thinking and tool calls (arguments and output included). Run transcripts are **read-only**: the web UI cannot send messages to a scheduled session, and the API rejects attempts to continue one.

## How runs execute

The `seizu-scheduled-chats` worker (`python -m reporting.scheduled_chats`) polls for due schedules and runs each as a headless agent session **owned by the schedule's creator**:

- The creator's RBAC permissions apply to every tool call, resolved from the last role claim seen on one of their authenticated requests. Archived users' schedules stop running and record failures.
- Stored role claims do not refresh in the background. An identity-provider
  downgrade has an unbounded propagation delay until the creator next
  authenticates, so a previously granted `chat:bypass_permissions` may remain
  effective. Archive the Seizu user or disable their schedules when revocation
  must be immediate.
- Action confirmations are bypassed only while the creator holds `chat:bypass_permissions`; otherwise confirmation-gated tools fail closed for the run.
- The headless system prompt tells the model nobody is present: it won't ask for confirmation and summarizes any blocked action instead of retrying.
- A distributed lock guarantees one run per due window even with multiple workers.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_SCHEDULES_ENABLED` | `true` | Master switch: gates the API routes, the sidebar UI, and the worker. Requires `CHAT_ENABLED`. |
| `CHAT_SCHEDULES_POLL_SECONDS` | `20` | Worker polling interval. |
| `CHAT_SCHEDULE_TIMEOUT_SECONDS` | `600` | Timeout for one headless agent session. |

Scheduled and interactive runs use the same router and chat orchestrator.
A persisted run-level budget tracks input/output
tokens, estimated USD cost when LiteLLM knows the model price, LLM call count,
and usage by planner/worker step/synthesizer phase.
`CHAT_RUN_RESERVE_PERCENT` keeps part of the budget unavailable to
normal worker steps so final step summaries and synthesis can produce an
explicit partial result instead of stopping mid-plan.

The primary controls are `CHAT_RUN_TOKEN_BUDGET`,
`CHAT_RUN_COST_BUDGET_USD`, `CHAT_RUN_SOFT_LIMIT_PERCENT`, and
`CHAT_RUN_MAX_LLM_CALLS`. They apply equally to an interactive turn and the
same prompt executed by a schedule. Optional `CHAT_LLM_*_MODEL` role overrides
select separate planner, worker, verifier, and synthesizer models;
`CHAT_LLM_ECONOMY_MODEL` is used for eligible work after the soft limit.
`CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS` separately controls plan generation so
thinking models have enough output room to emit the final structured plan.

Run outcomes distinguish `success`, `partial`, `budget_exhausted`, `blocked`,
and `failure`. The transcript metadata includes the final budget ledger.

The worker needs the same chat configuration as the web app (`CHAT_LLM_*`, `CHAT_CHECKPOINT_*`); see the [chat assistant documentation](chat.html). Note that `CHAT_LLM_PROVIDER=mock` echoes input and cannot call tools, so meaningful runs need a real LLM provider.
