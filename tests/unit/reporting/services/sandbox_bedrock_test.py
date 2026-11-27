"""The Amazon Bedrock AgentCore sandbox provider (SBX-021).

Driven against :class:`FakeAgentCore`, a stand-in for the AgentCore data plane
whose sessions are reference protocol images: files persist across a stopped
session, Python state does not -- the semantics the lifecycle depends on.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from reporting.services import sandbox_backend, sandbox_bedrock
from reporting.services.sandbox_backend import kill_sandbox, list_paused_sandboxes, open_backend, sandbox_is_paused
from reporting.services.sandbox_bedrock import SandboxOpError, SandboxRuntimeUnavailable
from reporting.services.sandbox_protocol import MARKER_FILENAME
from tests.unit.reporting.services.sandbox_protocol_fake import FakeAgentCore

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/seizu_sandbox_prod-abc"


def _settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "SANDBOX_PROVIDER": "bedrock",
        "SANDBOX_BEDROCK_STORAGE": "session",
        "SANDBOX_BEDROCK_S3_BUCKET": "",
        "SANDBOX_BEDROCK_S3_PREFIX": "",
        "SEIZU_DEPLOYMENT_ID": "prod",
        "SANDBOX_BEDROCK_MANAGE_RUNTIME": True,
        "SANDBOX_BEDROCK_RUNTIME_ARN": "",
    }
    values.update(overrides)
    return patch.multiple("reporting.settings", **values)


@pytest.fixture
def agentcore(tmp_path: Path) -> Iterator[FakeAgentCore]:
    fake = FakeAgentCore(tmp_path)
    with (
        _settings(),
        patch.object(sandbox_bedrock, "data_client", return_value=fake),
        patch.object(sandbox_bedrock, "resolve_runtime_arn", AsyncMock(return_value=ARN)),
    ):
        yield fake
    fake.close()


def _open(**kwargs: Any) -> Any:
    return open_backend(api_key="", domain="", **kwargs)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_create_warms_a_new_session_and_writes_its_marker(agentcore: FakeAgentCore) -> None:
    async with _open(purpose="chat-session", thread="user:u1:thread:t1") as backend:
        sandbox_id = backend.sandbox_id
        assert sandbox_bedrock.is_session_id(sandbox_id)
        marker = json.loads((agentcore.image(sandbox_id).mount / MARKER_FILENAME).read_text())
    assert marker["deployment"] == "prod"
    assert marker["purpose"] == "chat-session"
    assert marker["thread"] == "user:u1:thread:t1"
    assert agentcore.invocations[0] == (sandbox_id, "hello")


async def test_a_sandbox_not_kept_is_wiped_and_stopped(agentcore: FakeAgentCore) -> None:
    async with _open() as backend:
        sandbox_id = backend.sandbox_id
        await backend.write_file("/home/user/data.txt", "rows")
    assert (sandbox_id, "wipe") in agentcore.invocations
    # Stopped before the wipe, so nothing still running can write after it.
    assert agentcore.stopped == [sandbox_id, sandbox_id]
    assert list(agentcore.image(sandbox_id).mount.iterdir()) == []


async def test_suspend_stops_and_resume_restores_files_but_not_python_state(agentcore: FakeAgentCore) -> None:
    teardowns: list[bool] = []
    async with _open(suspend_on_exit=True, on_teardown=teardowns.append) as backend:
        sandbox_id = backend.sandbox_id
        await backend.write_file("/home/user/seizu_results/r.json", '{"results": [1, 2]}')
        await backend.run_python("kept = 1")
    assert teardowns == [True]
    assert agentcore.stopped == [sandbox_id]
    assert "wipe" not in [op for _, op in agentcore.invocations]

    async with _open(resume_sandbox_id=sandbox_id, suspend_on_exit=True) as resumed:
        assert resumed.sandbox_id == sandbox_id
        assert await resumed.read_file("/home/user/seizu_results/r.json") == '{"results": [1, 2]}'
        assert "NameError" in await resumed.run_python("kept")


async def test_resume_with_a_lost_workspace_replaces_the_session(agentcore: FakeAgentCore) -> None:
    async with _open(suspend_on_exit=True) as backend:
        old_id = backend.sandbox_id
    agentcore.lose_storage(old_id)
    agentcore.stopped.clear()

    async with _open(resume_sandbox_id=old_id, suspend_on_exit=True) as backend:
        assert backend.sandbox_id != old_id
        assert sandbox_bedrock.is_session_id(backend.sandbox_id)
    assert agentcore.stopped[0] == old_id


async def test_resume_of_an_id_from_another_provider_creates_without_calling_it(agentcore: FakeAgentCore) -> None:
    async with _open(resume_sandbox_id="i3x9e2bsandboxid", suspend_on_exit=True) as backend:
        assert sandbox_bedrock.is_session_id(backend.sandbox_id)
    assert all(session != "i3x9e2bsandboxid" for session, _ in agentcore.invocations)


async def test_a_transient_failure_on_resume_propagates(agentcore: FakeAgentCore) -> None:
    error = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "InvokeAgentRuntime")
    invoke = MagicMock(side_effect=error)
    with (
        patch.object(agentcore, "invoke_agent_runtime", invoke),
        patch.object(sandbox_bedrock.asyncio, "sleep", AsyncMock()),
    ):
        with pytest.raises(ClientError):
            async with _open(resume_sandbox_id=sandbox_bedrock.new_session_id()):
                pass
    # Retried, since a throttled call never ran, then reported rather than
    # mistaken for a lost workspace.
    assert invoke.call_count == sandbox_bedrock._MAX_ATTEMPTS


async def test_provisioning_conflicts_are_retried(agentcore: FakeAgentCore) -> None:
    agentcore.conflicts_before_ready = 2
    with patch.object(sandbox_bedrock.asyncio, "sleep", AsyncMock()):
        async with _open() as backend:
            assert backend.sandbox_id


async def test_attach_uses_the_session_and_leaves_it_running(agentcore: FakeAgentCore) -> None:
    async with _open(suspend_on_exit=True) as owner:
        sandbox_id = owner.sandbox_id
        async with _open(resume_sandbox_id=sandbox_id, create_if_missing=False, detach_on_exit=True) as step:
            assert step.sandbox_id == sandbox_id
            await step.write_file("/home/user/from_step.txt", "x")
        assert agentcore.stopped == []
        assert await owner.read_file("/home/user/from_step.txt") == "x"


async def test_attach_never_creates(agentcore: FakeAgentCore) -> None:
    gone = sandbox_bedrock.new_session_id()
    with pytest.raises(RuntimeError):
        async with _open(resume_sandbox_id=gone, create_if_missing=False, detach_on_exit=True):
            pass
    with pytest.raises(RuntimeError):
        async with _open(create_if_missing=False, detach_on_exit=True):
            pass
    assert [op for _, op in agentcore.invocations] == ["hello"]


async def test_a_failed_suspend_destroys_the_session(agentcore: FakeAgentCore) -> None:
    calls = {"n": 0}
    real_stop = agentcore.stop_runtime_session

    def _flaky_stop(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "x"}}, "StopRuntimeSession")
        return real_stop(**kwargs)

    teardowns: list[bool] = []
    with patch.object(agentcore, "stop_runtime_session", side_effect=_flaky_stop):
        async with _open(suspend_on_exit=True, on_teardown=teardowns.append) as backend:
            sandbox_id = backend.sandbox_id
    assert teardowns == [False]
    assert (sandbox_id, "wipe") in agentcore.invocations


async def test_per_sandbox_internet_access_is_refused(agentcore: FakeAgentCore) -> None:
    with pytest.raises(RuntimeError, match="internet"):
        async with _open(allow_internet=True):
            pass


async def test_an_image_without_protocol_1_is_refused(tmp_path: Path) -> None:
    fake = FakeAgentCore(tmp_path, protocol_versions=(2,))
    with (
        _settings(),
        patch.object(sandbox_bedrock, "data_client", return_value=fake),
        patch.object(sandbox_bedrock, "resolve_runtime_arn", AsyncMock(return_value=ARN)),
    ):
        with pytest.raises(SandboxOpError, match="protocol"):
            async with _open():
                pass
    fake.close()


async def test_an_image_that_writes_no_marker_is_refused(tmp_path: Path) -> None:
    fake = FakeAgentCore(tmp_path, write_marker=False)
    with (
        _settings(),
        patch.object(sandbox_bedrock, "data_client", return_value=fake),
        patch.object(sandbox_bedrock, "resolve_runtime_arn", AsyncMock(return_value=ARN)),
    ):
        with pytest.raises(SandboxOpError, match="marker"):
            async with _open():
                pass
    fake.close()


# ---------------------------------------------------------------------------
# Output: same strings as the E2B backend
# ---------------------------------------------------------------------------


async def test_python_output_matches_the_e2b_rendering(agentcore: FakeAgentCore) -> None:
    async with _open() as backend:
        assert await backend.run_python("print('line'); 6*7") == "line\n\n42"
        assert await backend.run_python("x = 1") == "(no output)"
        assert "stderr:\nwarn" in await backend.run_python("import sys; print('warn', file=sys.stderr)")
        out = await backend.run_python("1/0")
        assert out.startswith("Error: ZeroDivisionError: division by zero")


async def test_python_timeout_reports_the_restart(agentcore: FakeAgentCore) -> None:
    async with _open() as backend:
        with patch.object(sandbox_bedrock, "_exec_timeout", return_value=1):
            out = await backend.run_python("import time; time.sleep(5)")
    assert "restarted" in out


async def test_bash_reports_exit_codes_and_timeouts_as_output(agentcore: FakeAgentCore) -> None:
    async with _open() as backend:
        assert await backend.run_bash("echo hi") == "hi\n"
        assert await backend.run_bash("echo nope >&2; exit 3") == "stderr: nope\n\nexit code: 3"
        with patch.object(sandbox_bedrock, "_exec_timeout", return_value=1):
            assert "stopped for running too long" in await backend.run_bash("sleep 5")


async def test_files_round_trip_and_list(agentcore: FakeAgentCore) -> None:
    async with _open() as backend:
        assert await backend.write_file("/home/user/a/b.txt", "héllo") == "Wrote 5 bytes to /home/user/a/b.txt"
        assert await backend.read_file("/home/user/a/b.txt") == "héllo"
        assert await backend.list_files("/home/user/a") == "f  b.txt"
        with pytest.raises(SandboxOpError):
            await backend.read_file("/home/user/missing.txt")


async def test_remediation_only_members_say_so(agentcore: FakeAgentCore) -> None:
    async with _open() as backend:
        with pytest.raises(NotImplementedError):
            await backend.get_host(4000)
        with pytest.raises(NotImplementedError):
            await backend.run_bash_streaming("x", timeout_seconds=1, on_output=lambda _: None)
        assert await backend.get_traffic_access_token() == ""
        assert isinstance(backend, sandbox_backend.SandboxBackend)


# ---------------------------------------------------------------------------
# Dispatch and account-wide helpers
# ---------------------------------------------------------------------------


async def test_bedrock_never_imports_the_e2b_sdk(agentcore: FakeAgentCore) -> None:
    with patch.dict("sys.modules", {"e2b_code_interpreter": None, "e2b": None}):
        async with _open() as backend:
            sandbox_id = backend.sandbox_id
        await kill_sandbox(sandbox_id)
        assert await list_paused_sandboxes() == []
        assert await sandbox_is_paused(sandbox_id) is False


async def test_kill_sandbox_wipes_session_storage(agentcore: FakeAgentCore) -> None:
    async with _open(suspend_on_exit=True) as backend:
        sandbox_id = backend.sandbox_id
        await backend.write_file("/home/user/keep.txt", "x")
    with patch.object(sandbox_bedrock, "resolve_runtime_arn", AsyncMock(return_value=ARN)):
        await kill_sandbox(sandbox_id)
    assert list(agentcore.image(sandbox_id).mount.iterdir()) == []


async def test_kill_of_a_foreign_id_is_a_no_op(agentcore: FakeAgentCore) -> None:
    await kill_sandbox("i3x9e2bsandboxid")
    assert agentcore.invocations == []


async def test_an_unready_runtime_is_reported(tmp_path: Path) -> None:
    sandbox_bedrock._reset_runtime_cache()
    control = MagicMock()
    control.get_paginator.return_value.paginate.return_value = [
        {"agentRuntimes": [{"agentRuntimeName": "seizu_sandbox_prod", "status": "UPDATING", "agentRuntimeArn": ARN}]}
    ]
    with (
        _settings(SANDBOX_BEDROCK_RUNTIME_NAME=""),
        patch.object(sandbox_bedrock, "control_client", return_value=control),
    ):
        with pytest.raises(SandboxRuntimeUnavailable, match="UPDATING"):
            await sandbox_bedrock.resolve_runtime_arn()
        control.get_paginator.return_value.paginate.return_value = [
            {"agentRuntimes": [{"agentRuntimeName": "seizu_sandbox_prod", "status": "READY", "agentRuntimeArn": ARN}]}
        ]
        assert await sandbox_bedrock.resolve_runtime_arn() == ARN
        control.get_paginator.return_value.paginate.return_value = []
        assert await sandbox_bedrock.resolve_runtime_arn() == ARN
    sandbox_bedrock._reset_runtime_cache()


def test_runtime_name_is_derived_and_sanitized() -> None:
    with _settings(SANDBOX_BEDROCK_RUNTIME_NAME="", SEIZU_DEPLOYMENT_ID="prod-eu.1"):
        assert sandbox_bedrock.runtime_name() == "seizu_sandbox_prod_eu_1"
    with _settings(SANDBOX_BEDROCK_RUNTIME_NAME="custom_name"):
        assert sandbox_bedrock.runtime_name() == "custom_name"


# ---------------------------------------------------------------------------
# s3 storage
# ---------------------------------------------------------------------------


class _FakeS3:
    """Just enough of an S3 client for prefix listing, markers and deletes."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get_paginator(self, name: str) -> Any:
        s3 = self

        class _Paginator:
            def paginate(self, Bucket: str, Prefix: str, Delimiter: str = "") -> list[dict[str, Any]]:  # noqa: N803
                keys = sorted(k for k in s3.objects if k.startswith(Prefix))
                if Delimiter:
                    prefixes = sorted({Prefix + k[len(Prefix) :].split(Delimiter)[0] + Delimiter for k in keys})
                    return [{"CommonPrefixes": [{"Prefix": p} for p in prefixes]}]
                return [{"Contents": [{"Key": k} for k in keys]}]

        return _Paginator()

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": ""}}, "GetObject")
        body = MagicMock()
        body.read.return_value = self.objects[Key]
        return {"Body": body}

    log: list[str]
    #: Per-key errors the next delete_objects calls report instead of deleting.
    failures: list[dict[str, str]]

    def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
        errors: list[dict[str, str]] = []
        failing = {f["Key"]: f for f in getattr(self, "failures", [])}
        for obj in Delete["Objects"]:
            if obj["Key"] in failing:
                errors.append(failing[obj["Key"]])
                continue
            self.objects.pop(obj["Key"], None)
            getattr(self, "log", []).append(f"delete {obj['Key']}")
        self.failures = []
        return {"Errors": errors} if errors else {}

    def delete_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        self.objects.pop(Key, None)
        getattr(self, "log", []).append(f"delete {Key}")
        return {}


