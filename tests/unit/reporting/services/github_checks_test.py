"""Tests for the worker-side GitHub PR check classification."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

from reporting.services.github_checks import api_base_url, classify_checks, parse_pr_number
from reporting.temporal_workflows.shared import PrCiCheck

_NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
_STUCK = 1800


def _pull(state: str = "open", merged: bool = False, sha: str = "abc123") -> dict[str, Any]:
    return {"state": state, "merged": merged, "head": {"sha": sha}}


def _run(
    name: str,
    status: str = "completed",
    conclusion: str | None = "success",
    started_at: str | None = None,
    run_id: int = 1,
) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "started_at": started_at,
        "details_url": f"https://github.com/org/app/runs/{run_id}",
        "output": {"title": f"{name} output"},
    }


def _classify(check_runs: list[dict[str, Any]], statuses: list[dict[str, Any]] | None = None, pull=None):
    return classify_checks(pull or _pull(), check_runs, statuses or [], now=_NOW, queued_stuck_seconds=_STUCK)


def test_parse_pr_number() -> None:
    assert parse_pr_number("https://github.com/org/app/pull/42") == 42
    assert parse_pr_number("https://github.example.com/org/app/pull/7?tab=checks") == 7
    assert parse_pr_number("https://github.com/org/app/pulls") is None
    assert parse_pr_number("") is None


def test_api_base_url_github_and_ghes() -> None:
    with patch("reporting.settings.REMEDIATION_GITHUB_HOST", "github.com"):
        assert api_base_url() == "https://api.github.com"
    with patch("reporting.settings.REMEDIATION_GITHUB_HOST", "github.example.com"):
        assert api_base_url() == "https://github.example.com/api/v3"


def test_all_checks_passed() -> None:
    result = _classify([_run("tests"), _run("lint", conclusion="neutral"), _run("docs", conclusion="skipped")])
    assert result.state == "success"
    assert result.head_sha == "abc123"
    assert result.failing == [] and result.pending == [] and result.ignored == []


def test_running_checks_are_pending_even_with_failures() -> None:
    # A failure verdict waits for the full picture so one fix run sees every
    # failing check.
    result = _classify([_run("tests", conclusion="failure"), _run("lint", status="in_progress")])
    assert result.state == "pending"
    assert result.pending == ["lint"]
    assert [c.name for c in result.failing] == ["tests"]


def test_failure_once_everything_settles() -> None:
    result = _classify([_run("tests", conclusion="failure", run_id=11), _run("lint", conclusion="timed_out")])
    assert result.state == "failure"
    assert [c.name for c in result.failing] == ["tests", "lint"]
    assert result.failing[0].check_run_id == 11
    assert result.failing[0].summary == "tests output"


def test_recently_queued_check_is_pending() -> None:
    queued_at = (_NOW - timedelta(seconds=60)).isoformat()
    result = _classify([_run("tests", status="queued", started_at=queued_at)])
    assert result.state == "pending"
    assert result.pending == ["tests"]


def test_queued_forever_check_is_ignored_not_waited_on() -> None:
    # THE stuck-queue property: a check whose runner never comes must not
    # stall the watch until max wait.
    queued_at = (_NOW - timedelta(seconds=_STUCK + 60)).isoformat()
    result = _classify(
        [_run("tests", conclusion="success"), _run("ghost-runner", status="queued", started_at=queued_at)]
    )
    assert result.state == "success"
    assert result.ignored == [f"ghost-runner (queued for over {_STUCK}s)"]


def test_queued_forever_does_not_mask_real_failures() -> None:
    queued_at = (_NOW - timedelta(seconds=_STUCK + 60)).isoformat()
    result = _classify(
        [_run("tests", conclusion="failure"), _run("ghost-runner", status="queued", started_at=queued_at)]
    )
    assert result.state == "failure"
    assert [c.name for c in result.failing] == ["tests"]
    assert len(result.ignored) == 1


def test_queued_with_no_timestamp_stays_pending() -> None:
    # No started_at to age against → keep waiting; the max wait bounds it.
    result = _classify([_run("tests", status="queued", started_at=None)])
    assert result.state == "pending"


def test_cancelled_and_stale_and_action_required_are_ignored() -> None:
    result = _classify(
        [
            _run("tests", conclusion="success"),
            _run("superseded", conclusion="cancelled"),
            _run("old", conclusion="stale"),
            _run("needs-approval", conclusion="action_required"),
        ]
    )
    assert result.state == "success"
    assert result.ignored == ["superseded (cancelled)", "old (stale)", "needs-approval (action_required)"]


def test_legacy_commit_statuses_classified() -> None:
    fresh = (_NOW - timedelta(seconds=30)).isoformat()
    old = (_NOW - timedelta(seconds=_STUCK + 60)).isoformat()
    statuses = [
        {"context": "ci/jenkins", "state": "failure", "description": "3 tests failed", "target_url": "https://j"},
        {"context": "codecov", "state": "pending", "updated_at": fresh},
        {"context": "forgotten", "state": "pending", "updated_at": old},
    ]
    result = _classify([], statuses)
    assert result.state == "pending"  # codecov still fresh-pending
    assert result.pending == ["codecov"]
    assert [c.name for c in result.failing] == ["ci/jenkins"]
    assert result.failing[0].check_run_id is None  # no logs to fetch for legacy statuses
    assert result.failing[0].summary == "3 tests failed"
    assert result.ignored == [f"forgotten (pending for over {_STUCK}s)"]


def test_no_checks_at_all() -> None:
    result = _classify([])
    assert result.state == "no_checks"


def test_merged_and_closed_prs_end_the_watch() -> None:
    assert _classify([], pull=_pull(state="closed", merged=True)).state == "merged"
    assert _classify([], pull=_pull(state="closed")).state == "closed"
    # Merged/closed wins even when checks would classify otherwise.
    assert _classify([_run("tests", conclusion="failure")], pull=_pull(state="closed")).state == "closed"


def test_failing_check_dataclass_carries_lookup_identity() -> None:
    result = _classify([_run("tests", conclusion="failure", run_id=99)])
    check = result.failing[0]
    assert isinstance(check, PrCiCheck)
    assert check.check_run_id == 99
    assert check.details_url.endswith("/99")
