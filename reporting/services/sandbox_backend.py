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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: Metadata key stamped on every sandbox Seizu creates. Its value is the
#: *deployment* id, because a sweep has to tell this installation's sandboxes
#: from a sibling installation's on a shared API key -- "created by some Seizu"
#: is not an ownership claim any reaper may act on.
MANAGED_METADATA_KEY = "seizu_managed"

#: What the sandbox is for. Diagnostics only; nothing branches on it.
PURPOSE_METADATA_KEY = "seizu_purpose"

#: The chat thread a sandbox belongs to, namespaced (``user:<id>:thread:<id>``).
#: This is what makes a sandbox's session findable, and therefore what lets the
#: reaper distinguish a sandbox with a live session from an orphan.
THREAD_METADATA_KEY = "seizu_thread"

#: Deployment id written when ``SEIZU_DEPLOYMENT_ID`` is unset. Deployments that
#: leave it unset share this bucket and can reap each other; the setting exists
#: to be set whenever the provider credentials are shared.
DEFAULT_DEPLOYMENT_ID = "default"

#: Pages a single listing will walk before giving up. A provider that keeps
#: handing out a next-token would otherwise spin here forever; at the default
#: page size this is far more sandboxes than any deployment holds.
_MAX_LIST_PAGES = 200


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

    @property
    def sandbox_id(self) -> str:
        """Provider-side identifier, or ``""`` when the backend has no notion of one.

        The handle a suspended sandbox is resumed by: a caller stores this and
        passes it back as ``resume_sandbox_id``. Comparing it against the id that
        was asked for is also how a caller learns whether it got the *same*
        sandbox back or a replacement — which decides whether files it remembers
        writing are still there.
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

    @property
    def sandbox_id(self) -> str:
        return str(getattr(self._sandbox, "sandbox_id", "") or "")


def _api_params(api_key: str, domain: str) -> dict[str, Any]:
    """Connection options every provider call shares."""
    params: dict[str, Any] = {}
    if api_key:
        params["api_key"] = api_key
    if domain:
        # Custom endpoint (e.g. OpenKruise Agents): domain sets the API base URL
        # to https://api.<domain>; disable client-side key-format validation
        # because non-E2B deployments issue tokens that don't match "e2b_*".
        params["domain"] = domain
        params["validate_api_key"] = False
    return params


def _account_api_params() -> dict[str, Any]:
    """Connection options for account-wide calls, which have no open sandbox."""
    from reporting import settings as _settings

    return _api_params(_settings.SANDBOX_API_KEY, _settings.SANDBOX_DOMAIN)


def deployment_id() -> str:
    """This installation's identity in sandbox metadata."""
    from reporting import settings as _settings

    return _settings.SEIZU_DEPLOYMENT_ID or DEFAULT_DEPLOYMENT_ID


