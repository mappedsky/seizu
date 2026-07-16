import uuid

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from reporting.temporal_workflows.configured_workflow import ConfiguredWorkflow
from reporting.temporal_workflows.shared import (
    CodeWorkflowInputRequest,
    ConfiguredActivity,
    ConfiguredActivityInput,
    ConfiguredQueryInput,
    ConfiguredQueryResult,
    ConfiguredWorkflowDefinition,
    ConfiguredWorkflowInvocation,
)


@activity.defn(name="load_configured_workflow")
async def _load(invocation: ConfiguredWorkflowInvocation) -> ConfiguredWorkflowDefinition:
    return ConfiguredWorkflowDefinition(
        workflow_id=invocation.workflow_id,
        creator_user_id="user-1",
        version=2,
        inputs=[ConfiguredQueryInput(input_id="query", cypher="RETURN 1")],
        activities=[ConfiguredActivity(type="log", input_id="query", parameters={})],
    )


@activity.defn(name="load_configured_workflow")
async def _load_skipped(invocation: ConfiguredWorkflowInvocation) -> ConfiguredWorkflowDefinition:
    return ConfiguredWorkflowDefinition(
        workflow_id=invocation.workflow_id,
        creator_user_id="user-1",
        version=2,
        skipped_reason="disabled",
    )


@activity.defn(name="execute_configured_query")
async def _query(value: ConfiguredQueryInput) -> ConfiguredQueryResult:
    return ConfiguredQueryResult(input_id=value.input_id, rows=[{"details": {"id": 1}}])


@activity.defn(name="execute_configured_query")
async def _query_fails(value: ConfiguredQueryInput) -> ConfiguredQueryResult:
    raise RuntimeError("query failed")


@activity.defn(name="execute_configured_query")
async def _query_empty(value: ConfiguredQueryInput) -> ConfiguredQueryResult:
    return ConfiguredQueryResult(input_id=value.input_id, rows=[])


@activity.defn(name="execute_configured_activity")
async def _action(value: ConfiguredActivityInput) -> dict:
    return {"status": "completed", "rows": len(value.rows)}


@activity.defn(name="record_configured_workflow_result")
async def _record(value: dict) -> None:
    return None


@activity.defn(name="load_configured_workflow")
async def _load_code(invocation: ConfiguredWorkflowInvocation) -> ConfiguredWorkflowDefinition:
    return ConfiguredWorkflowDefinition(
        workflow_id=invocation.workflow_id,
        creator_user_id="user-1",
        version=2,
        activities=[
            ConfiguredActivity(
                type="workflow",
                input_id=None,
                parameters={"workflow": "example"},
                requires_rows=False,
            )
        ],
    )


@activity.defn(name="load_configured_workflow")
async def _load_empty_code(invocation: ConfiguredWorkflowInvocation) -> ConfiguredWorkflowDefinition:
    return ConfiguredWorkflowDefinition(
        workflow_id=invocation.workflow_id,
        creator_user_id="user-1",
        version=2,
        inputs=[ConfiguredQueryInput(input_id="query", cypher="RETURN 1")],
        activities=[
            ConfiguredActivity(
                type="workflow",
                input_id="query",
                parameters={"workflow": "example"},
                requires_rows=True,
            )
        ],
    )


@activity.defn(name="build_code_workflow_input")
async def _build_code(value: CodeWorkflowInputRequest) -> dict:
    return {"workflow_id": value.workflow_id}


@workflow.defn(name="example")
class _ExampleWorkflow:
    @workflow.run
    async def run(self, value: dict) -> dict:
        return {"status": "child-completed", **value}


async def _execute(*activities):
    queue = f"configured-{uuid.uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[ConfiguredWorkflow, _ExampleWorkflow],
            activities=list(activities),
        ):
            return await env.client.execute_workflow(
                "seizu_configured_workflow",
                ConfiguredWorkflowInvocation(workflow_id="workflow-1"),
                id=f"workflow-{uuid.uuid4()}",
                task_queue=queue,
            )


async def test_configured_workflow_runs_queries_and_activities():
    result = await _execute(_load, _query, _action, _record)
    assert result["status"] == "completed"
    assert result["input_rows"] == {"query": 1}
    assert result["activity_results"] == [{"status": "completed", "rows": 1}]


async def test_configured_workflow_returns_skip_reason():
    result = await _execute(_load_skipped)
    assert result["status"] == "skipped"
    assert result["skipped_reason"] == "disabled"


async def test_configured_workflow_records_and_raises_failure():
    with pytest.raises(Exception):
        await _execute(_load, _query_fails, _action, _record)


async def test_configured_workflow_runs_code_defined_child():
    result = await _execute(_load_code, _build_code, _record)
    assert result["activity_results"] == [{"status": "child-completed", "workflow_id": "workflow-1"}]


async def test_configured_workflow_skips_row_dependent_child_without_rows():
    result = await _execute(_load_empty_code, _query_empty, _record)
    assert result["activity_results"] == [{"status": "skipped", "reason": "input returned no rows"}]
