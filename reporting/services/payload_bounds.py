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
    lo, hi, best = 0, len(rows), 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if json_size_bytes(wrap(rows[:mid]), indent=indent) <= max_bytes:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


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