def _marker(deployment: str, thread: str = "user:u1:thread:t1") -> bytes:
    return json.dumps(
        {
            "deployment": deployment,
            "purpose": "chat-session",
            "thread": thread,
            "created_at": "2026-08-01T00:00:00+00:00",
        }
    ).encode()


@pytest.fixture
def s3_storage(agentcore: FakeAgentCore) -> Iterator[_FakeS3]:
    s3 = _FakeS3()
    with (
        _settings(SANDBOX_BEDROCK_STORAGE="s3", SANDBOX_BEDROCK_S3_BUCKET="bucket", SANDBOX_BEDROCK_S3_PREFIX=""),
        patch.object(sandbox_bedrock, "s3_client", return_value=s3),
    ):
        yield s3


async def test_s3_kill_deletes_the_prefix_without_booting_the_session(
    agentcore: FakeAgentCore, s3_storage: _FakeS3
) -> None:
    sandbox_id = sandbox_bedrock.new_session_id()
    prefix = f"seizu-sandbox/prod/{sandbox_id}/"
    s3_storage.objects[f"{prefix}{MARKER_FILENAME}"] = _marker("prod")
    s3_storage.objects[f"{prefix}seizu_results/r.json"] = b"{}"
    s3_storage.objects["seizu-sandbox/prod/other/keep"] = b"x"

    await kill_sandbox(sandbox_id)

    assert sorted(s3_storage.objects) == ["seizu-sandbox/prod/other/keep"]
    assert agentcore.invocations == []
    assert agentcore.stopped == [sandbox_id]


