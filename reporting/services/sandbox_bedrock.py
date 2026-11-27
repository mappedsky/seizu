"""Sandbox provider backed by Amazon Bedrock AgentCore Runtime.

Each conversation's sandbox is one AgentCore *runtime session*: its
``runtimeSessionId`` is the :attr:`SandboxBackend.sandbox_id`, every operation
is one ``InvokeAgentRuntime`` call carrying a ``seizu-sandbox/1`` request
(:mod:`reporting.services.sandbox_protocol`), and the container answering it is
an image built outside this repository to that protocol.

Lifecycle, mapped onto the contract :func:`sandbox_backend.open_backend` keeps
for every provider (SBX-006, SBX-007, SBX-015, SBX-021):

- **create** -- a new session id, warmed with ``hello`` writing the marker.
- **suspend** -- ``StopRuntimeSession``. The workspace persists (session storage
  or the S3 mount); processes and Python state do not.
- **resume** -- invoke the same id: AgentCore starts a fresh microVM and
  restores the workspace. A missing marker means the workspace is gone, which is
  the terminal case: a new session replaces it.
- **kill** -- delete the workspace (``wipe`` for session storage, the S3 prefix
  for ``s3``), then stop the session.

The runtime itself is provisioned by
:mod:`reporting.services.sandbox_bedrock_runtime`, or by an operator; this
module only finds it (and, for an operator's runtime, has it checked).
"""

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from reporting.services import get_boto_client
from reporting.services.sandbox_protocol import (
    MARKER_FILENAME,
    PROTOCOL_VERSION,
    ErrorCode,
    HelloResult,
    ListFilesResult,
    Op,
    ReadFileResult,
    RunBashResult,
    RunPythonResult,
    SandboxMarker,
    SandboxRequest,
    SandboxResponse,
    WipeResult,
    WriteFileResult,
)

logger = logging.getLogger(__name__)

#: Prefix of every session id Seizu creates. Also how a resume id from another
#: provider is recognised: an E2B id handed to AgentCore is a validation error,
#: not a missing sandbox, and would otherwise wedge the thread.
SESSION_ID_PREFIX = "seizu-"
_SESSION_ID_RE = re.compile(r"^seizu-[0-9a-f]{32}$")

#: Longest a synchronous InvokeAgentRuntime may run, less a margin for the
#: response to come back before the service gives up on it.
_MAX_INVOKE_SECONDS = 840

#: Timeout for the operations that only move bytes.
_FILE_OP_TIMEOUT_SECONDS = 120

#: How long a brand-new session may answer 409 while it is provisioned.
_PROVISION_RETRY_SECONDS = 60.0

#: Pages a listing walks before giving up (mirrors the E2B listing's bound).
_MAX_LIST_PAGES = 200

_RUNTIME_NAME_RE = re.compile(r"[^A-Za-z0-9_]")


class SandboxOpError(RuntimeError):
    """The image reported that an operation itself failed."""

    def __init__(self, code: ErrorCode | str, message: str) -> None:
        super().__init__(f"{code}: {message}" if message else str(code))
        self.code = code


class SandboxRuntimeUnavailable(RuntimeError):
    """The AgentCore runtime does not exist yet or is not ready to serve."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def runtime_name() -> str:
    """The AgentCore runtime name: configured, or derived from the deployment."""
    from reporting import settings
    from reporting.services.sandbox_backend import deployment_id

    if settings.SANDBOX_BEDROCK_RUNTIME_NAME:
        return settings.SANDBOX_BEDROCK_RUNTIME_NAME
    name = _RUNTIME_NAME_RE.sub("_", f"seizu_sandbox_{deployment_id()}")
    return name[:48]


def storage_mode() -> str:
    from reporting import settings

    return settings.SANDBOX_BEDROCK_STORAGE


def s3_prefix() -> str:
    """Key prefix under which each session's workspace lives in ``s3`` mode."""
    from reporting import settings
    from reporting.services.sandbox_backend import deployment_id

    return (settings.SANDBOX_BEDROCK_S3_PREFIX or f"seizu-sandbox/{deployment_id()}").strip("/")


