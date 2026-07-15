import stat

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from cartography_sync import activities
from cartography_sync.activities import run_cartography_module
from cartography_sync.shared import CartographyModuleActivityInput
from cartography_sync.sync_lock import LockTimeoutError


def _write_stub(tmp_path, body: str) -> str:
    """Write an executable stand-in for the cartography CLI."""
    path = tmp_path / "cartography-stub"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class _FakeSyncLock:
    """Records constructor args; acquires/releases without Neo4j."""

    instances: list["_FakeSyncLock"] = []
    raise_timeout = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.entered = False
        self.exited = False
        _FakeSyncLock.instances.append(self)

    async def __aenter__(self):
        if _FakeSyncLock.raise_timeout:
            raise LockTimeoutError(f"sync lock '{self.kwargs['key']}' still held by another run after 0s")
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True


@pytest.fixture
def sync_env(monkeypatch, tmp_path):
    """Point the activity at a stub binary, a fixed Neo4j URI, and a fake lock."""
    monkeypatch.setenv("CARTOGRAPHY_NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.delenv("CARTOGRAPHY_NEO4J_USER", raising=False)
    monkeypatch.delenv("CARTOGRAPHY_ENABLED_MODULES", raising=False)
    monkeypatch.setattr(activities, "SyncLock", _FakeSyncLock)
    _FakeSyncLock.instances = []
    _FakeSyncLock.raise_timeout = False

    def _use(body: str) -> None:
        monkeypatch.setenv("CARTOGRAPHY_BIN", _write_stub(tmp_path, body))

    return _use


async def test_successful_run_returns_result_with_output_tail(sync_env):
    sync_env('echo "syncing all the things"')
    result = await ActivityEnvironment().run(
        run_cartography_module, CartographyModuleActivityInput(module="cve", params={})
    )
    assert result.status == "completed"
    assert result.exit_code == 0
    assert "syncing all the things" in result.output_tail
    assert result.duration_seconds >= 0


async def test_argv_reaches_subprocess_as_single_tokens(sync_env):
    sync_env('for arg in "$@"; do echo "TOKEN:$arg"; done')
    result = await ActivityEnvironment().run(
        run_cartography_module,
        CartographyModuleActivityInput(module="aws", params={"aws_requested_syncs": "ec2,s3"}),
    )
    assert "TOKEN:--neo4j-uri=bolt://neo4j:7687" in result.output_tail
    assert "TOKEN:--selected-modules=aws" in result.output_tail
    assert "TOKEN:--aws-requested-syncs=ec2,s3" in result.output_tail


async def test_nonzero_exit_raises_retryable_module_failure(sync_env):
    sync_env('echo "boom"; exit 3')
    with pytest.raises(ApplicationError) as excinfo:
        await ActivityEnvironment().run(run_cartography_module, CartographyModuleActivityInput(module="cve", params={}))
    assert excinfo.value.type == "CartographyModuleFailed"
    assert not excinfo.value.non_retryable
    assert "exited 3" in str(excinfo.value)
    assert "boom" in str(excinfo.value)


async def test_invalid_params_raise_non_retryable_config_error(sync_env):
    sync_env("echo never-runs")
    with pytest.raises(ApplicationError) as excinfo:
        await ActivityEnvironment().run(
            run_cartography_module,
            CartographyModuleActivityInput(module="cve", params={"neo4j_uri": "bolt://evil"}),
        )
    assert excinfo.value.type == "CartographyConfigError"
    assert excinfo.value.non_retryable


async def test_missing_required_env_raises_config_error(sync_env, monkeypatch):
    sync_env("echo never-runs")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ApplicationError) as excinfo:
        await ActivityEnvironment().run(
            run_cartography_module, CartographyModuleActivityInput(module="github", params={})
        )
    assert excinfo.value.type == "CartographyConfigError"
    assert "GITHUB_TOKEN" in str(excinfo.value)


async def test_missing_neo4j_uri_raises_config_error(sync_env, monkeypatch):
    sync_env("echo never-runs")
    monkeypatch.delenv("CARTOGRAPHY_NEO4J_URI")
    with pytest.raises(ApplicationError) as excinfo:
        await ActivityEnvironment().run(run_cartography_module, CartographyModuleActivityInput(module="cve", params={}))
    assert excinfo.value.type == "CartographyConfigError"


