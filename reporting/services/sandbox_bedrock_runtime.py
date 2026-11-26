"""Provision the Amazon Bedrock AgentCore runtime that hosts sandboxes.

:func:`reconcile_runtime` makes the runtime named
:func:`sandbox_bedrock.runtime_name` match the ``SANDBOX_BEDROCK_*`` settings:
it creates the runtime when it is missing and updates it only when what is
deployed differs from what is configured. It runs in the Temporal worker at
startup; web processes never write, they only look the runtime up
(:func:`sandbox_bedrock.resolve_runtime_arn`).

Every Create/Update carries a ``clientToken`` derived from the configuration it
applies and the version it replaces, so worker replicas starting together send
identical requests that AgentCore applies once (SBX-021).

An update creates a new runtime version, and with ``session`` storage a new
version starts every session with an empty workspace. That is why nothing is
updated unless a configured value actually changed, and why an update is logged
as a warning.
"""

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from reporting.services import sandbox_bedrock
from reporting.services.sandbox_protocol import WORKSPACE_MOUNT

logger = logging.getLogger(__name__)

#: How long to wait for the runtime to reach READY after a change.
_READY_TIMEOUT_SECONDS = 20 * 60
_POLL_SECONDS = 10.0

_PLATFORM_VERSION = "V2"


def _filesystem_configuration() -> dict[str, Any]:
    from reporting import settings

    if sandbox_bedrock.storage_mode() == "s3":
        mount: dict[str, Any] = {
            "bucket": settings.SANDBOX_BEDROCK_S3_BUCKET,
            "mountPath": WORKSPACE_MOUNT,
            # AgentCore substitutes the session id, giving each sandbox its own
            # prefix -- the same layout sandbox_bedrock lists and deletes.
            "prefix": f"{sandbox_bedrock.s3_prefix()}/{{runtimeSessionId}}/",
        }
        if settings.SANDBOX_BEDROCK_S3_BUCKET_OWNER:
            mount["s3BucketOwner"] = settings.SANDBOX_BEDROCK_S3_BUCKET_OWNER
        if settings.SANDBOX_BEDROCK_S3_BUCKET_REGION:
            mount["s3BucketRegion"] = settings.SANDBOX_BEDROCK_S3_BUCKET_REGION
        return {"s3BucketMount": mount}
    return {"sessionStorage": {"mountPath": WORKSPACE_MOUNT}}


def desired_configuration() -> dict[str, Any]:
    """The runtime fields Seizu manages, as Create/Update request parameters."""
    from reporting import settings

    if settings.SANDBOX_BEDROCK_SUBNETS:
        network: dict[str, Any] = {
            "networkMode": "VPC",
            "networkModeConfig": {
                "subnets": list(settings.SANDBOX_BEDROCK_SUBNETS),
                "securityGroups": list(settings.SANDBOX_BEDROCK_SECURITY_GROUPS),
            },
        }
    else:
        network = {"networkMode": "PUBLIC"}
    return {
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": settings.SANDBOX_BEDROCK_IMAGE}},
        "roleArn": settings.SANDBOX_BEDROCK_ROLE_ARN,
        "networkConfiguration": network,
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "lifecycleConfiguration": {
            "idleRuntimeSessionTimeout": settings.SANDBOX_SESSION_TIMEOUT_SECONDS,
            "maxLifetime": settings.SANDBOX_BEDROCK_MAX_LIFETIME_SECONDS,
        },
        "filesystemConfigurations": [_filesystem_configuration()],
        "platformVersion": _PLATFORM_VERSION,
    }


def _matches(desired: Any, current: Any) -> bool:
    """Whether everything in ``desired`` is present, equal, in ``current``.

    Fields the service adds on its side (defaults, ids) are ignored; lists of
    plain values compare as sets, since the service may reorder subnets.
    """
    if isinstance(desired, dict):
        return isinstance(current, dict) and all(_matches(v, current.get(k)) for k, v in desired.items())
    if isinstance(desired, list):
        if not isinstance(current, list) or len(desired) != len(current):
            return False
        if all(not isinstance(item, (dict, list)) for item in desired):
            return sorted(map(str, desired)) == sorted(map(str, current))
        return all(_matches(d, c) for d, c in zip(desired, current, strict=True))
    return bool(desired == current)


