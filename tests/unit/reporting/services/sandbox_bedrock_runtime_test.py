"""Provisioning the AgentCore runtime from settings (SBX-021).

An update erases every session-storage workspace, so the property under test is
as much "does nothing when nothing changed" as "applies a change".
"""

import copy
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from reporting.services import sandbox_bedrock, sandbox_bedrock_runtime
from reporting.services.sandbox_bedrock_runtime import desired_configuration, differences, reconcile_runtime

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
        {"SANDBOX_BEDROCK_MANAGE_RUNTIME": False},
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
