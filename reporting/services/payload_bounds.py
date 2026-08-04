"""Shared helpers for bounding JSON payloads by complete rows and UTF-8 bytes."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def json_size_bytes(value: Any, *, indent: int | None = None) -> int:
    """Return the size of *value* after the repository's JSON serialization."""

    return len(json.dumps(value, indent=indent, default=str).encode("utf-8"))


def largest_prefix_within_bytes(
    rows: list[Any],
    *,
    max_bytes: int,
    envelope: Callable[[list[Any]], Any] | None = None,
    indent: int | None = None,
) -> int:
    """Return the largest complete-row prefix whose JSON fits ``max_bytes``.

    ``envelope`` lets callers account for truncation metadata surrounding the
    rows. Binary search avoids repeatedly serializing every possible prefix.
    """

    if max_bytes <= 0:
        return len(rows)
    wrap = envelope or (lambda values: values)
    if not rows:
        return 0

    # One sizing pass, not a binary search over whole prefixes. The search
    # serialized the entire candidate on every probe -- about seventeen full
    # dumps of a multi-megabyte payload, six seconds of synchronous CPU inside
    # an async handler at the MCP byte ceiling, blocking the worker. Sizing each
    # row once costs roughly one dump in total.
    overhead = json_size_bytes(wrap([]), indent=indent)
    if overhead > max_bytes:
        return 0
    # Separator between rows: ", " compact, or a comma plus newline and indent.
    separator = 2 if indent is None else indent + 3
    used = 0
    keep = 0
    for row in rows:
        cost = json_size_bytes(row, indent=indent) + (separator if keep else 0)
        if used + cost > max_bytes - overhead:
            break
        used += cost
        keep += 1

    # Verify, because sizing a row alone under-counts the indentation it gains
    # once nested. Shrinking proportionally converges in a couple of passes, and
    # erring toward slightly fewer rows is the right way to be wrong: the bound
    # is a promise, the last row is not.
    while keep > 0 and json_size_bytes(wrap(rows[:keep]), indent=indent) > max_bytes:
        keep -= max(1, keep // 16)
    return max(0, keep)


def bounded_json_rows(
    rows: list[Any],
    *,
    max_rows: int | None,
    max_bytes: int | None,
) -> tuple[list[Any], dict[str, Any]]:
    """Bound a row list and return it with machine-readable truncation metadata."""

    original_count = len(rows)
    bounded = rows
    reasons: list[str] = []
    if max_rows is not None and max_rows > 0 and len(bounded) > max_rows:
        bounded = bounded[:max_rows]
        reasons.append("row_limit")
    if max_bytes is not None and max_bytes > 0 and json_size_bytes(bounded) > max_bytes:
        keep = largest_prefix_within_bytes(bounded, max_bytes=max_bytes)
        bounded = bounded[:keep]
        reasons.append("byte_limit")
    return bounded, {
        "truncated": bool(reasons),
        "truncated_reasons": reasons,
        "original_row_count": original_count,
        "row_count": len(bounded),
    }
