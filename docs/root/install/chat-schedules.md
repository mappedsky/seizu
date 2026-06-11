# Scheduled Chats

## Purpose

Scheduled chats run the chat agent on a recurring schedule with a prompt you write — no Cypher required, since the agent uses its chat tools to query the graph itself. Each run creates a regular chat session in your session list, so the full transcript (tool calls included) is reviewable afterwards.

Typical uses: a daily digest of new critical CVEs, a weekly security posture summary, or a recurring check that files findings into a report.

## Managing scheduled chats

Schedules live in the chat sidebar, in the **Schedules** section under your sessions. From there you can create, edit, enable/disable, and delete your scheduled chats. Schedules are personal: you only see and manage your own.

> **Permissions:** managing scheduled chats requires the `chat:schedule` permission (`seizu-editor` and above). The Schedules section is hidden without it, and the API rejects requests.

A schedule has:

| Field | Description |
|-------|-------------|
| name | Display name; also used as the chat session title prefix for each run. |
| prompt | Instructions for the agent. It runs headlessly as you, with your permissions, and can use chat tools to query the graph, render skills, and (with `chat:bypass_permissions`) create or update resources. |
| trigger | **Fixed frequency** (every N minutes) or **Watch scans** (run when matching Cartography `SyncMetadata` records update — same semantics as scheduled query `watch_scans`). |
| enabled | Whether the worker runs this schedule. |

The list shows each schedule's trigger and the status of its last run; run errors are recorded on the schedule (last five).

## How runs execute

The `seizu-scheduled-chats` worker (`python -m reporting.scheduled_chats`) polls for due schedules and runs each as a headless agent session **owned by the schedule's creator**:

- The creator's RBAC permissions apply to every tool call, resolved from the last role claim seen on one of their authenticated requests. Archived users' schedules stop running and record failures.
- Action confirmations are bypassed only while the creator holds `chat:bypass_permissions`; otherwise confirmation-gated tools fail closed for the run.
- The headless system prompt tells the model nobody is present: it won't ask for confirmation and summarizes any blocked action instead of retrying.
- A distributed lock guarantees one run per due window even with multiple workers.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_SCHEDULES_ENABLED` | `true` | Master switch: gates the API routes, the sidebar UI, and the worker. Requires `CHAT_ENABLED`. |
| `CHAT_SCHEDULES_POLL_SECONDS` | `20` | Worker polling interval. |
| `CHAT_SCHEDULE_TIMEOUT_SECONDS` | `600` | Timeout for one headless agent session. |

The worker needs the same chat configuration as the web app (`CHAT_LLM_*`, `CHAT_CHECKPOINT_*`); see `.env.example`. Note that `CHAT_LLM_PROVIDER=mock` echoes input and cannot call tools, so meaningful runs need a real LLM provider.
