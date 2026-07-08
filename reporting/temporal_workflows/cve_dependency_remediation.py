"""CVE dependency remediation workflow.

Deterministic orchestration only: group the scheduled query's result rows by
(repository, dependency package), then run one sandbox remediation activity
per group. A failing group records an error entry instead of aborting the
other groups.

After a PR is pushed, the workflow watches its CI (durable ``workflow.sleep``
timers + a short read-only status activity) until the checks settle or
``ci_watch_max_seconds`` elapses. Checks stuck in queued (no runner coming)
are ignored by the status activity so they can't stall the watch. On failing
checks it runs the CI-fix activity (bounded by ``ci_fix_max_attempts``): the
coding agent either pushes fixes — after which the watch resumes for the
re-triggered checks — or posts a comment explaining that the failures are
unrelated to the dependency change. Everything stays sequential on purpose:
it bounds concurrent coding-agent spend per run.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from reporting.temporal_workflows.activities import (
        get_pr_ci_status,
        run_dependency_ci_fix,
        run_dependency_remediation,
    )
    from reporting.temporal_workflows.shared import (
        CiFixInput,
        CveDependencyRemediationInput,
        CveDependencyRemediationResult,
        DependencyRemediationInput,
        DependencyRemediationResult,
        PrCiStatusInput,
        group_rows_by_repo_package,
    )

# Consecutive status-activity failures (each already retried by Temporal)
# before the watch gives up on GitHub API access.
_MAX_STATUS_ERRORS = 3
# Polls returning "no checks at all" tolerated before concluding the repo has
# no CI on this PR — right after a push, CI may not have registered runs yet.
_MAX_EMPTY_POLLS = 3


@workflow.defn(name="cve_dependency_remediation")
class CveDependencyRemediationWorkflow:
    @workflow.run
    async def run(self, input: CveDependencyRemediationInput) -> CveDependencyRemediationResult:
        by_dependency = group_rows_by_repo_package(input.rows)
        results: list[DependencyRemediationResult] = []
        # Sequential on purpose: bounds concurrent coding-agent spend per run.
        for repo, package in sorted(by_dependency):
            try:
                result = await workflow.execute_activity(
                    run_dependency_remediation,
                    DependencyRemediationInput(
                        repo=repo,
                        package=package,
                        cves=by_dependency[(repo, package)],
                        creator_user_id=input.creator_user_id,
                        scheduled_query_id=input.scheduled_query_id,
                    ),
                    start_to_close_timeout=timedelta(seconds=input.timeout_seconds + 60),
                    # The sandbox run heartbeats on every output chunk and at
                    # least every ~30s via a ticker; 300s of silence means the
                    # activity is truly stuck, not just quiet.
                    heartbeat_timeout=timedelta(seconds=300),
                    # A retry repeats a very expensive coding-agent session and
                    # risks duplicate PRs — record the failure instead. Manual
                    # re-runs are safe: deterministic branch names mean the run
                    # updates the existing PR rather than opening another.
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except ActivityError as exc:
                workflow.logger.error("Dependency remediation failed for %s/%s: %s", repo, package, exc)
                results.append(
                    DependencyRemediationResult(
                        repo=repo,
                        package=package,
                        error=str(exc),
                        status="failed",
                    )
                )
                continue
            # Watch CI only on a freshly pushed PR. A "skipped" run means an
            # earlier run's PR already exists — that run watched it (re-checking
            # here would re-trigger fixes/comments on every schedule tick).
            if input.ci_watch_max_seconds > 0 and result.status == "completed" and result.pr_url:
                await self._watch_pr_ci(result, input)
                workflow.logger.info(
                    "CI watch finished for %s: %s%s",
                    result.pr_url,
                    result.ci_status,
                    f" ({result.ci_detail})" if result.ci_detail else "",
                )
            results.append(result)
        return CveDependencyRemediationResult(per_dependency=results)

    async def _watch_pr_ci(self, result: DependencyRemediationResult, input: CveDependencyRemediationInput) -> None:
        """Poll one PR's checks until they settle; record the outcome on ``result``."""
        assert result.pr_url is not None
        deadline = workflow.now() + timedelta(seconds=input.ci_watch_max_seconds)
        fix_attempts = 0
        status_errors = 0
        empty_polls = 0
        while True:
            remaining = (deadline - workflow.now()).total_seconds()
            if remaining <= 0:
                result.ci_status = "timed_out"
                result.ci_detail = f"checks did not settle within {input.ci_watch_max_seconds}s"
                return
            await workflow.sleep(min(input.ci_poll_seconds, remaining))
            try:
                status = await workflow.execute_activity(
                    get_pr_ci_status,
                    PrCiStatusInput(
                        repo=result.repo,
                        pr_url=result.pr_url,
                        queued_stuck_seconds=input.ci_queued_stuck_seconds,
                    ),
                    start_to_close_timeout=timedelta(seconds=120),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except ActivityError as exc:
                status_errors += 1
                if status_errors >= _MAX_STATUS_ERRORS:
                    workflow.logger.error("CI status checks failing for %s: %s", result.pr_url, exc)
                    result.ci_status = "error"
                    result.ci_detail = str(exc)
                    return
                continue
            status_errors = 0

            if status.state in ("merged", "closed"):
                result.ci_status = "merged" if status.state == "merged" else "pr_closed"
                return
            if status.state == "pending":
                continue
            if status.state == "no_checks":
                empty_polls += 1
                if empty_polls >= _MAX_EMPTY_POLLS:
                    result.ci_status = "no_checks"
                    return
                continue
            if status.state == "success":
                result.ci_status = "fixed" if fix_attempts else "passed"
                if status.ignored:
                    result.ci_detail = "ignored checks: " + ", ".join(status.ignored)
                return

            # state == "failure": every non-ignored check finished, some failed.
            failing_names = ", ".join(check.name for check in status.failing)
            if fix_attempts >= input.ci_fix_max_attempts:
                result.ci_status = "ci_failed"
                result.ci_detail = f"failing checks: {failing_names}"
                return
            fix_attempts += 1
            try:
                fix = await workflow.execute_activity(
                    run_dependency_ci_fix,
                    CiFixInput(
                        repo=result.repo,
                        package=result.package,
                        pr_url=result.pr_url,
                        base_branch=result.base_branch,
                        branch_name=result.branch_name,
                        failing=status.failing,
                        creator_user_id=input.creator_user_id,
                        scheduled_query_id=input.scheduled_query_id,
                        ignored=status.ignored,
                    ),
                    start_to_close_timeout=timedelta(seconds=input.timeout_seconds + 60),
                    heartbeat_timeout=timedelta(seconds=300),
                    # Same rationale as remediation: never auto-repeat a
                    # coding-agent session.
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except ActivityError as exc:
                workflow.logger.error("CI fix failed for %s: %s", result.pr_url, exc)
                result.ci_status = "fix_failed"
                result.ci_detail = str(exc)
                return
            if fix.action == "commented":
                # The agent judged every failure unrelated to the upgrade;
                # nothing was pushed, so CI will not re-run — stop watching.
                result.ci_status = "failures_commented"
                result.ci_detail = fix.comment_url or ""
                return
            if fix.action not in ("pushed", "pushed_and_commented"):
                result.ci_status = "fix_failed"
                result.ci_detail = fix.error or "the CI fix run produced no fix and no comment"
                return
            # Fixes were pushed: the new head re-triggers CI; keep watching
            # until the deadline for the fresh checks to settle.
            if fix.comment_url:
                result.ci_detail = fix.comment_url