def _session_prefix(session_id: str) -> str:
    return f"{s3_prefix()}/{session_id}/"


def new_session_id() -> str:
    return f"{SESSION_ID_PREFIX}{uuid.uuid4().hex}"


def is_session_id(value: str) -> bool:
    """Whether ``value`` is a session id this provider could have created."""
    return bool(_SESSION_ID_RE.match(value or ""))


def _exec_timeout() -> int:
    """Timeout for operations that run code, inside the image."""
    from reporting import settings

    wanted = max(settings.SANDBOX_TIMEOUT_SECONDS, settings.SANDBOX_SCRIPT_TIMEOUT_SECONDS + 15)
    return max(1, min(_MAX_INVOKE_SECONDS, wanted))


# ---------------------------------------------------------------------------
# AWS clients (one per process; tests patch these functions)
# ---------------------------------------------------------------------------

_clients: dict[str, Any] = {}


def _client(service: str, *, read_timeout: int) -> Any:
    from botocore.config import Config

    from reporting import settings

    if service not in _clients:
        config = Config(
            connect_timeout=settings.AWS_CONNECT_TIMEOUT,
            read_timeout=read_timeout,
            retries={"mode": "adaptive", "max_attempts": 5},
        )
        _clients[service] = get_boto_client(service, region=settings.SANDBOX_BEDROCK_REGION or None, config=config)
    return _clients[service]


def data_client() -> Any:
    return _client("bedrock-agentcore", read_timeout=_exec_timeout() + 60)


def control_client() -> Any:
    from reporting import settings

    return _client("bedrock-agentcore-control", read_timeout=settings.AWS_READ_TIMEOUT)


def s3_client() -> Any:
    from reporting import settings

    return _client("s3", read_timeout=settings.AWS_READ_TIMEOUT)


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", ""))
    return ""


# ---------------------------------------------------------------------------
# Runtime lookup
# ---------------------------------------------------------------------------

_runtime_arn: str | None = None
_runtime_version = ""
_runtime_checked_at = 0.0

#: How long an operator-managed runtime's check is trusted. Its endpoint can be
#: moved to another version at any time, so the check is repeated.
_OPERATOR_RECHECK_SECONDS = 300.0


def find_runtime() -> dict[str, Any] | None:
    """The runtime summary named :func:`runtime_name`, or ``None``. Blocking."""
    name = runtime_name()
    paginator = control_client().get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for runtime in page.get("agentRuntimes", []):
            if runtime.get("agentRuntimeName") == name:
                return dict(runtime)
    return None


async def resolve_runtime_arn() -> str:
    """The ARN of a READY runtime, looked up once per process.

    Never creates or changes the runtime: that is the Temporal worker's job
    (:func:`sandbox_bedrock_runtime.reconcile_runtime`). Until the runtime is
    READY every open fails with :class:`SandboxRuntimeUnavailable`, which callers
    already treat as "no sandbox". Only a READY answer is cached.

    With ``SANDBOX_BEDROCK_MANAGE_RUNTIME=false`` the runtime is checked rather
    than merely found (:func:`_resolve_operator_runtime_arn`).
    """
    from reporting import settings

    global _runtime_arn
    if not settings.SANDBOX_BEDROCK_MANAGE_RUNTIME:
        return await _resolve_operator_runtime_arn()
    if _runtime_arn:
        return _runtime_arn
    runtime = await asyncio.to_thread(find_runtime)
    if runtime is None:
        raise SandboxRuntimeUnavailable(
            f"sandbox runtime {runtime_name()!r} does not exist yet; the Temporal worker creates it at startup "
            "when SANDBOX_BEDROCK_MANAGE_RUNTIME is true"
        )
    status = runtime.get("status", "")
    if status != "READY":
        raise SandboxRuntimeUnavailable(f"sandbox runtime {runtime_name()!r} is {status or 'not ready'}")
    _runtime_arn = str(runtime["agentRuntimeArn"])
    return _runtime_arn


