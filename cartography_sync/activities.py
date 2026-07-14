"""The run_cartography_module Temporal activity (sync-worker side).

Runs one cartography intel-module sync as a subprocess. Security posture:
argv is rebuilt here from the registry after re-validating the payload's
``module`` + ``params`` (Temporal payloads cross the network — never trust a
pre-built command), the subprocess is exec'd from an argv list (no shell),
and its environment is scrubbed down to a minimal base plus the env vars the
module's registry entry declares.
"""

import asyncio
import logging
import os
import time

from temporalio import activity
from temporalio.exceptions import ApplicationError

from cartography_sync.registry import MODULE_REGISTRY, validate_module_params
from cartography_sync.registry import build_module_argv as _build_registry_argv
from cartography_sync.shared import CartographyModuleActivityInput, CartographyModuleResult

logger = logging.getLogger(__name__)

# Non-retryable: the config can't become valid by retrying.
CONFIG_ERROR = "CartographyConfigError"
# Retryable: cartography ran and exited non-zero (transient API errors etc.).
MODULE_FAILED = "CartographyModuleFailed"

# Baseline env vars every cartography subprocess gets (when set on the
# worker). STATSD_* mirrors the standalone cartography compose service.
_BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "STATSD_ENABLED", "STATSD_HOST")
_HEARTBEAT_INTERVAL_SECONDS = 30.0
_OUTPUT_TAIL_MAX_BYTES = 16 * 1024
_FAILURE_EXCERPT_MAX_CHARS = 2048


def _subprocess_env(module: str) -> dict[str, str]:
    """Minimal base + only the env vars the module's registry entry declares."""
    spec = MODULE_REGISTRY[module]
    env: dict[str, str] = {}
    for name in (*_BASE_ENV, *spec.optional_env):
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    missing = [name for name in spec.required_env if not os.environ.get(name)]
    if missing:
        raise ApplicationError(
            f"cartography module '{module}' requires env vars not set on the sync worker: {missing}",
            type=CONFIG_ERROR,
            non_retryable=True,
        )
    for name in spec.required_env:
        env[name] = os.environ[name]
    password = os.environ.get("NEO4J_PASSWORD")
    if password:
        env["NEO4J_PASSWORD"] = password
    return env


def _build_argv(input: CartographyModuleActivityInput) -> list[str]:
    errors = validate_module_params(input.module, input.params)
    if errors:
        raise ApplicationError("; ".join(errors), type=CONFIG_ERROR, non_retryable=True)
    neo4j_uri = os.environ.get("CARTOGRAPHY_NEO4J_URI")
    if not neo4j_uri:
        raise ApplicationError(
            "CARTOGRAPHY_NEO4J_URI is not set on the sync worker",
            type=CONFIG_ERROR,
            non_retryable=True,
        )
    argv = [
        os.environ.get("CARTOGRAPHY_BIN", "cartography"),
        f"--neo4j-uri={neo4j_uri}",
    ]
    neo4j_user = os.environ.get("CARTOGRAPHY_NEO4J_USER")
    if neo4j_user:
        # The password rides only in the subprocess env, never in argv.
        argv.append(f"--neo4j-user={neo4j_user}")
        argv.append("--neo4j-password-env-var=NEO4J_PASSWORD")
    argv.extend(_build_registry_argv(input.module, input.params))
    return argv


class _TailBuffer:
    """Keeps the last ``limit`` bytes written to it."""

    def __init__(self, limit: int = _OUTPUT_TAIL_MAX_BYTES) -> None:
        self._limit = limit
        self._data = bytearray()

    def write(self, chunk: bytes) -> None:
        self._data.extend(chunk)
        if len(self._data) > self._limit:
            del self._data[: len(self._data) - self._limit]

    def text(self) -> str:
        return self._data.decode("utf-8", errors="replace")


async def _drain(stream: asyncio.StreamReader, tail: _TailBuffer) -> None:
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            return
        tail.write(chunk)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()


@activity.defn(name="run_cartography_module")
async def run_cartography_module(input: CartographyModuleActivityInput) -> CartographyModuleResult:
    argv = _build_argv(input)
    env = _subprocess_env(input.module)
    logger.info(
        "Starting cartography sync",
        extra={"type": "AUDIT", "cartography_module": input.module, "argv": argv},
    )

    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *argv,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    tail = _TailBuffer()
    assert process.stdout is not None  # PIPE above guarantees a reader
    drain_task = asyncio.create_task(_drain(process.stdout, tail))

    async def _heartbeat_loop() -> None:
        # Time-based, not output-based: cartography can be silent for long
        # stretches while a large sync stays healthy.
        while True:
            activity.heartbeat(input.module)
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)

    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    timed_out = False
    try:
        try:
            await asyncio.wait_for(process.wait(), timeout=input.timeout_seconds)
        except TimeoutError:
            timed_out = True
            await _terminate(process)
        await drain_task
    except asyncio.CancelledError:
        # Activity cancellation (or worker shutdown): don't orphan the sync.
        await _terminate(process)
        raise
    finally:
        heartbeat_task.cancel()
        if not drain_task.done():
            drain_task.cancel()

    duration = time.monotonic() - started
    if timed_out:
        raise ApplicationError(
            f"cartography {input.module} timed out after {input.timeout_seconds}s:"
            f" …{tail.text()[-_FAILURE_EXCERPT_MAX_CHARS:]}",
            type=MODULE_FAILED,
        )
    exit_code = process.returncode or 0
    if exit_code != 0:
        raise ApplicationError(
            f"cartography {input.module} exited {exit_code}: …{tail.text()[-_FAILURE_EXCERPT_MAX_CHARS:]}",
            type=MODULE_FAILED,
        )
    logger.info(
        "Cartography sync completed",
        extra={"type": "AUDIT", "cartography_module": input.module, "duration_seconds": round(duration, 1)},
    )
    return CartographyModuleResult(
        module=input.module,
        status="completed",
        exit_code=exit_code,
        duration_seconds=duration,
        output_tail=tail.text(),
    )
