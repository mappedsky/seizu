import stat

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from cartography_sync.activities import run_cartography_module
from cartography_sync.shared import CartographyModuleActivityInput


def _write_stub(tmp_path, body: str) -> str:
    """Write an executable stand-in for the cartography CLI."""
    path = tmp_path / "cartography-stub"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture
def sync_env(monkeypatch, tmp_path):
    """Point the activity at a stub binary and a fixed Neo4j URI."""
    monkeypatch.setenv("CARTOGRAPHY_NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.delenv("CARTOGRAPHY_NEO4J_USER", raising=False)

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
    assert "TOKEN:--selected-modules=create-indexes,aws,analysis" in result.output_tail
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
