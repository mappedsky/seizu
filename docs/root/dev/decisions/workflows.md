# Workflow decisions (`WF`)

Decisions behind the Temporal pipelines, the cartography sync worker, and CVE
dependency remediation. For configuration, see
[temporal workflows](../../install/temporal-workflows.md) and
[cartography sync](../../install/cartography-sync.md).

Primary code: `reporting/temporal_workflows/`, `cartography_sync/`,
`reporting/services/sandbox_remediation.py`,
`reporting/services/sandbox_agent.py`, `reporting/services/github_checks.py`.

## WF-001 — Workflows are deterministic; all I/O lives in activities

**Applies to:** `reporting/temporal_workflows/`

Workflow code uses dataclasses and pure helpers from `shared.py` only.
Everything that touches the world belongs in `activities.py`, and AI sessions go
through `headless_chat.run_headless_chat`.

**Why:** Temporal replays workflow code. Non-determinism there is not a bug you
find in testing.

`ChatStepFanoutWorkflow` follows the same split at a finer grain: it decides
*which* plan steps run concurrently and does nothing else, while every model
call, tool call, store write and sandbox operation stays in the activity it
schedules ([AGT-018](chat-agent.md#agt-018)).

## WF-002 — Code-defined workflows are top-level activity types

**Applies to:** `WORKFLOW_REGISTRY`, `workflows.normalized_stages`

Each registered workflow is its own activity type, and the activity starts it as
an awaited child workflow. The former `workflow`/`temporal` dispatcher module is
removed; stored activities using the old `type: workflow` sub-type are migrated
on read, and new saves reject it.

**Why:** a dispatcher module meant the activity type said nothing about what
would run, so neither validation nor the UI could reason about it.

## WF-003 — The cartography registry is the security boundary

**Applies to:** `cartography_sync/registry.py`

Per-module typed flag allowlists, fixed credential env-var names and paths,
argv-list exec (**never** a shell), and a scrubbed subprocess env.

**The activity re-validates params, re-enforces `CARTOGRAPHY_ENABLED_MODULES`,
and rebuilds argv worker-side.** That is what makes a forged Temporal payload
unable to escape the allowlist — the caller's argv is never trusted.

`cartography_sync` must **not** import `reporting.*` (that pulls pydantic
settings): it reads plain env vars. `registry`/`shared` stay stdlib-only;
`activities`/`worker` may use temporalio.

The worker runs as a separate image on its own task queue, holding only
cartography intel credentials.

## WF-004 — One module per subprocess, with a fixed workflow ID as the mutex

**Applies to:** `cartography_module` child workflows

Each subprocess runs exactly one `--selected-modules` stage, as a child workflow
whose fixed ID (`seizu-cartography-module:{module}`) is the per-module mutex.

**Why:** concurrent same-module syncs race on cartography's update tags. The
pipeline waits up to `CARTOGRAPHY_MODULE_WAIT_SECONDS`.

`create-indexes` and `analysis` are ordinary selectable modules that users place
explicitly — nothing is injected implicitly.

## WF-005 — Remediation uses two sandboxes so the GitHub token never meets untrusted code

**Applies to:** `reporting/services/sandbox_remediation.py`

*Agent sandbox:* install (no secrets) → clone/branch (GH token via
`gh auth setup-git`, never on disk or in a URL, **pre-agent**) → guard (skip if
an open PR exists) → agent run (**provider key only, never the GH token**) →
extract the change as a base64 git diff.

*Fresh push sandbox* (never ran the agent, npm, or tests): apply the patch to a
clean clone, commit, push, `gh pr create`.

**Why two:** an agent-planted git hook or PATH shadow in the first sandbox
cannot reach the token, because the token is only ever present in a VM that
never executed repository code. Per-command env isolation
(`run_bash_streaming(envs=...)`) enforces the split within each.

Branches are version-keyed (`seizu/dependency-update/{eco}-{pkg}-{version}`,
hash fallback) so same-fix re-runs converge and later different-version fixes
get distinct PRs.

`REMEDIATION_USE_FORK` pushes to a bot-owned fork instead and opens the PR
cross-repo, so the token needs no write access to target repos.

## WF-006 — CI watching and PR comments happen worker-side, never in the sandbox

**Applies to:** `reporting/services/github_checks.py`

Durable timers plus a read-only worker-side client poll the PR's CI every
`REMEDIATION_CI_POLL_SECONDS` up to `REMEDIATION_CI_MAX_WAIT_SECONDS` (0
disables), ignoring checks queued past `REMEDIATION_CI_QUEUED_STUCK_SECONDS` and
cancelled/stale runs.

On settled failures, up to `REMEDIATION_CI_FIX_MAX_ATTEMPTS` fix-mode sessions
run with the same two-sandbox isolation: check out the existing PR branch,
extract only new commits, fast-forward push — **no force, no `gh pr create`**.

Where the failure is unrelated to the upgrade, the agent writes a PR-comment
file. It is posted worker-side through a **fixed sanitized template** —
block-quoted, @-mentions and slash-commands neutralized, length-capped — and
**never verbatim**. The agent never gets credentials.

**Why:** the agent's output is untrusted text. Posting it verbatim under the
bot's identity would let repository content drive GitHub automation.

## WF-007 — Remediation is enabled by configuration, not by a flag or a permission

**Applies to:** `sandbox_remediation`, `SANDBOX_AGENT_*`

Configured (`REMEDIATION_GITHUB_TOKEN` + an agent key) means enabled. There is
no per-user permission and no enable flag, because scheduled queries are
admin-managed and `scheduled_queries:write` is re-checked per run.

**Agent credentials are exposed to untrusted repository code**, so:

- `SANDBOX_AGENT_API_KEY_COMMAND` mints short-lived per-run keys
  (**recommended**; a static key warns).
- `SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED` instead runs a LiteLLM proxy in a
  *third* sandbox holding the real key, handing the agent only the proxy's
  ephemeral master key, which dies at teardown. Per-key `/key/generate` needs a
  DB we don't run, so the config sets an in-memory `max_budget` cap instead.
  That proxy sandbox stays private (`allow_public_traffic=false`), reached via
  E2B's traffic-access token sent as a custom header per
  `SubagentProvider.proxy_transport`.

**Unverified:** the LiteLLM↔CLI wire. `make remediation_smoke SMOKE_PROXY=1`
probes it — smoke-test before production.

Keeping CVE ids out of PRs is prompt-only (they are public). Workflow-supplied
repo/branch values are regex-validated and reach scripts only via env vars. PR
review is the gate.

## WF-008 — The proxy sandbox runs a hash-locked requirement set

**Applies to:** `sandbox_agent.proxy_install_plan`,
`reporting/services/sandbox_proxy_requirements.txt`,
`SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE` / `_TEMPLATE`

**The requirement set is a hash-locked file, not a requirement string.** There
is no setting naming what to install: `make lock_proxy_requirements` compiles a
fully resolved, hashed lock (whose header records the file, requirements and
runtime it came from, so re-locking needs no arguments and cannot overwrite a
different lock — a configured lock that cannot be read from the maintenance
container is an error, never a silent fallback to the checked-in one), and
`_REQUIREMENTS_FILE` chooses *which* lock. A
requirement string alongside a lock is a second source of truth that can
silently disagree with it — the earlier design did exactly that, and a bumped
pin quietly downgraded the install to top-level-only.

It reaches the sandbox one of two ways, and these are separate concerns:

- **A template** (`SANDBOX_AGENT_CREDENTIAL_PROXY_TEMPLATE`): an image the
  operator supplied. The run uses it **as built** — no install, and no
  inspection of what it contains. Its only contract is that it can run a LiteLLM
  proxy.
- **No template:** the run provisions the base image itself, installing the lock
  with `pip --no-deps --require-hashes` and importing
  `litellm.proxy.proxy_server` before reporting success. It installs
  unconditionally rather than skipping when a LiteLLM is already present.

`build_proxy_template` builds from the same `proxy_install_plan()`, so *our*
template contains what a templateless run would install.

**A lock is valid only if every recorded field is present** — requirements,
python, machine, platform, hashes. Partial acceptance means each consumer
invents the rest, and the failure is destructive rather than loud: re-locking a
lock with no recorded requirements compiles *nothing* over it, and one with no
recorded platform quietly retargets an ARM lock at x86_64. `_parse_proxy_lock`
is the single definition, and it names what is missing.

**A lock is only valid for the runtime it was resolved for** — its hashes cover
wheels for one python ABI and architecture — so the header records them and the
install compares the sandbox against them **before running pip**, failing with
a re-lock instruction. Otherwise a base-image upgrade, or a self-hosted
`SANDBOX_DOMAIN` backend on another architecture, produces a wall of "no
matching distribution" inside a sandbox nobody is watching. Locks for other
runtimes are a supported configuration, not a fork:
`make lock_proxy_requirements PYTHON_VERSION=… PLATFORM=… OUTPUT=…`.

**The target runtime is measured rather than declared** when `SANDBOX_API_KEY`
is available: the generator opens a real templateless sandbox and reads its
python and architecture. Declaring it is how the lock came to target python 3.11
(the `e2bdev/base` image) while sandboxes run 3.13 — a discrepancy nothing could
catch before install time. The recorded *platform* still wins over a measurement
when the architecture is unchanged, because `uname -m` cannot distinguish
gnu from musl.

**Why:** the original `command -v litellm || pip install 'litellm[proxy]'` was
a dependency-resolution time bomb. LiteLLM's proxy extra allows a range of
FastAPI versions, FastAPI removed `get_flat_dependant` — which LiteLLM's
proxy imports — and every remediation run started failing with nothing changed
here. The presence check made it worse: it would happily use an unrelated
LiteLLM baked into an image.

Pinning only the top level does not close that: LiteLLM leaves FastAPI,
pydantic, aiohttp, openai and httpx on ranges, so the same failure mode survives
one level down. This sandbox holds the **real provider key**, so what executes
in it should be a fixed set of artifacts, not a resolution.

**FastAPI is pinned alongside LiteLLM, and the pin is a ceiling.** LiteLLM 1.96.0
asks for `fastapi<1.0,>=0.136.3` and still imports `get_flat_dependant`, which
FastAPI dropped in **0.140.7** — so resolving its range freely picks a FastAPI
whose proxy cannot import, exactly the original failure. `0.140.6` is the newest
that works; a security bump of LiteLLM's transitive tree therefore re-locks as
`REQUIREMENTS="litellm[proxy]==<v> fastapi==<newest still exporting it>"`. Check
whether a newer LiteLLM has dropped that import before raising the FastAPI pin —
and prove the pair with an install + `litellm.proxy.proxy_server` import in a
real templateless sandbox, because nothing else catches it.

Three details are non-obvious, and each was found by a failure rather than by
reading:

- `uv pip compile --no-config`, or this project's own `[tool.uv]`
  constraint-dependencies are applied to the sandbox's resolution — where they
  make it unsolvable against the lock's exact FastAPI pin.
- The resolution targets the **sandbox's** interpreter, on linux x86_64. An E2B
  sandbox with no template runs python **3.13**; the `e2bdev/base` docker image
  is **3.11**; neither is this project's. A lock built for the wrong one
  installs fine locally and fails in the sandbox, because the hashes cover
  wheels for another ABI — which is why `build_proxy_template` builds from
  `python:<the lock's python>` rather than a fixed base image, and why the
  target is recorded in the lock and re-checked in the sandbox.
  `make lock_proxy_requirements` runs in `seizu-temporal-worker`, the service
  that holds the proxy configuration.
- `pip install --no-deps`. The lock is the complete closure, so pip has nothing
  to resolve — and the base image's pip (23.2.1) otherwise rejects the whole
  install because `mcp` names `pyjwt[crypto]>=…`, which that version treats as
  unpinned even though the lock pins `pyjwt`.

The import check exists because the failure mode without it is bad: the CLI dies
in a backgrounded `nohup`, the phase reports a health-check timeout two minutes
later, and the real `ImportError` is only in a log tail. Bumping the pin is a
deliberate act — verify with `make remediation_smoke SMOKE_PROXY=1`.

The same validation is what makes the operator-supplied list safe to word-split
unquoted in the fallback install command; it is re-checked in `credential_proxy`
so direct callers cannot skip it.

The install phase was briefly kept for templated runs too (pip short-circuits on
satisfied pins, so a drifted template would self-correct). That was dropped
deliberately: it conflated two ownership models. Building an image *is* the
operator saying "this is the environment"; re-installing over it at run time
makes the template advisory and hides which set actually ran.

**A template is deliberately not verified against the lock**, either — no marker
file, no digest comparison. The checked-in lock is one valid answer, not the
definition of a correct proxy: it drifts from upstream by design, and an
operator may legitimately want a newer LiteLLM, or an image built from something
else entirely. Requiring a match would make `build_proxy_template` the only
supported way to have a template, which is not the intent. The accepted cost:
nothing notices a stale template, and a template with no LiteLLM at all fails at
`proxy_start` (health check plus the LiteLLM log) rather than at install.

## WF-009 — Remediation failures name the step they happened in

**Applies to:** `sandbox_remediation._run`, `sandbox_agent.PhaseReporter`

A failed run reports `"<step> phase: <detail>"`, falling back to the exception
type when the provider's exception carries no message.

**Why:** the sandbox provider raises a bare "command exited with code 1 and
error:" — often with an empty message, since the detail went to stdout. The
step is the first thing an operator needs and the one thing that message never
contains.

**Commands are not the only steps.** Sandbox creation (where a template that
does not exist fails), config writes, host/token resolution, the patch handoff
and teardown all sit *between* commands, and attributing those to whichever
command ran last is worse than saying nothing. So `_sandbox()` names a
sandbox for its whole lifetime — including `<name>_teardown`, set only once the
body has completed — and `credential_proxy` reports its own internal steps
through the `report_phase` callback rather than the caller guessing.

**Command timeouts carry their own bound.** `PhaseTimeout` records the phase and
the seconds that actually elapsed, because the proxy phases run under fixed
bounds (600s/240s) far below `REMEDIATION_TIMEOUT_SECONDS` — reporting the
run-wide deadline for one of them names a duration that never passed.
