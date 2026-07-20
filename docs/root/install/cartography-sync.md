# Scheduled Cartography Syncs

## Purpose

Seizu reports over graph data loaded by [cartography](https://github.com/cartography-cncf/cartography). Running cartography by hand (`make sync_*`) works for development, but production deployments want syncs on a schedule, split across parallel runs, and ordered so data dependencies land first. The `cartography_sync` activity type provides that: a configurable workflow's cartography activity runs an ordered list of cartography intel-module runs, executed by a dedicated sync worker. Ordering across module groups comes from workflow stages; parallelism comes from placing multiple cartography activities in one stage.

## Architecture

```
configurable workflow ── cartography_sync activity ──> Temporal server
              (input-free child workflow)            │
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

1. Create a workflow with the desired time or watch trigger. No query input is required.
2. Add a `cartography_sync` activity with no input. Its fields:
   - **Intel modules** — an ordered list of module runs, executed sequentially one at a time. **Add intel module** appends a run; each run picks a registry module and exposes that module's allowlisted params as a sub-form. Place `create-indexes` first and `analysis` after your ingestion modules — they are ordinary selectable modules now, so you control where (and how often) they run. Put dependency-sensitive modules later in the list — `cve_metadata` enriches CVEs produced by earlier modules, so it must follow them.
   - **Stop on failure** — skip remaining module runs when one fails (for lists whose later runs depend on earlier data). Off by default: failures are recorded and the list continues.
   - **Per-module timeout (minutes)** — a run past it is terminated and marked failed.
3. Save. The config is validated against the module registry at save time; invalid modules and disallowed params are rejected with a specific error.

Nothing is added implicitly: mirror cartography's own execution model by running `create-indexes` before any ingestion and `analysis` after all of it. Each subprocess executes exactly one `--selected-modules` stage. For parallel syncs, put multiple cartography activities in the same workflow stage — the per-module mutex still serializes runs of the same module.

Run history (per-stage activity status, retries, failure output) appears in the workflow's **Workflow runs** panel and the Temporal Web UI. One schedule tick starts at most one workflow run (the workflow ID embeds the run marker).

## Concurrency control

Cartography warns that concurrent jobs for the same resource type race on their update tags and can delete each other's freshly loaded data. Scheduled syncs enforce serialization rather than relying on interval tuning:

- **Within one cartography activity**: module runs execute sequentially in the listed order, so repeats of a module never overlap.
- **Across everything else** — activities, workflows, overlapping ticks of one long-running schedule, and sync-worker replicas: every module run executes as a `cartography_module` child workflow whose **fixed workflow ID** (`seizu-cartography-module:{module}`) is the mutex. Temporal allows one open workflow per ID, so an overlapping run of the same module waits (the pipeline retries the start every 30s, up to `CARTOGRAPHY_MODULE_WAIT_SECONDS`) and then records a busy failure rather than racing. A crashed or terminated run releases the mutex the instant Temporal closes its workflow — no external lock state anywhere, exact release timing, and the wait is visible in the runs UI and the Temporal Web UI.

Different modules still run concurrently — the mutex is per module name (conservatively treating e.g. two `aws` runs with different `aws_requested_syncs` subsets as conflicting).

## Supported modules and options

The registry (`cartography_sync/registry.py`) defines each module's CLI mapping. Credentials always use Cartography's `--*-env-var` indirection with **fixed `CARTOGRAPHY_*` env-var names** — schedule authors never supply env-var names, file paths, or the Neo4j URI.

The registry covers every top-level intelligence module shipped in Cartography
0.139.0:

`airbyte`, `aibom`, `anthropic`, `aws`, `azure`, `bigfix`, `circleci`,
`cloudflare`, `crowdstrike`, `cve`, `cve_metadata`, `databricks`,
`digitalocean`, `docker_scout`, `duo`, `gcp`, `github`, `gitlab`,
`googleworkspace`, `gsuite`, `jamf`, `jumpcloud`, `kandji`, `keycloak`,
`kubernetes`, `lastpass`, `microsoft`, `oci`, `okta`, `ontology`, `openai`,
`pagerduty`, `salesforce`, `scaleway`, `semgrep`, `sentry`, `sentinelone`,
`slack`, `snipeit`, `socketdev`, `spacelift`, `subimage`, `syft`, `tailscale`,
`tenable`, `trivy`, `ubuntu`, `vercel`, `workday`, and `workos`, plus the
structural `create-indexes` and `analysis` stages.

Every canonical module-specific CLI option is represented. Typed value options
appear in the module-run form. Credential env-var-name options and path options
do not: the registry supplies those as reviewed constants. Deprecated aliases
(`entra` credential names and the legacy report-source flag groups) are not
emitted because their canonical replacements provide the same functionality.

The fixed paths are:

- AWS, Azure, and GCP permission maps: `/etc/cartography/*permission_relationships.yaml`
- GCP application credentials: `/etc/cartography/gcp-credentials.json`
- OCI configuration: `/var/cartography/.oci/config`
- Kubernetes kubeconfig: `/etc/cartography/kube.config`
- Docker Scout, Trivy, Syft, AIBOM, and Semgrep reports: `/etc/cartography/reports/<module>`
- custom analysis jobs: `/etc/cartography/analysis`

The compose services mount the report and analysis roots from
`.compose/cartography/`. All supported credential settings are listed in
`.env.example`; only populate credentials for enabled modules. Every
deployment-facing module credential is prefixed with `CARTOGRAPHY_`, so shared
Seizu chat, remediation, and integration credentials cannot be selected by
accident. Modules that support alternate authentication (Databricks,
Salesforce, Spacelift, and Tailscale) copy whichever declared alternatives are
present.

CLI `--*-env-var` options read the prefixed names directly. Only upstream SDKs
that mandate conventional names are translated in the scrubbed subprocess
environment, for example `CARTOGRAPHY_AWS_PROFILE` → `AWS_PROFILE` and
`CARTOGRAPHY_GOOGLE_APPLICATION_CREDENTIALS` →
`GOOGLE_APPLICATION_CREDENTIALS`. The dedicated worker itself receives only
the prefixed deployment setting.

GitHub's preferred setting is `CARTOGRAPHY_GITHUB_CONFIG`, containing the
complete base64-encoded Cartography GitHub configuration. The JSON inside it
contains the bare PAT in its `token` field. The historical
`CARTOGRAPHY_GITHUB_TOKEN` name remains a deprecated compatibility alias for
the same encoded configuration; it never means a bare token.

Existing deployments must rename the earlier unprefixed intel settings:
`NIST_NVD_TOKEN`, `CROWDSTRIKE_CLIENT_ID`, `CROWDSTRIKE_CLIENT_SECRET`,
`PAGERDUTY_API_KEY`, and `OKTA_API_KEY` now have the `CARTOGRAPHY_` prefix.
They are intentionally not read as fallbacks because doing so would recreate
the cross-service configuration collision this namespace prevents.

`CARTOGRAPHY_ENABLED_MODULES` narrows which modules schedules may use (empty → all). It is enforced three times: at save time, at dispatch, and again by the sync worker itself — the worker holds the credentials, so it rejects disabled modules even for a forged Temporal payload. Adding a module means adding a registry entry (name, flags, credential env vars) — a code change by design, so the allowlist stays reviewed.

The registry is reviewed against a specific cartography release, so the sync image pins the upstream by tag **and digest** (`Dockerfile.cartography`; Dependabot bumps it deliberately). After bumping the pin, run `make cartography_contract_test` — it verifies the version, the complete module set, and every canonical option module-by-module against the installed CLI.

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
- `CARTOGRAPHY_NEO4J_URI` (required) and, when Neo4j auth is on, `CARTOGRAPHY_NEO4J_USER` + `CARTOGRAPHY_NEO4J_PASSWORD` (translated to the upstream password name only in the subprocess environment; it never appears in argv)
- the enabled intel modules' credentials from `.env.example`
- `CARTOGRAPHY_BIN` (default `cartography`; used by tests)

## Security model

Schedule configuration reaches a shell-adjacent surface (the cartography CLI), so the pipeline is locked down in layers:

1. **No shell, ever.** The activity execs an argv list (`create_subprocess_exec`); every value is confined to a single `--flag=value` token, so no input can become its own argument.
2. **Registry allowlist.** Only registered modules and their declared flags are accepted; string values must match a per-flag pattern *and* a global character allowlist (no whitespace, quotes, or control characters).
3. **No user-controlled env-var names, paths, or URIs.** Credential flags and file paths are fixed constants; the Neo4j URI comes from the worker's own environment.
4. **Worker-side re-validation.** The activity payload carries `module` + `params`, never a command; the worker re-validates against the registry and rebuilds argv itself, so a forged Temporal payload cannot escape the allowlist.
5. **Environment scrubbing.** The cartography subprocess sees a minimal base environment plus only the env vars its module's registry entry declares.
6. **Secrets isolation.** The sync container holds only Cartography intel credentials — never Seizu chat keys, the GitHub remediation token, or report-store credentials — and the reporting workers never receive the intel credentials.
7. **Operator gates and RBAC.** `TEMPORAL_ENABLED_WORKFLOWS`, `CARTOGRAPHY_ENABLED_MODULES` (enforced at save, at dispatch, and again by the credential-bearing sync worker), and the usual `scheduled_queries:write` (admin-managed) requirement for creating or editing schedules.
8. **Reviewed, pinned CLI.** The image pins cartography by tag + digest so the flag allowlist can't drift against an unreviewed upstream; `make cartography_contract_test` re-verifies the registry against the pinned CLI.

## Local development

The `seizu-cartography-worker` compose service starts with `make up` and builds `Dockerfile.cartography` locally (`make build_cartography_worker` rebuilds it on demand, e.g. after bumping the pin or the temporalio version). Populate the cartography token vars in `.env` for the modules you want (the `cve` module needs no credentials and is the easiest smoke test). Then create the scheduled query above, hit **Run now**, and watch:

- `docker compose logs -f seizu-cartography-worker` — the sync subprocess output
- the Temporal Web UI at `http://localhost:8233` — workflow + activity state
- the query console — `MATCH (s:SyncMetadata) RETURN s` shows the sync landing

The one-off `cartography` compose service (`make sync_*`) still exists for manual runs.