async def test_s3_listing_reads_markers_and_filters_by_deployment(
    agentcore: FakeAgentCore, s3_storage: _FakeS3
) -> None:
    ours, theirs, untagged = (sandbox_bedrock.new_session_id() for _ in range(3))
    s3_storage.objects[f"seizu-sandbox/prod/{ours}/{MARKER_FILENAME}"] = _marker("prod")
    s3_storage.objects[f"seizu-sandbox/prod/{theirs}/{MARKER_FILENAME}"] = _marker("staging")
    s3_storage.objects[f"seizu-sandbox/prod/{untagged}/file.txt"] = b"x"

    snapshots = await list_paused_sandboxes()
    assert [s.sandbox_id for s in snapshots] == [ours]
    assert snapshots[0].ours and snapshots[0].thread == "user:u1:thread:t1"
    assert snapshots[0].started_at is not None

    every = {s.sandbox_id for s in await list_paused_sandboxes(all_owners=True)}
    assert every == {ours, theirs, untagged}

    assert await sandbox_is_paused(ours) is True
    assert await sandbox_is_paused(untagged) is False


async def test_s3_storage_tells_the_model_about_object_semantics(agentcore: FakeAgentCore, s3_storage: _FakeS3) -> None:
    from reporting.services.mcp_builtins import sandbox as sandbox_tool

    note = sandbox_backend.workspace_note()
    assert "whole" in note
    assert note in sandbox_tool._subagent_prompt({"graph__query"})


