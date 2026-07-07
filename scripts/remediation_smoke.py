"""Manual smoke test for the CVE remediation two-sandbox flow.

Spins up **real** E2B sandboxes using the configured ``SANDBOX_*`` / ``REMEDIATION_*``
settings and reproduces the credential-sensitive mechanics of the
``cve_dependency_remediation`` workflow end to end — **without** running a coding
agent or opening a pull request:

1. *Agent sandbox*: install gh, ``gh auth setup-git`` + clone, make a trivial
   commit **with no GitHub token in the environment** (standing in for the
   agent), then extract the change as a base64 git diff (also token-free).
2. *Push sandbox* (fresh, never ran step 1): install gh, clone fresh, apply the
   patch, and push the branch with the token. Then delete the test branch.

This is the check the mocked unit tests can't provide: that two real sandboxes,
gh, git, and the base64 patch handoff actually work, and that the push happens
from a separate sandbox than the one that made the commit. It reuses the real
install scripts and paths so it stays faithful as the code evolves.

    make remediation_smoke SMOKE_REPO=org/repo

Requires ``SANDBOX_API_KEY`` and ``REMEDIATION_GITHUB_TOKEN`` configured, plus
``SMOKE_REPO`` naming a repo the token can push to. Exits 0 on a successful push.
"""

import asyncio
import os
import sys
import uuid

# Work both as a module (python -m scripts.remediation_smoke) and as a plain
# script (python scripts/remediation_smoke.py): the workspace package is not
# pip-installed in the dev image, so put the project root on the path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporting import settings  # noqa: E402
from reporting.services import sandbox_agent  # noqa: E402
from reporting.services import sandbox_remediation as sr  # noqa: E402
from reporting.services.sandbox_backend import SandboxBackend, open_backend  # noqa: E402

# Agent sandbox: gh only (the real agent CLI is irrelevant to this plumbing test).
_INSTALL = sr._agent_install_script("true")

# Clone the default branch (no --branch, so any repo works) and create the work
# branch. Mirrors the real setup phase's gh-based auth and tokenless clone URL.
_SETUP = f"""\
set -euo pipefail
export GIT_TERMINAL_PROMPT=0
git config --global user.name "$SEIZU_GIT_USER"
git config --global user.email "$SEIZU_GIT_EMAIL"
gh auth setup-git --hostname "$SEIZU_GITHUB_HOST"
git clone --depth 5 "https://$SEIZU_GITHUB_HOST/$SEIZU_REPO.git" {sr.REPO_PATH}
cd {sr.REPO_PATH}
git checkout -b "$SEIZU_BRANCH"
"""

# Stand-in for the agent phase: make a commit with NO token in the environment
# (proves local changes need no credentials, as the real agent phase relies on).
_AGENT = f"""\
set -euo pipefail
cd {sr.REPO_PATH}
printf 'seizu remediation smoke test: %s\\n' "$SEIZU_BRANCH" >> SEIZU_SMOKE_TEST.md
git add SEIZU_SMOKE_TEST.md
git commit -q -m "seizu smoke: add marker file"
echo SEIZU_SMOKE_AGENT_OK
"""

# Extract the change as a base64 git diff (token-free), for the push sandbox.
_EXTRACT = f"""\
set -euo pipefail
cd {sr.REPO_PATH}
git diff --binary "origin/HEAD..HEAD" | base64 -w0 > {sr.CHANGES_B64_PATH}
echo SEIZU_SMOKE_EXTRACT_OK
"""

# Push sandbox: fresh clone, apply the patch (never ran the "agent"), push.
_PUSH = f"""\
set -euo pipefail
export GIT_TERMINAL_PROMPT=0
git config --global user.name "$SEIZU_GIT_USER"
git config --global user.email "$SEIZU_GIT_EMAIL"
gh auth setup-git --hostname "$SEIZU_GITHUB_HOST"
git clone --depth 5 "https://$SEIZU_GITHUB_HOST/$SEIZU_REPO.git" {sr.REPO_PATH}
cd {sr.REPO_PATH}
git checkout -b "$SEIZU_BRANCH"
base64 -d {sr.CHANGES_B64_PATH} | git apply --index --whitespace=nowarn
git commit -q -m "seizu remediation smoke test"
git push --force -u origin "$SEIZU_BRANCH"
echo SEIZU_SMOKE_PUSH_OK
"""

