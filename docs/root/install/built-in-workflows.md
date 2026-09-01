# Built-in workflows

## Purpose

Seizu ships code-defined workflows that a [workflow](workflows.html) can use as
an activity type, alongside the generic `query`, `slack`, `sqs` and `log`
activities. Each one is a Temporal workflow in its own right, started and
awaited as a child of the workflow that names it:

| Activity type | What it does |
|---------------|--------------|
| `agent_chat` | Runs an AI session against a prompt you supply, optionally over an earlier stage's rows. |
| `cve_repo_report` | Runs an AI session per affected repository that assesses new CVEs and writes a findings report. |
| `cve_dependency_remediation` | Opens pull requests that upgrade vulnerable dependencies. Documented separately in [CVE remediation](cve-remediation.html). |
| `cartography_sync` | Runs cartography intel modules. See [Cartography sync](cartography-sync.html). |

`TEMPORAL_ENABLED_WORKFLOWS` controls which are offered in the workflow editor
and accepted at dispatch; unset means all of them. Settings live with
[workflow configuration](workflows.html#configuration).

## Architecture

```
stored workflow ── Temporal Schedule ──> Temporal server (task queue: seizu-workflows)
                                                │
                                                ▼
                                      seizu-temporal-worker
                              (configurable parent + code-defined children
                               + activities and schedule reconciliation)
```

- A **Temporal Schedule** starts the configurable parent workflow. Stages run sequentially, activities within a stage run in parallel, and an activity whose type names a code-defined workflow starts and awaits it as a child workflow.
- The **Temporal server** in local development is the lightweight CLI dev server (`temporal server start-dev`, in-memory). The Web UI is at `http://localhost:8233`.
- The **Seizu temporal worker** (`python -m reporting.temporal_worker`) hosts the workflow and activity code. Activities own all I/O: resolving the creator's identity, rendering skills, driving the chat agent, and storing results. It also reconciles schedules in the background, including the `seizu-session-reap` Schedule that retires idle chat sessions and the sandboxes they hold (see [Sandbox delegation](sandbox.md)).

## Identity and permissions

A workflow runs **as the user who created the workflow definition**. The worker resolves that user's permissions from the last role claim observed on one of their authenticated requests (stored on the user profile), through the same RBAC resolution the request path uses. Consequences:

- Every tool call the AI session makes is checked against the creator's permissions, exactly as in interactive chat (including the `chat:tools:call` gate).
- If the creator is archived, workflow sessions fail and stop.
- Role claims are snapshots updated only by an authenticated Seizu request.
  A downgrade at the identity provider therefore does **not** immediately
  revoke headless permissions: the lag is unbounded until the creator next
  authenticates. During that interval a previously authorized
  `chat:bypass_permissions` grant may still be used. Operators must archive
  the Seizu user or disable their workflows when immediate revocation
  is required.

## Confirmation bypass model

Interactive chat requires per-action confirmation before mutating tools run. A headless workflow has no approver, so confirmations are governed by the **`chat:bypass_permissions`** permission (granted to `seizu-editor` and `seizu-admin` by default):

- When the workflow's creator holds the permission, the workflow's AI sessions run with confirmations bypassed; every bypassed tool execution is audit-logged by `mcp_runtime`.
- When the creator does not hold it, confirmation-gated tools fail closed for the run; the headless system-prompt addendum tells the model to note the block in its summary and move on.
- Chat-safe gating (`chat_safe_only`) and the creator's RBAC permissions are enforced on top, unchanged.

The same permission gates the chat UI's optional "Bypass confirmations" mode, the `agent_chat` workflow activity, and scheduled chats.

## Run visibility in the UI

The workflow detail page shows recent Temporal parent and child executions.
The canonical `/api/v1/workflows/<id>/runs` endpoints expose the execution and
activity history; legacy scheduled-query run endpoints remain aliases.

- `GET /api/v1/workflows/<id>/runs` lists recent parent and child runs using the definition's Temporal workflow-ID prefix.
- `GET /api/v1/workflows/<id>/runs/<workflow_id>/<run_id>` folds the run's event history (plus pending-activity state for in-flight runs) into the activity breakdown.

Both are gated by `workflows:read` and refuse workflow IDs outside the selected
definition's prefix. The web service therefore needs
`TEMPORAL_ADDRESS`/`TEMPORAL_NAMESPACE` reachable. The dev server keeps
history in memory, so runs disappear on restart; for full event payloads and
worker state, use the Temporal Web UI.

**"Completed with errors":** a code-defined workflow activity can finish
without raising while still reporting a partial failure it deliberately chose
not to retry (one failed dependency remediation, one PR whose CI never went
green, one repo's CVE chat session erroring, a cartography module run that
failed). `WorkflowSpec.summarize_output` inspects that activity's own result
and surfaces `completed_with_errors` for it instead of a blanket `completed`;
`ConfiguredWorkflow` in turn records the overall run (`last_run_status`) as
`success_with_errors` rather than `success` when any activity reports this.
Both values are gated by a `workflow.patched(...)` marker so histories
recorded before this existed keep replaying deterministically as `success`.

## The agent_chat workflow

The `agent_chat` activity includes a **Model profile** selector. Empty follows
the current default; an explicit profile is validated when the workflow is
saved. Each run snapshots the resolved profile before its child workflow is
started, so later profile edits affect only later runs.

The general-purpose AI activity: instead of a fixed task, you supply the
prompt. Use it to run an agent step inside a larger pipeline — triage what a
query returned, write a summary report, or act on another activity's output.

- **Input is optional.** With no input reference it just runs the prompt. When
  it does reference an earlier stage's output, those rows are passed to the
  agent as untrusted evidence (JSON-encoded and escaped inside an
  `<untrusted_graph_data>` block behind an explicit security-boundary
  preamble), never as instructions. `max_rows` and `query_return_attribute`
  control which rows reach the prompt.
- **Output is named**, so a later stage can consume it: `status`, `thread_id`,
  `summary`, `error`, and the run's `budget`.
- **Config:** `prompt` (required), `session_title`, `skill` (an optional stored
  skill rendered into the prompt as `skillset__skill`, with its required tools
  pre-unlocked), and `timeout_minutes`.
- Like every headless surface it runs as the workflow's creator, under their
  RBAC, with confirmations bypassed only when they hold
  `chat:bypass_permissions`. The run is not retried — an agent session is
  expensive and non-idempotent — and a run that ends anything but cleanly
  (failed, blocked, out of budget) reports `completed_with_errors`.

A worked example:

```yaml
stages:
  - activities:
      - type: query
        output: stale_admins
        parameters:
          cypher: |
            MATCH (u:User) WHERE u.admin AND u.last_login < $cutoff
            RETURN {user: u.email, last_login: u.last_login} AS details
          parameters: [{name: cutoff, value: "2026-01-01"}]
  - activities:
      - type: agent_chat
        input: stale_admins
        output: admin_review
        parameters:
          prompt: |
            Review these dormant admin accounts and write a short risk summary
            into the report "Dormant admin review", creating it if missing.
          session_title: Dormant admin review
          timeout_minutes: 10
```

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

Temporal activities use the same `run_agent_session()` entry point as scheduled
chats (`reporting/services/agent_run.py`, which wraps `run_headless_chat()`).
They therefore share identity resolution, the untrusted-evidence boundary,
token/cost accounting, role-specific model selection, degradation behavior,
terminal statuses, and the final budget ledger. Scheduled chats also run on
Temporal (see the [scheduled chats documentation](chat-schedules.html)), so a
Temporal server is required for them; interactive chat is not.

The seeded example workflow **New CVEs affecting repositories** watches the
mappedsky GitHub organization sync and selects open `SecurityIssue` nodes whose
ISO-8601 `created_at` value falls within the scan window. It then joins
their existing `CVEMetadata` records to produce per-repository rows. This
detects a newly observed repository exposure even when the CVE itself was
published earlier.

## The cve_dependency_remediation workflow

Where `cve_repo_report` *assesses*, `cve_dependency_remediation` *fixes*: it
drives an ephemeral sandbox to run a headless coding-agent CLI that upgrades a
vulnerable dependency, then opens a pull request. It has its own credential,
GitHub and CI setup — see [CVE remediation](cve-remediation.html).

## Local development

`make up` starts the `temporal` dev server and the `seizu-temporal-worker` service alongside the rest of the stack:

- Temporal Web UI: `http://localhost:8233` — inspect workflow runs (`seizu:cve_repo_report:<scheduled_query_id>:<run marker>`), activity retries, and results.
- The dev server is in-memory: workflow history is lost on restart, which is fine for the lightweight testing it is meant for.

To add a workflow: define the workflow + activities under `reporting/temporal_workflows/`, register them in `reporting/temporal_worker.py`, and add a `WorkflowSpec` (name, description, input factory) to `WORKFLOW_REGISTRY`. A registered workflow automatically becomes its own top-level activity type in the workflow editor; the factory converts the common workflow context into its typed input, and the description is surfaced next to the activity-type picker. Use `reporting.services.headless_chat.run_headless_chat` for AI sessions so identity, confirmation, and audit handling stay consistent.