async def _resolve_operator_runtime_arn() -> str:
    """The ARN of an operator-managed runtime whose live version passed its check.

    The answer is re-checked every :data:`_OPERATOR_RECHECK_SECONDS`. A check
    that finds the version unusable drops the cached answer, so sandboxes stop
    until it is fixed; a check that cannot reach AWS keeps the last good answer.
    """
    from reporting.services import sandbox_bedrock_runtime

    global _runtime_arn, _runtime_version, _runtime_checked_at
    if _runtime_arn and time.monotonic() - _runtime_checked_at < _OPERATOR_RECHECK_SECONDS:
        return _runtime_arn
    try:
        arn, version, warnings = await sandbox_bedrock_runtime.check_operator_runtime()
    except SandboxRuntimeUnavailable:
        _runtime_arn, _runtime_version = None, ""
        raise
    except Exception:
        if _runtime_arn is None:
            raise
        logger.warning("could not re-check the sandbox runtime; keeping version %s", _runtime_version, exc_info=True)
        _runtime_checked_at = time.monotonic()
        return _runtime_arn
    if version != _runtime_version:
        logger.info("Sandbox runtime ready", extra={"runtime": arn, "runtime_version": version})
        for warning in warnings:
            logger.warning("sandbox runtime %s version %s: %s", arn, version, warning)
    _runtime_arn, _runtime_version, _runtime_checked_at = arn, version, time.monotonic()
    return _runtime_arn


def _reset_runtime_cache() -> None:
    global _runtime_arn, _runtime_version, _runtime_checked_at
    _runtime_arn, _runtime_version, _runtime_checked_at = None, "", 0.0
    _clients.clear()


# ---------------------------------------------------------------------------
# Protocol transport
# ---------------------------------------------------------------------------


def _invoke_sync(arn: str, session_id: str, op: Op, args: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    from reporting import settings

    request = SandboxRequest(op=op, args=args, timeout_seconds=timeout_seconds)
    response = data_client().invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,
        qualifier=settings.SANDBOX_BEDROCK_QUALIFIER,
        contentType="application/json",
        accept="application/json",
        payload=request.model_dump_json().encode(),
    )
    body = response["response"]
    raw = body.read() if hasattr(body, "read") else body
    try:
        parsed = SandboxResponse.model_validate(json.loads(raw))
    except Exception as exc:
        raise SandboxOpError(ErrorCode.INTERNAL, f"sandbox image returned an invalid response to {op.value}") from exc
    if not parsed.ok:
        error = parsed.error
        raise SandboxOpError(error.code if error else ErrorCode.INTERNAL, error.message if error else "")
    return parsed.result or {}


async def _invoke(arn: str, session_id: str, op: Op, args: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    return await asyncio.to_thread(_invoke_sync, arn, session_id, op, args, timeout_seconds)


async def _hello(arn: str, session_id: str, *, create_marker: SandboxMarker | None = None) -> HelloResult:
    """``hello``, retrying the 409 a session answers while it is provisioned.

    This is the first call any session sees, so it is the one that absorbs
    provisioning; once it returns, concurrent calls from a distributed batch
    land on a running microVM.
    """
    args: dict[str, Any] = {}
    if create_marker is not None:
        args["create_marker"] = create_marker.model_dump()
    deadline = time.monotonic() + _PROVISION_RETRY_SECONDS
    delay = 0.5
    while True:
        try:
            result = HelloResult.model_validate(
                await _invoke(arn, session_id, Op.HELLO, args, _FILE_OP_TIMEOUT_SECONDS)
            )
            break
        except Exception as exc:
            if _error_code(exc) != "RetryableConflictException" or time.monotonic() >= deadline:
                raise
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)
    if PROTOCOL_VERSION not in result.protocol_versions:
        raise SandboxOpError(
            ErrorCode.UNSUPPORTED_PROTOCOL,
            f"sandbox image speaks protocol versions {result.protocol_versions}, Seizu needs {PROTOCOL_VERSION}",
        )
    expected = "object" if storage_mode() == "s3" else "posix"
    if result.storage_semantics != expected:
        logger.warning(
            "sandbox image reports %s storage semantics; SANDBOX_BEDROCK_STORAGE=%s implies %s",
            result.storage_semantics,
            storage_mode(),
            expected,
        )
    return result