@dataclass(frozen=True)
class SandboxSnapshot:
    """What the provider's listing says about one sandbox, provider-agnostically.

    Deliberately thin: an id, who owns it, what thread it belongs to, and the
    timestamps a sweep can date it by. Anything richer would put provider
    response shapes back in front of callers, which is the thing
    :class:`SandboxBackend` exists to prevent (SBX-001).
    """

    sandbox_id: str
    #: The deployment tag, or ``""`` for a sandbox Seizu did not create (or
    #: created before tagging existed).
    owner: str = ""
    purpose: str = ""
    #: Namespaced thread id, or ``""`` for a sandbox belonging to no chat thread.
    thread: str = ""
    started_at: datetime | None = None
    end_at: datetime | None = None

    @property
    def ours(self) -> bool:
        return bool(self.owner) and self.owner == deployment_id()


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _snapshot(info: Any) -> SandboxSnapshot:
    metadata = getattr(info, "metadata", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    return SandboxSnapshot(
        sandbox_id=str(getattr(info, "sandbox_id", "") or ""),
        owner=str(metadata.get(MANAGED_METADATA_KEY) or ""),
        purpose=str(metadata.get(PURPOSE_METADATA_KEY) or ""),
        thread=str(metadata.get(THREAD_METADATA_KEY) or ""),
        started_at=_as_utc(getattr(info, "started_at", None)),
        end_at=_as_utc(getattr(info, "end_at", None)),
    )


async def list_paused_sandboxes(*, all_owners: bool = False) -> list[SandboxSnapshot]:
    """Every *suspended* sandbox this deployment can see.

    Paused only, because a running sandbox already has an expiry the provider
    enforces, while a paused one has none — that asymmetry is the whole reason
    a sweep exists.

    Filtered **provider-side** to this deployment's tag by default, so a shared
    account's other sandboxes are never paged through: an unfiltered listing on
    a busy account can spend the page cap on sandboxes that were never
    reapable, and leave this deployment's own permanently unseen.
    ``all_owners`` drops the filter for the one caller that needs the untagged
    ones too, and pays that cost knowingly.
    """
    from e2b import SandboxQuery, SandboxState
    from e2b_code_interpreter import AsyncSandbox

    query = SandboxQuery(
        state=[SandboxState.PAUSED],
        metadata=None if all_owners else {MANAGED_METADATA_KEY: deployment_id()},
    )
    paginator = AsyncSandbox.list(query=query, **_account_api_params())
    snapshots: list[SandboxSnapshot] = []
    pages = 0
    while paginator.has_next:
        if pages >= _MAX_LIST_PAGES:
            logger.warning("stopped listing sandboxes after %d pages; some were not seen", pages)
            break
        snapshots.extend(_snapshot(info) for info in await paginator.next_items())
        pages += 1
    return snapshots


async def sandbox_is_paused(sandbox_id: str) -> bool:
    """Whether the sandbox is *still* suspended, asked immediately before a kill.

    A listing is a snapshot, and between taking it and acting on it a user can
    resume the sandbox — turning a reap of dead storage into the destruction of
    a live delegation. This does not close that window (nothing short of a
    provider-side conditional delete would), it narrows it to the width of one
    API call. A sandbox that cannot be inspected reports ``False``: the caller's
    job is reclaiming storage, and skipping one is cheaper than being wrong.
    """
    from e2b import SandboxState
    from e2b_code_interpreter import AsyncSandbox

    try:
        info = await AsyncSandbox.get_info(sandbox_id, **_account_api_params())
    except Exception:
        logger.info("could not read sandbox %s state; leaving it alone", sandbox_id, exc_info=True)
        return False
    return bool(getattr(info, "state", None) == SandboxState.PAUSED)


async def kill_sandbox(sandbox_id: str) -> None:
    """Destroy a sandbox by id, running or suspended.

    Raises whatever the provider raises: the two callers want opposite things
    from a failure (one logs an orphan and carries on, the other counts it), so
    neither gets to have the decision made here.
    """
    from e2b_code_interpreter import AsyncSandbox

    await AsyncSandbox.kill(sandbox_id, **_account_api_params())


def _terminal_resume_errors() -> tuple[type[BaseException], ...]:
    """Exceptions that mean the sandbox is gone rather than briefly unreachable."""
    try:
        from e2b import NotFoundException, SandboxNotFoundException

        return (SandboxNotFoundException, NotFoundException)
    except Exception:  # pragma: no cover - SDK without those names
        return ()


@asynccontextmanager
async def open_backend(
    *,
    api_key: str,
    domain: str,
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

    Three things go into the sandbox's provider-side metadata: this
    deployment's id (:data:`MANAGED_METADATA_KEY`), which is the only ownership
    claim the reaper acts on; ``purpose``, for diagnostics; and ``thread`` — the
    namespaced chat thread this sandbox belongs to, which is how
    :mod:`reporting.services.session_reaper` finds the session that owns it and
    tells a live sandbox from an orphan. A sandbox with no thread belongs to no
    session and outlives nothing.

    ``resume_sandbox_id`` reconnects to an existing sandbox — resuming it if it
    was suspended — instead of creating one.  Only *terminal* failures yield a
    fresh sandbox (the caller detects that by comparing
    :attr:`SandboxBackend.sandbox_id` against what it asked for); a timeout,
    rate limit or auth failure propagates.  ``on_teardown`` reports whether the
    sandbox was actually left suspended, since a failed pause becomes a kill.
    ``suspend_on_exit`` pauses instead of killing, and may be a callable
    evaluated at exit for a caller that only learns on the way out whether the
    sandbox is worth keeping.

    ``create_if_missing=False`` with ``detach_on_exit=True`` is the **attaching**
    caller: a distributed plan step running on another worker, which uses the
    conversation's sandbox but does not own it (SBX-015). It must not create one
    -- a second sandbox for a conversation that is meant to have exactly one is
    worse than a failed step, and nobody would hold its id -- and it must leave
    the sandbox exactly as it found it, because the coordinating turn is still
    using it and will suspend it when the turn ends.

    Read SBX-006 and SBX-007 in ``docs/root/dev/decisions/sandbox.md`` before
    changing any of that — each rule exists because the obvious alternative
    strands a paid-for sandbox or discards a conversation's work.
    """
    from e2b_code_interpreter import AsyncSandbox

    from reporting import settings as _settings

    api_kwargs = _api_params(api_key, domain)
    create_kwargs: dict[str, Any] = dict(api_kwargs)
    # Set at creation and never afterwards -- the provider has no way to amend a
    # sandbox's metadata -- so everything here must be true for the sandbox's
    # whole life. Ownership and the owning thread are; "when it was last used"
    # would not be, which is why the reaper asks the session store instead.
    create_kwargs["metadata"] = {
        MANAGED_METADATA_KEY: deployment_id(),
        PURPOSE_METADATA_KEY: purpose,
        **({THREAD_METADATA_KEY: thread} if thread else {}),
    }
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

    sandbox = None
    if resume_sandbox_id:
        try:
            # connect() resumes a paused sandbox and re-arms its lifetime; a
            # running one keeps the longer of the two timeouts.
            sandbox = await AsyncSandbox.connect(resume_sandbox_id, timeout=timeout_seconds, **api_kwargs)
        except _terminal_resume_errors() as exc:
            # Gone for good: expired, reaped, or never existed. Falling through
            # to create is the whole recovery, and the caller compares
            # sandbox_id and treats anything it remembered writing as gone.
            logger.info("sandbox %s is gone (%s); creating a new one", resume_sandbox_id, type(exc).__name__)
            if not create_if_missing:
                raise
    if sandbox is None:
        if not create_if_missing:
            raise RuntimeError("no sandbox to attach to")
        sandbox = await AsyncSandbox.create(**create_kwargs)

    suspended = False
    try:
        yield _E2BSandboxBackend(sandbox)
    finally:
        # Never `async with sandbox` — its __aexit__ kills unconditionally, and
        # the point of suspend_on_exit is to survive.
        if detach_on_exit:
            # Someone else's sandbox. Neither pausing nor killing it is this
            # caller's to do: the owner is still working in it.
            pass
        elif suspend_on_exit() if callable(suspend_on_exit) else suspend_on_exit:
            try:
                # Memory snapshot included, deliberately. keep_memory=False
                # was tried first, for the isolation of not carrying untrusted
                # processes between turns, and it does not work: the code
                # interpreter is itself a process, so every resumed sandbox
                # came back with port 49999 closed and run_python failing 502
                # for the rest of the turn. Reproduced in bare SDK code, and
                # five ways of restarting the service by hand all failed --
                # jupyter starts but E2B's /execute extension does not load.
                #
                # So the cost is real and accepted: untrusted processes from
                # one turn survive into the next, bounded to a single user's
                # thread, still network-isolated and still holding no
                # credentials. See SBX-005 for the trade and the planned way
                # out (object-store file persistence plus cold boots).
                await sandbox.pause(keep_memory=True)
                suspended = True
            except Exception:
                # A backend that cannot pause must not leave the sandbox running
                # until the provider's timeout, so fall back to destroying it --
                # and say so, because the caller is about to decide whether the
                # id is worth storing and this one is now dead.
                logger.warning("could not suspend sandbox %s; killing it", sandbox.sandbox_id, exc_info=True)
                await sandbox.kill()
        else:
            await sandbox.kill()
        if on_teardown is not None:
            on_teardown(suspended)