_CLEANUP = f"""\
set -euo pipefail
export GIT_TERMINAL_PROMPT=0
cd {sr.REPO_PATH}
git push origin --delete "$SEIZU_BRANCH" || true
echo SEIZU_SMOKE_CLEANUP_DONE
"""


def _fail(message: str) -> int:
    print(f"SMOKE FAILED: {message}", file=sys.stderr)
    return 2


async def _run() -> int:
    repo = os.environ.get("SMOKE_REPO", "").strip()
    if not repo:
        return _fail("set SMOKE_REPO=org/repo (a repo the token can push to)")
    if not settings.SANDBOX_API_KEY:
        return _fail("SANDBOX_API_KEY is not configured")
    if not settings.REMEDIATION_GITHUB_TOKEN:
        return _fail("REMEDIATION_GITHUB_TOKEN is not configured")
    if (invalid := sr.validate_target(repo, "placeholder", "placeholder")) is not None:
        return _fail(invalid)

    token = settings.REMEDIATION_GITHUB_TOKEN
    host = settings.REMEDIATION_GITHUB_HOST
    branch = f"seizu/dependency-update/smoke-{uuid.uuid4().hex[:8]}"
    provider = sandbox_agent.resolve_provider()
    template = sandbox_agent.resolve_template(provider) if provider is not None else None

    def mask(text: str) -> str:
        return text.replace(token, "***") if token else text

    def on_output(chunk: str) -> None:
        sys.stdout.write(mask(chunk))
        sys.stdout.flush()

    gh_env = {"GH_TOKEN": token, "GH_ENTERPRISE_TOKEN": token, "GH_HOST": host}
    target_env = {"SEIZU_GITHUB_HOST": host, "SEIZU_REPO": repo, "SEIZU_BRANCH": branch}
    git_id_env = {"SEIZU_GIT_USER": settings.REMEDIATION_GIT_USER, "SEIZU_GIT_EMAIL": settings.REMEDIATION_GIT_EMAIL}
    token_env = {**gh_env, **git_id_env, **target_env}
    # gh install reads the pinned version from env (no secret); required by set -u.
    gh_install_env = {"SEIZU_GH_VERSION": settings.REMEDIATION_GH_VERSION}

    async def run(backend: SandboxBackend, name: str, script: str, envs: dict[str, str]) -> str:
        print(f"\n===== {name} =====")
        return await backend.run_bash_streaming(script, timeout_seconds=300, on_output=on_output, envs=envs)

    print(f"Smoke test: repo={repo} host={host} branch={branch} template={template or '(base image)'}")

    # --- Agent sandbox: clone, "agent" commit (no token), extract patch --------
    print("\n########## AGENT SANDBOX ##########")
    async with open_backend(
        api_key=settings.SANDBOX_API_KEY,
        domain=settings.SANDBOX_DOMAIN,
        allow_internet=True,
        timeout_seconds=420,
        template=template,
    ) as agent:
        try:
            await run(agent, "install", _INSTALL, gh_install_env)
            await run(agent, "setup", _SETUP, token_env)
            # No token here — mirrors the real agent phase.
            await run(agent, "agent (no token)", _AGENT, {"SEIZU_BRANCH": branch})
            await run(agent, "extract (no token)", _EXTRACT, {})
            patch_b64 = await agent.read_file(sr.CHANGES_B64_PATH)
        except Exception as exc:  # noqa: BLE001
            print(f"\nagent sandbox failed: {mask(str(exc))[:800]}", file=sys.stderr)
            return _fail("agent sandbox / extract failed — see output above")

    if not patch_b64.strip():
        return _fail("extract produced an empty patch")

    # --- Push sandbox (fresh): apply patch + push, then delete the branch ------
    print("\n########## PUSH SANDBOX (fresh) ##########")
    pushed = False
    async with open_backend(
        api_key=settings.SANDBOX_API_KEY,
        domain=settings.SANDBOX_DOMAIN,
        allow_internet=True,
        timeout_seconds=420,
        template=None,
    ) as push:
        try:
            await push.write_file(sr.CHANGES_B64_PATH, patch_b64)
            await run(push, "push_install", sr._PUSH_INSTALL, gh_install_env)
            out = await run(push, "push", _PUSH, token_env)
            pushed = "SEIZU_SMOKE_PUSH_OK" in out
        except Exception as exc:  # noqa: BLE001
            print(f"\npush sandbox failed: {mask(str(exc))[:800]}", file=sys.stderr)
        finally:
            print("\n===== cleanup =====")
            try:
                await run(push, "cleanup", _CLEANUP, {**gh_env, **target_env})
            except Exception as exc:  # noqa: BLE001
                print(f"cleanup best-effort failed: {mask(str(exc))}", file=sys.stderr)

    if pushed:
        print("\nSMOKE PASS — patch handed from the agent sandbox to a fresh push sandbox and pushed")
        return 0
    return _fail("push did not succeed — see output above")


