"""Tests for the worker-side GitHub PR check classification."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from reporting.services.github_checks import (
    _paginate,
    api_base_url,
    classify_checks,
    ensure_fork,
    fetch_pr_head,
    parse_pr_number,
    render_agent_pr_comment,
)
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


async def test_fetch_pr_head_returns_authoritative_fork_and_ref() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/org/app/pulls/42"
        return httpx.Response(
            200,
            json={
                "head": {
                    "ref": "seizu/dependency-update/pip-requests-2.32.4",
                    "repo": {"full_name": "seizu-bot/app"},
                }
            },
        )

    with _fork_client(handler):
        assert await fetch_pr_head("org/app", 42) == (
            "seizu-bot/app",
            "seizu/dependency-update/pip-requests-2.32.4",
        )


async def test_fetch_pr_head_fails_when_the_head_repository_was_deleted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"head": {"ref": "branch", "repo": None}})

    with _fork_client(handler), pytest.raises(RuntimeError, match="no available head repository"):
        await fetch_pr_head("org/app", 42)


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


def test_queued_ages_from_created_at_when_started_at_is_null() -> None:
    # Queued runs commonly have no started_at yet; created_at must drive the
    # stuck detection or REMEDIATION_CI_QUEUED_STUCK_SECONDS never fires.
    run = _run("ghost", status="queued", started_at=None)
    run["created_at"] = (_NOW - timedelta(seconds=_STUCK + 60)).isoformat()
    result = _classify([run])
    assert result.state == "success"
    assert result.ignored == [f"ghost (queued for over {_STUCK}s)"]

    fresh = _run("fresh", status="queued", started_at=None)
    fresh["created_at"] = (_NOW - timedelta(seconds=60)).isoformat()
    assert _classify([fresh]).state == "pending"


def test_queued_with_no_timestamps_ages_from_fallback_anchor() -> None:
    # No created_at/started_at at all → the caller's head-commit-date anchor
    # decides; without an anchor the check stays pending (max wait bounds it).
    run = _run("ghost", status="queued", started_at=None)
    old_anchor = _NOW - timedelta(seconds=_STUCK + 60)
    result = classify_checks(_pull(), [run], [], now=_NOW, queued_stuck_seconds=_STUCK, fallback_queued_at=old_anchor)
    assert result.state == "success"
    assert result.ignored == [f"ghost (queued for over {_STUCK}s)"]

    assert _classify([_run("tests", status="queued", started_at=None)]).state == "pending"


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


# ---------------------------------------------------------------------------
# Link-header pagination
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, payload: dict[str, Any], next_url: str | None = None) -> None:
        self._payload = payload
        self.links = {"next": {"url": next_url}} if next_url else {}

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._payload


class _StubClient:
    def __init__(self, responses: dict[str, _StubResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def get(self, url: str, params: dict[str, Any] | None = None) -> _StubResponse:
        self.calls.append((url, params))
        return self._responses[url]


async def test_paginate_follows_next_links() -> None:
    # Matrix-heavy workflows exceed one page of check runs; a single page
    # would miss failures and report a false success.
    client = _StubClient(
        {
            "/checks": _StubResponse({"check_runs": [{"name": "a"}]}, next_url="https://api/checks?page=2"),
            "https://api/checks?page=2": _StubResponse({"check_runs": [{"name": "b"}]}),
        }
    )
    items = await _paginate(client, "/checks", "check_runs")  # type: ignore[arg-type]
    assert [i["name"] for i in items] == ["a", "b"]
    # per_page only on the first request; the next link carries its own query.
    assert client.calls == [("/checks", {"per_page": 100}), ("https://api/checks?page=2", None)]


async def test_paginate_is_bounded() -> None:
    # A pathological/looping Link chain cannot spin forever.
    client = _StubClient({"/loop": _StubResponse({"check_runs": [{"name": "x"}]}, next_url="/loop")})
    items = await _paginate(client, "/loop", "check_runs")  # type: ignore[arg-type]
    assert len(items) == 10  # _MAX_PAGES


# ---------------------------------------------------------------------------
# Agent-comment rendering (untrusted text → fixed server-owned template)
# ---------------------------------------------------------------------------


def test_render_agent_pr_comment_neutralizes_mentions_and_commands() -> None:
    body = "/deploy production\ncc @octocat and @org/security-team\n  /retest"
    rendered = render_agent_pr_comment(body)
    # Fixed server-owned header, agent text only as a block quote.
    assert rendered.startswith("**Automated CI triage**")
    for line in rendered.splitlines():
        assert not line.startswith("/")
    assert all(line.startswith(">") or not line.strip().startswith("/") for line in rendered.splitlines())
    # Mentions can never ping: the @ is broken with a zero-width space.
    assert "@octocat" not in rendered
    assert "@&#8203;octocat" in rendered
    assert "@org/security-team" not in rendered
    # Slash commands are quoted AND zero-width-prefixed.
    assert "> &#8203;/deploy production" in rendered
    assert "&#8203;/retest" in rendered


def test_render_agent_pr_comment_caps_length() -> None:
    rendered = render_agent_pr_comment("x" * 50_000)
    assert "… (truncated)" in rendered
    assert len(rendered) < 6_000


def test_render_agent_pr_comment_quotes_every_line() -> None:
    rendered = render_agent_pr_comment("first\n\nsecond")
    quoted = rendered.split("\n\n", 2)[2]
    assert quoted.splitlines() == ["> first", ">", "> second"]


# ---------------------------------------------------------------------------
# Fork management (fork-mode remediation)
# ---------------------------------------------------------------------------


def _fork_client(handler: Callable[[httpx.Request], httpx.Response]) -> Any:
    def _make() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com")

    return patch("reporting.services.github_checks._client", _make)


async def test_ensure_fork_creates_the_fork_and_waits_for_git_data() -> None:
    # POST /forks answers immediately with the fork's (API-authoritative) full
    # name, but a brand-new fork's git data appears asynchronously — the commits
    # endpoint answers 409 until then.
    posted: list[dict[str, Any]] = []
    commit_probes = iter([409, 404, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/repos/org/app/forks":
            posted.append(json.loads(request.content or b"{}"))
            return httpx.Response(202, json={"full_name": "seizu-bot/app"})
        if request.method == "GET" and request.url.path == "/repos/seizu-bot/app/commits":
            return httpx.Response(next(commit_probes), json=[])
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    with _fork_client(handler), patch("reporting.services.github_checks._FORK_READY_POLL_SECONDS", 0):
        assert await ensure_fork("org/app") == "seizu-bot/app"
    assert posted == [{}]  # no organization → the token user's account


async def test_ensure_fork_forks_into_the_configured_org() -> None:
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append(json.loads(request.content))
            return httpx.Response(202, json={"full_name": "bots-inc/app"})
        return httpx.Response(200, json=[])  # existing fork: ready on first probe

    with _fork_client(handler):
        assert await ensure_fork("org/app", org="bots-inc") == "bots-inc/app"
    assert posted == [{"organization": "bots-inc"}]


async def test_ensure_fork_rejects_a_response_without_full_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={})

    with _fork_client(handler), pytest.raises(RuntimeError, match="no full_name"):
        await ensure_fork("org/app")


async def test_ensure_fork_times_out_when_git_data_never_appears() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"full_name": "seizu-bot/app"})
        return httpx.Response(409, json={"message": "Git Repository is empty."})

    with (
        _fork_client(handler),
        patch("reporting.services.github_checks._FORK_READY_TIMEOUT_SECONDS", 0),
        pytest.raises(TimeoutError, match="no git data"),
    ):
        await ensure_fork("org/app")


async def test_ensure_fork_raises_on_api_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"full_name": "seizu-bot/app"})
        return httpx.Response(403, json={"message": "rate limited"})

    with _fork_client(handler), pytest.raises(httpx.HTTPStatusError):
        await ensure_fork("org/app")
