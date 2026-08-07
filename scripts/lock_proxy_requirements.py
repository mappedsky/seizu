"""Compile the hash-locked requirement set the credential proxy installs.

The proxy sandbox holds the **real** provider key, so what runs in it should be
a fixed, verifiable set of artifacts rather than "whatever resolves today".
Pinning only a top-level `litellm[proxy]==…` leaves its transitive tree
(pydantic, aiohttp, openai, httpx, …) on ranges, which is the same drift that
broke the proxy in the first place, one level down.

    make lock_proxy_requirements                                  # re-lock as-is
    make lock_proxy_requirements REQUIREMENTS="litellm[proxy]==1.90.0"
    make lock_proxy_requirements PYTHON_VERSION=3.12 OUTPUT=/srv/litellm-3.12.txt

With no arguments this recompiles the checked-in lock from the requirements and
runtime recorded in its own header, so re-locking is idempotent and the lock is
the single source of truth for what the proxy runs. `REQUIREMENTS` changes what
is pinned; `PYTHON_VERSION`/`PLATFORM`/`OUTPUT` produce a lock for a different
sandbox runtime (point `SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE` at it).

Then rebuild any template (`make build_proxy_template`) and re-probe the path
(`make remediation_smoke SMOKE_PROXY=1`).

Three resolution details matter and are easy to get wrong by hand:

- ``--no-config`` — without it ``uv`` applies *this project's* `[tool.uv]`
  constraint-dependencies to the sandbox's resolution, which is both wrong
  (they are Seizu's security floors, not the proxy's) and, today, unsolvable
  against LiteLLM's exact FastAPI pin.
- The python/platform target is the **sandbox's**, not this container's. A lock
  built for the wrong interpreter installs fine locally and fails in the sandbox
  on wheel ABI, so the target is recorded in the header and verified there.
- The default target is python 3.13: the **E2B default sandbox's** interpreter,
  which is what a templateless run installs into. It is *not* the `e2bdev/base`
  docker image's (3.11) — the templates built by
  ``scripts/build_proxy_template.py`` are therefore built from the lock's own
  python, so the two cannot disagree.
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporting.services import sandbox_agent  # noqa: E402

# Fallbacks for a lock that does not exist yet (the header supplies them
# otherwise). The python version is the **E2B default sandbox's** — 3.13 today,
# which is not the same as the `e2bdev/base` docker image (3.11), so read it off
# a real sandbox rather than a local `docker run`: a templateless proxy run
# prints it in the SEIZU_PROXY_RUNTIME_MISMATCH message. `build_proxy_template`
# builds from the lock's python precisely so the two cannot diverge.
# LiteLLM 1.87.0 separately caps itself at python <3.14.
DEFAULT_REQUIREMENTS = "litellm[proxy]==1.87.0"
DEFAULT_PYTHON_VERSION = "3.13"
DEFAULT_PLATFORM = "x86_64-unknown-linux-gnu"


def _fail(message: str) -> int:
    print(f"LOCK FAILED: {message}", file=sys.stderr)
    return 2


def _machine_of(platform: str) -> str:
    """The `uname -m` value for a uv platform tag (x86_64-unknown-linux-gnu →
    x86_64), which is what the sandbox compares itself against."""
    return platform.split("-", 1)[0]


def _lock() -> int:
    existing = sandbox_agent.read_proxy_lock()
    requirements = (
        os.environ.get("PROXY_REQUIREMENTS", "").split()
        or (existing.requirements if existing else [])
        or DEFAULT_REQUIREMENTS.split()
    )
    python_version = os.environ.get("PROXY_PYTHON_VERSION", "").strip() or (
        existing.python if existing else DEFAULT_PYTHON_VERSION
    )
    platform = os.environ.get("PROXY_PLATFORM", "").strip() or DEFAULT_PLATFORM
    if existing and _machine_of(platform) != existing.machine and not os.environ.get("PROXY_PLATFORM"):
        # The header records the machine, not the full uv tag; keep them agreeing.
        return _fail(f"{existing.path} records machine {existing.machine!r}; pass PROXY_PLATFORM explicitly")
    target = Path(os.environ.get("PROXY_OUTPUT", "").strip() or sandbox_agent.DEFAULT_PROXY_LOCK_PATH)

    print(f"Compiling {' '.join(requirements)} for python {python_version} ({platform}) → {target}")
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
    target.write_text(header + result.stdout)
    pins = sum(1 for line in result.stdout.splitlines() if "==" in line)
    print(f"Wrote {target} ({pins} pinned packages).")
    print("Next: `make build_proxy_template` if you use one, then `make remediation_smoke SMOKE_PROXY=1`.")
    return 0


def main() -> None:
    raise SystemExit(_lock())


if __name__ == "__main__":
    main()