def test_session_storage_needs_no_workspace_note(agentcore: FakeAgentCore) -> None:
    from reporting.services.mcp_builtins import sandbox as sandbox_tool

    assert sandbox_backend.workspace_note() == ""
    assert sandbox_tool._subagent_prompt({"graph__query"}).endswith("description of what you tried.")


# ---------------------------------------------------------------------------
# Retries never run code twice
# ---------------------------------------------------------------------------


def _server_error(status: int = 500, code: str = "InternalServerException") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "x"}, "ResponseMetadata": {"HTTPStatusCode": status}}, "InvokeAgentRuntime"
    )


def _fail_after_running(agentcore: FakeAgentCore, op: str, error: Exception) -> MagicMock:
    """Run ``op`` for real, then report ``error`` once, as if the response were lost."""
    real = agentcore.invoke_agent_runtime
    state = {"failed": False}

    def _invoke(**kwargs: Any) -> dict[str, Any]:
        response = real(**kwargs)
        if json.loads(kwargs["payload"])["op"] == op and not state["failed"]:
            state["failed"] = True
            raise error
        return response

    return MagicMock(side_effect=_invoke)


def test_the_data_client_never_retries_on_its_own() -> None:
    sandbox_bedrock._reset_runtime_cache()
    with patch.object(sandbox_bedrock, "get_boto_client") as make:
        sandbox_bedrock.data_client()
    sandbox_bedrock._reset_runtime_cache()
    assert make.call_args.kwargs["config"].retries == {"mode": "standard", "total_max_attempts": 1}


