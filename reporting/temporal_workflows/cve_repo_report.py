"""CVE repository report workflow.

Deterministic orchestration only: group the scheduled query's result rows by
repository, then run one chat-session activity per repository. A failing
repository records an error entry instead of aborting the other repositories.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from reporting.temporal_workflows.activities import run_repo_cve_chat
    from reporting.temporal_workflows.shared import (
        CveRepoReportInput,
        CveRepoReportResult,
        RepoChatInput,
        RepoChatResult,
        group_rows_by_repo,
    )


@workflow.defn(name="cve_repo_report")
class CveRepoReportWorkflow:
    @workflow.run
    async def run(self, input: CveRepoReportInput) -> CveRepoReportResult:
        by_repo = group_rows_by_repo(input.rows)
        results: list[RepoChatResult] = []
        # Sequential on purpose: bounds concurrent LLM spend per workflow run.
        for repo in sorted(by_repo):
            try:
                result = await workflow.execute_activity(
                    run_repo_cve_chat,
                    RepoChatInput(
                        repo=repo,
                        cves=by_repo[repo],
                        creator_user_id=input.creator_user_id,
                        scheduled_query_id=input.scheduled_query_id,
                    ),
                    start_to_close_timeout=timedelta(seconds=input.chat_timeout_seconds + 30),
                    heartbeat_timeout=timedelta(seconds=180),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                results.append(result)
            except ActivityError as exc:
                workflow.logger.error("Repo CVE chat failed for %s: %s", repo, exc)
                results.append(RepoChatResult(repo=repo, thread_id=None, summary="", error=str(exc)))
        return CveRepoReportResult(per_repo=results)
