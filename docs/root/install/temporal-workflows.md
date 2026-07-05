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

## The cve_dependency_remediation workflow

Where `cve_repo_report` *assesses*, `cve_dependency_remediation` *fixes*: per
(repository, vulnerable dependency package) group, the workflow activity drives
an ephemeral sandbox directly (no chat session, no MCP tool) and runs a
headless coding-agent CLI (Claude Code by default; `codex` also supported via
`REMEDIATION_AGENT_PROVIDER`). The agent works on a deterministic bot-owned
branch (`seizu/dependency-update/{ecosystem}-{package}`): it upgrades the
dependency in every affected manifest — including any code changes needed for
compatibility, not just a version bump — runs the repository's test suite, and
writes the PR title and body. The workflow then pushes the branch and opens
(or updates) the pull request.

Two deliberate policies shape the result:

- **Least-change upgrades.** The agent targets the smallest released version
  that clears every vulnerable range, preferring the current version family
  (same major, then same minor); it crosses a major version only when no fixed
  release exists in the current family.
- **No CVE references in public artifacts.** Like Dependabot, the branch name,
  commits, PR title, and body present the change as a routine dependency bump
  (`Bump requests from 2.31.0 to 2.32.4`), without CVE identifiers or advisory
  links — a PR is public until merged, and naming the vulnerability would
  advertise the unpatched window. The CVE context lives in Seizu (workflow
  results, audit logs), not in the PR.

### Phase-isolated credentials

The run is split into four sandbox commands, each receiving only the
environment it needs (per-command env injection — secrets are never set on the
sandbox itself):

1. **install** — install `gh` and the coding-agent CLI. *No secrets*: npm
   packages execute third-party postinstall scripts. The step is idempotent
   (`command -v … ||`), so with a prebuilt template (see below) it is a near
   no-op, and on a plain base image it installs the tools.
2. **setup** — clone the repository and create the work branch. *GitHub token
   only*, consumed by a process-scoped git credential helper (`git -c`), so
   nothing token-derived is written to disk.
3. **agent** — run the coding-agent CLI with permission prompts disabled.
   *Provider API key only* — **no GitHub token exists anywhere in the sandbox
   during this phase**, so a prompt-injected agent (repository contents and
   advisory text are untrusted input) has no token to exfiltrate and no
   ability to push.
4. **push** — verify the agent committed changes, force-push the bot-owned
   branch, and create or find the PR. *GitHub token only.*

Still keep branch protection on and scope `REMEDIATION_GITHUB_TOKEN` to the
target org/repos with only `contents:write` + `pull_requests:write` — nothing
lands without human PR review. Workflow-supplied values (repo, branch names)
are strictly validated and reach the phase scripts only via environment
variables; tokens are masked in all captured output.

### Behavior and access control

Input rows must carry `repo` and `package` keys; multiple CVEs (or manifests)
for the same package in one repository collapse into one run and one PR.
Groups run sequentially to bound concurrent coding-agent spend, and the
per-group activity uses `maximum_attempts=1`: a retry would repeat a very
expensive run and risk duplicate PRs. Manual re-runs are safe — the
deterministic branch name means the run updates the existing PR instead of
opening another. A failing group records an error without aborting the rest.

There is no per-user permission and no dedicated enable flag for this
workflow: it has direct, fixed targets and is reachable only through scheduled
queries, which are admin-managed (`scheduled_queries:write`). It runs only
when configured (`REMEDIATION_GITHUB_TOKEN` plus an agent API key); operators
turn it off by disabling the scheduled query, removing the configuration,
dropping `cve_dependency_remediation` from `TEMPORAL_ENABLED_WORKFLOWS` (while
keeping other workflows), or removing the `temporal` module from
`SCHEDULED_QUERY_MODULES` entirely. The scheduled
query's creator is still resolved before each run, so archived users hard-stop
their automations, and each run is audit-logged against them.