@pytest.mark.parametrize(
    "error",
    [
        _server_error(),
        _server_error(502, "BadGateway"),
        ReadTimeoutError(endpoint_url="https://bedrock-agentcore"),
    ],
)
async def test_code_is_not_run_again_after_an_ambiguous_failure(agentcore: FakeAgentCore, error: Exception) -> None:
    async with _open() as backend:
        invoke = _fail_after_running(agentcore, "run_python", error)
        with patch.object(agentcore, "invoke_agent_runtime", invoke):
            with pytest.raises(type(error)):
                await backend.run_python("open('runs.txt', 'a').write('x')")
        assert await backend.read_file("/home/user/runs.txt") == "x"


async def test_a_repeatable_operation_is_retried_after_an_ambiguous_failure(agentcore: FakeAgentCore) -> None:
    async with _open() as backend:
        invoke = _fail_after_running(agentcore, "write_file", _server_error())
        with (
            patch.object(agentcore, "invoke_agent_runtime", invoke),
            patch.object(sandbox_bedrock.asyncio, "sleep", AsyncMock()),
        ):
            await backend.write_file("/home/user/a.txt", "content")
        assert invoke.call_count == 2
        assert await backend.read_file("/home/user/a.txt") == "content"


async def test_a_throttled_execution_is_retried_because_it_never_ran(agentcore: FakeAgentCore) -> None:
    async with _open() as backend:
        real = agentcore.invoke_agent_runtime
        throttled = {"done": False}

        def _invoke(**kwargs: Any) -> dict[str, Any]:
            if json.loads(kwargs["payload"])["op"] == "run_python" and not throttled["done"]:
                throttled["done"] = True
                raise ClientError({"Error": {"Code": "ThrottlingException", "Message": ""}}, "InvokeAgentRuntime")
            return real(**kwargs)

        with (
            patch.object(agentcore, "invoke_agent_runtime", side_effect=_invoke),
            patch.object(sandbox_bedrock.asyncio, "sleep", AsyncMock()),
        ):
            await backend.run_python("open('runs.txt', 'a').write('x')")
        assert await backend.read_file("/home/user/runs.txt") == "x"


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


