"""Cartography sync pipeline workflow.

Deterministic orchestration only: stages run sequentially; module runs within
a stage run as parallel activities. The run_cartography_module activity is
referenced by name and dispatched to the cartography sync worker's task queue
(input.activity_task_queue) — it is not registered on this worker.

A failed module run records a failed result; by default later stages still
execute (matching the other Seizu workflows), unless the pipeline was
configured with stop_on_failure.
"""

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from cartography_sync.shared import (
        STATUS_COMPLETED,
        STATUS_COMPLETED_WITH_ERRORS,
        STATUS_STOPPED_ON_FAILURE,
        CartographyModuleActivityInput,
        CartographyModuleResult,
        CartographyStage,
        CartographyStageResult,
        CartographySyncInput,
        CartographySyncResult,
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
        # ActivityError's own message is a generic "Activity task failed";
        # the ApplicationError raised by the activity (with the output
        # excerpt) is its cause.
        if exc.__cause__ is not None:
            return str(exc.__cause__)
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
        return await workflow.execute_activity(
            "run_cartography_module",
            CartographyModuleActivityInput(
                module=module,
                params=params,
                timeout_seconds=input.module_timeout_seconds,
            ),
            result_type=CartographyModuleResult,
            task_queue=input.activity_task_queue,
            # Slightly above the activity's own subprocess watchdog so the
            # in-activity timeout (with its output excerpt) wins.
            start_to_close_timeout=timedelta(seconds=input.module_timeout_seconds + 60),
            heartbeat_timeout=timedelta(seconds=input.heartbeat_timeout_seconds),
            retry_policy=RetryPolicy(
                maximum_attempts=max(1, input.retry_attempts),
                non_retryable_error_types=["CartographyConfigError"],
            ),
        )
