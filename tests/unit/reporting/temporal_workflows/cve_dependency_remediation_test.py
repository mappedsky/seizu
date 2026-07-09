from typing import Any

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from reporting.temporal_workflows.cve_dependency_remediation import CveDependencyRemediationWorkflow
from reporting.temporal_workflows.shared import (
    CiFixInput,
    CiFixResult,
    CveDependencyRemediationInput,
    DependencyRemediationInput,
    DependencyRemediationResult,
    PrCiCheck,
    PrCiStatusInput,
    PrCiStatusResult,
)


def _input(**kwargs) -> CveDependencyRemediationInput:
    defaults = dict(
        creator_user_id="user-1",
        scheduled_query_id="sq-1",
        timeout_seconds=60,
        # The grouping/error tests below exercise remediation only; the CI
        # watch has its own tests further down.
        ci_watch_max_seconds=0,
        rows=[
            {"repo": "org/app", "package": "requests", "cve_id": "CVE-2026-0001"},
            {"repo": "org/app", "package": "requests", "cve_id": "CVE-2026-0002"},
            {"repo": "org/app", "package": "flask", "cve_id": "CVE-2026-0003"},
            {"repo": "org/lib", "package": "lodash", "cve_id": "CVE-2026-0004"},
        ],
    )
    defaults.update(kwargs)
    return CveDependencyRemediationInput(**defaults)


@activity.defn(name="run_dependency_remediation")
async def _mock_remediation(inp: DependencyRemediationInput) -> DependencyRemediationResult:
    return DependencyRemediationResult(
        repo=inp.repo,
        package=inp.package,
        pr_url=f"https://github.com/{inp.repo}/pull/1",
        status="completed",
        base_branch="main",
        branch_name="seizu/dependency-update/pip-requests-2.32.4",
    )


@activity.defn(name="run_dependency_remediation")
async def _mock_remediation_fail(inp: DependencyRemediationInput) -> DependencyRemediationResult:
    raise RuntimeError(f"activity failed for {inp.repo}/{inp.package}")


def _status_activity(states: list[PrCiStatusResult]) -> tuple[Any, list[PrCiStatusInput]]:
    """Mock get_pr_ci_status returning ``states`` in order (last one repeats)."""
    calls: list[PrCiStatusInput] = []

    @activity.defn(name="get_pr_ci_status")
    async def _mock(inp: PrCiStatusInput) -> PrCiStatusResult:
        calls.append(inp)
        return states[min(len(calls) - 1, len(states) - 1)]

    return _mock, calls


def _fix_activity(result_action: str, comment_url: str | None = None) -> tuple[Any, list[CiFixInput]]:
    calls: list[CiFixInput] = []

    @activity.defn(name="run_dependency_ci_fix")
    async def _mock(inp: CiFixInput) -> CiFixResult:
        calls.append(inp)
        return CiFixResult(repo=inp.repo, package=inp.package, action=result_action, comment_url=comment_url)

    return _mock, calls


_FAILING = [PrCiCheck(name="tests", check_run_id=7, summary="3 failed")]


async def _run_workflow(input: CveDependencyRemediationInput, activities: list[Any], wf_id: str) -> dict[str, Any]:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=f"test-q-{wf_id}",
            workflows=[CveDependencyRemediationWorkflow],
            activities=activities,
        ):
            return await env.client.execute_workflow(
                "cve_dependency_remediation",
                input,
                id=wf_id,
                task_queue=f"test-q-{wf_id}",
            )


async def test_workflow_runs_one_remediation_per_repo_package_group():
    result = await _run_workflow(_input(), [_mock_remediation], "wf-1")

    groups = {(r["repo"], r["package"]) for r in result["per_dependency"]}
    # Two CVEs on org/app requests collapse into one group (one run / one PR).
    assert groups == {("org/app", "requests"), ("org/app", "flask"), ("org/lib", "lodash")}
    assert all(r["status"] == "completed" for r in result["per_dependency"])
    assert all(r["pr_url"] for r in result["per_dependency"])
    # CI watch disabled (ci_watch_max_seconds=0) → no watch outcome recorded.
    assert all(r["ci_status"] == "" for r in result["per_dependency"])


async def test_workflow_records_error_and_continues_on_activity_failure():
    result = await _run_workflow(_input(), [_mock_remediation_fail], "wf-2")

    assert len(result["per_dependency"]) == 3
    assert all(r["status"] == "failed" for r in result["per_dependency"])
    assert all(r["error"] for r in result["per_dependency"])


_ONE_ROW = [{"repo": "org/app", "package": "requests", "cve_id": "CVE-2026-0001"}]


def _watch_input(**kwargs) -> CveDependencyRemediationInput:
    kwargs.setdefault("ci_watch_max_seconds", 3600)
    return _input(rows=_ONE_ROW, **kwargs)


async def test_ci_watch_records_passed_after_pending():
    status, status_calls = _status_activity(
        [PrCiStatusResult(state="pending", pending=["tests"]), PrCiStatusResult(state="success")]
    )
    fix, fix_calls = _fix_activity("pushed")
    result = await _run_workflow(_watch_input(), [_mock_remediation, status, fix], "wf-ci-pass")

    (dep,) = result["per_dependency"]
    assert dep["ci_status"] == "passed"
    assert len(status_calls) == 2
    assert status_calls[0].pr_url == "https://github.com/org/app/pull/1"
    assert fix_calls == []  # nothing failed, no agent spend