async def test_session_storage_kill_stops_first_and_absorbs_the_cold_start(agentcore: FakeAgentCore) -> None:
    async with _open(suspend_on_exit=True) as backend:
        sandbox_id = backend.sandbox_id
        await backend.write_file("/home/user/secret.txt", "x")
    agentcore.stopped.clear()
    agentcore.invocations.clear()
    # The wipe boots a stopped session, which answers 409 while it starts.
    agentcore.conflicts_before_ready = 2

    with patch.object(sandbox_bedrock.asyncio, "sleep", AsyncMock()):
        await kill_sandbox(sandbox_id)

    assert list(agentcore.image(sandbox_id).mount.iterdir()) == []
    assert agentcore.invocations == [(sandbox_id, "wipe")]
    assert agentcore.stopped == [sandbox_id, sandbox_id]


@pytest.fixture
def s3_log(agentcore: FakeAgentCore, s3_storage: _FakeS3) -> Iterator[list[str]]:
    """One log of stops and S3 deletes, in the order they happened."""
    log: list[str] = []
    s3_storage.log = log
    real_stop = agentcore.stop_runtime_session

    def _stop(**kwargs: Any) -> dict[str, Any]:
        log.append("stop")
        return real_stop(**kwargs)

    with patch.object(agentcore, "stop_runtime_session", side_effect=_stop):
        yield log


def _s3_workspace(s3: _FakeS3, sandbox_id: str, *names: str) -> str:
    prefix = f"seizu-sandbox/prod/{sandbox_id}/"
    s3.objects[f"{prefix}{MARKER_FILENAME}"] = _marker("prod")
    for name in names:
        s3.objects[f"{prefix}{name}"] = b"data"
    return prefix


async def test_s3_kill_stops_the_session_before_deleting_and_the_marker_goes_last(
    agentcore: FakeAgentCore, s3_storage: _FakeS3, s3_log: list[str]
) -> None:
    sandbox_id = sandbox_bedrock.new_session_id()
    prefix = _s3_workspace(s3_storage, sandbox_id, "a.txt", "b.txt")

    await kill_sandbox(sandbox_id)

    assert s3_log[0] == "stop"
    assert s3_log[-1] == f"delete {prefix}{MARKER_FILENAME}"
    assert not any(k.startswith(prefix) for k in s3_storage.objects)


async def test_s3_kill_that_cannot_stop_deletes_nothing(agentcore: FakeAgentCore, s3_storage: _FakeS3) -> None:
    sandbox_id = sandbox_bedrock.new_session_id()
    prefix = _s3_workspace(s3_storage, sandbox_id, "a.txt")
    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": ""}}, "StopRuntimeSession")

    with patch.object(agentcore, "stop_runtime_session", side_effect=denied):
        with pytest.raises(ClientError):
            await kill_sandbox(sandbox_id)

    assert sorted(s3_storage.objects) == [f"{prefix}{MARKER_FILENAME}", f"{prefix}a.txt"]


