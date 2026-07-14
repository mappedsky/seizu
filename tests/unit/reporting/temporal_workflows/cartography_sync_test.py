import asyncio

from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from cartography_sync.shared import (
    CartographyModuleActivityInput,
    CartographyModuleResult,
    CartographyModuleRun,
    CartographyStage,
    CartographySyncInput,
)
from reporting.temporal_workflows.cartography_sync import CartographySyncWorkflow

_started: list[str] = []
_stage_gate: asyncio.Event = asyncio.Event()


def _input(stages: list[CartographyStage], task_queue: str, **kwargs) -> CartographySyncInput:
    defaults = dict(
        scheduled_query_id="sq-1",
        stages=stages,
        activity_task_queue=task_queue,
        module_timeout_seconds=60,
        retry_attempts=1,
    )
    defaults.update(kwargs)
    return CartographySyncInput(**defaults)


@activity.defn(name="run_cartography_module")
async def _mock_run_module(inp: CartographyModuleActivityInput) -> CartographyModuleResult:
    _started.append(inp.module)
    return CartographyModuleResult(module=inp.module, status="completed", output_tail="ok")


@activity.defn(name="run_cartography_module")
async def _mock_run_module_parallel_gate(inp: CartographyModuleActivityInput) -> CartographyModuleResult:
    # Both stage members must be running concurrently for either to finish.
    _started.append(inp.module)
    if len([m for m in _started if m in ("aws", "github")]) == 2:
        _stage_gate.set()
    await asyncio.wait_for(_stage_gate.wait(), timeout=10)
    return CartographyModuleResult(module=inp.module, status="completed")


@activity.defn(name="run_cartography_module")
async def _mock_run_module_fail_github(inp: CartographyModuleActivityInput) -> CartographyModuleResult:
    _started.append(inp.module)
    if inp.module == "github":
        raise ApplicationError("boom", type="CartographyModuleFailed", non_retryable=True)
    return CartographyModuleResult(module=inp.module, status="completed")


async def _execute(workflow_input: CartographySyncInput, task_queue: str, mock_activity) -> dict:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[CartographySyncWorkflow],
            activities=[mock_activity],
        ):
            return await env.client.execute_workflow(
                "cartography_sync",
                workflow_input,
                id="wf-1",
                task_queue=task_queue,
            )


async def test_stages_run_in_order():
    _started.clear()
    stages = [
        CartographyStage(runs=[CartographyModuleRun(module="aws")]),
        CartographyStage(runs=[CartographyModuleRun(module="cve")]),
    ]
    result = await _execute(_input(stages, "carto-q1"), "carto-q1", _mock_run_module)
    assert _started == ["aws", "cve"]
    assert result["status"] == "completed"
    assert [r["status"] for stage in result["stages"] for r in stage["results"]] == ["completed", "completed"]


async def test_stage_members_run_in_parallel():
    _started.clear()
    _stage_gate.clear()
    stages = [
        CartographyStage(runs=[CartographyModuleRun(module="aws"), CartographyModuleRun(module="github")]),
        CartographyStage(runs=[CartographyModuleRun(module="cve")]),
    ]
    result = await _execute(_input(stages, "carto-q2"), "carto-q2", _mock_run_module_parallel_gate)
    assert set(_started[:2]) == {"aws", "github"}
    assert _started[2] == "cve"
    assert result["status"] == "completed"


async def test_failure_recorded_and_later_stages_still_run():
    _started.clear()
    stages = [
        CartographyStage(runs=[CartographyModuleRun(module="github")]),
        CartographyStage(runs=[CartographyModuleRun(module="cve")]),
    ]
    result = await _execute(_input(stages, "carto-q3"), "carto-q3", _mock_run_module_fail_github)
    assert _started == ["github", "cve"]
    assert result["status"] == "completed_with_errors"
    assert result["stages"][0]["results"][0]["status"] == "failed"
    assert "boom" in result["stages"][0]["results"][0]["output_tail"]
    assert result["stages"][1]["results"][0]["status"] == "completed"


async def test_stop_on_failure_skips_remaining_stages():
    _started.clear()
    stages = [
        CartographyStage(runs=[CartographyModuleRun(module="github")]),
        CartographyStage(runs=[CartographyModuleRun(module="cve"), CartographyModuleRun(module="aws")]),
    ]
    result = await _execute(_input(stages, "carto-q4", stop_on_failure=True), "carto-q4", _mock_run_module_fail_github)
    assert _started == ["github"]
    assert result["status"] == "stopped_on_failure"
    skipped = result["stages"][1]["results"]
    assert [r["status"] for r in skipped] == ["skipped", "skipped"]