async def stop_session(arn: str, session_id: str) -> None:
    """Stop a session, flushing its workspace. Already stopped is success."""
    from reporting import settings

    def _stop() -> None:
        try:
            data_client().stop_runtime_session(
                agentRuntimeArn=arn,
                runtimeSessionId=session_id,
                qualifier=settings.SANDBOX_BEDROCK_QUALIFIER,
            )
        except Exception as exc:
            if _error_code(exc) == "ResourceNotFoundException":
                return
            raise

    await asyncio.to_thread(_stop)


# ---------------------------------------------------------------------------
# Output rendering (shared strings with the E2B backend)
# ---------------------------------------------------------------------------


def _render_python(result: RunPythonResult) -> str:
    from reporting.services.sandbox_backend import python_output

    rendered = python_output(
        result.stdout,
        result.stderr,
        result.text,
        error_name=result.error.name if result.error else "",
        error_value=result.error.value if result.error else "",
        error_traceback=result.error.traceback if result.error else "",
    )
    if result.restarted:
        rendered += (
            "\nThe Python interpreter was restarted because this code ran too long; variables defined "
            "earlier are gone. Files are unaffected."
        )
    return rendered


_BASH_TIMEOUT_MESSAGE = (
    "The command was stopped for running too long. Nothing it may have printed was kept. "
    "Try a narrower command, or run it in the background and read its output from a file."
)


# ---------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------


