"""CVE dependency remediation workflow.

Deterministic orchestration only: group the scheduled query's result rows by
(repository, dependency package), then run one sandbox remediation activity
per group. A failing group records an error entry instead of aborting the
other groups.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from reporting.temporal_workflows.activities import run_dependency_remediation
    from reporting.temporal_workflows.shared import (
        CveDependencyRemediationInput,
        CveDependencyRemediationResult,
        DependencyRemediationInput,
        DependencyRemediationResult,
        group_rows_by_repo_package,
    )


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
                results.append(result)
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
        return CveDependencyRemediationResult(per_dependency=results)
