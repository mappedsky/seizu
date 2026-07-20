import asyncio
import uuid
from types import SimpleNamespace
from typing import cast

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from reporting.temporal_workflows.configured_workflow import (
    ConfiguredWorkflow,
    ConfiguredWorkflowWatchPoll,
    _activity_maximum_attempts,
)
from reporting.temporal_workflows.shared import (
    CodeWorkflowInputRequest,
    CodeWorkflowOutputRequest,
    ConfiguredActivity,
    ConfiguredActivityInput,
    ConfiguredActivityOutput,
    ConfiguredQueryInput,
    ConfiguredStage,
    ConfiguredWorkflowDefinition,
    ConfiguredWorkflowInvocation,
)


@activity.defn(name="load_configured_workflow")
async def _load(invocation: ConfiguredWorkflowInvocation) -> ConfiguredWorkflowDefinition:
    return ConfiguredWorkflowDefinition(
        workflow_id=invocation.workflow_id,
        creator_user_id="user-1",
        version=2,
        stages=[
            ConfiguredStage(
                activities=[
                    ConfiguredActivity(
                        type="query",
                        input_id=None,
                        output_id="first",
                        parameters={"cypher": "RETURN 1"},
                        requires_rows=False,
                    ),
                    ConfiguredActivity(
                        type="query",
                        input_id=None,
                        output_id="second",
                        parameters={"cypher": "RETURN 2"},
                        requires_rows=False,
                    ),
                ]
            ),
            ConfiguredStage(
                activities=[ConfiguredActivity(type="log", input_id="first", output_id="logged", parameters={})]
            ),
        ],
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
async def _query(value: ConfiguredQueryInput) -> ConfiguredActivityOutput:
    return ConfiguredActivityOutput(
        output_id=value.output_id,
        value=[{"details": {"id": value.output_id}}],
        metadata={"status": "completed", "row_count": 1},
    )


_settled: list[str] = []


@activity.defn(name="execute_configured_query")
async def _query_one_fails(value: ConfiguredQueryInput) -> ConfiguredActivityOutput:
    if value.output_id == "first":
        raise RuntimeError("query failed")
    await asyncio.sleep(0.01)
    _settled.append(value.output_id)
    return ConfiguredActivityOutput(output_id=value.output_id, value=[], metadata={"status": "completed"})


@activity.defn(name="execute_configured_query")
async def _query_empty(value: ConfiguredQueryInput) -> ConfiguredActivityOutput:
    return ConfiguredActivityOutput(output_id=value.output_id, value=[], metadata={"status": "completed"})


@activity.defn(name="execute_configured_activity")
async def _action(value: ConfiguredActivityInput) -> ConfiguredActivityOutput:
    return ConfiguredActivityOutput(
        output_id=value.output_id,
        value={"rows_logged": len(value.input_value)},
        metadata={"status": "completed"},
    )


@activity.defn(name="record_configured_workflow_result")
async def _record(value: dict) -> None:
    return None


@activity.defn(name="check_configured_workflow_watch")
async def _watch_triggered(value: ConfiguredWorkflowInvocation) -> bool:
    return True


@activity.defn(name="check_configured_workflow_watch")
async def _watch_unchanged(value: ConfiguredWorkflowInvocation) -> bool:
    return False


@activity.defn(name="load_configured_workflow")
async def _load_code(invocation: ConfiguredWorkflowInvocation) -> ConfiguredWorkflowDefinition:
    return ConfiguredWorkflowDefinition(
        workflow_id=invocation.workflow_id,
        creator_user_id="user-1",
        version=2,
        stages=[
            ConfiguredStage(
                activities=[
                    ConfiguredActivity(
                        type="workflow",
                        input_id=None,
                        output_id="child",
                        parameters={"workflow": "example"},
                        requires_rows=False,
                    )
                ]
            )
        ],
    )


@activity.defn(name="load_configured_workflow")
async def _load_empty_code(invocation: ConfiguredWorkflowInvocation) -> ConfiguredWorkflowDefinition:
    return ConfiguredWorkflowDefinition(
        workflow_id=invocation.workflow_id,
        creator_user_id="user-1",
        version=2,
        stages=[
            ConfiguredStage(
                activities=[
                    ConfiguredActivity(
                        type="query",
                        input_id=None,
                        output_id="query",
                        parameters={"cypher": "RETURN 1"},
                        requires_rows=False,
                    )
                ]
            ),
            ConfiguredStage(
                activities=[
                    ConfiguredActivity(
                        type="workflow",
                        input_id="query",
                        output_id="child",
                        parameters={"workflow": "example"},
                        requires_rows=True,
                    )
                ]
            ),
        ],
    )


@activity.defn(name="build_code_workflow_input")
async def _build_code(value: CodeWorkflowInputRequest) -> dict:
    return {"workflow_id": value.workflow_id}


@activity.defn(name="normalize_code_workflow_output")
async def _normalize_code(value: CodeWorkflowOutputRequest) -> ConfiguredActivityOutput:
    return ConfiguredActivityOutput(
        output_id=value.output_id,
        value=value.value,
        metadata={"status": "completed", "workflow": value.workflow_name},
    )


@workflow.defn(name="example")
class _ExampleWorkflow:
    @workflow.run
    async def run(self, value: dict) -> dict:
        return {"status": "child-completed", **value}


@activity.defn(name="load_configured_workflow")
async def _load_top_level_code(invocation: ConfiguredWorkflowInvocation) -> ConfiguredWorkflowDefinition:
    # A registry workflow as its own top-level activity type (post-migration
    # shape). The child-workflow classification rides in the durable
    # code_workflow_name field, so replay never consults the live registry.
    return ConfiguredWorkflowDefinition(
        workflow_id=invocation.workflow_id,
        creator_user_id="user-1",
        version=2,
        stages=[
            ConfiguredStage(
                activities=[
                    ConfiguredActivity(
                        type="cartography_sync",
                        input_id=None,
                        output_id="child",
                        parameters={},
                        requires_rows=False,
                        code_workflow_name="cartography_sync",
                    )
                ]
            )
        ],
    )


@workflow.defn(name="cartography_sync")
class _FakeCartographySyncWorkflow:
    @workflow.run
    async def run(self, value: dict) -> dict:
        return {"status": "child-completed", **value}


async def _execute(*activities):
    queue = f"configured-{uuid.uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[ConfiguredWorkflow, _ExampleWorkflow, _FakeCartographySyncWorkflow],
            activities=list(activities),
        ):
            return await env.client.execute_workflow(
                "seizu_configured_workflow",
                ConfiguredWorkflowInvocation(workflow_id="workflow-1"),
                id=f"workflow-{uuid.uuid4()}",
                task_queue=queue,
            )