class BedrockSandboxBackend:
    """:class:`SandboxBackend` over one AgentCore runtime session."""

    def __init__(self, arn: str, session_id: str) -> None:
        self._arn = arn
        self._session_id = session_id

    async def _call(self, op: Op, args: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
        return await _invoke(self._arn, self._session_id, op, args, timeout_seconds)

    async def run_python(self, code: str) -> str:
        try:
            raw = await self._call(Op.RUN_PYTHON, {"code": code}, _exec_timeout())
        except SandboxOpError as exc:
            if exc.code == ErrorCode.TIMEOUT:
                return _render_python(RunPythonResult(restarted=True))
            raise
        return _render_python(RunPythonResult.model_validate(raw))

    async def run_bash(self, cmd: str) -> str:
        """Run a shell command, reporting a non-zero exit or a timeout as text (see the E2B backend)."""
        from reporting.services.sandbox_backend import bash_output

        try:
            raw = await self._call(Op.RUN_BASH, {"command": cmd}, _exec_timeout())
        except SandboxOpError as exc:
            if exc.code == ErrorCode.TIMEOUT:
                return _BASH_TIMEOUT_MESSAGE
            raise
        result = RunBashResult.model_validate(raw)
        if result.timed_out:
            return _BASH_TIMEOUT_MESSAGE
        return bash_output(result.stdout, result.stderr, result.exit_code)

    async def read_file(self, path: str) -> str:
        raw = await self._call(Op.READ_FILE, {"path": path}, _FILE_OP_TIMEOUT_SECONDS)
        result = ReadFileResult.model_validate(raw)
        return base64.b64decode(result.content_b64).decode(errors="replace")

    async def write_file(self, path: str, content: str) -> str:
        encoded = base64.b64encode(content.encode()).decode()
        raw = await self._call(Op.WRITE_FILE, {"path": path, "content_b64": encoded}, _FILE_OP_TIMEOUT_SECONDS)
        WriteFileResult.model_validate(raw)
        return f"Wrote {len(content)} bytes to {path}"

    async def list_files(self, path: str = "/") -> str:
        raw = await self._call(Op.LIST_FILES, {"path": path}, _FILE_OP_TIMEOUT_SECONDS)
        entries = ListFilesResult.model_validate(raw).entries
        lines = [f"{'d' if e.type == 'dir' else 'f'}  {e.name}" for e in entries]
        return "\n".join(lines) if lines else "(empty)"

    async def run_bash_streaming(
        self,
        cmd: str,
        *,
        timeout_seconds: int,
        on_output: Callable[[str], None],
        envs: dict[str, str] | None = None,
    ) -> str:
        raise NotImplementedError("the bedrock sandbox provider does not support streaming commands (remediation)")

    async def get_host(self, port: int) -> str:
        raise NotImplementedError("the bedrock sandbox provider does not expose sandbox ports")

    async def get_traffic_access_token(self) -> str:
        return ""

    @property
    def sandbox_id(self) -> str:
        return self._session_id


# ---------------------------------------------------------------------------
# Destruction, listing (account-wide helpers)
# ---------------------------------------------------------------------------


def _delete_s3_prefix_sync(prefix: str) -> int:
    from reporting import settings

    client = s3_client()
    bucket = settings.SANDBOX_BEDROCK_S3_BUCKET
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        # list_objects_v2 pages hold at most 1000 keys, which is delete_objects' limit.
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
            deleted += len(keys)
    return deleted


async def destroy_session(arn: str, session_id: str) -> None:
    """Delete a session's workspace, then stop it. Raises if the delete fails.

    Neither storage mode has a provider-side delete: session storage is erased
    from inside the session (which starts one if it was stopped), an S3
    workspace by deleting its prefix here.
    """
    if storage_mode() == "s3":
        await asyncio.to_thread(_delete_s3_prefix_sync, _session_prefix(session_id))
        try:
            await stop_session(arn, session_id)
        except Exception:
            logger.info("could not stop sandbox session %s after deleting it", session_id, exc_info=True)
        return
    WipeResult.model_validate(await _invoke(arn, session_id, Op.WIPE, {}, _FILE_OP_TIMEOUT_SECONDS))
    await stop_session(arn, session_id)


async def kill_bedrock_sandbox(sandbox_id: str) -> None:
    """Destroy a sandbox by id. Raises whatever the provider raises."""
    if not is_session_id(sandbox_id):
        # Not something this provider created (an E2B id from before a switch):
        # there is nothing here to delete.
        return
    await destroy_session(await resolve_runtime_arn(), sandbox_id)


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _read_marker_sync(session_id: str) -> SandboxMarker | None:
    from reporting import settings

    try:
        obj = s3_client().get_object(
            Bucket=settings.SANDBOX_BEDROCK_S3_BUCKET, Key=f"{_session_prefix(session_id)}{MARKER_FILENAME}"
        )
    except Exception as exc:
        if _error_code(exc) in {"NoSuchKey", "404", "NotFound"}:
            return None
        raise
    try:
        return SandboxMarker.model_validate(json.loads(obj["Body"].read()))
    except Exception:
        return None


def _list_sandboxes_sync(all_owners: bool) -> list[Any]:
    from reporting import settings
    from reporting.services.sandbox_backend import SandboxSnapshot, deployment_id

    client = s3_client()
    base = f"{s3_prefix()}/"
    snapshots: list[SandboxSnapshot] = []
    paginator = client.get_paginator("list_objects_v2")
    pages = 0
    for page in paginator.paginate(Bucket=settings.SANDBOX_BEDROCK_S3_BUCKET, Prefix=base, Delimiter="/"):
        if pages >= _MAX_LIST_PAGES:
            logger.warning("stopped listing sandboxes after %d pages; some were not seen", pages)
            break
        pages += 1
        for common in page.get("CommonPrefixes", []):
            session_id = str(common.get("Prefix", ""))[len(base) :].strip("/")
            if not is_session_id(session_id):
                continue
            marker = _read_marker_sync(session_id)
            owner = marker.deployment if marker else ""
            if not all_owners and owner != deployment_id():
                continue
            snapshots.append(
                SandboxSnapshot(
                    sandbox_id=session_id,
                    owner=owner,
                    purpose=marker.purpose if marker else "",
                    thread=marker.thread if marker else "",
                    started_at=_parse_time(marker.created_at) if marker else None,
                )
            )
    return snapshots


async def list_bedrock_sandboxes(*, all_owners: bool = False) -> list[Any]:
    """Every sandbox workspace this deployment can see, as ``SandboxSnapshot``s.

    Session storage cannot be listed -- AgentCore has no session listing -- so in
    ``session`` mode this is empty and workspaces expire on the provider's
    schedule. In ``s3`` mode each workspace prefix is read through its marker.
    """
    if storage_mode() != "s3":
        return []
    return await asyncio.to_thread(_list_sandboxes_sync, all_owners)


async def bedrock_sandbox_is_paused(sandbox_id: str) -> bool:
    """Whether a listed workspace is still there to reap (``s3`` mode only).

    An AgentCore session cannot be inspected, so "paused" here means the
    workspace still exists; the reaper's own rule -- no live chat session owns
    it -- is what makes a kill safe. Anything uninspectable reports ``False``.
    """
    if storage_mode() != "s3" or not is_session_id(sandbox_id):
        return False
    try:
        return await asyncio.to_thread(_read_marker_sync, sandbox_id) is not None
    except Exception:
        logger.info("could not read sandbox %s marker; leaving it alone", sandbox_id, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------


@asynccontextmanager
async def open_bedrock_backend(
    *,
    allow_internet: bool | None = None,
    timeout_seconds: int | None = None,
    template: str | None = None,
    allow_public_traffic: bool = False,
    resume_sandbox_id: str | None = None,
    suspend_on_exit: bool | Callable[[], bool] = False,
    on_teardown: Callable[[bool], None] | None = None,
    purpose: str = "sandbox",
    thread: str = "",
    create_if_missing: bool = True,
    detach_on_exit: bool = False,
) -> AsyncIterator[BedrockSandboxBackend]:
    """Open (or resume, or attach to) an AgentCore sandbox session.

    Same contract as :func:`sandbox_backend.open_backend`. Network access is a
    property of the runtime, not of a session, so a per-call request for
    internet access is refused rather than silently ignored; ``template``,
    ``allow_public_traffic`` and ``timeout_seconds`` have no per-session
    equivalent and are ignored (the session lifetime is the runtime's lifecycle
    configuration).
    """
    from reporting.services.sandbox_backend import deployment_id

    if allow_internet:
        raise RuntimeError(
            "the bedrock sandbox provider cannot grant internet access per sandbox; it is set by the "
            "runtime's network configuration"
        )
    if template or allow_public_traffic or timeout_seconds is not None:
        logger.debug(
            "bedrock sandbox ignores template=%r allow_public_traffic=%r timeout_seconds=%r",
            template,
            allow_public_traffic,
            timeout_seconds,
        )

    arn = await resolve_runtime_arn()
    session_id = ""
    if resume_sandbox_id:
        if is_session_id(resume_sandbox_id):
            hello = await _hello(arn, resume_sandbox_id)
            if hello.marker is not None:
                session_id = resume_sandbox_id
            else:
                logger.info("sandbox %s has no workspace any more; creating a new one", resume_sandbox_id)
                try:
                    await stop_session(arn, resume_sandbox_id)
                except Exception:
                    logger.info("could not stop emptied sandbox %s", resume_sandbox_id, exc_info=True)
        else:
            logger.info("sandbox %r was not created by this provider; creating a new one", resume_sandbox_id)
        if not session_id and not create_if_missing:
            raise RuntimeError(f"sandbox {resume_sandbox_id} is gone")
    if not session_id:
        if not create_if_missing:
            raise RuntimeError("no sandbox to attach to")
        session_id = new_session_id()
        marker = SandboxMarker(
            deployment=deployment_id(),
            purpose=purpose,
            thread=thread,
            created_at=datetime.now(UTC).isoformat(),
        )
        hello = await _hello(arn, session_id, create_marker=marker)
        if hello.marker is None:
            raise SandboxOpError(ErrorCode.INTERNAL, "sandbox image did not write the session marker")

    suspended = False
    try:
        yield BedrockSandboxBackend(arn, session_id)
    finally:
        if detach_on_exit:
            # Someone else's session (SBX-015): the owner suspends it.
            pass
        elif suspend_on_exit() if callable(suspend_on_exit) else suspend_on_exit:
            try:
                await stop_session(arn, session_id)
                suspended = True
            except Exception:
                logger.warning("could not suspend sandbox %s; destroying it", session_id, exc_info=True)
                await destroy_session(arn, session_id)
        else:
            await destroy_session(arn, session_id)
        if on_teardown is not None:
            on_teardown(suspended)
