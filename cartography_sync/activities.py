"""The run_cartography_module Temporal activity (sync-worker side).

Runs one cartography sync stage as a subprocess. Security posture: argv is
rebuilt here from the registry after re-validating the payload's ``module`` +
``params`` (Temporal payloads cross the network — never trust a pre-built
command), the operator allowlist (``CARTOGRAPHY_ENABLED_MODULES``) is
re-enforced on this credential-bearing worker, the subprocess is exec'd from
an argv list (no shell), and its environment is scrubbed down to a minimal
base plus the env vars the module's registry entry declares.

Concurrency: overlapping runs of the same module race on cartography's update
tags and can delete each other's data — serialization is enforced upstream by
the cartography_module child workflow's fixed workflow ID (one open workflow
per module), so this activity only ever runs one-at-a-time per module.
"""

import asyncio
import logging
import os
import time

from temporalio import activity
from temporalio.exceptions import ApplicationError

from cartography_sync.registry import (
    ALWAYS_ENABLED_MODULES,
    MODULE_REGISTRY,
    parse_enabled_modules,
    validate_module_params,
)
from cartography_sync.registry import build_module_argv as _build_registry_argv
from cartography_sync.shared import CartographyModuleActivityInput, CartographyModuleResult

logger = logging.getLogger(__name__)

# Non-retryable: the config can't become valid by retrying.
CONFIG_ERROR = "CartographyConfigError"
# Retryable: cartography ran and exited non-zero (transient API errors etc.).
MODULE_FAILED = "CartographyModuleFailed"

# Baseline process env plus Cartography-specific worker settings that upstream
# libraries require under conventional names.
_BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
_GLOBAL_ENV_MAPPINGS = (
    ("CARTOGRAPHY_STATSD_ENABLED", "STATSD_ENABLED"),
    ("CARTOGRAPHY_STATSD_HOST", "STATSD_HOST"),
)
_HEARTBEAT_INTERVAL_SECONDS = 30.0
_OUTPUT_TAIL_MAX_BYTES = 16 * 1024
_FAILURE_EXCERPT_MAX_CHARS = 2048


def _subprocess_env(module: str) -> dict[str, str]:
    """Build a scrubbed env, translating only registry-declared SDK names."""
    spec = MODULE_REGISTRY[module]
    env: dict[str, str] = {}
    for name in _BASE_ENV:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value

    for worker_name, subprocess_name in _GLOBAL_ENV_MAPPINGS:
        value = os.environ.get(worker_name)
        if value is not None:
            env[subprocess_name] = value

    aliases = dict(spec.env_aliases)
    mappings = dict(spec.env_mappings)

    def _resolve(worker_name: str) -> tuple[str | None, str | None]:
        for candidate in (worker_name, *aliases.get(worker_name, ())):
            value = os.environ.get(candidate)
            if value:
                return value, candidate
        return None, None

    missing: list[str] = []
    for worker_name in (*spec.optional_env, *spec.required_env):
        value, source_name = _resolve(worker_name)
        if value is None:
            if worker_name in spec.required_env:
                missing.append(worker_name)
            continue
        if source_name != worker_name:
            logger.warning(
                "%s is deprecated for Cartography; use %s",
                source_name,
                worker_name,
            )
        env[mappings.get(worker_name, worker_name)] = value

    if missing:
        raise ApplicationError(
            f"cartography module '{module}' requires env vars not set on the sync worker: {missing}",
            type=CONFIG_ERROR,
            non_retryable=True,
        )
    password = os.environ.get("CARTOGRAPHY_NEO4J_PASSWORD")
    if password:
        env["NEO4J_PASSWORD"] = password
    return env


def _check_module_enabled(module: str) -> None:
    """Re-enforce the operator allowlist on this credential-bearing worker.

    The web app and dispatcher validate configs against
    CARTOGRAPHY_ENABLED_MODULES too, but a forged Temporal payload bypasses
    them — the worker that holds the credentials must reject disabled modules
    itself. Credential-free structural stages (create-indexes, analysis) are
    always allowed.
    """
    enabled = parse_enabled_modules(os.environ.get("CARTOGRAPHY_ENABLED_MODULES"))
    if not enabled:
        return
    if module in ALWAYS_ENABLED_MODULES:
        return
    if module not in enabled:
        raise ApplicationError(
            f"cartography module '{module}' is not in this worker's CARTOGRAPHY_ENABLED_MODULES allowlist",
            type=CONFIG_ERROR,
            non_retryable=True,
        )


