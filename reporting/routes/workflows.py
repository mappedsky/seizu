"""Canonical configurable-workflow REST API."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

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
from reporting.services import report_store, temporal_runs, workflow_schedules, workflows

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/v1/workflows", response_model=WorkflowListResponse)
async def list_workflows(
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_READ)),
) -> WorkflowListResponse:
    return WorkflowListResponse(
        workflows=[workflows.item_to_workflow(item) for item in await report_store.list_scheduled_queries()]
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
    error = await workflows.validate_definition(body)
    if error:
        raise HTTPException(status_code=400, detail=error)
    created = await workflows.create(body, current.user.user_id)
    await workflow_schedules.reconcile_by_id(created.workflow_id)
    item = await report_store.get_scheduled_query(created.workflow_id)
    assert item is not None
    return workflows.item_to_workflow(item)


@router.put("/api/v1/workflows/{workflow_id}", response_model=WorkflowItem)
async def update_workflow(
    workflow_id: str,
    body: CreateWorkflowRequest,
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_WRITE)),
) -> WorkflowItem:
    error = await workflows.validate_definition(body)
    if error:
        raise HTTPException(status_code=400, detail=error)
    updated = await workflows.update(workflow_id, body, current.user.user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    await workflow_schedules.reconcile_by_id(workflow_id)
    item = await report_store.get_scheduled_query(workflow_id)
    assert item is not None
    return workflows.item_to_workflow(item)


@router.delete("/api/v1/workflows/{workflow_id}", response_model=WorkflowIdResponse)
async def delete_workflow(
    workflow_id: str,
    current: CurrentUser = Depends(require_permission(Permission.WORKFLOWS_DELETE)),
) -> WorkflowIdResponse:
    if not await report_store.delete_scheduled_query(workflow_id):
        raise HTTPException(status_code=404, detail="Workflow not found")
    await workflow_schedules.delete_schedule(workflow_id)
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
    if await report_store.get_scheduled_query(workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        temporal_workflow_id, run_id = await workflow_schedules.run_now(workflow_id)
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
    if await report_store.get_scheduled_query(workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        runs = await temporal_runs.list_workflow_runs(workflow_id, limit)
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
    try:
        detail = await temporal_runs.get_workflow_run_detail(
            workflow_id,
            temporal_workflow_id,
            run_id,
        )
    except temporal_runs.TemporalUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return detail
