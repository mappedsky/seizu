"""Pluggable sandbox execution backend.

Shared infrastructure — used both by the chat ``sandbox__delegate`` tool
(:mod:`reporting.services.mcp_builtins.sandbox`) and by the CVE dependency
remediation workflow (:mod:`reporting.services.sandbox_remediation`). Kept
separate from the chat tool so it is not a chat-only private helper.

Add a new provider by implementing :class:`SandboxBackend` and opening it in
:func:`open_backend`.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class SandboxBackend(Protocol):
    """Standard interface for a sandbox execution environment.

    Implement this protocol to add a new sandbox provider (E2B, Docker, Daytona,
    etc.) without changing any skill-facing or agent-facing code.  Each method
    returns a plain string so the inner agent always gets consistent output
    regardless of which backend is active.
    """

    async def run_python(self, code: str) -> str:
        """Run Python code and return stdout/stderr/result as text."""
        ...

    async def run_bash(self, cmd: str) -> str:
        """Run a shell command and return stdout/stderr as text."""
        ...

    async def read_file(self, path: str) -> str:
        """Return the contents of a file as text."""
        ...

    async def write_file(self, path: str, content: str) -> str:
        """Write content to a file; return a confirmation string."""
        ...

    async def list_files(self, path: str) -> str:
        """List files/directories at path; return a human-readable string."""
        ...

    async def run_bash_streaming(
        self,
        cmd: str,
        *,
        timeout_seconds: int,
        on_output: Callable[[str], None],
        envs: dict[str, str] | None = None,
    ) -> str:
        """Run a long shell command, invoking ``on_output`` per output chunk.

        Used for runs (e.g. a headless coding-agent CLI) that exceed a normal
        command round-trip: the chunk callback lets callers stream progress and
        keep upstream heartbeats alive.  ``envs`` are scoped to this one command
        (not the sandbox), so credentials can be exposed to a single phase of a
        multi-step run and withheld from the others.  Returns the accumulated
        output.
        """
        ...

    async def get_host(self, port: int) -> str:
        """Return a hostname another party can reach this sandbox's ``port`` on.

        Used to let one sandbox call a service running in another (e.g. the
        ephemeral credential-proxy sandbox). Returns bare ``host[:port]``; the
        caller prepends the scheme.
        """
        ...

    async def get_traffic_access_token(self) -> str:
        """Return the token required to reach this sandbox's exposed ports when
        it was created with ``allow_public_traffic=False``.

        Callers send it as the ``e2b-traffic-access-token`` request header (agent
        CLIs support custom headers), so a proxy sandbox can stay non-public.
        Returns ``""`` if the backend has no such token.
        """
        ...


class _E2BSandboxBackend:
    """SandboxBackend backed by an ``e2b_code_interpreter.AsyncSandbox``."""

    def __init__(self, sandbox: Any) -> None:
        self._sandbox = sandbox

    async def run_python(self, code: str) -> str:
        execution = await self._sandbox.run_code(code)
        parts: list[str] = []
        # logs.stdout captures print() output; execution.text is the return-value
        # text of the last expression (display output, not stdout).  We need both.
        if execution.logs.stdout:
            parts.append("".join(execution.logs.stdout))
        if execution.logs.stderr:
            parts.append("stderr:\n" + "".join(execution.logs.stderr))
        if execution.text:
            parts.append(execution.text)
        if execution.error:
            parts.append(f"Error: {execution.error.name}: {execution.error.value}")
            if execution.error.traceback:
                parts.append(execution.error.traceback)
        return "\n".join(parts) if parts else "(no output)"

    async def run_bash(self, cmd: str) -> str:
        result = await self._sandbox.commands.run(cmd)
        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"stderr: {result.stderr}")
        return "\n".join(parts) if parts else "(no output)"

    async def read_file(self, path: str) -> str:
        content = await self._sandbox.files.read(path)
        return content if isinstance(content, str) else content.decode(errors="replace")

    async def write_file(self, path: str, content: str) -> str:
        await self._sandbox.files.write(path, content)
        return f"Wrote {len(content)} bytes to {path}"

    async def list_files(self, path: str = "/") -> str:
        entries = await self._sandbox.files.list(path)
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
        chunks: list[str] = []

        def _collect(data: str) -> None:
            chunks.append(data)
            on_output(data)

        # timeout=0 disables E2B's per-command timeout; the asyncio.wait_for is
        # the single authoritative bound so callers get a consistent TimeoutError.
        # envs are per-command: E2B sets them only for this process tree, so a
        # secret handed to one phase never lingers for later phases.
        await asyncio.wait_for(
            self._sandbox.commands.run(cmd, timeout=0, envs=envs, on_stdout=_collect, on_stderr=_collect),
            timeout=timeout_seconds,
        )
        return "".join(chunks)

    async def get_host(self, port: int) -> str:
        result = self._sandbox.get_host(port)
        return await result if asyncio.iscoroutine(result) else result

    async def get_traffic_access_token(self) -> str:
        # The exact attribute name is E2B-SDK-specific; try the known spellings.
        # Verify against your SDK version (part of the unverified proxy path).
        for attr in ("traffic_access_token", "_traffic_access_token", "envd_access_token"):
            value = getattr(self._sandbox, attr, None)
            if value:
                return str(await value if asyncio.iscoroutine(value) else value)
        return ""


@asynccontextmanager
async def open_backend(
    *,
    api_key: str,
    domain: str,
    allow_internet: bool | None = None,
    timeout_seconds: int | None = None,
    template: str | None = None,
    allow_public_traffic: bool = False,
) -> AsyncIterator[SandboxBackend]:
    """Open a sandbox and yield a :class:`SandboxBackend` for it.

    This is the extension point for swapping providers.  To add a new backend:
    1. Implement :class:`SandboxBackend`.
    2. Add a ``SANDBOX_BACKEND`` setting (or branch on an existing one).
    3. Open the new backend here and ``yield`` it.

    Tests patch this function to inject any :class:`SandboxBackend` without
    touching provider SDKs.  The ``e2b_code_interpreter`` import stays lazy so
    importing this module never fails when the package is absent.

    Security defaults applied to every sandbox:
    - ``allow_internet_access``: off by default (``SANDBOX_ALLOW_INTERNET``);
      ``allow_internet`` overrides per call (the remediation workflow requires
      outbound access to clone and push, without opening it for every delegate
      call).
    - ``network={"allow_public_traffic": False}``: any HTTP server the sandbox
      exposes requires the auto-generated ``sandbox.traffic_access_token`` in
      the ``e2b-traffic-access-token`` header.  Our SDK calls (run_code,
      files.read/write, etc.) use a separate transport and are unaffected.

    ``timeout_seconds`` sets the sandbox lifetime (E2B kills the sandbox at its
    default lifetime otherwise, mid-run for long tasks).  There is deliberately
    no way to inject environment variables at creation: secrets enter only via
    per-command ``envs`` on :meth:`SandboxBackend.run_bash_streaming`, scoped to
    a single phase.

    ``template`` selects a prebuilt E2B template (e.g. the official ``claude``
    image with the CLI preinstalled).  Templates are an E2B-cloud feature, so it
    is ignored when ``domain`` is set (self-hosted backends such as OpenKruise
    Agents); callers keep an idempotent install step so the base image still
    works.  The template only provides tools — no credentials — so credential
    phase-isolation is unaffected.

    ``allow_public_traffic`` opens the sandbox's exposed ports to the internet
    without the ``e2b-traffic-access-token`` header.  The credential-proxy
    sandbox keeps this ``False`` (private) when the agent CLI can send that
    header (via :meth:`SandboxBackend.get_traffic_access_token`); it is only set
    ``True`` as a fallback for CLIs that can't, where access is then gated by the
    service's own auth (a budget-capped virtual key) instead of the E2B token.
    """
    from e2b_code_interpreter import AsyncSandbox

    from reporting import settings as _settings

    create_kwargs: dict[str, Any] = {}
    if api_key:
        create_kwargs["api_key"] = api_key
    if domain:
        # Custom endpoint (e.g. OpenKruise Agents): domain sets the API base URL
        # to https://api.<domain>; disable client-side key-format validation
        # because non-E2B deployments issue tokens that don't match "e2b_*".
        create_kwargs["domain"] = domain
        create_kwargs["validate_api_key"] = False
    if template and not domain:
        create_kwargs["template"] = template
    elif template and domain:
        logger.debug("Ignoring sandbox template %r on self-hosted backend (domain=%r)", template, domain)
    if timeout_seconds is not None:
        create_kwargs["timeout"] = timeout_seconds
    # Security hardening — applied unconditionally so the defaults are safe
    # regardless of how the sandbox was provisioned.
    create_kwargs["allow_internet_access"] = (
        allow_internet if allow_internet is not None else _settings.SANDBOX_ALLOW_INTERNET
    )
    create_kwargs["network"] = {"allow_public_traffic": allow_public_traffic}
    sandbox = await AsyncSandbox.create(**create_kwargs)
    async with sandbox:
        yield _E2BSandboxBackend(sandbox)
