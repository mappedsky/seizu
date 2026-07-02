from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from reporting.temporal_workflows.cve_dependency_remediation import CveDependencyRemediationWorkflow
from reporting.temporal_workflows.shared import (
    CveDependencyRemediationInput,
    DependencyChatInput,
    DependencyChatResult,
)


def _input(**kwargs) -> CveDependencyRemediationInput:
    defaults = dict(
        creator_user_id="user-1",
        scheduled_query_id="sq-1",
        chat_timeout_seconds=60,
        rows=[
            {"repo": "org/app", "package": "requests", "cve_id": "CVE-2026-0001"},
            {"repo": "org/app", "package": "requests", "cve_id": "CVE-2026-0002"},
            {"repo": "org/app", "package": "flask", "cve_id": "CVE-2026-0003"},
            {"repo": "org/lib", "package": "lodash", "cve_id": "CVE-2026-0004"},
        ],
    )
    defaults.update(kwargs)
    return CveDependencyRemediationInput(**defaults)


@activity.defn(name="run_dependency_remediation_chat")
async def _mock_remediation_chat(inp: DependencyChatInput) -> DependencyChatResult:
    return DependencyChatResult(
        repo=inp.repo, package=inp.package, thread_id="t1", summary="PR opened", status="completed"
    )


@activity.defn(name="run_dependency_remediation_chat")
async def _mock_remediation_chat_fail(inp: DependencyChatInput) -> DependencyChatResult:
    raise RuntimeError(f"activity failed for {inp.repo}/{inp.package}")


async def test_workflow_runs_one_chat_per_repo_package_group():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-q",
            workflows=[CveDependencyRemediationWorkflow],
            activities=[_mock_remediation_chat],
        ):
            result = await env.client.execute_workflow(
                "cve_dependency_remediation",
                _input(),
                id="wf-1",
                task_queue="test-q",
            )

    groups = {(r["repo"], r["package"]) for r in result["per_dependency"]}
    # Two CVEs on org/app requests collapse into one group (one chat / one PR).
    assert groups == {("org/app", "requests"), ("org/app", "flask"), ("org/lib", "lodash")}
    assert all(r["status"] == "completed" for r in result["per_dependency"])


async def test_workflow_records_error_and_continues_on_activity_failure():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-q2",
            workflows=[CveDependencyRemediationWorkflow],
            activities=[_mock_remediation_chat_fail],
        ):
            result = await env.client.execute_workflow(
                "cve_dependency_remediation",
                _input(),
                id="wf-2",
                task_queue="test-q2",
            )

    assert len(result["per_dependency"]) == 3
    assert all(r["status"] == "failed" for r in result["per_dependency"])
    assert all(r["error"] for r in result["per_dependency"])
