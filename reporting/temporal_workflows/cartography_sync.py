"""Cartography sync pipeline workflow.

Deterministic orchestration only: stages run sequentially; module runs within
a stage run in parallel. Each module run is a ``cartography_module`` child
workflow whose fixed workflow ID (``seizu-cartography-module:{module}``) is
the per-module mutex — Temporal allows one open workflow per ID, so
overlapping runs of one module (across stages, schedules, ticks, and sync
worker replicas) serialize on it, and a crashed run releases it the moment
Temporal closes the workflow. The child executes the run_cartography_module
activity cross-queue on the cartography sync worker.

A failed module run records a failed result; by default later stages still
execute (matching the other Seizu workflows), unless the pipeline was
configured with stop_on_failure.
"""

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    from cartography_sync.shared import (
        STATUS_COMPLETED,
        STATUS_COMPLETED_WITH_ERRORS,
        STATUS_STOPPED_ON_FAILURE,
        CartographyModuleActivityInput,
        CartographyModuleResult,
        CartographyModuleRunRequest,
        CartographyStage,
        CartographyStageResult,
        CartographySyncInput,
        CartographySyncResult,
    )

MODULE_MUTEX_ID_PREFIX = "seizu-cartography-module:"
# How often a pipeline retries starting a module run that is blocked by an
# overlapping run of the same module.
_MODULE_WAIT_POLL_SECONDS = 30


@workflow.defn(name="cartography_module")
class CartographyModuleWorkflow:
    """One cartography module run; its fixed workflow ID is the mutex."""

    @workflow.run
    async def run(self, request: CartographyModuleRunRequest) -> CartographyModuleResult:
        return await workflow.execute_activity(
            "run_cartography_module",  # by name — registered on the sync worker
            CartographyModuleActivityInput(
                module=request.module,
                params=request.params,
                timeout_seconds=request.timeout_seconds,
            ),
            result_type=CartographyModuleResult,
            task_queue=request.activity_task_queue,
            # Slightly above the activity's own subprocess watchdog so the
            # in-activity timeout (with its output excerpt) wins.
            start_to_close_timeout=timedelta(seconds=request.timeout_seconds + 60),
            heartbeat_timeout=timedelta(seconds=request.heartbeat_timeout_seconds),
            retry_policy=RetryPolicy(
                maximum_attempts=max(1, request.retry_attempts),
                non_retryable_error_types=["CartographyConfigError"],
            ),
        )


@workflow.defn(name="cartography_sync")
class CartographySyncWorkflow:
    @workflow.run
    async def run(self, input: CartographySyncInput) -> CartographySyncResult:
        stage_results: list[CartographyStageResult] = []
        had_failure = False
        stopped = False
        for stage in input.stages:
            if stopped:
                stage_results.append(self._skipped_stage(stage))
                continue
            settled = await asyncio.gather(
                *[self._run_module(input, run.module, run.params) for run in stage.runs],
                return_exceptions=True,
            )
            results: list[CartographyModuleResult] = []
            for run, outcome in zip(stage.runs, settled):
                if isinstance(outcome, BaseException):
                    failure = self._failure_text(outcome)
                    workflow.logger.error("Cartography module %s failed: %s", run.module, failure)
                    had_failure = True
                    results.append(CartographyModuleResult(module=run.module, status="failed", output_tail=failure))
                else:
                    results.append(outcome)
            stage_results.append(CartographyStageResult(results=results))
            if had_failure and input.stop_on_failure:
                stopped = True

        if stopped:
            status = STATUS_STOPPED_ON_FAILURE
        elif had_failure:
            status = STATUS_COMPLETED_WITH_ERRORS
        else:
            status = STATUS_COMPLETED
        return CartographySyncResult(stages=stage_results, status=status)

    @staticmethod
    def _failure_text(exc: BaseException) -> str:
        # Child workflow / activity errors carry generic messages; the
        # ApplicationError with the output excerpt is the root cause.
        while exc.__cause__ is not None:
            exc = exc.__cause__
        return str(exc)

    @staticmethod
    def _skipped_stage(stage: CartographyStage) -> CartographyStageResult:
        return CartographyStageResult(
            results=[
                CartographyModuleResult(
                    module=run.module, status="skipped", output_tail="skipped: earlier stage failed"
                )
                for run in stage.runs
            ]
        )

    @staticmethod
    async def _run_module(input: CartographySyncInput, module: str, params: dict[str, Any]) -> CartographyModuleResult:
        request = CartographyModuleRunRequest(
            module=module,
            params=params,
            timeout_seconds=input.module_timeout_seconds,
            activity_task_queue=input.activity_task_queue,
            heartbeat_timeout_seconds=input.heartbeat_timeout_seconds,
            retry_attempts=input.retry_attempts,
        )
        waited = 0
        while True:
            try:
                handle = await workflow.start_child_workflow(
                    CartographyModuleWorkflow.run,
                    request,
                    id=f"{MODULE_MUTEX_ID_PREFIX}{module}",
                    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                )
                break
            except WorkflowAlreadyStartedError:
                # An overlapping run of this module holds the mutex; wait for
                # it (bounded) rather than racing it on update tags.
                if waited >= input.module_wait_seconds:
                    raise ApplicationError(
                        f"another run of cartography module '{module}' was still in progress after waiting {waited}s",
                        type="CartographyModuleBusy",
                    ) from None
                await workflow.sleep(_MODULE_WAIT_POLL_SECONDS)
                waited += _MODULE_WAIT_POLL_SECONDS
        return await handle
