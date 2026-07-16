import asyncio
import uuid

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
from reporting.temporal_workflows.cartography_sync import CartographyModuleWorkflow, CartographySyncWorkflow

_started: list[str] = []
# Events are created per test (inside that test's event loop) — a module-level
# asyncio.Event binds to the first loop that awaits it and breaks later tests.
_events: dict[str, asyncio.Event] = {}
_concurrent = 0
_max_concurrent = 0


def _fresh_events() -> None:
    _events["stage_gate"] = asyncio.Event()
    _events["hold_gate"] = asyncio.Event()
    _events["holder_running"] = asyncio.Event()


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
        _events["stage_gate"].set()
    await asyncio.wait_for(_events["stage_gate"].wait(), timeout=10)
    return CartographyModuleResult(module=inp.module, status="completed")


@activity.defn(name="run_cartography_module")
async def _mock_run_module_fail_github(inp: CartographyModuleActivityInput) -> CartographyModuleResult:
    _started.append(inp.module)
    if inp.module == "github":
        raise ApplicationError("boom", type="CartographyModuleFailed", non_retryable=True)
    return CartographyModuleResult(module=inp.module, status="completed")


@activity.defn(name="run_cartography_module")
async def _mock_run_module_hold(inp: CartographyModuleActivityInput) -> CartographyModuleResult:
    global _concurrent, _max_concurrent
    _concurrent += 1
    _max_concurrent = max(_max_concurrent, _concurrent)
    _events["holder_running"].set()
    await asyncio.wait_for(_events["hold_gate"].wait(), timeout=20)
    _concurrent -= 1
    return CartographyModuleResult(module=inp.module, status="completed")


async def _execute(workflow_input: CartographySyncInput, task_queue: str, mock_activity) -> dict:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[CartographySyncWorkflow, CartographyModuleWorkflow],
            activities=[mock_activity],
        ):
            return await env.client.execute_workflow(
                "cartography_sync",
                workflow_input,
                id=f"wf-{uuid.uuid4()}",
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
    _fresh_events()
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


async def test_same_module_never_overlaps_across_pipelines():
    """Two concurrent pipelines syncing one module serialize on the child
    workflow ID mutex — the module activity never runs twice at once.

    Auto time-skipping is disabled so virtual time (activity timeouts, the
    blocked pipeline's wait timers) only advances when the test says so.
    """
    global _concurrent, _max_concurrent
    _concurrent = 0
    _max_concurrent = 0
    _fresh_events()
    stages = [CartographyStage(runs=[CartographyModuleRun(module="github")])]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with env.auto_time_skipping_disabled():
            async with Worker(
                env.client,
                task_queue="carto-q5",
                workflows=[CartographySyncWorkflow, CartographyModuleWorkflow],
                activities=[_mock_run_module_hold],
            ):
                holder = await env.client.start_workflow(
                    "cartography_sync", _input(stages, "carto-q5"), id="wf-mutex-a", task_queue="carto-q5"
                )
                await asyncio.wait_for(_events["holder_running"].wait(), timeout=10)
                blocked = await env.client.start_workflow(
                    "cartography_sync", _input(stages, "carto-q5"), id="wf-mutex-b", task_queue="carto-q5"
                )
                # The blocked pipeline hits the taken child workflow ID and
                # waits; the module activity must not have run concurrently.
                await asyncio.sleep(0.5)
                assert _max_concurrent == 1
                _events["hold_gate"].set()
                holder_result = await holder.result()
                # Nudge virtual time so the blocked pipeline's wait timers
                # fire until its own run gets the mutex and completes.
                blocked_task = asyncio.ensure_future(blocked.result())
                for _ in range(20):
                    if blocked_task.done():
                        break
                    await env.sleep(31)
                blocked_result = await asyncio.wait_for(blocked_task, timeout=10)
    assert _max_concurrent == 1
    assert holder_result["status"] == "completed"
    assert blocked_result["status"] == "completed"


async def test_module_wait_budget_exhausted_records_busy_failure():
    """A pipeline that cannot get the module mutex within its wait budget
    records a failed result instead of racing the in-flight run."""
    _fresh_events()
    stages = [CartographyStage(runs=[CartographyModuleRun(module="github")])]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="carto-q6",
            workflows=[CartographySyncWorkflow, CartographyModuleWorkflow],
            activities=[_mock_run_module_hold],
        ):
            holder = await env.client.start_workflow(
                "cartography_sync", _input(stages, "carto-q6"), id="wf-busy-holder", task_queue="carto-q6"
            )
            await asyncio.wait_for(_events["holder_running"].wait(), timeout=10)
            blocked_result = await env.client.execute_workflow(
                "cartography_sync",
                _input(stages, "carto-q6", module_wait_seconds=0),
                id="wf-busy-blocked",
                task_queue="carto-q6",
            )
            _events["hold_gate"].set()
            holder_result = await holder.result()
    assert blocked_result["status"] == "completed_with_errors"
    failure = blocked_result["stages"][0]["results"][0]
    assert failure["status"] == "failed"
    assert "still in progress" in failure["output_tail"]
    assert holder_result["status"] == "completed"
