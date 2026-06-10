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
- A role change propagates to workflows on the creator's next authenticated request.

## Confirmation bypass model

Interactive chat requires per-action confirmation before mutating tools run. A headless workflow has no approver, so each registered workflow **declares** the confirmation-gated tools it intends to run (`reporting/temporal_workflows/WORKFLOW_REGISTRY`). The declaration is server-side code — it is never taken from user input.

- When configuring a `temporal` action, the form shows a warning and requires an acknowledgement checkbox (`accept_confirmation_bypass`). The scheduled query cannot be saved without it.
- At run time, a confirmation-gated tool **in** the declared list executes with an audit log entry; any other confirmation-gated tool fails closed.
- Chat-safe gating (`chat_safe_only`) and the creator's RBAC permissions are enforced on top, unchanged.

The `cve_repo_report` workflow declares only `reports__create_version` (`reports__create` is already allowed in chat without confirmation because it only creates a new private report).

## The cve_repo_report workflow

Input: the scheduled query's result rows, each carrying at least a `repo` key (repository fullname). Per repository, sequentially:

1. Renders the `cve_response/cve_repo_assessment` skill with the repository name and its CVE rows.
2. Creates a chat session owned by the creator (visible in their chat UI) and runs the full chat agent with the rendered skill as the first message. The agent verifies each CVE with the `cve_analysis`/`github_security` tools.
3. The agent creates the report `CVE Findings – {repo}` if missing and appends a new version with a dated markdown findings summary. Reports are versioned; prior findings are preserved.

A failing repository records an error in the workflow result without aborting the remaining repositories.

The seeded scheduled query **New CVEs affecting repositories** watches CVE metadata syncs (`watch_scans` on `SyncMetadata` with `grouptype: CVEMetadata`, `syncedtype: CVEMetadata`, `groupid: CVE_METADATA`) and joins recently published `CVEMetadata` against open `SecurityIssue` nodes to produce the per-repository rows.

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

To add a workflow: define the workflow + activities under `reporting/temporal_workflows/`, register them in `reporting/temporal_worker.py`, and add a `WorkflowSpec` (name, description, declared confirmation bypasses) to `WORKFLOW_REGISTRY`. Document any new bypass on the spec — the action form surfaces it to users.
