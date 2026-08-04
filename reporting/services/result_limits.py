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


@dataclass(frozen=True)
class Truncation:
    """Everything a client is told about why a result is incomplete.

    One object shared by the three places that can shorten a result -- the
    streaming read, the response limiter, and the byte-sizing search that
    decides how many rows the limiter can keep. They drifted apart when each
    built its own dict: the stream marker omitted ``returned``, the graph
    built-in omitted the limits, a row-then-byte cut dropped ``max_rows``, and
    the sizing search measured a smaller envelope than the one actually emitted,
    so responses could exceed the very budget the search existed to enforce.
    """

    reasons: tuple[str, ...]
    returned: int
    source_rows: int
    source_complete: bool
    max_rows: int | None = None
    max_bytes: int | None = None

    def with_reason(self, reason: str, **limits: int | None) -> "Truncation":
        """A further cut, keeping what earlier bounds already established."""
        return Truncation(
            reasons=(*self.reasons, reason),
            returned=self.returned,
            source_rows=self.source_rows,
            source_complete=self.source_complete,
            max_rows=limits.get("max_rows", self.max_rows),
            max_bytes=limits.get("max_bytes", self.max_bytes),
        )

    def fields(self) -> dict[str, object]:
        """The response fields, identical wherever this is rendered."""
        marker: dict[str, object] = {
            "truncated": True,
            "truncated_reasons": list(self.reasons),
            "returned": self.returned,
        }
        # A total is claimed only when the query ran out of rows on its own.
        # Otherwise the real number is unknown and larger than anything seen,
        # and calling the visible count a total invites a client to page toward
        # an end that does not exist.
        if self.source_complete:
            marker["total_rows"] = self.source_rows
        elif self.source_rows:
            marker["total_rows_at_least"] = self.source_rows
        # Where nothing was kept -- a first row too large to fit -- there is no
        # lower bound worth stating. "at least 0" is true of every result and
        # would read as "the query found nothing", which is the opposite of what
        # happened.
        if self.max_rows is not None:
            marker["max_rows"] = self.max_rows
        if self.max_bytes is not None:
            marker["max_bytes"] = self.max_bytes
        return marker


def stream_truncation(reason: str, rows: list[object], limits: ResultLimits) -> Truncation:
    """State for a read the database stopped early."""
    return Truncation(
        reasons=(reason,),
        returned=len(rows),
        source_rows=len(rows),
        source_complete=False,
        max_rows=limits.max_rows if reason == "row_limit" else None,
        max_bytes=limits.max_bytes if reason == "byte_limit" else None,
    )