The seeded scheduled query **New CVE dependencies requiring remediation** uses
the same watch-scan trigger and `SecurityIssue.created_at` window as the
assessment query, additionally requires `dependency_package_name`, and returns
per-dependency rows (ecosystem, manifest path, vulnerable range, patched
version) that the remediation prompt is built from.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TEMPORAL_ADDRESS` | `localhost:7233` | Temporal frontend (gRPC) address. `temporal:7233` in docker compose. |
| `TEMPORAL_NAMESPACE` | `default` | Namespace workflows run in. |
| `TEMPORAL_TASK_QUEUE` | `seizu-workflows` | Task queue shared by the action module and the worker. |
| `TEMPORAL_WORKER_ENABLED` | `true` | Set `false` to disable the worker process. |
| `TEMPORAL_WORKFLOW_MAX_RESULT_ROWS` | `200` | Cap on result rows forwarded into a workflow. |
| `TEMPORAL_ENABLED_WORKFLOWS` | `""` (all) | Comma-separated allowlist of workflows the temporal action may start. Enabling the temporal module otherwise makes every registered workflow dispatchable; this narrows it (e.g. `cve_repo_report` to allow assessment but not remediation). The workflow picker only offers enabled workflows and dispatch refuses disabled ones. Set it on both the web service (picker) and the scheduled query worker (enforcement). |
| `TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS` | `600` | Per-repository AI chat activity timeout. |
| `REMEDIATION_AGENT_PROVIDER` | `claude` | Coding-agent CLI: `claude` (Claude Code), `codex`, or `opencode`. `opencode` is multi-provider — set `REMEDIATION_AGENT_MODEL` to a `provider/model` id (e.g. `deepseek/deepseek-chat`) and it uses that provider's key, reusing the same global `*_API_KEY` (e.g. `DEEPSEEK_API_KEY`) the chat assistant uses. |
| `REMEDIATION_SANDBOX_TEMPLATE` | `""` (official) | E2B sandbox template. Empty → the provider's official prebuilt template (E2B ships first-party `claude`/`codex` images with the CLI installed), which removes the per-run `npm install` and its postinstall scripts from the flow. A template name → that template (e.g. a self-pinned copy). `none` → the plain base image (the run installs the CLI itself). Ignored on self-hosted backends (`SANDBOX_DOMAIN` set): E2B templates are a cloud feature, and the idempotent install step covers those. The template provides tools only, never credentials, so the phase isolation above is unchanged. |
| `REMEDIATION_AGENT_API_KEY` | `""` | Static API key for the CLI, exported only to the agent phase. Empty → falls back to `ANTHROPIC_API_KEY` for `claude`. Prefer the key command below. |
| `REMEDIATION_AGENT_API_KEY_COMMAND` | `""` | Command run in the worker before each remediation; its stdout becomes that run's agent API key. Use it to mint **short-lived** credentials from a broker (Vault, an LLM-gateway virtual-key issuer, …) instead of handing the sandbox a long-lived key. Takes precedence over the static key. |
| `REMEDIATION_AGENT_BASE_URL` | `""` | LLM gateway/proxy base URL exported to the agent phase (`ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL`); typically paired with the key command so the sandbox only ever holds a short-lived gateway key. |
| `REMEDIATION_AGENT_MODEL` | `""` | Model override for the CLI (Claude Code's `ANTHROPIC_MODEL`). Empty → the CLI's default. |
| `REMEDIATION_TIMEOUT_SECONDS` | `1800` | Hard cap for one remediation run (all sandbox phases). |
| `REMEDIATION_GITHUB_HOST` | `github.com` | GitHub host the target repositories live on — `github.com` or a GitHub Enterprise Server hostname. Used for the clone URL and `gh` (`GH_HOST`/`GH_ENTERPRISE_TOKEN`). |
| `REMEDIATION_GITHUB_TOKEN` | `""` | Fine-grained PAT used only by the setup and push phases. Required (configured = enabled). |
| `REMEDIATION_GIT_USER` / `REMEDIATION_GIT_EMAIL` | `seizu-remediation-bot` / `seizu-remediation@localhost` | git author identity for the remediation commits. |

The remediation workflow also uses the sandbox provider configuration
(`SANDBOX_API_KEY`, `SANDBOX_DOMAIN`; see [Sandbox delegation](sandbox.md)) —
`SANDBOX_ENABLED` and the chat tool are not required.

The worker also needs the chat configuration (`CHAT_LLM_*`, `CHAT_CHECKPOINT_*`) for workflows that drive headless chat sessions (`cve_repo_report`); for that workflow, `CHAT_LLM_PROVIDER=mock` echoes input and cannot call tools, so exercising it end-to-end requires a real LLM provider. The `cve_dependency_remediation` workflow does not use the chat LLM at all — it needs the sandbox provider (`SANDBOX_API_KEY`/`SANDBOX_DOMAIN`) and `REMEDIATION_*` configuration instead.

## Local development

`make up` starts the `temporal` dev server and the `seizu-temporal-worker` service alongside the rest of the stack:

- Temporal Web UI: `http://localhost:8233` — inspect workflow runs (`seizu:cve_repo_report:<scheduled_query_id>:<run marker>`), activity retries, and results.
- The dev server is in-memory: workflow history is lost on restart, which is fine for the lightweight testing it is meant for.

To add a workflow: define the workflow + activities under `reporting/temporal_workflows/`, register them in `reporting/temporal_worker.py`, and add a `WorkflowSpec` (name, description, input factory) to `WORKFLOW_REGISTRY`. The factory converts the common scheduled-query context into that workflow's typed input; the description is surfaced in the action form's workflow picker. Use `reporting.services.headless_chat.run_headless_chat` for AI sessions so identity, confirmation, and audit handling stay consistent.
