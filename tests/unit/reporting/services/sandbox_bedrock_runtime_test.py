"""Provisioning the AgentCore runtime from settings (SBX-021).

An update erases every session-storage workspace, so the property under test is
as much "does nothing when nothing changed" as "applies a change".
"""

import copy
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from reporting.services import sandbox_bedrock, sandbox_bedrock_runtime
from reporting.services.sandbox_bedrock import SandboxRuntimeUnavailable
from reporting.services.sandbox_bedrock_runtime import (
    desired_configuration,
    differences,
    operator_runtime_findings,
    reconcile_runtime,
)

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/seizu_sandbox_prod-abc"


def _settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "SANDBOX_ENABLED": True,
        "SANDBOX_PROVIDER": "bedrock",
        "SANDBOX_BEDROCK_MANAGE_RUNTIME": True,
        "SANDBOX_BEDROCK_IMAGE": "123456789012.dkr.ecr.us-east-1.amazonaws.com/seizu-sandbox:1",
        "SANDBOX_BEDROCK_ROLE_ARN": "arn:aws:iam::123456789012:role/seizu-sandbox",
        "SANDBOX_BEDROCK_SUBNETS": ["subnet-a", "subnet-b"],
        "SANDBOX_BEDROCK_SECURITY_GROUPS": ["sg-1"],
        "SANDBOX_BEDROCK_RUNTIME_NAME": "",
        "SANDBOX_BEDROCK_RUNTIME_ARN": "",
        "SANDBOX_BEDROCK_QUALIFIER": "DEFAULT",
        "SANDBOX_BEDROCK_STORAGE": "session",
        "SANDBOX_BEDROCK_S3_BUCKET": "",
        "SANDBOX_BEDROCK_S3_PREFIX": "",
        "SANDBOX_BEDROCK_S3_BUCKET_OWNER": "",
        "SANDBOX_BEDROCK_S3_BUCKET_REGION": "",
        "SANDBOX_SESSION_TIMEOUT_SECONDS": 1800,
        "SANDBOX_BEDROCK_MAX_LIFETIME_SECONDS": 28800,
        "SANDBOX_ALLOW_INTERNET": False,
        "SEIZU_DEPLOYMENT_ID": "prod",
    }
    values.update(overrides)
    return patch.multiple("reporting.settings", **values)


class _FakeControl:
    """A control plane holding at most one runtime, READY after every change."""

    def __init__(self, runtime: dict[str, Any] | None = None) -> None:
        self.runtime = runtime
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []

    def get_paginator(self, name: str) -> Any:
        paginator = MagicMock()
        summaries = (
            [
                {
                    "agentRuntimeName": self.runtime["agentRuntimeName"],
                    "agentRuntimeId": self.runtime["agentRuntimeId"],
                    "agentRuntimeArn": ARN,
                    "status": self.runtime["status"],
                }
            ]
            if self.runtime
            else []
        )
        paginator.paginate.return_value = [{"agentRuntimes": summaries}]
        return paginator

    def get_agent_runtime(self, agentRuntimeId: str) -> dict[str, Any]:  # noqa: N803
        assert self.runtime is not None
        return copy.deepcopy(self.runtime)

    def create_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        fields = {k: v for k, v in kwargs.items() if k not in {"clientToken", "tags", "description"}}
        self.runtime = {**fields, "agentRuntimeId": "rt-1", "agentRuntimeVersion": "1", "status": "READY"}
        return {"agentRuntimeId": "rt-1", "agentRuntimeArn": ARN, "status": "CREATING"}

    def update_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        self.updated.append(kwargs)
        assert self.runtime is not None
        fields = {k: v for k, v in kwargs.items() if k not in {"clientToken", "agentRuntimeId"}}
        version = str(int(self.runtime["agentRuntimeVersion"]) + 1)
        self.runtime = {**self.runtime, **fields, "agentRuntimeVersion": version, "status": "READY"}
        return {"agentRuntimeId": "rt-1", "status": "UPDATING"}


