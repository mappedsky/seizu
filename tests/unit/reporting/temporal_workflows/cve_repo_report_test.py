from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from reporting.temporal_workflows.cve_repo_report import CveRepoReportWorkflow
from reporting.temporal_workflows.shared import (
    CveRepoReportInput,
    RepoChatInput,
    RepoChatResult,
)


def _input(**kwargs) -> CveRepoReportInput:
    defaults = dict(
        creator_user_id="user-1",
        scheduled_query_id="sq-1",
        chat_timeout_seconds=60,
        rows=[
            {"repo": "org/app", "cve_id": "CVE-2026-0001"},
            {"repo": "org/app", "cve_id": "CVE-2026-0002"},
            {"repo": "org/lib", "cve_id": "CVE-2026-0003"},
        ],
    )
    defaults.update(kwargs)
    return CveRepoReportInput(**defaults)


@activity.defn(name="run_repo_cve_chat")
async def _mock_run_repo_cve_chat(inp: RepoChatInput) -> RepoChatResult:
    return RepoChatResult(repo=inp.repo, thread_id="t1", summary="done", status="completed")


@activity.defn(name="run_repo_cve_chat")
async def _mock_run_repo_cve_chat_fail(inp: RepoChatInput) -> RepoChatResult:
    raise RuntimeError(f"activity failed for {inp.repo}")


async def test_workflow_runs_all_repos_and_returns_results():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-q",
            workflows=[CveRepoReportWorkflow],
            activities=[_mock_run_repo_cve_chat],
        ):
            result = await env.client.execute_workflow(
                "cve_repo_report",
                _input(),
                id="wf-1",
                task_queue="test-q",
            )

    repos = {r["repo"] for r in result["per_repo"]}
    assert repos == {"org/app", "org/lib"}
    assert all(r["status"] == "completed" for r in result["per_repo"])


async def test_workflow_records_error_and_continues_on_activity_failure():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-q2",
            workflows=[CveRepoReportWorkflow],
            activities=[_mock_run_repo_cve_chat_fail],
        ):
            result = await env.client.execute_workflow(
                "cve_repo_report",
                _input(),
                id="wf-2",
                task_queue="test-q2",
            )

    assert len(result["per_repo"]) == 2
    assert all(r["status"] == "failed" for r in result["per_repo"])
    assert all(r["error"] for r in result["per_repo"])
