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

## Run visibility in the UI

The scheduled query detail page shows a **Workflow runs** panel for queries with a `temporal` action: the most recent workflow executions (status, start/close time), and per run an expandable breakdown of its activities — status, attempt count (retries), retry state, failure messages (including the previous attempt's failure while retrying), and truncated input/result previews. The data is read live from Temporal:

- `GET /api/v1/scheduled-queries/<id>/workflow-runs` lists recent runs via a visibility query on the workflow ID prefix (`seizu:<workflow>:<scheduled_query_id>:`).
- `GET /api/v1/scheduled-queries/<id>/workflow-runs/<workflow_id>/<run_id>` folds the run's event history (plus pending-activity state for in-flight runs) into the activity breakdown.

Both are gated by `scheduled_queries:read` and refuse workflow IDs not minted for that scheduled query by a registered workflow. Activity **input/result previews** carry query-result rows and activity outputs — more than the scheduled-query definition — so they are included only for callers who also hold `scheduled_queries:write` (Editor+); readers get status, timing, attempts, and failure detail without payloads. The **web service** therefore needs `TEMPORAL_ADDRESS`/`TEMPORAL_NAMESPACE` reachable; when Temporal is down the endpoints return 503 and the panel shows an error. Queries without a temporal action return an empty list (and 404 on run detail) without contacting Temporal, so deployments that don't use Temporal are unaffected. The dev server keeps history in memory, so runs disappear on its restart; for deeper digging (full event payloads, worker state) the Temporal Web UI remains the tool of choice.

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
headless coding-agent CLI (`SANDBOX_AGENT_PROVIDER`: Claude Code by default,
`codex`, or `opencode` — the last is multi-provider, so it can drive DeepSeek and
others). The agent works on a bot-owned branch keyed on
the target version (`seizu/dependency-update/{ecosystem}-{package}-{version}`,
Dependabot-style; a short CVE-set hash replaces the version when no fixed
version is known): it upgrades the dependency in every affected manifest —
including any code changes needed for compatibility, not just a version bump —
and writes the PR title and body. The agent does **not** run the test suite (the
sandbox usually lacks its dependencies); CI runs the tests on the pull request.
The workflow then pushes the branch and opens (or updates) the pull request.

Before the agent runs, a guard checks whether an open PR already exists for the
branch and, if so, skips the run entirely (status `skipped`) — so repeated
syncs that re-select the same pending fix don't re-run the expensive agent or
force-push over a PR under review. Because the branch is version-keyed, a
*later* fix that needs a higher version gets its own branch and PR instead of
colliding with (or clobbering) the earlier one.

Two deliberate policies shape the result:

- **Least-change upgrades.** The agent targets the smallest released version
  that clears every vulnerable range, preferring the current version family
  (same major, then same minor); it crosses a major version only when no fixed
  release exists in the current family.
- **Routine-looking PRs.** The agent is prompted to present the change as an
  ordinary dependency bump (`Bump requests from 2.31.0 to 2.32.4`) without CVE
  identifiers — a Dependabot-style convention. This is prompt guidance only,
  not enforced: CVE ids are public and discoverable from the repo's dependency
  manifest anyway, so it is a nicety rather than a security control.

### Phase-isolated credentials

The run is split into four sandbox commands, each receiving only the
environment it needs (per-command env injection — secrets are never set on the
sandbox itself):

1. **install** — install `gh` and the coding-agent CLI. *No secrets*: npm
   packages execute third-party postinstall scripts. The step is idempotent
   (`command -v … ||`), so with a prebuilt template (see below) it is a near
   no-op, and on a plain base image it installs the tools.
2. **setup** — clone the repository and create the work branch. *GitHub token
   only*, supplied to `gh auth setup-git`, which configures git to authenticate
   through `gh`'s credential helper reading the token from this command's
   environment. No token is written to disk and none is embedded in the clone
   URL, so nothing token-derived persists into the agent phase.
3. **agent** — run the coding-agent CLI with permission prompts disabled.
   *Provider API key only* — **no GitHub token exists anywhere in the sandbox
   during this phase**, so a prompt-injected agent (repository contents and
   advisory text are untrusted input) has no token to exfiltrate and no
   ability to push.
4. **push** — verify the agent committed changes, force-push the bot-owned
   branch, and create or find the PR. *GitHub token only.*

Still keep branch protection on — nothing lands without human PR review — and
scope the token as described in [GitHub token setup](#github-token-setup).
Workflow-supplied values (repo, branch names) are strictly validated and reach
the phase scripts only via environment variables; tokens are masked in all
captured output.

### Fork-based pull requests

By default the push phase writes the work branch into the target repository,
which requires `contents:write` there. With `REMEDIATION_USE_FORK=true` the
worker instead ensures a bot-owned fork of each target repository — created on
demand through the GitHub API, under `REMEDIATION_FORK_ORG` when set, else the
token user's account — the push sandbox pushes the branch to that fork, and
the PR is opened cross-repo (`fork-owner:branch` → target base branch). The
token no longer needs write access to the target repositories — but this mode
requires a different kind of token and account; see
[GitHub token setup](#github-token-setup). Before each push the fork's base
branch is best-effort synced with upstream (GitHub's "sync fork" API) so the
shallow push finds its ancestor objects; CI-fix runs clone and push the PR
branch on the fork. The credential phase isolation above is identical in both
modes.

Two operational caveats: the token owner must be able to fork the target (a
repository cannot be forked into the account that already owns it), and many
organizations restrict GitHub Actions on PRs from forks (no secrets, or
`approve-first` policies) — if the CI watch keeps reporting `no_checks` on
fork PRs, check the target's Actions fork policy.

### GitHub token setup

Which token to use — and where it needs access — differs between direct
(branch) mode and fork mode. In both modes `REMEDIATION_GITHUB_TOKEN` is used
by the setup phase (clone), the push phase (push + `gh pr create`), and
worker-side by the CI watch and fork management; it is never present while the
coding agent runs.

**Direct mode (default)** — work branches are pushed into the target
repositories, so the token needs write access there. Use a **fine-grained
PAT**, minimally scoped:

1. Create the PAT with **resource owner** = the user/org that owns the target
   repositories, and grant **repository access** to only the repositories the
   workflow should remediate. (Organizations may require fine-grained PAT
   approval: *Org settings → Third-party access → Personal access tokens*.)
2. Repository permissions:
   - **Contents: Read and write** — clone and push the work branches.
   - **Pull requests: Read and write** — open PRs; read PR state for the CI
     watch.
   - **Issues: Read and write** — the CI watch posts triage comments through
     the issue-comments API.
   - **Checks: Read** and **Commit statuses: Read** — the CI watch polls
     check runs and legacy commit statuses.
   - **Actions: Read** — job-log tails for the fix agent's failure context
     (optional; the watch degrades gracefully without it).
   - **Workflows: Read and write** — only if remediation/fix commits may
     touch `.github/workflows/` files; GitHub rejects pushes modifying
     workflow files without it.
   - Metadata: Read is added automatically.
3. Keep branch protection on — nothing merges without human review.

A classic PAT with `repo` scope also works for direct mode but grants far
more than needed.

**Fork mode** — a fine-grained PAT cannot span this flow across owners: it
has a single resource owner, so it cannot both write to the bot's forks and
open PRs on targets under a different owner (and creating a fork additionally
requires its **Administration: write** permission, since a fork is a
repository creation). Use a dedicated **machine account** with a **classic
PAT** instead:

1. Create a machine account used only for this automation (e.g.
   `myorg-remediation-bot`) — the standard pattern for classic-PAT bots,
   explicitly permitted by GitHub's terms of service. Forks accumulate under
   it, or under `REMEDIATION_FORK_ORG` if you point that at an org the
   account can create repositories in.
2. Logged in as the machine account, create a **classic** PAT with scope
   **`public_repo`** (all targets public) or **`repo`** (any target private);
   add **`workflow`** if fixes may modify `.github/workflows/` files. Classic
   scopes are coarse — `repo`/`public_repo` covers forking, pushing to the
   bot's own forks, opening cross-repo PRs, and everything the CI watch reads
   and posts.
3. For private targets, grant the machine account read (pull) access on each
   target repository and enable the target org's *Allow forking of private
   repositories* setting.
4. Check the target org's policies: classic PATs must be allowed by its
   personal access token policy, and with SAML SSO enforced the token must be
   authorized for the org.

A GitHub App installation token also works in either mode if you have
external tooling to mint and rotate it — Seizu just reads whatever token is
in `REMEDIATION_GITHUB_TOKEN`.

Whichever token you use, verify it before enabling the workflow with the
[smoke test](#verifying-the-remediation-flow): `make remediation_smoke
SMOKE_REPO=org/repo` (add `SMOKE_FORK=1` for fork mode).

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

### CI watch and fix

Pushing a PR is not the end of the run: the upgrade can break the repository's
CI. After each freshly pushed PR, the workflow watches the PR's check suite
(check runs plus legacy commit statuses) with durable Temporal timers — a
short read-only GitHub API activity polls every `REMEDIATION_CI_POLL_SECONDS`
until the checks settle or `REMEDIATION_CI_MAX_WAIT_SECONDS` elapses (`0`
disables the watch). Checks that sit in *queued* longer than
`REMEDIATION_CI_QUEUED_STUCK_SECONDS` (no runner coming — offline self-hosted
runner, disabled app) are ignored rather than waited on, as are cancelled and
stale runs; a PR that gets merged or closed mid-watch ends it. A `skipped`
remediation (the guard found an existing open PR) is not re-watched — the run
that pushed it already was.

When every non-ignored check has finished and at least one failed, the
workflow runs a CI-fix activity (at most `REMEDIATION_CI_FIX_MAX_ATTEMPTS`
coding-agent runs per PR; `0` → watch and record only). The fix run reuses the
same phase-isolated two-sandbox flow against the existing PR branch, with the
failing checks' output summaries, annotations, and log tails in the prompt as
untrusted data. The agent triages each failure:

- **Caused by the upgrade** → it fixes the code/tests and commits; only its
  new commits are extracted and fast-forward pushed from a fresh push sandbox,
  which re-triggers CI and resumes the watch.
- **Unrelated to the upgrade** (flaky test, failure already on the base
  branch, infrastructure error) → it writes a PR-comment file explaining why,
  per check; the worker posts it through the GitHub API (the agent still has
  no credentials), and the watch ends. The agent's text is never posted
  verbatim: it is rendered into a fixed template as a block quote, with
  `@`-mentions and line-leading slash commands neutralized and its length
  capped, so a prompt-injected agent cannot ping people or drive
  slash-command bots under the bot identity.

The per-dependency workflow result records the outcome in `ci_status`
(`passed`, `fixed`, `failures_commented`, `ci_failed`, `fix_failed`,
`timed_out`, `no_checks`, `merged`, `pr_closed`, or `error`) with detail in
`ci_detail`.

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
| `SANDBOX_AGENT_PROVIDER` | `claude` | Coding-agent CLI: `claude` (Claude Code), `codex`, or `opencode`. `opencode` is multi-provider — set `SANDBOX_AGENT_MODEL` to a `provider/model` id (e.g. `deepseek/deepseek-v4-pro`) and it uses that provider's key, reusing the same global `*_API_KEY` (e.g. `DEEPSEEK_API_KEY`) the chat assistant uses. For opencode, an explicit `SANDBOX_AGENT_API_KEY` must belong to the model's provider — an Anthropic key with a `deepseek/…` model is exported as `DEEPSEEK_API_KEY` and fails auth. |
| `SANDBOX_AGENT_TEMPLATE` | `""` (official) | E2B sandbox template. Empty → the provider's official prebuilt template (E2B ships first-party `claude`/`codex`/`opencode` images with the CLI installed), which removes the per-run `npm install` and its postinstall scripts from the flow. A template name → that template (e.g. a self-pinned copy). `none` → the plain base image (the run installs the CLI itself). Ignored on self-hosted backends (`SANDBOX_DOMAIN` set): E2B templates are a cloud feature, and the idempotent install step covers those. The template provides tools only, never credentials, so the phase isolation above is unchanged. |
| `SANDBOX_AGENT_API_KEY` | `""` | Static API key for the CLI, exported only to the agent phase. Empty → falls back to `ANTHROPIC_API_KEY` for `claude`. Prefer the key command below. |
| `SANDBOX_AGENT_API_KEY_COMMAND` | `""` | Command run in the worker before each remediation; its stdout becomes that run's agent API key. Use it to mint **short-lived** credentials from a broker (Vault, an LLM-gateway virtual-key issuer, …) instead of handing the sandbox a long-lived key. Takes precedence over the static key. **Recommended for production:** unlike the GitHub token (kept out of the agent sandbox), the agent's provider key is present while it runs untrusted repo code with internet on, so a long-lived key is stealable — the worker logs a warning when a static key is used. |
| `SANDBOX_AGENT_BASE_URL` | `""` | LLM gateway/proxy base URL exported to the agent phase (`ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL`); typically paired with the key command so the sandbox only ever holds a short-lived gateway key. Mutually exclusive with the credential proxy below. |
| `SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED` | `false` | Run a short-lived LiteLLM proxy in its **own** sandbox holding the real provider key, and hand the agent sandbox only the proxy's ephemeral per-run key with an in-memory spend cap (see below). All providers (`opencode` needs `SANDBOX_AGENT_MODEL` set). |
| `SANDBOX_AGENT_CREDENTIAL_PROXY_MAX_BUDGET` | `5` | USD spend cap (LiteLLM's in-memory global `max_budget`) — bounds real-time abuse of the ephemeral key while the proxy is up. |
| `SANDBOX_AGENT_MODEL` | `""` | Model for the CLI. For `claude`/`codex` a bare model override (empty → the CLI's default). For `opencode` it is **required** and takes the `provider/model` form (e.g. `deepseek/deepseek-v4-pro`), which also selects the provider key and — in credential-proxy mode — the LiteLLM namespace. |
| `REMEDIATION_TIMEOUT_SECONDS` | `1800` | Hard cap for one remediation run (all sandbox phases). Also caps each CI-fix run. |
| `REMEDIATION_CI_MAX_WAIT_SECONDS` | `3600` | Total time the workflow watches one PR's checks (including re-runs after a fix push). `0` disables the CI watch. |
| `REMEDIATION_CI_POLL_SECONDS` | `120` | Interval between check-status polls. |
| `REMEDIATION_CI_QUEUED_STUCK_SECONDS` | `1800` | A check still queued (never started) after this long is ignored by the watch instead of waited on. |
| `REMEDIATION_CI_FIX_MAX_ATTEMPTS` | `1` | Coding-agent CI-fix runs allowed per PR (each is a full sandbox agent session — this bounds spend). `0` → watch and record the outcome but never fix. |
| `REMEDIATION_GH_SHA256` | `""` | Expected SHA-256 of the pinned `gh` linux_amd64 tarball. Set it (out of band) for an independent supply-chain pin, or bake `gh` into a pinned sandbox image — since the installed `gh` later handles the token. Empty → verify against the release's own checksums (integrity only). |
| `REMEDIATION_GITHUB_HOST` | `github.com` | GitHub host the target repositories live on — `github.com` or a GitHub Enterprise Server hostname. Used for the clone URL and `gh` (`GH_HOST`/`GH_ENTERPRISE_TOKEN`). |
| `REMEDIATION_GITHUB_TOKEN` | `""` | GitHub token for the setup/push phases and the worker-side CI watch: a minimally-scoped fine-grained PAT in direct mode, a machine-account classic PAT in fork mode — see [GitHub token setup](#github-token-setup). Required (configured = enabled). |
| `REMEDIATION_USE_FORK` | `false` | Push the work branch to a bot-owned fork (created on demand) and open cross-repo PRs instead of writing branches into the target repositories — see [Fork-based pull requests](#fork-based-pull-requests). |
| `REMEDIATION_FORK_ORG` | `""` | Organization that owns the bot forks in fork mode; empty → forks live under the token user's account. |
| `REMEDIATION_GIT_USER` / `REMEDIATION_GIT_EMAIL` | `seizu-remediation-bot` / `seizu-remediation@localhost` | git author identity for the remediation commits. |

The remediation workflow also uses the sandbox provider configuration
(`SANDBOX_API_KEY`, `SANDBOX_DOMAIN`; see [Sandbox delegation](sandbox.md)) —
`SANDBOX_ENABLED` and the chat tool are not required.

The worker also needs the chat configuration (`CHAT_LLM_*`, `CHAT_CHECKPOINT_*`) for workflows that drive headless chat sessions (`cve_repo_report`); for that workflow, `CHAT_LLM_PROVIDER=mock` echoes input and cannot call tools, so exercising it end-to-end requires a real LLM provider. The `cve_dependency_remediation` workflow does not use the chat LLM at all — it needs the sandbox provider (`SANDBOX_API_KEY`/`SANDBOX_DOMAIN`) and `REMEDIATION_*` configuration instead.

### Ephemeral credential-proxy sandbox

The agent's provider key is the one credential that must be present while the agent runs untrusted repo code (the GitHub token is kept out entirely). `SANDBOX_AGENT_API_KEY_COMMAND` mitigates this with a short-lived per-run key, but for Anthropic/OpenAI the direct API has no short-lived token, so that needs an external LLM gateway. `SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=true` runs that gateway self-contained: a **separate** sandbox boots a LiteLLM proxy seeded with the real key (the agent VM never sees it), and the agent sandbox is pointed at the proxy with only the proxy's **ephemeral master key** — a random per-run key that exists only inside that sandbox. (Per-key minting via LiteLLM's `/key/generate` needs a Postgres database we don't provision, and a run uses a single key anyway; instead the proxy config sets an in-memory global spend cap from `SANDBOX_AGENT_CREDENTIAL_PROXY_MAX_BUDGET`.) Because the proxy sandbox is torn down with the run, that key's effective lifetime equals the run — a leak is worthless afterward, and the budget cap bounds real-time abuse while it's up. All three providers support it (for `opencode`, set `SANDBOX_AGENT_MODEL` so the LiteLLM namespace can be derived from the model's provider prefix), and it is mutually exclusive with `SANDBOX_AGENT_BASE_URL`.

The proxy sandbox stays **private** (`allow_public_traffic: false`): the agent CLI reaches it using E2B's traffic-access token sent as a custom request header, so the proxy port is never world-reachable. Each provider carries that header differently — Claude Code via `ANTHROPIC_CUSTOM_HEADERS`; codex via a written `~/.codex/config.toml` `model_provider` with `env_http_headers`; opencode via a written `@ai-sdk/openai-compatible` provider block with `options.headers`. (A hypothetical provider with no header support would fall back to a public port gated only by the ephemeral key.)

This path stands up LiteLLM inside a sandbox and depends on each CLI talking to it with a custom header and correct model routing; **verify it against your CLI/LiteLLM versions with a real run before enabling in production** (`make remediation_smoke SMOKE_PROXY=1` probes exactly this — it boots the private proxy, mints a key, and confirms a second sandbox can reach it via the traffic-access header). The LiteLLM ↔ agent-CLI wire compatibility (model routing, endpoint shape, header passthrough) is the fragile part.

## Local development

`make up` starts the `temporal` dev server and the `seizu-temporal-worker` service alongside the rest of the stack:

- Temporal Web UI: `http://localhost:8233` — inspect workflow runs (`seizu:cve_repo_report:<scheduled_query_id>:<run marker>`), activity retries, and results.
- The dev server is in-memory: workflow history is lost on restart, which is fine for the lightweight testing it is meant for.

To add a workflow: define the workflow + activities under `reporting/temporal_workflows/`, register them in `reporting/temporal_worker.py`, and add a `WorkflowSpec` (name, description, input factory) to `WORKFLOW_REGISTRY`. The factory converts the common scheduled-query context into that workflow's typed input; the description is surfaced in the action form's workflow picker. Use `reporting.services.headless_chat.run_headless_chat` for AI sessions so identity, confirmation, and audit handling stay consistent.

## Verifying the remediation flow

Unit tests mock the sandbox, so they confirm the phase/credential logic but **not** that a coding-agent CLI, `gh`, and git actually authenticate and push inside a real sandbox. A couple things that are easy to miss:

- **The temporal worker does not hot-reload.** `seizu-temporal-worker` runs `python -m reporting.temporal_worker` with no `--reload`, so it imports the remediation code once at startup. After changing `sandbox_remediation.py` (or any workflow code), restart it or the next run silently uses the old code: `docker compose restart seizu-temporal-worker` (dev, bind-mounted) or rebuild the image (if not bind-mounted).
- **A public target repo hides auth failures until push.** Cloning a public repo is anonymous, so the GitHub token is first exercised on `git push`. When testing, always push a branch — a successful clone proves nothing about the token.

To smoke-test the sandbox side directly (auth, gh, git, and the two-sandbox patch handoff) without running the full workflow, use the bundled `make remediation_smoke SMOKE_REPO=org/repo` (`scripts/remediation_smoke.py`). It opens two real E2B sandboxes with the configured `SANDBOX_*`/`REMEDIATION_*` settings and reproduces the credential-sensitive mechanics: an *agent sandbox* installs `gh`, clones (token), makes a throwaway commit **with no token in the environment** (standing in for the coding agent), and extracts the change as a base64 git diff; a fresh *push sandbox* (which never ran the "agent") applies the patch and pushes the branch with the token. It then deletes the branch. It does not run a real coding agent or open a PR; a `SMOKE PASS` line confirms the handoff and auth end-to-end. In direct mode, check the token's write access first with `gh api repos/<org>/<repo> --jq .permissions` — `push` must be `true`; see [GitHub token setup](#github-token-setup) for the full permission list per mode.

With `REMEDIATION_USE_FORK=true` (or `SMOKE_FORK=1` to force it for one run) the smoke test exercises the fork path instead: it ensures the bot fork through the real `ensure_fork` (creation, readiness poll, `REMEDIATION_FORK_ORG`), and the push sandbox syncs the fork's base branch (merge-upstream) and pushes — then deletes — the branch on the **fork**. `SMOKE_REPO` then only needs read/fork access, not push; cross-repo PR creation itself is not exercised (the smoke test never opens PRs).