@pytest.fixture
def control() -> Iterator[_FakeControl]:
    fake = _FakeControl()
    with _settings(), patch.object(sandbox_bedrock, "control_client", return_value=fake):
        yield fake


async def test_creates_the_runtime_then_requires_mmdsv2(control: _FakeControl) -> None:
    runtime = await reconcile_runtime()

    assert runtime is not None and runtime["status"] == "READY"
    (create,) = control.created
    assert create["agentRuntimeName"] == "seizu_sandbox_prod"
    assert create["tags"] == {"seizu:deployment": "prod"}
    assert create["networkConfiguration"] == {
        "networkMode": "VPC",
        "networkModeConfig": {"subnets": ["subnet-a", "subnet-b"], "securityGroups": ["sg-1"]},
    }
    assert create["filesystemConfigurations"] == [{"sessionStorage": {"mountPath": "/mnt/workspace"}}]
    assert create["lifecycleConfiguration"] == {"idleRuntimeSessionTimeout": 1800, "maxLifetime": 28800}
    assert create["platformVersion"] == "V2"
    assert len(create["clientToken"]) == 64
    # Create does not accept the metadata setting, so one update follows it.
    (update,) = control.updated
    assert update["metadataConfiguration"] == {"requireMMDSV2": True}


async def test_an_unchanged_runtime_is_left_alone(control: _FakeControl) -> None:
    await reconcile_runtime()
    control.created.clear()
    control.updated.clear()

    await reconcile_runtime()

    assert control.created == [] and control.updated == []


async def test_service_side_ordering_and_defaults_are_not_changes(control: _FakeControl) -> None:
    await reconcile_runtime()
    assert control.runtime is not None
    control.runtime["networkConfiguration"]["networkModeConfig"]["subnets"] = ["subnet-b", "subnet-a"]
    control.runtime["networkConfiguration"]["networkModeConfig"]["requireServiceS3Endpoint"] = False
    control.runtime["workloadIdentityDetails"] = {"workloadIdentityArn": "x"}

    assert differences(desired_configuration(), control.runtime) == []


async def test_a_changed_image_is_applied_with_a_warning(control: _FakeControl, caplog: Any) -> None:
    await reconcile_runtime()
    control.updated.clear()

    with patch(
        "reporting.settings.SANDBOX_BEDROCK_IMAGE", "123456789012.dkr.ecr.us-east-1.amazonaws.com/seizu-sandbox:2"
    ):
        await reconcile_runtime()

    (update,) = control.updated
    assert update["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"].endswith(":2")
    assert "empty workspace" in caplog.text


async def test_update_tokens_differ_per_replaced_version(control: _FakeControl) -> None:
    await reconcile_runtime()
    first_token = control.updated[0]["clientToken"]
    with patch("reporting.settings.SANDBOX_BEDROCK_IMAGE", "img:2"):
        await reconcile_runtime()
    with patch(
        "reporting.settings.SANDBOX_BEDROCK_IMAGE",
        control.created[0]["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"],
    ):
        await reconcile_runtime()
    tokens = [u["clientToken"] for u in control.updated]
    assert len(tokens) == 3 and len(set(tokens)) == 3 and tokens[0] == first_token


async def test_s3_storage_mounts_a_per_session_prefix(control: _FakeControl) -> None:
    with _settings(
        SANDBOX_BEDROCK_STORAGE="s3",
        SANDBOX_BEDROCK_S3_BUCKET="sandboxes",
        SANDBOX_BEDROCK_S3_BUCKET_REGION="us-west-2",
    ):
        await reconcile_runtime()
    assert control.created[0]["filesystemConfigurations"] == [
        {
            "s3BucketMount": {
                "bucket": "sandboxes",
                "mountPath": "/mnt/workspace",
                "prefix": "seizu-sandbox/prod/{runtimeSessionId}/",
                "s3BucketRegion": "us-west-2",
            }
        }
    ]