async def _execute_watch(check_activity):
    queue = f"configured-watch-{uuid.uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[ConfiguredWorkflow, ConfiguredWorkflowWatchPoll],
            activities=[check_activity, _load, _query, _action, _record],
        ):
            return await env.client.execute_workflow(
                "seizu_configured_workflow_watch_poll",
                ConfiguredWorkflowInvocation(workflow_id="workflow-1"),
                id="seizu-workflow-poll:workflow-1-2026-07-18T00:35:34Z",
                task_queue=queue,
            )


async def test_stages_run_and_publish_named_outputs():
    result = await _execute(_load, _query, _action, _record)
    assert result["status"] == "completed"
    assert result["activity_results"] == [
        {"output": "first", "status": "completed", "row_count": 1},
        {"output": "second", "status": "completed", "row_count": 1},
        {"output": "logged", "status": "completed"},
    ]


async def test_returns_skip_reason():
    result = await _execute(_load_skipped)
    assert result["status"] == "skipped"
    assert result["skipped_reason"] == "disabled"


async def test_failed_stage_waits_for_started_siblings_and_stops():
    _settled.clear()
    with pytest.raises(Exception):
        await _execute(_load, _query_one_fails, _action, _record)
    assert _settled == ["second"]


async def test_runs_code_defined_child_and_normalizes_output():
    # Superseded dispatcher shape (type="workflow"): kept for durable-history
    # replay of runs recorded before top-level types existed.
    result = await _execute(_load_code, _build_code, _normalize_code, _record)
    assert result["activity_results"] == [{"output": "child", "status": "completed", "workflow": "example"}]


async def test_runs_top_level_code_workflow_type():
    result = await _execute(_load_top_level_code, _build_code, _normalize_code, _record)
    assert result["activity_results"] == [{"output": "child", "status": "completed", "workflow": "cartography_sync"}]


async def test_skips_row_dependent_child_without_rows():
    result = await _execute(_load_empty_code, _query_empty, _record)
    assert result["activity_results"][-1] == {
        "output": "child",
        "status": "skipped",
        "reason": "input returned no rows",
    }


def test_old_configured_activity_history_uses_previous_retry_policy():
    configured = cast(
        ConfiguredActivity,
        SimpleNamespace(type="log", input_id="rows", output_id="logged", parameters={}),
    )

    assert _activity_maximum_attempts(configured) == 3


async def test_watch_poll_only_creates_real_run_when_triggered():
    skipped = await _execute_watch(_watch_unchanged)
    assert skipped["status"] == "skipped"
    assert skipped["skipped_reason"] == "watch scan unchanged"

    completed = await _execute_watch(_watch_triggered)
    assert completed["status"] == "completed"
    assert completed["activity_results"][-1]["output"] == "logged"
