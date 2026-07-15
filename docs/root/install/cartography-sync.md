# Scheduled Cartography Syncs

## Purpose

Seizu reports over graph data loaded by [cartography](https://github.com/cartography-cncf/cartography). Running cartography by hand (`make sync_*`) works for development, but production deployments want syncs on a schedule, split across parallel runs, and ordered so data dependencies land first. The `cartography_sync` Temporal workflow provides that: a scheduled query's `temporal` action starts a staged pipeline of cartography intel-module runs, executed by a dedicated sync worker.

## Architecture

```
seizu-scheduled-queries ── temporal action ──> Temporal server
        (schedule trigger, e.g. RETURN 1)            │
                                                     ▼
                                        seizu-temporal-worker
                                     (cartography_sync workflow:
                                      stages sequential, runs parallel)
                                                     │ task queue: seizu-cartography
                                                     ▼
                                       seizu-cartography-worker
                                  (upstream cartography image + thin
                                   Temporal activity worker; runs the
                                   cartography CLI as a subprocess)
```

Two workers, two task queues:

- The **workflow** runs in the existing `seizu-temporal-worker` and contains orchestration only.
- Each **module run** is a `run_cartography_module` activity dispatched to the `CARTOGRAPHY_TASK_QUEUE` (default `seizu-cartography`), served by the dedicated `seizu-cartography-worker` container built from `Dockerfile.cartography` (published as `ghcr.io/mappedsky/seizu-cartography`). That image contains the upstream cartography CLI plus the small `cartography_sync` worker package — none of the Seizu backend, and none of its secrets.

Because cartography bumps `SyncMetadata` on completion, scheduled syncs compose with the existing **watch scans** trigger: a reporting scheduled query watching the module's `SyncMetadata` group fires automatically after each sync run.

## Configuring a scheduled sync

1. Create a scheduled query with a trivial query such as `RETURN 1` — the query is only the trigger; the workflow consumes no result rows.
2. Add a `temporal` action and pick the `cartography_sync` workflow. Extra fields appear:
   - **Modules** — a list of intel modules, run one at a time in order (one stage each). The common case.
   - **Pipeline (JSON)** — the full staged form, mutually exclusive with Modules: stages run sequentially, runs within a stage in parallel, and each run may set the module's allowlisted params. Put dependency-sensitive modules in later stages — `cve_metadata` enriches CVEs produced by earlier modules, so it must follow them:

     ```json
     {"stages": [
       {"runs": [{"module": "aws", "params": {"aws_sync_all_profiles": true}},
                 {"module": "github"}]},
       {"runs": [{"module": "cve_metadata"}]}
     ]}
     ```

   - **Stop on failure** — skip remaining stages when a module run fails (for pipelines whose later stages depend on earlier data). Off by default: failures are recorded and the pipeline continues.
   - **Per-module timeout (minutes)** — a run past it is terminated and marked failed.
3. Save. The config is validated against the module registry at save time; invalid modules, disallowed params, and duplicate modules within a stage are rejected with a specific error.

Every pipeline is automatically wrapped in two implicit stages mirroring cartography's own execution model: `create-indexes` runs once before any ingestion, and `analysis` runs once after all of it — parallel module runs never repeat them, and analysis never runs mid-ingestion. Each subprocess executes exactly one `--selected-modules` stage.

Run history (per-stage activity status, retries, failure output) appears in the scheduled query's **Workflow runs** panel and the Temporal Web UI. One schedule tick starts at most one workflow run (the workflow ID embeds the run marker).

## Concurrency control

Cartography warns that concurrent jobs for the same resource type race on their update tags and can delete each other's freshly loaded data. Scheduled syncs enforce serialization rather than relying on interval tuning:

- **Within a stage**: a module may appear at most once per stage (rejected at save time).
- **Across everything else** — stages, schedules, overlapping ticks of one long-running schedule, and sync-worker replicas: every module run executes as a `cartography_module` child workflow whose **fixed workflow ID** (`seizu-cartography-module:{module}`) is the mutex. Temporal allows one open workflow per ID, so an overlapping run of the same module waits (the pipeline retries the start every 30s, up to `CARTOGRAPHY_MODULE_WAIT_SECONDS`) and then records a busy failure rather than racing. A crashed or terminated run releases the mutex the instant Temporal closes its workflow — no external lock state anywhere, exact release timing, and the wait is visible in the runs UI and the Temporal Web UI.

Different modules still run concurrently — the mutex is per module name (conservatively treating e.g. two `aws` runs with different `aws_requested_syncs` subsets as conflicting).

## Supported modules

The registry (`cartography_sync/registry.py`) defines each module's CLI mapping. Credentials always use cartography's `--*-env-var` indirection with **fixed env-var names** — schedule authors never supply env-var names, file paths, or the Neo4j URI.

| Module | Credentials (worker env) | User-settable params |
|---|---|---|
| `aws` | mounted AWS config (`/srv/cartography/.aws/config`) or `AWS_*` env | `aws_sync_all_profiles` (bool, default true), `aws_requested_syncs` |
| `github` | `GITHUB_TOKEN` (base64 JSON config) | — |
| `cve` | — | — |
| `cve_metadata` | `NIST_NVD_TOKEN` | — |
| `crowdstrike` | `CROWDSTRIKE_CLIENT_ID` + `CROWDSTRIKE_CLIENT_SECRET` | — |
| `kubernetes` | mounted kubeconfig (`/etc/cartography/kube.config`) | — |
| `okta` | `OKTA_API_KEY` | `okta_org_id` |
| `pagerduty` | `PAGERDUTY_API_KEY` | — |

`CARTOGRAPHY_ENABLED_MODULES` narrows which modules schedules may use (empty → all). It is enforced three times: at save time, at dispatch, and again by the sync worker itself — the worker holds the credentials, so it rejects disabled modules even for a forged Temporal payload. Adding a module means adding a registry entry (name, flags, credential env vars) — a code change by design, so the allowlist stays reviewed.

The registry is reviewed against a specific cartography release, so the sync image pins the upstream by tag **and digest** (`Dockerfile.cartography`; Dependabot bumps it deliberately). After bumping the pin, run `make cartography_contract_test` — it verifies inside the image that every registry flag still exists in that release's CLI.

## Settings

On the **web service** and **scheduled query worker** (see `.env.example`):

- `CARTOGRAPHY_TASK_QUEUE` (default `seizu-cartography`)
- `CARTOGRAPHY_ENABLED_MODULES` (default: all registered)
- `CARTOGRAPHY_MODULE_TIMEOUT_SECONDS` (default 3600)
- `CARTOGRAPHY_MODULE_WAIT_SECONDS` (default 3600; see Concurrency control)
- `CARTOGRAPHY_SYNC_RETRY_ATTEMPTS` (default 2; configuration errors never retry)

The workflow must also be dispatchable: leave `TEMPORAL_ENABLED_WORKFLOWS` empty or include `cartography_sync`.

The **sync worker** reads plain env vars (it does not use Seizu settings):

- `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `CARTOGRAPHY_TASK_QUEUE`
- `CARTOGRAPHY_ENABLED_MODULES` (the worker-side allowlist; keep it aligned with the web/dispatcher value)
- `CARTOGRAPHY_NEO4J_URI` (required) and, when Neo4j auth is on, `CARTOGRAPHY_NEO4J_USER` + `NEO4J_PASSWORD` (the password rides only in the subprocess environment, never in argv)
- the intel-module credentials from the table above
- `CARTOGRAPHY_BIN` (default `cartography`; used by tests)

## Security model

Schedule configuration reaches a shell-adjacent surface (the cartography CLI), so the pipeline is locked down in layers:

1. **No shell, ever.** The activity execs an argv list (`create_subprocess_exec`); every value is confined to a single `--flag=value` token, so no input can become its own argument.
2. **Registry allowlist.** Only registered modules and their declared flags are accepted; string values must match a per-flag pattern *and* a global character allowlist (no whitespace, quotes, or control characters).
3. **No user-controlled env-var names, paths, or URIs.** Credential flags and file paths are fixed constants; the Neo4j URI comes from the worker's own environment.
4. **Worker-side re-validation.** The activity payload carries `module` + `params`, never a command; the worker re-validates against the registry and rebuilds argv itself, so a forged Temporal payload cannot escape the allowlist.
5. **Environment scrubbing.** The cartography subprocess sees a minimal base environment plus only the env vars its module's registry entry declares.
6. **Secrets isolation.** The sync container holds only cartography intel credentials — no LLM keys, GitHub remediation token, or report-store credentials — and the reporting workers never receive the intel credentials.
7. **Operator gates and RBAC.** `TEMPORAL_ENABLED_WORKFLOWS`, `CARTOGRAPHY_ENABLED_MODULES` (enforced at save, at dispatch, and again by the credential-bearing sync worker), and the usual `scheduled_queries:write` (admin-managed) requirement for creating or editing schedules.
8. **Reviewed, pinned CLI.** The image pins cartography by tag + digest so the flag allowlist can't drift against an unreviewed upstream; `make cartography_contract_test` re-verifies the registry against the pinned CLI.

## Local development

The `seizu-cartography-worker` compose service starts with `make up` and builds `Dockerfile.cartography` locally (`make build_cartography_worker` rebuilds it on demand, e.g. after bumping the pin or the temporalio version). Populate the cartography token vars in `.env` for the modules you want (the `cve` module needs no credentials and is the easiest smoke test). Then create the scheduled query above, hit **Run now**, and watch:

- `docker compose logs -f seizu-cartography-worker` — the sync subprocess output
- the Temporal Web UI at `http://localhost:8233` — workflow + activity state
- the query console — `MATCH (s:SyncMetadata) RETURN s` shows the sync landing

The one-off `cartography` compose service (`make sync_*`) still exists for manual runs.
