"""Tests for the A/B harness itself.

The harness is the instrument every settings comparison rests on, so a silent
defect here does not fail a run -- it produces a number that gets believed. Both
cases below are regressions for exactly that: each shipped a wrong figure into a
comparison before being noticed.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from scripts.chat_harness import _same_value, aggregate_stream_metrics  # noqa: E402


def _turn(*, delegation: str, step: str, status: str, answer: str, child: str) -> str:
    """One turn's SSE stream, with the ids a real stream reuses across turns."""
    events = [
        # Literally "routing" every turn -- an id that collides by design.
        {"type": "data-seizu-detail", "id": "routing", "data": {"kind": "routing", "title": "Routing"}},
        {
            "type": "data-seizu-detail",
            "data": {
                "kind": "subagent",
                "detail_id": delegation,
                "children": [{"detail_id": child, "title": "Sandbox: graph__query"}],
            },
        },
        {"type": "data-seizu-detail", "data": {"kind": "verify", "step_id": step, "status": status}},
        {"type": "text-delta", "delta": answer},
    ]
    return "\n".join(f"data: {json.dumps(event)}" for event in events)


async def test_metrics_are_summed_over_every_turn() -> None:
    """Stream counts must cover the same turns the ledger numbers do.

    Keeping only the last turn reported "0 delegations" for an arm whose ledger
    showed it doing *more* sandbox work than the baseline -- two scopes in one
    row, with nothing saying which was which.
    """
    turns = [
        _turn(delegation="call_a", step="s1", status="completed", answer="first", child="c1"),
        _turn(delegation="call_b", step="s2", status="completed", answer="second", child="c2"),
        _turn(delegation="call_c", step="s3", status="completed", answer="third", child="c3"),
    ]
    metrics = aggregate_stream_metrics(turns)

    assert metrics["delegations"] == 3
    assert metrics["inner_calls"] == 3
    assert metrics["queries"] == 3
    assert metrics["steps_total"] == 3
    # Last turn for comparability with older runs; the sum alongside it.
    assert metrics["answer_chars"] == len("third")
    assert metrics["answer_chars_total"] == len("firstsecondthird")


async def test_reused_step_ids_are_not_collapsed_across_turns() -> None:
    """Step ids restart at s1 every turn, so the streams cannot be concatenated
    and parsed as one: de-duplication would fold every turn's steps into one."""
    turns = [
        _turn(delegation="call_a", step="s1", status="completed", answer="a", child="c1"),
        _turn(delegation="call_b", step="s1", status="failed", answer="b", child="c2"),
    ]
    metrics = aggregate_stream_metrics(turns)

    assert metrics["steps_total"] == 2
    assert metrics["steps_failed"] == 1


async def test_no_turns_does_not_crash() -> None:
    assert aggregate_stream_metrics([])["delegations"] == 0


async def test_list_settings_compare_against_a_comma_separated_arm() -> None:
    """List settings read back as a repr, so string and float comparison alone
    rejected every one of them as "did not apply" -- including the empty value,
    which is the whole point of arming a list."""
    assert _same_value("[]", "")
    assert _same_value("['graph__schema']", "graph__schema")
    assert _same_value("['a', 'b']", "a,b")
    assert not _same_value("['a']", "b")
    assert not _same_value("[]", "graph__query")
    # Unchanged for the scalar cases.
    assert _same_value("2000", "2000")
    assert _same_value("True", "true")


def test_every_service_an_arm_touches_is_restored() -> None:
    """The restore must cover ARM_SERVICES, not just the web service.

    A completed run left `seizu-temporal-worker` on
    `CHAT_RUN_TOKEN_BUDGET=200000` while `.env` read `0`: the arm was applied to
    both services and restored on one. Chat turns execute on the worker, so
    every later turn was silently capped -- and because the run exited normally,
    nothing looked wrong.
    """
    import inspect

    from scripts import chat_harness

    source = inspect.getsource(chat_harness.main)
    restore = [line for line in source.splitlines() if "--force-recreate" in line]

    assert restore, "the harness no longer restores the stack"
    for line in restore:
        assert "ARM_SERVICES" in line, f"restore recreates a fixed service list: {line.strip()}"
    # And the worker is in that list, which is what makes the above meaningful.
    assert "seizu-temporal-worker" in chat_harness.ARM_SERVICES