def differences(desired: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Names of the managed fields whose deployed value differs from the configured one."""
    changed = [key for key, value in desired.items() if not _matches(value, current.get(key))]
    if not (current.get("metadataConfiguration") or {}).get("requireMMDSV2"):
        changed.append("metadataConfiguration")
    return changed


def _client_token(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


async def _get(runtime_id: str) -> dict[str, Any]:
    client = sandbox_bedrock.control_client()
    return dict(await asyncio.to_thread(client.get_agent_runtime, agentRuntimeId=runtime_id))


async def _wait_ready(runtime_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    while True:
        runtime = await _get(runtime_id)
        status = runtime.get("status", "")
        if status == "READY":
            return runtime
        if status.endswith("_FAILED"):
            raise RuntimeError(
                f"sandbox runtime {runtime.get('agentRuntimeName')!r} is {status}: {runtime.get('failureReason', '')}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"sandbox runtime {runtime.get('agentRuntimeName')!r} still {status}")
        await asyncio.sleep(_POLL_SECONDS)


async def _update(runtime: dict[str, Any], desired: dict[str, Any], changed: list[str]) -> dict[str, Any]:
    runtime_id = str(runtime["agentRuntimeId"])
    logger.warning(
        "Updating sandbox runtime %s (%s). This creates a new runtime version%s.",
        runtime.get("agentRuntimeName"),
        ", ".join(changed),
        "; with session storage every sandbox starts again with an empty workspace"
        if sandbox_bedrock.storage_mode() != "s3"
        else "",
    )
    client = sandbox_bedrock.control_client()
    await asyncio.to_thread(
        client.update_agent_runtime,
        agentRuntimeId=runtime_id,
        clientToken=_client_token("update", runtime_id, runtime.get("agentRuntimeVersion"), desired),
        metadataConfiguration={"requireMMDSV2": True},
        **desired,
    )
    return await _wait_ready(runtime_id)


async def reconcile_runtime() -> dict[str, Any] | None:
    """Create or update the sandbox runtime to match settings; return it when READY.

    Does nothing (returns ``None``) unless the sandbox is enabled on the bedrock
    provider with ``SANDBOX_BEDROCK_MANAGE_RUNTIME``.
    """
    from reporting import settings
    from reporting.services.sandbox_backend import deployment_id

    if not (
        settings.SANDBOX_ENABLED and settings.SANDBOX_PROVIDER == "bedrock" and settings.SANDBOX_BEDROCK_MANAGE_RUNTIME
    ):
        return None
    name = sandbox_bedrock.runtime_name()
    desired = desired_configuration()
    summary = await asyncio.to_thread(sandbox_bedrock.find_runtime)
    if summary is None:
        logger.info("Creating sandbox runtime %s", name)
        client = sandbox_bedrock.control_client()
        created = await asyncio.to_thread(
            client.create_agent_runtime,
            agentRuntimeName=name,
            description="Seizu chat sandbox (seizu-sandbox/1)",
            tags={"seizu:deployment": deployment_id()},
            clientToken=_client_token("create", name, desired),
            **desired,
        )
        runtime = await _wait_ready(str(created["agentRuntimeId"]))
    else:
        runtime_id = str(summary["agentRuntimeId"])
        runtime = await _get(runtime_id)
        if runtime.get("status") in {"CREATING", "UPDATING"}:
            runtime = await _wait_ready(runtime_id)
        elif runtime.get("status") == "CREATE_FAILED":
            raise RuntimeError(
                f"sandbox runtime {name!r} failed to create ({runtime.get('failureReason', '')}); delete it so "
                "Seizu can create it again"
            )
    # A new runtime needs the metadata setting Create does not accept, so this
    # also runs once right after creation.
    changed = differences(desired, runtime)
    if changed:
        runtime = await _update(runtime, desired, changed)
    logger.info(
        "Sandbox runtime ready",
        extra={"runtime": name, "runtime_version": runtime.get("agentRuntimeVersion", "")},
    )
    return runtime


async def reconcile_in_background() -> None:
    """:func:`reconcile_runtime`, with failures logged rather than raised.

    Run as a task beside the worker: provisioning takes minutes, and the
    worker's other activities must not wait on it. Until it succeeds, opening a
    sandbox fails the way it does with no runtime at all.
    """
    try:
        await reconcile_runtime()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Sandbox runtime reconciliation failed; Bedrock sandboxes are unavailable until it succeeds")
