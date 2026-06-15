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
    WORKFLOW_REGISTRY,
    WorkflowInputContext,
    WorkflowSpec,
    get_workflow_spec,
)

logger = logging.getLogger(__name__)


def action_name() -> str:
    return "temporal"


def _workflow_descriptions() -> str:
    return " ".join(f"{name}: {spec.description}" for name, spec in sorted(WORKFLOW_REGISTRY.items()))


def action_config_schema() -> list[ActionConfigFieldDef]:
    return [
        ActionConfigFieldDef(
            name="workflow",
            label="Workflow",
            type="select",
            required=True,
            options=sorted(WORKFLOW_REGISTRY),
            description=f"Temporal workflow to start with the query results. {_workflow_descriptions()}",
        ),
        ActionConfigFieldDef(
            name="max_rows",
            label="Max result rows",
            type="number",
            required=False,
            default=settings.TEMPORAL_WORKFLOW_MAX_RESULT_ROWS,
            description="Result rows beyond this limit are dropped before starting the workflow (payload size cap).",
        ),
        ActionConfigFieldDef(
            name="query_return_attribute",
            label="Query return attribute",
            type="string",
            required=False,
            description="Top-level attribute of each result row that contains the data map.",
            default="details",
        ),
    ]


async def setup() -> None:
    return


async def handle_results(
    scheduled_query_id: str,
    action: ScheduledQueryAction,
    results: list[dict[str, Any]],
) -> None:
    if not results:
        return
    await _start_workflow(scheduled_query_id, action, results)


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
    spec = get_workflow_spec(workflow_name) if isinstance(workflow_name, str) else None
    if spec is None:
        logger.error(
            "Refusing to start unknown workflow",
            extra={"scheduled_query_id": scheduled_query_id, "workflow": workflow_name},
        )
    return spec


async def _start_workflow(
    scheduled_query_id: str,
    action: ScheduledQueryAction,
    results: list[dict[str, Any]],
) -> None:
    import temporalio.client
    import temporalio.exceptions

    from reporting.services import report_store

    spec = _validated_spec(scheduled_query_id, action)
    if spec is None:
        return

    item = await report_store.get_scheduled_query(scheduled_query_id)
    if item is None:
        logger.error(
            "Scheduled query not found; cannot resolve creator identity",
            extra={"scheduled_query_id": scheduled_query_id},
        )
        return

    rows = _project_rows(scheduled_query_id, action, results)
    workflow_input = spec.build_input(
        WorkflowInputContext(
            scheduled_query_id=scheduled_query_id,
            creator_user_id=item.created_by,
            rows=rows,
            chat_timeout_seconds=settings.TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS,
        )
    )
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