async def test_subprocess_env_is_scrubbed(sync_env, monkeypatch):
    sync_env("env | cut -d= -f1")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    monkeypatch.setenv("REMEDIATION_GITHUB_TOKEN", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    result = await ActivityEnvironment().run(
        run_cartography_module, CartographyModuleActivityInput(module="github", params={})
    )
    names = set(result.output_tail.split())
    assert "GITHUB_TOKEN" in names
    assert "PATH" in names
    assert "REMEDIATION_GITHUB_TOKEN" not in names
    assert "ANTHROPIC_API_KEY" not in names
    # The activity's own config vars don't leak into the subprocess either.
    assert "CARTOGRAPHY_NEO4J_URI" not in names


async def test_neo4j_auth_flags_added_without_password_in_argv(sync_env, monkeypatch):
    sync_env('for arg in "$@"; do echo "TOKEN:$arg"; done; env | cut -d= -f1')
    monkeypatch.setenv("CARTOGRAPHY_NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "hunter2")
    result = await ActivityEnvironment().run(
        run_cartography_module, CartographyModuleActivityInput(module="cve", params={})
    )
    assert "TOKEN:--neo4j-user=neo4j" in result.output_tail
    assert "TOKEN:--neo4j-password-env-var=NEO4J_PASSWORD" in result.output_tail
    assert "hunter2" not in result.output_tail  # value only in env, never argv
    assert "NEO4J_PASSWORD" in result.output_tail.split()


async def test_timeout_terminates_subprocess(sync_env):
    sync_env("sleep 30")
    with pytest.raises(ApplicationError) as excinfo:
        await ActivityEnvironment().run(
            run_cartography_module,
            CartographyModuleActivityInput(module="cve", params={}, timeout_seconds=1),
        )
    assert excinfo.value.type == "CartographyModuleFailed"
    assert "timed out" in str(excinfo.value)


async def test_run_holds_per_module_lock(sync_env):
    sync_env("echo ok")
    await ActivityEnvironment().run(
        run_cartography_module,
        CartographyModuleActivityInput(module="cve", params={}, timeout_seconds=30, lock_wait_seconds=45),
    )
    (lock,) = _FakeSyncLock.instances
    assert lock.kwargs["key"] == "cartography-module:cve"
    assert lock.kwargs["ttl_seconds"] == 30 + 120  # outlives the subprocess watchdog
    assert lock.kwargs["wait_timeout_seconds"] == 45
    assert lock.entered and lock.exited


async def test_lock_timeout_raises_retryable_failure(sync_env):
    sync_env("echo never-runs")
    _FakeSyncLock.raise_timeout = True
    with pytest.raises(ApplicationError) as excinfo:
        await ActivityEnvironment().run(run_cartography_module, CartographyModuleActivityInput(module="cve", params={}))
    assert excinfo.value.type == "CartographyModuleFailed"
    assert not excinfo.value.non_retryable
    assert "still held" in str(excinfo.value)


async def test_worker_side_enabled_modules_allowlist(sync_env, monkeypatch):
    sync_env("echo ok")
    monkeypatch.setenv("CARTOGRAPHY_ENABLED_MODULES", "github")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token")
    # Disabled module → non-retryable config error even though it's registered.
    with pytest.raises(ApplicationError) as excinfo:
        await ActivityEnvironment().run(run_cartography_module, CartographyModuleActivityInput(module="cve", params={}))
    assert excinfo.value.type == "CartographyConfigError"
    assert excinfo.value.non_retryable
    assert "CARTOGRAPHY_ENABLED_MODULES" in str(excinfo.value)
    # Enabled module runs; internal stages are always allowed.
    result = await ActivityEnvironment().run(
        run_cartography_module, CartographyModuleActivityInput(module="github", params={})
    )
    assert result.status == "completed"
    result = await ActivityEnvironment().run(
        run_cartography_module, CartographyModuleActivityInput(module="analysis", params={})
    )
    assert result.status == "completed"