def _build_argv(input: CartographyModuleActivityInput) -> list[str]:
    errors = validate_module_params(input.module, input.params)
    if errors:
        raise ApplicationError("; ".join(errors), type=CONFIG_ERROR, non_retryable=True)
    _check_module_enabled(input.module)
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


# Pathologically long unterminated output lines are flushed at this size.
_LOG_LINE_MAX_BYTES = 8 * 1024


def _log_subprocess_line(module: str, raw: bytes) -> None:
    text = raw.decode("utf-8", errors="replace").rstrip()
    if text:
        logger.info("cartography[%s] %s", module, text)


async def _drain(stream: asyncio.StreamReader, tail: _TailBuffer, module: str) -> None:
    """Capture subprocess output into the tail buffer and mirror it to the
    worker log line-by-line, attributed to its module (parallel runs
    interleave)."""
    pending = b""
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            if pending:
                _log_subprocess_line(module, pending)
            return
        tail.write(chunk)
        pending += chunk
        *lines, pending = pending.split(b"\n")
        for line in lines:
            _log_subprocess_line(module, line)
        if len(pending) > _LOG_LINE_MAX_BYTES:
            _log_subprocess_line(module, pending)
            pending = b""


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

    async def _heartbeat_loop() -> None:
        # Time-based, not output-based: cartography can be silent for long
        # stretches while a large sync stays healthy.
        while True:
            activity.heartbeat(input.module)
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)

    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        return await _run_subprocess(input, argv, env)
    finally:
        heartbeat_task.cancel()


async def _run_subprocess(
    input: CartographyModuleActivityInput, argv: list[str], env: dict[str, str]
) -> CartographyModuleResult:
    # Context rides in the message itself: the thin worker uses plain stdlib
    # logging, which does not render `extra` fields (they are still attached
    # for deployments that ship a structured formatter).
    info = activity.info()
    run_context = f"module={input.module} workflow={info.workflow_id} attempt={info.attempt}"
    logger.info(
        "Starting cartography sync: %s timeout=%ss argv=%s",
        run_context,
        input.timeout_seconds,
        argv,
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
    drain_task = asyncio.create_task(_drain(process.stdout, tail, input.module))
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
        logger.warning("Cartography sync canceled, terminating subprocess: %s", run_context)
        await _terminate(process)
        raise
    finally:
        if not drain_task.done():
            drain_task.cancel()

    duration = time.monotonic() - started
    if timed_out:
        logger.error(
            "Cartography sync timed out: %s after=%ss",
            run_context,
            input.timeout_seconds,
            extra={"type": "AUDIT", "cartography_module": input.module},
        )
        raise ApplicationError(
            f"cartography {input.module} timed out after {input.timeout_seconds}s:"
            f" …{tail.text()[-_FAILURE_EXCERPT_MAX_CHARS:]}",
            type=MODULE_FAILED,
        )
    exit_code = process.returncode or 0
    if exit_code != 0:
        logger.error(
            "Cartography sync failed: %s exit_code=%s duration=%ss",
            run_context,
            exit_code,
            round(duration, 1),
            extra={"type": "AUDIT", "cartography_module": input.module},
        )
        raise ApplicationError(
            f"cartography {input.module} exited {exit_code}: …{tail.text()[-_FAILURE_EXCERPT_MAX_CHARS:]}",
            type=MODULE_FAILED,
        )
    logger.info(
        "Cartography sync completed: %s exit_code=0 duration=%ss",
        run_context,
        round(duration, 1),
        extra={"type": "AUDIT", "cartography_module": input.module, "duration_seconds": round(duration, 1)},
    )
    return CartographyModuleResult(
        module=input.module,
        status="completed",
        exit_code=exit_code,
        duration_seconds=duration,
        output_tail=tail.text(),
    )
