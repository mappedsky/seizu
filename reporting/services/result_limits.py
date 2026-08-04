"""How much of a query result a caller may receive.

One object rather than a pair of loose integers, because the two bounds have to
travel together to be meaningful: a row cap alone does not bound memory (a
hundred records holding large maps, or a single ``collect()`` aggregate, exceed
any sensible budget), and a byte cap applied after materialization does not
bound it either, since by then the whole result is already in the process.

Carried on a context var so a built-in handler can see it. Handlers are invoked
with ``(args, current_user)`` and nothing else, and they run *before* any limit
would otherwise be applied, so without this they can only fetch everything and
let something downstream trim it.
"""

from contextvars import ContextVar
from dataclasses import dataclass

from reporting import settings


@dataclass(frozen=True)
class ResultLimits:
    """Row and byte bounds for one tool result."""

    max_rows: int | None = None
    max_bytes: int | None = None

    @classmethod
    def for_chat(cls) -> "ResultLimits":
        """What the chat path allows, sized to protect a model's context."""
        return cls(
            max_rows=settings.CHAT_TOOL_RESULT_MAX_ROWS,
            max_bytes=settings.CHAT_TOOL_RESULT_MAX_BYTES,
        )

    @classmethod
    def for_mcp(cls) -> "ResultLimits":
        """What a normal MCP call allows.

        Deliberately its own setting rather than the chat cap. An external MCP
        client is not a model context and has never been bounded by one; making
        it inherit that limit silently truncated ordinary calls at 100 rows and
        contradicted the documented contract. These exist so the server has a
        finite bound at all, and are set far above what a chat turn permits.
        """
        return cls(
            max_rows=settings.MCP_TOOL_RESULT_MAX_ROWS,
            max_bytes=settings.MCP_TOOL_RESULT_MAX_BYTES,
        )


_current_result_limits: ContextVar[ResultLimits | None] = ContextVar("_current_result_limits", default=None)


def set_current_result_limits(limits: ResultLimits | None) -> object:
    return _current_result_limits.set(limits)


def reset_current_result_limits(token: object) -> None:
    _current_result_limits.reset(token)  # type: ignore[arg-type]


def current_result_limits() -> ResultLimits:
    """The limits in force, defaulting to the MCP contract.

    A handler reached outside a chat turn is serving an MCP client, so the MCP
    bound is the right default rather than no bound at all.
    """
    return _current_result_limits.get() or ResultLimits.for_mcp()
