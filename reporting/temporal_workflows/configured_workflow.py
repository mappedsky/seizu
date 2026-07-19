"""Generic Temporal workflow for user-configured staged activity pipelines."""

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from reporting.temporal_workflows.activities import (
        build_code_workflow_input,
        check_configured_workflow_watch,
        execute_configured_activity,
        execute_configured_query,
        load_configured_workflow,
        normalize_code_workflow_output,
        record_configured_workflow_result,
    )
    from reporting.temporal_workflows.shared import (
        CodeWorkflowInputRequest,
        CodeWorkflowOutputRequest,
        ConfiguredActivity,
        ConfiguredActivityInput,
        ConfiguredActivityOutput,
        ConfiguredQueryInput,
        ConfiguredWorkflowInvocation,
        ConfiguredWorkflowResult,
    )


def _activity_maximum_attempts(configured: ConfiguredActivity) -> int:
    """Read the retry field without breaking histories created before it existed."""

    # Old load_configured_workflow activity results are durable history. Their
    # decoded ConfiguredActivity objects have no maximum_attempts attribute and
    # previously used the hard-coded three-attempt policy.
    return int(getattr(configured, "maximum_attempts", 3))


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

        outputs: dict[str, ConfiguredActivityOutput] = {}
        summaries: list[Any] = []

        async def run_activity(
            configured: ConfiguredActivity,
            stage_position: int,
            activity_position: int,
        ) -> ConfiguredActivityOutput:
            input_output = outputs.get(configured.input_id or "")
            input_value = input_output.value if input_output is not None else None
            prefix = f"stage:{stage_position}:activity:{activity_position}:{configured.output_id}"

            if configured.type == "query":
                raw_parameters = configured.parameters.get("parameters", [])
                parameters = {
                    str(value["name"]): value.get("value")
                    for value in raw_parameters
                    if isinstance(value, dict) and value.get("name")
                }
                return await workflow.execute_activity(
                    execute_configured_query,
                    ConfiguredQueryInput(
                        output_id=configured.output_id,
                        cypher=str(configured.parameters.get("cypher", "")),
                        parameters=parameters,
                        max_rows=int(configured.parameters.get("max_rows") or 200),
                        max_bytes=int(configured.parameters.get("max_bytes") or 1_000_000),
                        has_input=configured.input_id is not None,
                        input_value=input_value,
                    ),
                    activity_id=f"{prefix}:query",
                    start_to_close_timeout=timedelta(seconds=300),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

            if configured.type == "workflow":
                if configured.requires_rows and isinstance(input_value, list) and not input_value:
                    return ConfiguredActivityOutput(
                        output_id=configured.output_id,
                        value=None,
                        metadata={"status": "skipped", "reason": "input returned no rows"},
                    )
                workflow_name = configured.parameters.get("workflow")
                if not isinstance(workflow_name, str) or not workflow_name:
                    raise ValueError("workflow activity is missing its workflow parameter")
                child_input = await workflow.execute_activity(
                    build_code_workflow_input,
                    CodeWorkflowInputRequest(
                        workflow_id=definition.workflow_id,
                        creator_user_id=definition.creator_user_id,
                        workflow_name=workflow_name,
                        parameters=configured.parameters,
                        input_value=input_value,
                    ),
                    activity_id=f"{prefix}:build_input",
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                result = await workflow.execute_child_workflow(
                    workflow_name,
                    child_input,
                    id=f"{workflow.info().workflow_id}:{prefix}:{workflow_name}",
                )
                return await workflow.execute_activity(
                    normalize_code_workflow_output,
                    CodeWorkflowOutputRequest(
                        workflow_name=workflow_name,
                        output_id=configured.output_id,
                        value=result,
                    ),
                    activity_id=f"{prefix}:normalize_output",
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )

            return await workflow.execute_activity(
                execute_configured_activity,
                ConfiguredActivityInput(
                    workflow_id=definition.workflow_id,
                    activity_type=configured.type,
                    output_id=configured.output_id,
                    parameters=configured.parameters,
                    input_value=input_value,
                ),
                activity_id=f"{prefix}:{configured.type}",
                start_to_close_timeout=timedelta(seconds=300),
                retry_policy=RetryPolicy(maximum_attempts=_activity_maximum_attempts(configured)),
            )

        try:
            for stage_position, stage in enumerate(definition.stages, start=1):
                settled = await asyncio.gather(
                    *[
                        run_activity(configured, stage_position, activity_position)
                        for activity_position, configured in enumerate(stage.activities, start=1)
                    ],
                    return_exceptions=True,
                )
                failures = [result for result in settled if isinstance(result, BaseException)]
                if failures:
                    raise failures[0]
                for result in settled:
                    # Temporal's workflow sandbox can load a distinct class
                    # identity for this dataclass even though the decoded
                    # value has the expected fields. Avoid an ``isinstance``
                    # assertion here; the activity return type already drives
                    # payload conversion and validation.
                    outputs[result.output_id] = result
                    summaries.append({"output": result.output_id, **result.metadata})
            await workflow.execute_activity(
                record_configured_workflow_result,
                {"workflow_id": definition.workflow_id, "status": "success", "error": None},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            return ConfiguredWorkflowResult(
                status="completed",
                version=definition.version,
                activity_results=summaries,
            )
        except BaseException as exc:
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


@workflow.defn(name="seizu_configured_workflow_watch_poll")
class ConfiguredWorkflowWatchPoll:
    """Poll SyncMetadata and create a visible child only for an actual run."""

    @workflow.run
    async def run(self, invocation: ConfiguredWorkflowInvocation) -> ConfiguredWorkflowResult:
        triggered = await workflow.execute_activity(
            check_configured_workflow_watch,
            invocation,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        if not triggered:
            return ConfiguredWorkflowResult(
                status="skipped",
                skipped_reason="watch scan unchanged",
            )

        poll_id = workflow.info().workflow_id
        poll_prefix = f"seizu-workflow-poll:{invocation.workflow_id}"
        suffix = poll_id.removeprefix(poll_prefix).lstrip("-:")
        actual_id = f"seizu-workflow:{invocation.workflow_id}:run:{suffix}"
        return await workflow.execute_child_workflow(
            "seizu_configured_workflow",
            ConfiguredWorkflowInvocation(
                workflow_id=invocation.workflow_id,
                watch_checked=True,
            ),
            id=actual_id,
        )
