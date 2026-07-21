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
    ConfiguredWorkflowExecution,
    ConfiguredWorkflowWaitingSlot,
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
    TriggerConfiguredWorkflowsRequest,
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
_query_gate: asyncio.Event | None = None
_first_query_started: asyncio.Event | None = None
_active_queries = 0
_maximum_active_queries = 0


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


@activity.defn(name="execute_configured_query")
async def _query_blocked(value: ConfiguredQueryInput) -> ConfiguredActivityOutput:
    global _active_queries, _maximum_active_queries
    assert _query_gate is not None
    assert _first_query_started is not None
    _active_queries += 1
    _maximum_active_queries = max(_maximum_active_queries, _active_queries)
    _first_query_started.set()
    try:
        await _query_gate.wait()
        return ConfiguredActivityOutput(output_id=value.output_id, value=[], metadata={"status": "completed"})
    finally:
        _active_queries -= 1


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


@activity.defn(name="load_configured_workflow")
async def _load_with_triggers(invocation: ConfiguredWorkflowInvocation) -> ConfiguredWorkflowDefinition:
    definition = await _load(invocation)
    definition.trigger_workflows = ["workflow-2"]
    return definition


@activity.defn(name="trigger_configured_workflows")
async def _trigger_workflows(value: TriggerConfiguredWorkflowsRequest) -> list[str]:
    assert value.source_workflow_id == "workflow-1"
    assert value.source_creator_user_id == "user-1"
    assert value.workflow_ids == ["workflow-2"]
    return ["seizu-workflow:workflow-2:triggered"]


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
            workflows=[
                ConfiguredWorkflow,
                ConfiguredWorkflowExecution,
                ConfiguredWorkflowWaitingSlot,
                _ExampleWorkflow,
                _FakeCartographySyncWorkflow,
            ],
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
            workflows=[
                ConfiguredWorkflow,
                ConfiguredWorkflowExecution,
                ConfiguredWorkflowWaitingSlot,
                ConfiguredWorkflowWatchPoll,
            ],
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


async def test_successful_pipeline_starts_post_completion_workflows():
    result = await _execute(
        _load_with_triggers,
        _query,
        _action,
        _trigger_workflows,
        _record,
    )
    assert result["activity_results"][-1] == {
        "triggered_workflows": 1,
        "temporal_workflow_ids": ["seizu-workflow:workflow-2:triggered"],
    }


async def test_overlapping_runs_allow_only_one_waiter():
    global _query_gate, _first_query_started, _active_queries, _maximum_active_queries
    _query_gate = asyncio.Event()
    _first_query_started = asyncio.Event()
    _active_queries = 0
    _maximum_active_queries = 0
    queue = f"configured-overlap-{uuid.uuid4()}"

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[ConfiguredWorkflow, ConfiguredWorkflowExecution, ConfiguredWorkflowWaitingSlot],
            activities=[_load, _query_blocked, _action, _record],
        ):
            first = await env.client.start_workflow(
                "seizu_configured_workflow",
                ConfiguredWorkflowInvocation(workflow_id="workflow-overlap"),
                id=f"workflow-{uuid.uuid4()}",
                task_queue=queue,
            )
            await asyncio.wait_for(_first_query_started.wait(), timeout=5)
            second = await env.client.start_workflow(
                "seizu_configured_workflow",
                ConfiguredWorkflowInvocation(workflow_id="workflow-overlap"),
                id=f"workflow-{uuid.uuid4()}",
                task_queue=queue,
            )
            # Let the second parent process its first child-start attempt.
            # Fetching history here would make the time-skipping test server
            # repeatedly advance the 30-second retry timer while the first
            # activity is intentionally gated.
            await asyncio.sleep(0.1)
            assert _active_queries == 2  # the first run's two parallel stage-1 queries only
            assert _maximum_active_queries == 2
            third = await env.client.start_workflow(
                "seizu_configured_workflow",
                ConfiguredWorkflowInvocation(workflow_id="workflow-overlap"),
                id=f"workflow-{uuid.uuid4()}",
                task_queue=queue,
            )
            with env.auto_time_skipping_disabled():
                skipped = await asyncio.wait_for(third.result(), timeout=5)
            assert skipped["status"] == "skipped"
            assert skipped["skipped_reason"] == "workflow already has a waiting run"
            _query_gate.set()
            with env.auto_time_skipping_disabled():
                await asyncio.wait_for(first.result(), timeout=5)
            await env.sleep(30)
            with env.auto_time_skipping_disabled():
                await asyncio.wait_for(second.result(), timeout=5)

    assert _maximum_active_queries == 2