async def test_ci_watch_runs_fix_then_records_fixed():
    status, status_calls = _status_activity(
        [PrCiStatusResult(state="failure", failing=_FAILING), PrCiStatusResult(state="success")]
    )
    fix, fix_calls = _fix_activity("pushed")
    result = await _run_workflow(_watch_input(), [_mock_remediation, status, fix], "wf-ci-fix")

    (dep,) = result["per_dependency"]
    assert dep["ci_status"] == "fixed"
    assert len(fix_calls) == 1
    # The fix activity gets the branch identity and the failing checks.
    assert fix_calls[0].branch_name == "seizu/dependency-update/pip-requests-2.32.4"
    assert fix_calls[0].base_branch == "main"
    assert [c.name for c in fix_calls[0].failing] == ["tests"]
    # After the fix push, the watch resumed and saw success.
    assert len(status_calls) == 2


async def test_ci_watch_stops_after_unrelated_failures_are_commented():
    status, status_calls = _status_activity([PrCiStatusResult(state="failure", failing=_FAILING)])
    fix, fix_calls = _fix_activity("commented", comment_url="https://github.com/org/app/pull/1#issuecomment-9")
    result = await _run_workflow(_watch_input(), [_mock_remediation, status, fix], "wf-ci-comment")

    (dep,) = result["per_dependency"]
    # Nothing was pushed, CI won't re-run: the watch ends immediately.
    assert dep["ci_status"] == "failures_commented"
    assert dep["ci_detail"] == "https://github.com/org/app/pull/1#issuecomment-9"
    assert len(fix_calls) == 1
    assert len(status_calls) == 1


async def test_ci_watch_respects_fix_attempt_budget():
    # ci_fix_max_attempts=0 → watch and record, never spend on an agent run.
    status, _ = _status_activity([PrCiStatusResult(state="failure", failing=_FAILING)])
    fix, fix_calls = _fix_activity("pushed")
    result = await _run_workflow(_watch_input(ci_fix_max_attempts=0), [_mock_remediation, status, fix], "wf-ci-budget")

    (dep,) = result["per_dependency"]
    assert dep["ci_status"] == "ci_failed"
    assert "tests" in dep["ci_detail"]
    assert fix_calls == []


async def test_ci_watch_times_out_on_checks_that_never_settle():
    status, status_calls = _status_activity([PrCiStatusResult(state="pending", pending=["tests"])])
    fix, fix_calls = _fix_activity("pushed")
    result = await _run_workflow(
        _watch_input(ci_watch_max_seconds=600, ci_poll_seconds=120),
        [_mock_remediation, status, fix],
        "wf-ci-timeout",
    )

    (dep,) = result["per_dependency"]
    assert dep["ci_status"] == "timed_out"
    assert fix_calls == []
    # Bounded polling: ~max_wait / poll_interval status checks, not unbounded.
    assert len(status_calls) <= 6


async def test_ci_watch_gives_up_on_missing_checks_after_grace():
    # A repo with no CI at all: allow a grace period for checks to register,
    # then stop rather than polling until max wait.
    status, status_calls = _status_activity([PrCiStatusResult(state="no_checks")])
    fix, _ = _fix_activity("pushed")
    result = await _run_workflow(_watch_input(), [_mock_remediation, status, fix], "wf-ci-nochecks")

    (dep,) = result["per_dependency"]
    assert dep["ci_status"] == "no_checks"
    assert len(status_calls) == 3


async def test_ci_watch_no_checks_grace_counts_consecutive_polls_only():
    # An early empty poll must not combine with later ones once checks were
    # seen: 2× no_checks, then pending resets the grace, then 3 consecutive
    # empty polls end the watch.
    states = [
        PrCiStatusResult(state="no_checks"),
        PrCiStatusResult(state="pending", pending=["tests"]),
        PrCiStatusResult(state="no_checks"),
        PrCiStatusResult(state="no_checks"),
        PrCiStatusResult(state="no_checks"),
    ]
    status, status_calls = _status_activity(states)
    fix, _ = _fix_activity("pushed")
    result = await _run_workflow(_watch_input(), [_mock_remediation, status, fix], "wf-ci-grace")

    (dep,) = result["per_dependency"]
    assert dep["ci_status"] == "no_checks"
    # Without the consecutive reset this would have ended one poll earlier.
    assert len(status_calls) == 5


async def test_ci_watch_no_checks_grace_resets_after_fix_push():
    # Right after a fix push, CI may not have registered runs on the new head
    # yet — those empty polls must not inherit the pre-push count.
    states = [
        PrCiStatusResult(state="no_checks"),
        PrCiStatusResult(state="no_checks"),
        PrCiStatusResult(state="failure", failing=_FAILING),
        PrCiStatusResult(state="no_checks"),
        PrCiStatusResult(state="no_checks"),
        PrCiStatusResult(state="success"),
    ]
    status, status_calls = _status_activity(states)
    fix, fix_calls = _fix_activity("pushed")
    result = await _run_workflow(_watch_input(), [_mock_remediation, status, fix], "wf-ci-grace-push")

    (dep,) = result["per_dependency"]
    assert dep["ci_status"] == "fixed"
    assert len(fix_calls) == 1
    assert len(status_calls) == 6


async def test_ci_watch_ends_when_pr_is_merged():
    status, _ = _status_activity([PrCiStatusResult(state="merged")])
    fix, fix_calls = _fix_activity("pushed")
    result = await _run_workflow(_watch_input(), [_mock_remediation, status, fix], "wf-ci-merged")

    (dep,) = result["per_dependency"]
    assert dep["ci_status"] == "merged"
    assert fix_calls == []