async def test_public_network_mode_without_subnets(control: _FakeControl) -> None:
    with _settings(SANDBOX_BEDROCK_SUBNETS=[], SANDBOX_BEDROCK_SECURITY_GROUPS=[], SANDBOX_ALLOW_INTERNET=True):
        await reconcile_runtime()
    assert control.created[0]["networkConfiguration"] == {"networkMode": "PUBLIC"}


async def test_nothing_happens_unless_managed_here(control: _FakeControl) -> None:
    for overrides in (
        {"SANDBOX_PROVIDER": "e2b"},
        {"SANDBOX_ENABLED": False},
    ):
        with _settings(**overrides):
            assert await reconcile_runtime() is None
    assert control.created == [] and control.updated == []


async def test_a_failed_creation_is_reported_not_retried(control: _FakeControl) -> None:
    control.runtime = {
        "agentRuntimeName": "seizu_sandbox_prod",
        "agentRuntimeId": "rt-1",
        "agentRuntimeVersion": "1",
        "status": "CREATE_FAILED",
        "failureReason": "image not found",
    }
    with pytest.raises(RuntimeError, match="image not found"):
        await reconcile_runtime()
    assert control.updated == []


async def test_background_reconcile_logs_instead_of_raising(control: _FakeControl, caplog: Any) -> None:
    with patch.object(sandbox_bedrock_runtime, "reconcile_runtime", side_effect=RuntimeError("boom")):
        await sandbox_bedrock_runtime.reconcile_in_background()
    assert "Sandbox runtime reconciliation failed" in caplog.text


# ---------------------------------------------------------------------------
# A runtime an operator manages
# ---------------------------------------------------------------------------

OPERATOR_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/seizu_sandbox_prod-a1B2c3D4e5"
_S3_MOUNT = {
    "s3BucketMount": {
        "bucket": "sandboxes",
        "mountPath": "/mnt/workspace",
        "prefix": "seizu-sandbox/prod/{runtimeSessionId}/",
    }
}


def _version(version: str, **fields: Any) -> dict[str, Any]:
    runtime: dict[str, Any] = {
        "agentRuntimeArn": OPERATOR_ARN,
        "agentRuntimeId": "seizu_sandbox_prod-a1B2c3D4e5",
        "agentRuntimeName": "seizu_sandbox_prod",
        "agentRuntimeVersion": version,
        "status": "READY",
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "networkConfiguration": {"networkMode": "VPC"},
        "filesystemConfigurations": [copy.deepcopy(_S3_MOUNT)],
        "metadataConfiguration": {"requireMMDSV2": True},
    }
    runtime.update(fields)
    return runtime


class _ReadOnlyControl:
    """A control plane with runtime versions and endpoints, and no write calls.

    Seizu must never write to a runtime it does not manage, so the methods that
    would are absent: calling one fails the test with AttributeError.
    """

    def __init__(self) -> None:
        self.versions = {"1": _version("1"), "2": _version("2")}
        self.endpoints = {"prod": {"status": "READY", "liveVersion": "2"}}
        self.listed = 0
        self.endpoint_error: Exception | None = None

    def get_paginator(self, name: str) -> Any:
        self.listed += 1
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "agentRuntimes": [
                    {
                        "agentRuntimeName": "seizu_sandbox_prod",
                        "agentRuntimeId": "seizu_sandbox_prod-a1B2c3D4e5",
                        "agentRuntimeArn": OPERATOR_ARN,
                        "status": "READY",
                    }
                ]
            }
        ]
        return paginator

    def get_agent_runtime_endpoint(self, agentRuntimeId: str, endpointName: str) -> dict[str, Any]:  # noqa: N803
        assert agentRuntimeId == "seizu_sandbox_prod-a1B2c3D4e5"
        if self.endpoint_error is not None:
            raise self.endpoint_error
        if endpointName not in self.endpoints:
            raise ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "GetAgentRuntimeEndpoint")
        return dict(self.endpoints[endpointName])

    def get_agent_runtime(self, agentRuntimeId: str, agentRuntimeVersion: str) -> dict[str, Any]:  # noqa: N803
        assert agentRuntimeId == "seizu_sandbox_prod-a1B2c3D4e5"
        return copy.deepcopy(self.versions[agentRuntimeVersion])