async def test_s3_kill_reports_keys_it_could_not_delete_and_keeps_the_marker(
    agentcore: FakeAgentCore, s3_storage: _FakeS3
) -> None:
    sandbox_id = sandbox_bedrock.new_session_id()
    prefix = _s3_workspace(s3_storage, sandbox_id, "secret.txt", "other.txt")
    s3_storage.failures = [{"Key": f"{prefix}secret.txt", "Code": "AccessDenied", "Message": "Access Denied"}]

    with pytest.raises(RuntimeError, match="secret.txt: AccessDenied"):
        await kill_sandbox(sandbox_id)

    # Still findable by the orphan sweep, so the delete can be retried.
    assert sorted(s3_storage.objects) == [f"{prefix}{MARKER_FILENAME}", f"{prefix}secret.txt"]
    assert await sandbox_is_paused(sandbox_id) is True


async def test_s3_kill_retries_transient_key_failures(agentcore: FakeAgentCore, s3_storage: _FakeS3) -> None:
    sandbox_id = sandbox_bedrock.new_session_id()
    prefix = _s3_workspace(s3_storage, sandbox_id, "a.txt")
    s3_storage.failures = [{"Key": f"{prefix}a.txt", "Code": "SlowDown", "Message": ""}]

    with patch.object(sandbox_bedrock.time, "sleep"):
        await kill_sandbox(sandbox_id)

    assert not any(k.startswith(prefix) for k in s3_storage.objects)


async def test_s3_kill_finds_objects_written_while_it_deletes(agentcore: FakeAgentCore, s3_storage: _FakeS3) -> None:
    sandbox_id = sandbox_bedrock.new_session_id()
    prefix = _s3_workspace(s3_storage, sandbox_id, "a.txt")
    real_delete = s3_storage.delete_objects
    late = {"written": False}

    def _delete_then_a_late_upload(**kwargs: Any) -> dict[str, Any]:
        response = real_delete(**kwargs)
        if not late["written"]:
            late["written"] = True
            s3_storage.objects[f"{prefix}late.txt"] = b"flushed after the listing"
        return response

    with patch.object(s3_storage, "delete_objects", side_effect=_delete_then_a_late_upload):
        await kill_sandbox(sandbox_id)

    assert not any(k.startswith(prefix) for k in s3_storage.objects)


async def test_s3_kill_gives_up_on_a_workspace_that_keeps_refilling(
    agentcore: FakeAgentCore, s3_storage: _FakeS3
) -> None:
    sandbox_id = sandbox_bedrock.new_session_id()
    prefix = _s3_workspace(s3_storage, sandbox_id, "a.txt")
    real_delete = s3_storage.delete_objects

    def _refill(**kwargs: Any) -> dict[str, Any]:
        response = real_delete(**kwargs)
        s3_storage.objects[f"{prefix}again.txt"] = b"x"
        return response

    with patch.object(s3_storage, "delete_objects", side_effect=_refill):
        with pytest.raises(RuntimeError, match="remain"):
            await kill_sandbox(sandbox_id)
    assert f"{prefix}{MARKER_FILENAME}" in s3_storage.objects


# ---------------------------------------------------------------------------
# Thread pools
# ---------------------------------------------------------------------------


async def test_cleanup_runs_while_executions_hold_every_execution_thread(agentcore: FakeAgentCore) -> None:
    import asyncio
    import threading

    release = threading.Event()
    saved = dict(sandbox_bedrock._executors)
    sandbox_bedrock._executors.clear()
    try:
        with patch.object(sandbox_bedrock, "_EXEC_THREADS", 1):
            busy = asyncio.ensure_future(sandbox_bedrock.run_blocking(release.wait, 30, pool="exec"))
            queued = asyncio.ensure_future(sandbox_bedrock.run_blocking(release.wait, 30, pool="exec"))
            await asyncio.sleep(0.05)
            busy.cancel()
            queued.cancel()
            # The execution thread is still held by the cancelled call; a stop
            # must not wait for it.
            await asyncio.wait_for(sandbox_bedrock.stop_session(ARN, sandbox_bedrock.new_session_id()), timeout=5)
    finally:
        release.set()
        for executor in sandbox_bedrock._executors.values():
            executor.shutdown(wait=True)
        sandbox_bedrock._executors.clear()
        sandbox_bedrock._executors.update(saved)
