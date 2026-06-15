# Temporal Workflows

## Purpose

Scheduled query actions like `slack` or `sqs` are fire-and-forget. The `temporal` action instead hands the query results to a durable, multi-step [Temporal](https://temporal.io/) workflow executed by a dedicated Seizu worker. The first built-in workflow, `cve_repo_report`, runs an AI chat session per affected repository that evaluates newly discovered CVEs and creates or updates a versioned findings report.

## Architecture

```
seizu-scheduled-queries ── temporal action ──> Temporal server (task queue: seizu-workflows)
                                                        │
                                                        ▼
                                              seizu-temporal-worker
                                     (workflows + activities: headless AI chat
                                      sessions, skill rendering, report tools)
```

- The **scheduled query worker** dispatches matching query results to the `temporal` action, which starts a workflow run. The workflow ID embeds the scheduled query ID and run marker, so redelivery of the same run is idempotent.
- The **Temporal server** in local development is the lightweight CLI dev server (`temporal server start-dev`, in-memory). The Web UI is at `http://localhost:8233`.
- The **Seizu temporal worker** (`python -m reporting.temporal_worker`) hosts the workflow and activity code. Activities own all I/O: resolving the creator's identity, rendering skills, driving the chat agent, and storing results.

## Identity and permissions

A workflow runs **as the user who created the scheduled query**. The worker resolves that user's permissions from the last role claim observed on one of their authenticated requests (stored on the user profile), through the same RBAC resolution the request path uses. Consequences:

- Every tool call the AI session makes is checked against the creator's permissions, exactly as in interactive chat (including the `chat:tools:call` gate).
- If the creator is archived, workflow sessions fail and stop.
- Role claims are snapshots updated only by an authenticated Seizu request.
  A downgrade at the identity provider therefore does **not** immediately
  revoke headless permissions: the lag is unbounded until the creator next
  authenticates. During that interval a previously authorized
  `chat:bypass_permissions` grant may still be used. Operators must archive
  the Seizu user or disable their scheduled queries when immediate revocation
  is required.

## Confirmation bypass model

Interactive chat requires per-action confirmation before mutating tools run. A headless workflow has no approver, so confirmations are governed by the **`chat:bypass_permissions`** permission (granted to `seizu-editor` and `seizu-admin` by default):

- When the scheduled query's creator holds the permission, the workflow's AI sessions run with confirmations bypassed; every bypassed tool execution is audit-logged by `mcp_runtime`.
- When the creator does not hold it, confirmation-gated tools fail closed for the run; the headless system-prompt addendum tells the model to note the block in its summary and move on.
- Chat-safe gating (`chat_safe_only`) and the creator's RBAC permissions are enforced on top, unchanged.

The same permission gates the chat UI's optional "Bypass confirmations" mode and the `agent_chat` scheduled query action (see the [scheduled queries documentation](scheduled-queries.html)).

## The cve_repo_report workflow

Input: the scheduled query's result rows, each carrying at least a `repo` key (repository fullname). Per repository, sequentially:

1. Renders the `cve_response/cve_repo_assessment` skill with the repository name and its CVE rows.
2. Creates a workflow chat session owned by the creator and runs the full chat agent with the rendered skill as the first message. Workflow sessions are excluded from the interactive chat sidebar and cannot be continued through the chat API.
3. The agent creates the report `CVE Findings – {repo}` if missing and appends a new version with a dated markdown findings summary. Reports are versioned; prior findings are preserved.

A failing repository records an error in the workflow result without aborting the remaining repositories.

The CVE rows originate in the graph and are an untrusted prompt input. Seizu
JSON-encodes and HTML-escapes them inside an `<untrusted_cve_data>` block, then
prepends an instruction that the block is evidence rather than executable
instructions. Keep this boundary when adding fields or workflows. It reduces
prompt-injection risk but does not make graph data trusted; retain normal RBAC,
chat-safe tool filtering, result limits, and bypass audit logging.

Temporal activities use the same `run_headless_chat()` entry point as scheduled
chats. They therefore share token/cost accounting, role-specific model
selection, degradation behavior, terminal statuses, and the final budget
ledger. Temporal remains optional: scheduled chats and interactive chat do not
require a Temporal server.

The seeded scheduled query **New CVEs affecting repositories** watches the
mappedsky GitHub organization sync and selects open `SecurityIssue` nodes whose
ISO-8601 `created_at` value falls within the scan window. It then joins
their existing `CVEMetadata` records to produce per-repository rows. This
detects a newly observed repository exposure even when the CVE itself was
published earlier.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TEMPORAL_ADDRESS` | `localhost:7233` | Temporal frontend (gRPC) address. `temporal:7233` in docker compose. |
| `TEMPORAL_NAMESPACE` | `default` | Namespace workflows run in. |
| `TEMPORAL_TASK_QUEUE` | `seizu-workflows` | Task queue shared by the action module and the worker. |
| `TEMPORAL_WORKER_ENABLED` | `true` | Set `false` to disable the worker process. |
| `TEMPORAL_WORKFLOW_MAX_RESULT_ROWS` | `200` | Cap on result rows forwarded into a workflow. |
| `TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS` | `600` | Per-repository AI chat activity timeout. |

The worker also needs the chat configuration (`CHAT_LLM_*`, `CHAT_CHECKPOINT_*`) because it drives headless chat sessions. Note that `CHAT_LLM_PROVIDER=mock` echoes input and cannot call tools — exercising the CVE workflow end-to-end requires a real LLM provider.

## Local development

`make up` starts the `temporal` dev server and the `seizu-temporal-worker` service alongside the rest of the stack:

- Temporal Web UI: `http://localhost:8233` — inspect workflow runs (`seizu:cve_repo_report:<scheduled_query_id>:<run marker>`), activity retries, and results.
- The dev server is in-memory: workflow history is lost on restart, which is fine for the lightweight testing it is meant for.

To add a workflow: define the workflow + activities under `reporting/temporal_workflows/`, register them in `reporting/temporal_worker.py`, and add a `WorkflowSpec` (name, description, input factory) to `WORKFLOW_REGISTRY`. The factory converts the common scheduled-query context into that workflow's typed input; the description is surfaced in the action form's workflow picker. Use `reporting.services.headless_chat.run_headless_chat` for AI sessions so identity, confirmation, and audit handling stay consistent.