@pytest.fixture
def operator() -> Iterator[_ReadOnlyControl]:
    fake = _ReadOnlyControl()
    sandbox_bedrock._reset_runtime_cache()
    with (
        _settings(
            SANDBOX_BEDROCK_MANAGE_RUNTIME=False,
            SANDBOX_BEDROCK_IMAGE="",
            SANDBOX_BEDROCK_ROLE_ARN="",
            SANDBOX_BEDROCK_RUNTIME_ARN=OPERATOR_ARN,
            SANDBOX_BEDROCK_QUALIFIER="prod",
            SANDBOX_BEDROCK_STORAGE="s3",
            SANDBOX_BEDROCK_S3_BUCKET="sandboxes",
        ),
        patch.object(sandbox_bedrock, "control_client", return_value=fake),
    ):
        yield fake
    sandbox_bedrock._reset_runtime_cache()


async def test_an_operator_runtime_is_read_at_the_version_its_endpoint_serves(operator: _ReadOnlyControl) -> None:
    operator.versions["1"]["filesystemConfigurations"] = [{"sessionStorage": {"mountPath": "/mnt/workspace"}}]

    assert await sandbox_bedrock.resolve_runtime_arn() == OPERATOR_ARN
    # Found by ARN, so ListAgentRuntimes (which cannot be scoped) is not needed.
    assert operator.listed == 0


async def test_an_operator_runtime_is_found_by_name_without_an_arn(operator: _ReadOnlyControl) -> None:
    with _settings(SANDBOX_BEDROCK_RUNTIME_ARN=""):
        assert await sandbox_bedrock.resolve_runtime_arn() == OPERATOR_ARN
    assert operator.listed == 1


async def test_a_version_mounting_another_workspace_is_refused(operator: _ReadOnlyControl) -> None:
    operator.versions["2"]["filesystemConfigurations"][0]["s3BucketMount"]["prefix"] = "other/{runtimeSessionId}/"

    with pytest.raises(SandboxRuntimeUnavailable, match=r"must mount s3://sandboxes/seizu-sandbox/prod/"):
        await sandbox_bedrock.resolve_runtime_arn()


async def test_moving_the_endpoint_is_checked_again(operator: _ReadOnlyControl) -> None:
    assert await sandbox_bedrock.resolve_runtime_arn() == OPERATOR_ARN
    operator.versions["3"] = _version("3", protocolConfiguration={"serverProtocol": "MCP"})
    operator.endpoints["prod"]["liveVersion"] = "3"

    # Within the recheck window the last answer stands...
    assert await sandbox_bedrock.resolve_runtime_arn() == OPERATOR_ARN
    # ...and after it the new version is checked, and refused.
    with patch.object(sandbox_bedrock, "_OPERATOR_RECHECK_SECONDS", 0.0):
        with pytest.raises(SandboxRuntimeUnavailable, match="version 3.*not HTTP"):
            await sandbox_bedrock.resolve_runtime_arn()
        operator.endpoints["prod"]["liveVersion"] = "2"
        assert await sandbox_bedrock.resolve_runtime_arn() == OPERATOR_ARN


async def test_a_recheck_that_cannot_reach_aws_keeps_the_last_good_answer(operator: _ReadOnlyControl) -> None:
    assert await sandbox_bedrock.resolve_runtime_arn() == OPERATOR_ARN
    operator.endpoint_error = ClientError({"Error": {"Code": "ThrottlingException"}}, "GetAgentRuntimeEndpoint")

    with patch.object(sandbox_bedrock, "_OPERATOR_RECHECK_SECONDS", 0.0):
        assert await sandbox_bedrock.resolve_runtime_arn() == OPERATOR_ARN
    sandbox_bedrock._reset_runtime_cache()
    with pytest.raises(ClientError):
        await sandbox_bedrock.resolve_runtime_arn()