async def test_canceling_waiter_releases_waiting_slot():
    global _query_gate, _first_query_started, _active_queries, _maximum_active_queries
    _query_gate = asyncio.Event()
    _first_query_started = asyncio.Event()
    _active_queries = 0
    _maximum_active_queries = 0
    queue = f"configured-cancel-waiter-{uuid.uuid4()}"

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[ConfiguredWorkflow, ConfiguredWorkflowExecution, ConfiguredWorkflowWaitingSlot],
            activities=[_load, _query_blocked, _action, _record],
        ):
            first = await env.client.start_workflow(
                "seizu_configured_workflow",
                ConfiguredWorkflowInvocation(workflow_id="workflow-cancel-waiter"),
                id=f"workflow-{uuid.uuid4()}",
                task_queue=queue,
            )
            await asyncio.wait_for(_first_query_started.wait(), timeout=5)
            waiting = await env.client.start_workflow(
                "seizu_configured_workflow",
                ConfiguredWorkflowInvocation(workflow_id="workflow-cancel-waiter"),
                id=f"workflow-{uuid.uuid4()}",
                task_queue=queue,
            )
            await asyncio.sleep(0.1)

            await waiting.signal("cancel_waiting")
            with env.auto_time_skipping_disabled():
                canceled = await asyncio.wait_for(waiting.result(), timeout=5)
            assert canceled["status"] == "canceled"

            replacement = await env.client.start_workflow(
                "seizu_configured_workflow",
                ConfiguredWorkflowInvocation(workflow_id="workflow-cancel-waiter"),
                id=f"workflow-{uuid.uuid4()}",
                task_queue=queue,
            )
            await asyncio.sleep(0.1)
            _query_gate.set()
            with env.auto_time_skipping_disabled():
                await asyncio.wait_for(first.result(), timeout=5)
            await env.sleep(30)
            with env.auto_time_skipping_disabled():
                result = await asyncio.wait_for(replacement.result(), timeout=5)

    assert result["status"] == "completed"
    assert _maximum_active_queries == 2


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


async def test_real_load_configured_workflow_dispatches_code_workflow_as_child(mocker):
    """Round-trips the REAL load_configured_workflow activity (not a stub)
    through Temporal's data converter, to prove code_workflow_name survives
    encode/decode and the workflow dispatches a top-level code-workflow
    activity as a child workflow rather than falling through to the
    scheduled_query_modules module-dispatch path."""
    from reporting.schema.report_config import ScheduledQueryItem
    from reporting.temporal_workflows.activities import load_configured_workflow as real_load_configured_workflow

    item = ScheduledQueryItem.model_validate(
        {
            "scheduled_query_id": "workflow-1",
            "name": "Cartography",
            "cypher": "",
            "stages": [
                {
                    "activities": [
                        {
                            "type": "cartography_sync",
                            "output": "sync",
                            "parameters": {"module_runs": [{"module": "create-indexes", "params": {}}]},
                        }
                    ]
                }
            ],
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "created_by": "user-1",
        }
    )
    mocker.patch(
        "reporting.temporal_workflows.activities.report_store.get_scheduled_query",
        mocker.AsyncMock(return_value=item),
    )
    result = await _execute(real_load_configured_workflow, _build_code, _normalize_code, _record)
    assert result["activity_results"] == [{"output": "sync", "status": "completed", "workflow": "cartography_sync"}]


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
