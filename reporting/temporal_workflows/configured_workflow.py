"""Generic Temporal workflow for user-configured query/activity pipelines."""

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from reporting.temporal_workflows.activities import (
        build_code_workflow_input,
        execute_configured_activity,
        execute_configured_query,
        load_configured_workflow,
        record_configured_workflow_result,
    )
    from reporting.temporal_workflows.shared import (
        CodeWorkflowInputRequest,
        ConfiguredActivityInput,
        ConfiguredWorkflowInvocation,
        ConfiguredWorkflowResult,
        normalize_configured_rows,
    )


@workflow.defn(name="seizu_configured_workflow")
class ConfiguredWorkflow:
    @workflow.run
    async def run(self, invocation: ConfiguredWorkflowInvocation) -> ConfiguredWorkflowResult:
        definition = await workflow.execute_activity(
            load_configured_workflow,
            invocation,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        if definition.skipped_reason is not None:
            return ConfiguredWorkflowResult(
                status="skipped",
                version=definition.version,
                skipped_reason=definition.skipped_reason,
            )

        try:
            settled = await asyncio.gather(
                *[
                    workflow.execute_activity(
                        execute_configured_query,
                        query_input,
                        activity_id=f"query:{query_input.input_id}",
                        start_to_close_timeout=timedelta(seconds=300),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    for query_input in definition.inputs
                ]
            )
            query_results = {result.input_id: result.rows for result in settled}
            activity_results: list[Any] = []
            for position, configured_activity in enumerate(definition.activities, start=1):
                return_attribute = configured_activity.parameters.get(
                    "query_return_attribute",
                    "details",
                )
                rows = normalize_configured_rows(
                    query_results.get(configured_activity.input_id or "", []),
                    return_attribute if isinstance(return_attribute, str) else "details",
                )
                if configured_activity.type == "workflow":
                    if configured_activity.requires_rows and not rows:
                        activity_results.append({"status": "skipped", "reason": "input returned no rows"})
                        continue
                    workflow_name = configured_activity.parameters.get("workflow")
                    if not isinstance(workflow_name, str) or not workflow_name:
                        raise ValueError("workflow activity is missing its workflow parameter")
                    child_input = await workflow.execute_activity(
                        build_code_workflow_input,
                        CodeWorkflowInputRequest(
                            workflow_id=definition.workflow_id,
                            creator_user_id=definition.creator_user_id,
                            workflow_name=workflow_name,
                            parameters=configured_activity.parameters,
                            rows=rows,
                        ),
                        activity_id=f"activity:{position}:build_input",
                        start_to_close_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    result = await workflow.execute_child_workflow(
                        workflow_name,
                        child_input,
                        id=f"{workflow.info().workflow_id}:activity:{position}:{workflow_name}",
                    )
                else:
                    result = await workflow.execute_activity(
                        execute_configured_activity,
                        ConfiguredActivityInput(
                            workflow_id=definition.workflow_id,
                            activity_type=configured_activity.type,
                            parameters=configured_activity.parameters,
                            rows=rows,
                        ),
                        activity_id=f"activity:{position}:{configured_activity.type}",
                        start_to_close_timeout=timedelta(seconds=300),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                activity_results.append(result)
            await workflow.execute_activity(
                record_configured_workflow_result,
                {"workflow_id": definition.workflow_id, "status": "success", "error": None},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            return ConfiguredWorkflowResult(
                status="completed",
                version=definition.version,
                input_rows={key: len(value) for key, value in query_results.items()},
                activity_results=activity_results,
            )
        except Exception as exc:
            await workflow.execute_activity(
                record_configured_workflow_result,
                {
                    "workflow_id": definition.workflow_id,
                    "status": "failure",
                    "error": str(exc),
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            raise