@pytest.mark.parametrize(
    ("endpoints", "match"),
    [
        ({}, "endpoint 'prod' does not exist"),
        ({"prod": {"status": "CREATING"}}, "endpoint 'prod' .* is CREATING"),
    ],
)
async def test_an_endpoint_that_cannot_serve_is_reported(
    operator: _ReadOnlyControl, endpoints: dict[str, Any], match: str
) -> None:
    operator.endpoints = endpoints
    with pytest.raises(SandboxRuntimeUnavailable, match=match):
        await sandbox_bedrock.resolve_runtime_arn()


async def test_an_endpoint_being_moved_serves_its_live_version(operator: _ReadOnlyControl) -> None:
    operator.endpoints["prod"] = {"status": "UPDATING", "liveVersion": "2", "targetVersion": "3"}
    assert await sandbox_bedrock.resolve_runtime_arn() == OPERATOR_ARN


async def test_startup_checks_an_operator_runtime_and_changes_nothing(operator: _ReadOnlyControl, caplog: Any) -> None:
    operator.versions["2"]["metadataConfiguration"] = {}
    with caplog.at_level("INFO"):
        assert await reconcile_runtime() is None
        # The warning is logged when a version is first seen, not on every check.
        with patch.object(sandbox_bedrock, "_OPERATOR_RECHECK_SECONDS", 0.0):
            await sandbox_bedrock.resolve_runtime_arn()
    assert "Sandbox runtime ready" in caplog.text
    assert caplog.text.count("does not require MMDSv2") == 1


@pytest.mark.parametrize(
    ("fields", "settings_overrides", "problem"),
    [
        ({"protocolConfiguration": {"serverProtocol": "MCP"}}, {}, "not HTTP"),
        ({"filesystemConfigurations": []}, {}, "but mounts nothing"),
        (
            {"filesystemConfigurations": [{"sessionStorage": {"mountPath": "/mnt/workspace"}}]},
            {},
            "but mounts session storage at /mnt/workspace",
        ),
        ({}, {"SANDBOX_BEDROCK_S3_BUCKET": "elsewhere"}, "must mount s3://elsewhere/"),
        ({}, {"SANDBOX_BEDROCK_STORAGE": "session"}, "must mount session storage at /mnt/workspace"),
        ({"networkConfiguration": {"networkMode": "PUBLIC"}}, {}, "PUBLIC network mode"),
    ],
)
def test_operator_runtime_problems(fields: dict[str, Any], settings_overrides: dict[str, Any], problem: str) -> None:
    values: dict[str, Any] = {"SANDBOX_BEDROCK_STORAGE": "s3", "SANDBOX_BEDROCK_S3_BUCKET": "sandboxes"}
    values.update(settings_overrides)
    with _settings(**values):
        problems, _ = operator_runtime_findings(_version("2", **fields))
    assert any(problem in p for p in problems), problems


def test_an_operator_runtime_that_fits_has_no_problems() -> None:
    with _settings(SANDBOX_BEDROCK_STORAGE="s3", SANDBOX_BEDROCK_S3_BUCKET="sandboxes"):
        # Another mount beside the workspace, and fields Seizu does not set, are fine.
        extra = {"efsAccessPoint": {"accessPointArn": "arn:efs", "mountPath": "/mnt/data"}}
        runtime = _version("2", filesystemConfigurations=[extra, copy.deepcopy(_S3_MOUNT)])
        assert operator_runtime_findings(runtime) == ([], [])
    with _settings(SANDBOX_ALLOW_INTERNET=True, SANDBOX_BEDROCK_STORAGE="s3", SANDBOX_BEDROCK_S3_BUCKET="sandboxes"):
        public = _version("2", networkConfiguration={"networkMode": "PUBLIC"})
        assert operator_runtime_findings(public) == ([], [])
