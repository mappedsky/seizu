"""Scheduled query action that starts a named Temporal workflow with the results.

The workflow's AI sessions run headlessly as the scheduled query's creator;
confirmations are bypassed only when the creator holds
``chat:bypass_permissions`` (enforced and audit-logged in mcp_runtime).

``temporalio`` is imported lazily inside functions so the web process can
import this module for its action schema without pulling in the SDK.
"""

import logging
from typing import Any

from reporting import settings
from reporting.schema.report_config import ActionConfigFieldDef
from reporting.schema.reporting_config import ScheduledQueryAction
from reporting.temporal_workflows import (
    WorkflowInputContext,
    WorkflowSpec,
    enabled_workflow_names,
    get_enabled_workflow_spec,
    get_workflow_spec,
    validate_workflow_action_config,
)

logger = logging.getLogger(__name__)


def action_name() -> str:
    return "temporal"


def activity_description() -> str:
    return "Runs a selected code-defined child workflow."


def activity_input_type() -> Any:
    return list[dict[str, Any]]


def activity_output_type() -> Any:
    return dict[str, Any]


def action_config_schema() -> list[ActionConfigFieldDef]:
    return [
        ActionConfigFieldDef(
            name="workflow",
            label="Workflow",
            type="select",
            required=True,
            options=enabled_workflow_names(),
            description="Code-defined child workflow to run.",
        ),
        ActionConfigFieldDef(
            name="max_rows",
            label="Max result rows",
            type="number",
            required=False,
            default=settings.TEMPORAL_WORKFLOW_MAX_RESULT_ROWS,
            description="Maximum query rows passed to the child workflow.",
        ),
        ActionConfigFieldDef(
            name="query_return_attribute",
            label="Query return attribute",
            type="string",
            required=False,
            description="Result field containing the data passed to the child workflow.",
            default="details",
        ),
    ]


async def setup() -> None:
    return


def validate_action_config(action_config: dict[str, Any]) -> str | None:
    """Validate the selected workflow's own config fields (see WorkflowSpec)."""
    return validate_workflow_action_config(action_config)


async def handle_results(
    scheduled_query_id: str,
    action: ScheduledQueryAction,
    results: list[dict[str, Any]],
) -> None:
    spec = _validated_spec(scheduled_query_id, action)
    if spec is None:
        return
    # For row-consuming workflows an empty result set means nothing to do; for
    # requires_rows=False workflows the query is only the trigger.
    if not results and spec.requires_rows:
        return
    await _start_workflow(scheduled_query_id, spec, action, results)


def _project_rows(
    scheduled_query_id: str,
    action: ScheduledQueryAction,
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    attr = action.action_config.get("query_return_attribute", "details")
    max_rows = action.action_config.get("max_rows") or settings.TEMPORAL_WORKFLOW_MAX_RESULT_ROWS
    rows = [result[attr] for result in results if isinstance(result.get(attr), dict)]
    if len(rows) > int(max_rows):
        logger.warning(
            "Truncating scheduled query results for workflow",
            extra={
                "scheduled_query_id": scheduled_query_id,
                "result_count": len(rows),
                "max_rows": int(max_rows),
            },
        )
        rows = rows[: int(max_rows)]
    return rows


def _validated_spec(scheduled_query_id: str, action: ScheduledQueryAction) -> WorkflowSpec | None:
    workflow_name = action.action_config.get("workflow")
    if not isinstance(workflow_name, str) or get_workflow_spec(workflow_name) is None:
        logger.error(
            "Refusing to start unknown workflow",
            extra={"scheduled_query_id": scheduled_query_id, "workflow": workflow_name},
        )
        return None
    spec = get_enabled_workflow_spec(workflow_name)
    if spec is None:
        # Registered but not in the operator's TEMPORAL_ENABLED_WORKFLOWS allowlist.
        logger.error(
            "Refusing to start workflow disabled by TEMPORAL_ENABLED_WORKFLOWS",
            extra={"scheduled_query_id": scheduled_query_id, "workflow": workflow_name},
        )
    return spec


async def _start_workflow(
    scheduled_query_id: str,
    spec: WorkflowSpec,
    action: ScheduledQueryAction,
    results: list[dict[str, Any]],
) -> None:
    import temporalio.client
    import temporalio.exceptions

    from reporting.services import report_store

    item = await report_store.get_scheduled_query(scheduled_query_id)
    if item is None:
        logger.error(
            "Scheduled query not found; cannot resolve creator identity",
            extra={"scheduled_query_id": scheduled_query_id},
        )
        return

    rows = _project_rows(scheduled_query_id, action, results) if spec.requires_rows else []
    try:
        workflow_input = spec.build_input(
            WorkflowInputContext(
                scheduled_query_id=scheduled_query_id,
                creator_user_id=item.created_by,
                rows=rows,
                chat_timeout_seconds=settings.TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS,
                action_config=dict(action.action_config),
            )
        )
    except ValueError as exc:
        # A stored config that no longer validates (e.g. the operator narrowed
        # an allowlist after the schedule was saved) must not dispatch.
        logger.error(
            "Refusing to start workflow with invalid action config",
            extra={"scheduled_query_id": scheduled_query_id, "workflow": spec.name, "error": str(exc)},
        )
        return
    # last_scheduled_at identifies this run (the lock sets it before the query
    # executes), making redelivery of the same run idempotent.
    workflow_id = f"seizu:{spec.name}:{scheduled_query_id}:{item.last_scheduled_at}"

    client = await temporalio.client.Client.connect(
        settings.TEMPORAL_ADDRESS,
        namespace=settings.TEMPORAL_NAMESPACE,
    )
    try:
        await client.start_workflow(
            spec.name,
            workflow_input,
            id=workflow_id,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
        )
    except temporalio.exceptions.WorkflowAlreadyStartedError:
        logger.info(
            "Workflow already started for this run",
            extra={"scheduled_query_id": scheduled_query_id, "workflow_id": workflow_id},
        )
        return
    logger.info(
        "Started workflow",
        extra={
            "type": "AUDIT",
            "scheduled_query_id": scheduled_query_id,
            "workflow_id": workflow_id,
            "workflow": spec.name,
            "creator_user_id": item.created_by,
            "row_count": len(rows),
        },
    )
