"""Manual smoke test for the CVE remediation sandbox auth path.

Spins up a **real** E2B sandbox using the configured ``SANDBOX_*`` / ``REMEDIATION_*``
settings and reproduces the credential-sensitive phases the
``cve_dependency_remediation`` workflow relies on — install ``gh``,
``gh auth setup-git``, clone, and push a throwaway branch — then deletes the
branch. It does **not** run a coding agent or open a pull request.

Unit tests mock the sandbox, so they can't confirm that a real sandbox, ``gh``,
and git actually authenticate and push. Run this after changing the sandbox /
auth code or the GitHub token to verify the path end to end:

    make remediation_smoke SMOKE_REPO=org/repo

Requires ``SANDBOX_API_KEY`` and ``REMEDIATION_GITHUB_TOKEN`` to be configured
(as for the real workflow), plus ``SMOKE_REPO`` naming a repo the token can
push to. Exits 0 on a successful push, non-zero otherwise.
"""

import asyncio
import os
import sys
import uuid

from reporting import settings
from reporting.services import sandbox_remediation as sr
from reporting.services.mcp_builtins.sandbox import _open_backend

# gh only — skip the agent CLI (irrelevant to the auth path, and slow).
_INSTALL = sr._INSTALL_SCRIPT_TEMPLATE.format(install_cmd="true")

# These mirror the real setup/push phases' auth mechanism (gh auth setup-git,
# tokenless URLs, per-command token env). The clone omits --branch so the smoke
# test works against any default branch without needing to know it.
_SETUP = f"""\
set -euo pipefail
export GIT_TERMINAL_PROMPT=0
git config --global user.name "$SEIZU_GIT_USER"
git config --global user.email "$SEIZU_GIT_EMAIL"
gh auth setup-git --hostname "$SEIZU_GITHUB_HOST"
git clone --depth 1 "https://$SEIZU_GITHUB_HOST/$SEIZU_REPO.git" {sr.REPO_PATH}
cd {sr.REPO_PATH}
git checkout -b "$SEIZU_BRANCH"
git commit --allow-empty -m "seizu remediation smoke test"
"""

_PUSH = f"""\
set -euo pipefail
export GIT_TERMINAL_PROMPT=0
cd {sr.REPO_PATH}
gh auth setup-git --hostname "$SEIZU_GITHUB_HOST"
git push --force -u origin "$SEIZU_BRANCH"
echo SEIZU_SMOKE_PUSH_OK
"""

_CLEANUP = f"""\
set -euo pipefail
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
    provider = sr.PROVIDERS.get(settings.REMEDIATION_AGENT_PROVIDER)
    template = sr._resolve_template(provider) if provider is not None else None

    def mask(text: str) -> str:
        return text.replace(token, "***") if token else text

    def on_output(chunk: str) -> None:
        sys.stdout.write(mask(chunk))
        sys.stdout.flush()

    git_env = {
        "GH_TOKEN": token,
        "GH_ENTERPRISE_TOKEN": token,
        "GH_HOST": host,
        "SEIZU_GITHUB_HOST": host,
        "SEIZU_REPO": repo,
        "SEIZU_BRANCH": branch,
    }
    setup_env = {
        **git_env,
        "SEIZU_GIT_USER": settings.REMEDIATION_GIT_USER,
        "SEIZU_GIT_EMAIL": settings.REMEDIATION_GIT_EMAIL,
    }

    print(f"Smoke test: repo={repo} host={host} branch={branch} template={template or '(base image)'}")
    async with _open_backend(
        api_key=settings.SANDBOX_API_KEY,
        domain=settings.SANDBOX_DOMAIN,
        allow_internet=True,
        timeout_seconds=300,
        template=template,
    ) as backend:
        pushed = False
        phases: list[tuple[str, str, dict[str, str]]] = [
            ("install", _INSTALL, {}),
            ("setup", _SETUP, setup_env),
            ("push", _PUSH, git_env),
        ]
        try:
            for name, script, env in phases:
                print(f"\n===== {name} =====")
                out = await backend.run_bash_streaming(script, timeout_seconds=300, on_output=on_output, envs=env)
                if name == "push" and "SEIZU_SMOKE_PUSH_OK" in out:
                    pushed = True
        except Exception as exc:  # noqa: BLE001 — report any phase failure verbatim
            print(f"\n===== push/clone failed =====\n{mask(str(exc))}", file=sys.stderr)
        finally:
            print("\n===== cleanup =====")
            try:
                await backend.run_bash_streaming(_CLEANUP, timeout_seconds=120, on_output=on_output, envs=git_env)
            except Exception as exc:  # noqa: BLE001
                print(f"cleanup best-effort failed: {mask(str(exc))}", file=sys.stderr)

    if pushed:
        print("\nSMOKE PASS — gh auth cloned and pushed from the sandbox")
        return 0
    return _fail("push did not succeed — see output above")


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
