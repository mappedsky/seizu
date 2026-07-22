"""Canonical configurable-workflow REST API."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.report_config import (
    CreateWorkflowRequest,
    WorkflowIdResponse,
    WorkflowItem,
    WorkflowListResponse,
    WorkflowRunDetail,
    WorkflowRunListResponse,
    WorkflowRunRequestedResponse,
    WorkflowVersion,
    WorkflowVersionListResponse,
)
from reporting.services import report_store, temporal_runs, workflows

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/v1/workflows", response_model=WorkflowListResponse)
async def list_workflows(
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_READ)),
) -> WorkflowListResponse:
    workflow_items = [workflows.item_to_workflow(item) for item in await report_store.list_scheduled_queries()]
    try:
        active_statuses = await temporal_runs.list_active_workflow_statuses(item.workflow_id for item in workflow_items)
    except Exception:
        # Workflow definitions remain useful when Temporal visibility is
        # temporarily unavailable; fall back to their persisted last result.
        logger.warning("Unable to load active workflow statuses", exc_info=True)
        active_statuses = {}
    return WorkflowListResponse(
        workflows=[
            item.model_copy(update={"last_run_status": active_statuses.get(item.workflow_id, item.last_run_status)})
            for item in workflow_items
        ]
    )


@router.get("/api/v1/workflows/{workflow_id}", response_model=WorkflowItem)
async def get_workflow(
    workflow_id: str,
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_READ)),
) -> WorkflowItem:
    item = await report_store.get_scheduled_query(workflow_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflows.item_to_workflow(item)


@router.post("/api/v1/workflows", response_model=WorkflowItem, status_code=201)
async def create_workflow(
    body: CreateWorkflowRequest,
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_WRITE)),
) -> WorkflowItem:
    try:
        return await workflows.create_managed(body, current.user.user_id)
    except workflows.WorkflowDefinitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/api/v1/workflows/{workflow_id}", response_model=WorkflowItem)
async def update_workflow(
    workflow_id: str,
    body: CreateWorkflowRequest,
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_WRITE)),
) -> WorkflowItem:
    try:
        return await workflows.update_managed(workflow_id, body, current.user.user_id)
    except workflows.WorkflowDefinitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except workflows.WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/v1/workflows/{workflow_id}", response_model=WorkflowIdResponse)
async def delete_workflow(
    workflow_id: str,
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_DELETE)),
) -> WorkflowIdResponse:
    try:
        await workflows.delete_managed(workflow_id, current.user.user_id)
    except workflows.WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unable to delete workflow schedule", extra={"workflow_id": workflow_id})
        raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
    return WorkflowIdResponse(workflow_id=workflow_id)


@router.post(
    "/api/v1/workflows/{workflow_id}/run",
    response_model=WorkflowRunRequestedResponse,
    status_code=202,
)
async def run_workflow(
    workflow_id: str,
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_WRITE)),
) -> WorkflowRunRequestedResponse:
    try:
        temporal_workflow_id, run_id = await workflows.run_managed(workflow_id, current.user.user_id)
    except workflows.WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Unable to start workflow run",
            extra={"workflow_id": workflow_id},
        )
        raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
    logger.info(
        "Workflow run requested",
        extra={"type": "AUDIT", "workflow_id": workflow_id, "user": current.user.user_id},
    )
    return WorkflowRunRequestedResponse(
        workflow_id=workflow_id,
        temporal_workflow_id=temporal_workflow_id,
        run_id=run_id,
    )


@router.get(
    "/api/v1/workflows/{workflow_id}/versions",
    response_model=WorkflowVersionListResponse,
)
async def list_workflow_versions(
    workflow_id: str,
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_READ)),
) -> WorkflowVersionListResponse:
    if await report_store.get_scheduled_query(workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    versions = await report_store.list_scheduled_query_versions(workflow_id)
    return WorkflowVersionListResponse(versions=[workflows.version_to_workflow(value) for value in versions])


@router.get(
    "/api/v1/workflows/{workflow_id}/versions/{version}",
    response_model=WorkflowVersion,
)
async def get_workflow_version(
    workflow_id: str,
    version: int,
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_READ)),
) -> WorkflowVersion:
    item = await report_store.get_scheduled_query_version(workflow_id, version)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow version not found")
    return workflows.version_to_workflow(item)


@router.get(
    "/api/v1/workflows/{workflow_id}/runs",
    response_model=WorkflowRunListResponse,
)
async def list_workflow_runs(
    workflow_id: str,
    limit: int = Query(default=20, ge=1, le=50),
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_READ)),
) -> WorkflowRunListResponse:
    item = await report_store.get_scheduled_query(workflow_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        runs = await temporal_runs.list_workflow_runs(
            workflow_id,
            limit,
            configured_name=item.name,
            watch_polling=bool(item.watch_scans),
        )
    except temporal_runs.TemporalUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
    return WorkflowRunListResponse(runs=runs)


@router.get(
    "/api/v1/workflows/{workflow_id}/runs/{temporal_workflow_id}/{run_id}",
    response_model=WorkflowRunDetail,
)
async def get_workflow_run(
    workflow_id: str,
    temporal_workflow_id: str,
    run_id: str,
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_READ)),
) -> Any:
    item = await report_store.get_scheduled_query(workflow_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        detail = await temporal_runs.get_workflow_run_detail(
            workflow_id,
            temporal_workflow_id,
            run_id,
            include_payload_previews=Permission.WORKFLOWS_WRITE.value in current.permissions,
            configured_name=item.name,
        )
    except temporal_runs.TemporalUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return detail


@router.post(
    "/api/v1/workflows/{workflow_id}/runs/{temporal_workflow_id}/{run_id}/cancel",
    status_code=204,
)
async def cancel_waiting_workflow_run(
    workflow_id: str,
    temporal_workflow_id: str,
    run_id: str,
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_WRITE)),
) -> Response:
    try:
        await workflows.require_owned_item(workflow_id, current.user.user_id)
        canceled = await temporal_runs.cancel_waiting_workflow_run(
            workflow_id,
            temporal_workflow_id,
            run_id,
        )
    except workflows.WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except temporal_runs.TemporalUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
    if not canceled:
        raise HTTPException(status_code=409, detail="Workflow run is no longer waiting")
    logger.info(
        "Waiting workflow run cancellation requested",
        extra={
            "type": "AUDIT",
            "workflow_id": workflow_id,
            "temporal_workflow_id": temporal_workflow_id,
            "run_id": run_id,
            "user": current.user.user_id,
        },
    )
    return Response(status_code=204)
