"""Compile the hash-locked requirement set the credential proxy installs.

The proxy sandbox holds the **real** provider key, so what runs in it should be
a fixed, verifiable set of artifacts rather than "whatever resolves today".
Pinning only a top-level `litellm[proxy]==…` leaves its transitive tree
(pydantic, aiohttp, openai, httpx, …) on ranges, which is the same drift that
broke the proxy in the first place, one level down.

    make lock_proxy_requirements                                  # re-lock as-is
    make lock_proxy_requirements REQUIREMENTS="litellm[proxy]==1.90.0"
    make lock_proxy_requirements PYTHON_VERSION=3.12 OUTPUT=locks/litellm-3.12.txt

With no arguments this recompiles the lock **in place**, reading its file path,
requirements and target runtime from its own header, so the lock is the single
source of truth for what the proxy runs. The inputs are reproduced; the resolved
versions are not frozen — a transitive dependency with a newer compatible
release moves, which is the point of re-locking. `REQUIREMENTS` changes what is
pinned; `PYTHON_VERSION`/`PLATFORM`/`OUTPUT` produce a lock for a different
sandbox runtime (then point
`SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE` at it).

**The target runtime is measured, not assumed.** With `SANDBOX_API_KEY` set, this
opens a real templateless sandbox — the thing a templateless run installs into —
and reads its python and architecture. Guessing that from a local `docker run`
is how the lock ended up built for python 3.11 while sandboxes run 3.13, which
is only detectable at install time. `PROBE=0` skips it (the header's recorded
runtime is then used, or a declared default).

Then rebuild any template (`make build_proxy_template`) and re-probe the path
(`make remediation_smoke SMOKE_PROXY=1`).

Two resolution details are easy to get wrong by hand:

- ``--no-config`` — without it ``uv`` applies *this project's* `[tool.uv]`
  constraint-dependencies to the sandbox's resolution, which is both wrong
  (they are Seizu's security floors, not the proxy's) and, today, unsolvable
  against LiteLLM's exact FastAPI pin.
- The python/platform target is the **sandbox's**, not this container's. A lock
  built for the wrong interpreter installs fine locally and fails in the sandbox
  on wheel ABI, so the target is recorded in the header and verified there.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporting import settings  # noqa: E402
from reporting.services import sandbox_agent  # noqa: E402
from reporting.services.sandbox_backend import open_backend  # noqa: E402

# Only used when there is no lock to re-read and no sandbox to measure.
DEFAULT_REQUIREMENTS = "litellm[proxy]==1.87.0"
DEFAULT_PYTHON_VERSION = "3.13"
DEFAULT_PLATFORM = "x86_64-unknown-linux-gnu"
# uv target tag for a measured `uname -m`. Deliberately only the two E2B runs on:
# anything else should be declared explicitly rather than guessed, since the tag
# also carries the libc (…-gnu vs …-musl) that `uname -m` cannot tell us.
_PLATFORM_FOR_MACHINE = {
    "x86_64": "x86_64-unknown-linux-gnu",
    "aarch64": "aarch64-unknown-linux-gnu",
}
# The container's view of the repository — the only path that outlives it.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_MARKER = "SEIZU_SANDBOX_RUNTIME"
_PROBE_PYTHON = "python3 -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])'"
_PROBE_SCRIPT = f'printf "{_RUNTIME_MARKER} %s %s\\n" "$({_PROBE_PYTHON})" "$(uname -m)"'


def _fail(message: str) -> int:
    print(f"LOCK FAILED: {message}", file=sys.stderr)
    return 2


def _machine_of(platform: str) -> str:
    """The `uname -m` value for a uv platform tag (x86_64-unknown-linux-gnu →
    x86_64), which is what the sandbox compares itself against."""
    return platform.split("-", 1)[0]


async def _probe_sandbox_runtime() -> tuple[str, str] | None:
    """Measure ``(python, machine)`` in a real templateless sandbox, or None.

    Templateless is deliberate: that is the sandbox a lock is installed into,
    and `build_proxy_template` builds its image from the lock's python, so this
    one measurement keeps both paths on the same runtime.
    """
    if not settings.SANDBOX_API_KEY:
        return None
    async with open_backend(
        api_key=settings.SANDBOX_API_KEY,
        domain=settings.SANDBOX_DOMAIN,
        allow_internet=False,
        timeout_seconds=120,
        template=None,
    ) as sandbox:
        output = await sandbox.run_bash(_PROBE_SCRIPT)
    for line in output.splitlines():
        if line.startswith(_RUNTIME_MARKER):
            fields = line.split()
            if len(fields) >= 3:
                return fields[1], fields[2]
    return None


def _resolve_output(existing: sandbox_agent.ProxyLock | None) -> Path | str:
    """Where to write, or an error string.

    Defaults to the lock being re-locked — writing a *configured* lock's contents
    back to the checked-in default would silently swap what every unconfigured
    deployment installs.
    """
    configured = os.environ.get("PROXY_OUTPUT", "").strip()
    if not configured:
        if existing is not None:
            return Path(existing.path)
        # No readable lock. Falling back to the checked-in path is right when
        # that *is* what was configured (bootstrapping, or a corrupted default),
        # but not when a deployment lock was configured and simply is not
        # mounted here: re-locking would then rewrite the default with default
        # requirements, which is the accident this guard exists to prevent.
        lock_path = sandbox_agent.proxy_lock_path()
        if lock_path != sandbox_agent.DEFAULT_PROXY_LOCK_PATH:
            return (
                f"SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE points at {lock_path}, which cannot be read "
                "or parsed here, so there is nothing to re-lock. Run this where that file is available, or pass "
                "OUTPUT=<path in the repo> (with REQUIREMENTS=) to compile a new lock — otherwise this would "
                "rewrite the checked-in default instead."
            )
        return Path(sandbox_agent.DEFAULT_PROXY_LOCK_PATH)  # bootstrap or repair the default
    # This runs in a disposable container where only the repository is mounted,
    # so anything outside it is written to a filesystem that ceases to exist.
    target = Path(configured)
    resolved = (target if target.is_absolute() else _REPO_ROOT / target).resolve()
    if not resolved.is_relative_to(_REPO_ROOT):
        return (
            f"OUTPUT={configured} is outside the repository. This runs in a disposable container that only "
            f"mounts the repo, so write it under {_REPO_ROOT} (a relative path is resolved from there) and "
            "copy it to its deployment location afterwards."
        )
    return resolved


def _resolve_runtime(existing: sandbox_agent.ProxyLock | None, probed: tuple[str, str] | None) -> tuple[str, str] | str:
    """The ``(python, uv platform)`` to resolve for, or an error string.

    Explicit arguments win, then the measured sandbox, then whatever the lock
    recorded. The platform is treated separately from the python version because
    a measured ``uname -m`` cannot tell gnu from musl: a recorded platform is
    kept as long as the architecture still matches, so re-locking a musl or ARM
    lock reproduces it instead of quietly resolving for x86_64 gnu.
    """
    python_version = (
        os.environ.get("PROXY_PYTHON_VERSION", "").strip()
        or (probed[0] if probed else "")
        or (existing.python if existing else DEFAULT_PYTHON_VERSION)
    )
    platform = os.environ.get("PROXY_PLATFORM", "").strip()
    if not platform and existing and existing.platform and (not probed or probed[1] == existing.machine):
        platform = existing.platform
    if not platform and probed:
        platform = _PLATFORM_FOR_MACHINE.get(probed[1], "")
        if not platform:
            return f"no known uv platform tag for machine {probed[1]!r}; pass PLATFORM explicitly"
    return python_version, platform or DEFAULT_PLATFORM


def _lock() -> int:
    existing = sandbox_agent.read_proxy_lock()
    output = _resolve_output(existing)
    if isinstance(output, str):
        return _fail(output)
    if existing is None and Path(sandbox_agent.proxy_lock_path()).exists():
        # A file is there but did not parse. Replacing it is legitimate (that is
        # how you repair one), but it must not look like an ordinary re-lock:
        # nothing of what it recorded is being carried forward.
        print(f"NOTE: {sandbox_agent.proxy_lock_error()}")
        print("      Compiling from scratch — nothing from that file is carried over.\n")

    if requested := os.environ.get("PROXY_REQUIREMENTS", "").split():
        requirements, source = requested, "REQUIREMENTS"
    elif existing:
        requirements, source = existing.requirements, f"{existing.path} header"
    else:
        requirements, source = DEFAULT_REQUIREMENTS.split(), "built-in default"

    probed: tuple[str, str] | None = None
    if os.environ.get("PROBE", "1") != "0":
        try:
            probed = asyncio.run(_probe_sandbox_runtime())
        except Exception as exc:  # noqa: BLE001 - a probe failure must not block re-locking
            print(f"Could not measure the sandbox runtime ({type(exc).__name__}); falling back to the header.")
    if probed:
        print(f"Measured sandbox runtime: python {probed[0]} on {probed[1]}")

    runtime = _resolve_runtime(existing, probed)
    if isinstance(runtime, str):
        return _fail(runtime)
    python_version, platform = runtime

    print(f"Requirements ({source}): {' '.join(requirements)}")
    print(f"Compiling for python {python_version} ({platform}) → {output}")
    result = subprocess.run(
        [
            "uv",
            "pip",
            "compile",
            "-",
            "--no-config",
            "--generate-hashes",
            "--no-header",
            "--no-annotate",
            "--python-version",
            python_version,
            "--python-platform",
            platform,
        ],
        input="\n".join(requirements) + "\n",
        capture_output=True,
        text=True,
        # Resolve from a directory with no pyproject.toml, so --no-config has no
        # project settings to find even if uv's discovery changes.
        cwd="/tmp",
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return _fail("uv pip compile did not resolve")

    header = (
        "# Generated by `make lock_proxy_requirements` — do not edit by hand.\n"
        "# Installed into the credential-proxy sandbox with `pip --no-deps --require-hashes`.\n"
        f"{sandbox_agent.PROXY_LOCK_INPUT_MARKER} {' '.join(requirements)}\n"
        f"{sandbox_agent.PROXY_LOCK_RUNTIME_MARKER} python={python_version} "
        f"machine={_machine_of(platform)} platform={platform}\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(header + result.stdout)
    pins = sum(1 for line in result.stdout.splitlines() if "==" in line)
    print(f"Wrote {output} ({pins} pinned packages).")
    if output != Path(sandbox_agent.DEFAULT_PROXY_LOCK_PATH):
        print("Point SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE at it (copy it out of the repo first).")
    print("Next: `make build_proxy_template` if you use one, then `make remediation_smoke SMOKE_PROXY=1`.")
    return 0


def main() -> None:
    raise SystemExit(_lock())


if __name__ == "__main__":
    main()