async def _run_proxy() -> int:
    """Probe the credential-proxy path: boot a private LiteLLM proxy in its own
    sandbox, mint a virtual key, and confirm a *second* sandbox can reach it via
    the E2B traffic-access header — the fragile, otherwise-unverified plumbing.
    Does not run a real coding agent. Needs SANDBOX_AGENT_* configured with a real
    provider key (and SANDBOX_AGENT_MODEL for opencode)."""
    if not settings.SANDBOX_API_KEY:
        return _fail("SANDBOX_API_KEY is not configured")
    provider = sandbox_agent.resolve_provider()
    if provider is None:
        return _fail("SANDBOX_AGENT_PROVIDER is unknown (expected claude, codex, or opencode)")
    if sandbox_agent.proxy_namespace(provider) is None:
        return _fail("no proxy namespace — set SANDBOX_AGENT_MODEL (required for opencode)")
    # Pass ONLY literal strings to _fail(): resolve_*() returns the fallback API
    # key in the same tuple as its error, so surfacing that error to a print/log
    # trips CodeQL's clear-text-logging (and risks leaking on real imprecision).
    # ``fallback``/``real_key`` are secrets — used for masking + the proxy, never printed.
    _key_envs, fallback, _err = sandbox_agent.resolve_key_envs_and_fallback(provider)
    real_key = settings.SANDBOX_AGENT_API_KEY or fallback
    if not real_key:
        return _fail("no real provider key (SANDBOX_AGENT_API_KEY or the global provider key)")

    mask_secrets: list[str] = [real_key]

    def mask(text: str) -> str:
        for secret in mask_secrets:
            text = text.replace(secret, "***") if secret else text
        return text

    def on_output(chunk: str) -> None:
        sys.stdout.write(mask(chunk))
        sys.stdout.flush()

    async def run_phase(
        backend: SandboxBackend, name: str, script: str, envs: dict[str, str], timeout_seconds: int | None = None
    ) -> str:
        print(f"\n===== {name} =====")
        return await backend.run_bash_streaming(
            script, timeout_seconds=timeout_seconds or 300, on_output=on_output, envs=envs
        )

    print(f"Proxy smoke: provider={provider.name} namespace={sandbox_agent.proxy_namespace(provider)}")
    print("\n########## PROXY SANDBOX (private LiteLLM) ##########")
    reachable = False
    async with sandbox_agent.credential_proxy(
        provider=provider,
        real_key=real_key,
        budget="1",
        sandbox_timeout_seconds=480,
        run_phase=run_phase,
        mask_secrets=mask_secrets,
    ) as (base_url, _agent_key, access_token):
        suffix = provider.proxy_base_suffix
        root = base_url[: -len(suffix)] if suffix and base_url.endswith(suffix) else base_url
        print(f"\nproxy up: base_url={base_url} private={bool(access_token)}")
        print("\n########## SECOND SANDBOX (reaches the private proxy) ##########")
        async with open_backend(
            api_key=settings.SANDBOX_API_KEY,
            domain=settings.SANDBOX_DOMAIN,
            allow_internet=True,
            timeout_seconds=180,
        ) as agent:
            hdr = f'-H "e2b-traffic-access-token: {access_token}"' if access_token else ""
            check = f'curl -fsS {hdr} "{root}/health/liveliness" && printf "\\nSEIZU_PROXY_REACHABLE\\n"'
            out = await run_phase(agent, "reachability", check, {}, timeout_seconds=120)
            reachable = "SEIZU_PROXY_REACHABLE" in out

    if reachable:
        print("\nSMOKE PASS — private proxy booted and is reachable from a second sandbox via the traffic header")
        return 0
    return _fail("the second sandbox could not reach the private proxy — see output above")


def main() -> None:
    # SMOKE_PROXY=1 probes the credential proxy; otherwise the two-sandbox git flow.
    runner = _run_proxy if os.environ.get("SMOKE_PROXY") else _run
    raise SystemExit(asyncio.run(runner()))


if __name__ == "__main__":
    main()
