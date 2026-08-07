"""Unit tests for the credential-proxy lock generator.

Small surface, but the parts that decide *where* a lock is written and *what
runtime* it targets are exactly where a mistake is silent: the file still
compiles, and the damage only shows up as a swapped lock or an install failing
inside a sandbox.
"""

from pathlib import Path
from typing import Any
from unittest.mock import patch

from reporting.services import sandbox_agent
from scripts import lock_proxy_requirements as lock


def _lock_record(**overrides: Any) -> sandbox_agent.ProxyLock:
    values: dict[str, Any] = {
        "path": "/srv/custom-lock.txt",
        "text": "litellm==1.87.0 --hash=sha256:abc\n",
        "requirements": ["litellm[proxy]==1.87.0"],
        "python": "3.13",
        "machine": "x86_64",
        "platform": "x86_64-unknown-linux-gnu",
    }
    values.update(overrides)
    return sandbox_agent.ProxyLock(**values)


def test_relocking_writes_back_to_the_lock_it_read(monkeypatch: Any) -> None:
    # Defaulting to the checked-in lock would copy a *configured* lock's
    # contents over what every unconfigured deployment installs.
    monkeypatch.delenv("PROXY_OUTPUT", raising=False)
    assert lock._resolve_output(_lock_record()) == Path("/srv/custom-lock.txt")
    # With no lock to re-read there is nothing to preserve.
    assert lock._resolve_output(None) == Path(sandbox_agent.DEFAULT_PROXY_LOCK_PATH)


def test_output_must_land_in_the_repository(monkeypatch: Any) -> None:
    # The generator runs in a disposable container that mounts only the repo, so
    # an absolute path elsewhere is written to a filesystem that then vanishes.
    monkeypatch.setenv("PROXY_OUTPUT", "/srv/seizu/litellm.txt")
    assert isinstance(outside := lock._resolve_output(None), str)
    assert "outside the repository" in outside
    # A relative path is resolved from the repo root.
    monkeypatch.setenv("PROXY_OUTPUT", "locks/litellm-3.12.txt")
    assert lock._resolve_output(None) == lock._REPO_ROOT / "locks/litellm-3.12.txt"


def test_runtime_prefers_explicit_then_measured_then_recorded(monkeypatch: Any) -> None:
    monkeypatch.delenv("PROXY_PYTHON_VERSION", raising=False)
    monkeypatch.delenv("PROXY_PLATFORM", raising=False)
    recorded = _lock_record(python="3.11")
    # Measured beats recorded — the whole point of probing a real sandbox.
    assert lock._resolve_runtime(recorded, ("3.13", "x86_64")) == ("3.13", "x86_64-unknown-linux-gnu")
    # …and explicit beats measured.
    monkeypatch.setenv("PROXY_PYTHON_VERSION", "3.12")
    assert lock._resolve_runtime(recorded, ("3.13", "x86_64"))[0] == "3.12"
    monkeypatch.delenv("PROXY_PYTHON_VERSION")
    # No probe (PROBE=0 or no API key): the header's runtime is reproduced.
    assert lock._resolve_runtime(recorded, None) == ("3.11", "x86_64-unknown-linux-gnu")


def test_recorded_platform_survives_a_probe_that_cannot_see_the_libc(monkeypatch: Any) -> None:
    monkeypatch.delenv("PROXY_PYTHON_VERSION", raising=False)
    monkeypatch.delenv("PROXY_PLATFORM", raising=False)
    # `uname -m` says x86_64 for both musl and gnu, so a musl lock must not
    # silently re-lock as gnu.
    musl = _lock_record(platform="x86_64-unknown-linux-musl")
    assert lock._resolve_runtime(musl, ("3.13", "x86_64"))[1] == "x86_64-unknown-linux-musl"
    # An ARM lock re-locks as ARM without extra arguments…
    arm = _lock_record(machine="aarch64", platform="aarch64-unknown-linux-gnu")
    assert lock._resolve_runtime(arm, None)[1] == "aarch64-unknown-linux-gnu"
    # …but a genuine architecture change follows the sandbox.
    assert lock._resolve_runtime(arm, ("3.13", "x86_64"))[1] == "x86_64-unknown-linux-gnu"


def test_unknown_machine_is_an_error_rather_than_a_guess(monkeypatch: Any) -> None:
    monkeypatch.delenv("PROXY_PLATFORM", raising=False)
    assert "no known uv platform tag" in str(lock._resolve_runtime(None, ("3.13", "riscv64")))


async def test_probe_is_skipped_without_a_sandbox_key() -> None:
    with patch("reporting.settings.SANDBOX_API_KEY", ""):
        assert await lock._probe_sandbox_runtime() is None
