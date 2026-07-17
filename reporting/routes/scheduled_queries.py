import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.report_config import (
    CreateScheduledQueryRequest,
    ScheduledQueryIdResponse,
    ScheduledQueryItem,
    ScheduledQueryListResponse,
    ScheduledQueryRunRequestedResponse,
    ScheduledQueryVersion,
    ScheduledQueryVersionListResponse,
    WorkflowRunDetail,
    WorkflowRunListResponse,
)
from reporting.services import report_store, temporal_runs, workflow_schedules, workflows
from reporting.services.query_validator import validate_query
from reporting.services.scheduled_query_validation import validate_action_configs

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/v1/scheduled-queries", response_model=ScheduledQueryListResponse)
async def list_scheduled_queries(
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_READ)),
) -> ScheduledQueryListResponse:
    """List all scheduled queries."""
    items = await report_store.list_scheduled_queries()
    return ScheduledQueryListResponse(
        scheduled_queries=[
            workflows.project_legacy_item(item) for item in items if workflows.legacy_representable(item)
        ]
    )


@router.get("/api/v1/scheduled-queries/{sq_id}", response_model=ScheduledQueryItem)
async def get_scheduled_query(
    sq_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_READ)),
) -> ScheduledQueryItem:
    """Return a scheduled query by ID."""
    item = await report_store.get_scheduled_query(sq_id)
    if not item:
        raise HTTPException(status_code=404, detail="Scheduled query not found")
    if not workflows.legacy_representable(item):
        raise HTTPException(status_code=404, detail="Scheduled query not found")
    return workflows.project_legacy_item(item)


@router.post(
    "/api/v1/scheduled-queries",
    response_model=ScheduledQueryItem,
    status_code=201,
)
async def create_scheduled_query(
    body: CreateScheduledQueryRequest,
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_WRITE)),
) -> Any:
    """Create a new scheduled query."""
    err = validate_action_configs(body.actions)
    if err:
        raise HTTPException(status_code=400, detail=err)
    validation = await validate_query(body.cypher)
    if validation.has_errors:
        return JSONResponse(
            content={
                "errors": validation.errors,
                "warnings": validation.warnings,
            },
            status_code=400,
        )
    item = await report_store.create_scheduled_query(
        name=body.name,
        cypher=body.cypher,
        params=body.params,
        frequency=body.frequency,
        schedule=body.schedule.model_dump() if body.schedule else None,
        watch_scans=body.watch_scans,
        enabled=body.enabled,
        actions=body.actions,
        created_by=current.user.user_id,
    )
    return workflows.project_legacy_item(item)


@router.put("/api/v1/scheduled-queries/{sq_id}", response_model=ScheduledQueryItem)
async def update_scheduled_query(
    sq_id: str,
    body: CreateScheduledQueryRequest,
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_WRITE)),
) -> Any:
    """Update a scheduled query."""
    err = validate_action_configs(body.actions)
    if err:
        raise HTTPException(status_code=400, detail=err)
    validation = await validate_query(body.cypher)
    if validation.has_errors:
        return JSONResponse(
            content={
                "errors": validation.errors,
                "warnings": validation.warnings,
            },
            status_code=400,
        )
    item = await report_store.update_scheduled_query(
        sq_id=sq_id,
        name=body.name,
        cypher=body.cypher,
        params=body.params,
        frequency=body.frequency,
        schedule=body.schedule.model_dump() if body.schedule else None,
        watch_scans=body.watch_scans,
        enabled=body.enabled,
        actions=body.actions,
        updated_by=current.user.user_id,
        comment=body.comment,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Scheduled query not found")
    return workflows.project_legacy_item(item)


@router.post(
    "/api/v1/scheduled-queries/{sq_id}/run",
    response_model=ScheduledQueryRunRequestedResponse,
    status_code=202,
)
async def run_scheduled_query(
    sq_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_WRITE)),
) -> ScheduledQueryRunRequestedResponse:
    """Request an immediate run of a scheduled query.

    The worker picks the request up on its next poll and runs the query even
    if it is disabled (so it can be tested before enabling).
    """
    run_requested_at = await report_store.request_scheduled_query_run(sq_id)
    if run_requested_at is None:
        raise HTTPException(status_code=404, detail="Scheduled query not found")
    logger.info(
        "Scheduled query run requested",
        extra={"type": "AUDIT", "scheduled_query_id": sq_id, "user": current.user.user_id},
    )
    return ScheduledQueryRunRequestedResponse(scheduled_query_id=sq_id, run_requested_at=run_requested_at)


@router.get(
    "/api/v1/scheduled-queries/{sq_id}/workflow-runs",
    response_model=WorkflowRunListResponse,
)
async def list_scheduled_query_workflow_runs(
    sq_id: str,
    limit: int = Query(default=20, ge=1, le=50),
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_READ)),
) -> WorkflowRunListResponse:
    """List recent Temporal workflow runs started by this query's temporal action.

    Returns an empty list without contacting Temporal when the query has no
    temporal action, so the endpoint is safe on deployments where Temporal
    isn't configured.
    """
    item = await report_store.get_scheduled_query(sq_id)
    if not item:
        raise HTTPException(status_code=404, detail="Scheduled query not found")
    if not workflows.has_code_workflow(item):
        return WorkflowRunListResponse(runs=[])
    try:
        runs = await temporal_runs.list_workflow_runs(sq_id, limit=limit)
    except temporal_runs.TemporalUnavailableError:
        raise HTTPException(status_code=503, detail="Temporal is unavailable")
    return WorkflowRunListResponse(runs=runs)


@router.get(
    "/api/v1/scheduled-queries/{sq_id}/workflow-runs/{workflow_id}/{run_id}",
    response_model=WorkflowRunDetail,
)
async def get_scheduled_query_workflow_run(
    sq_id: str,
    workflow_id: str,
    run_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_READ)),
) -> WorkflowRunDetail:
    """Return one workflow run's activity breakdown (status, attempts, failures).

    Only reachable for queries with a temporal action; workflow ids not minted
    for this scheduled query by a registered workflow are treated as not
    found, so the endpoint can't read arbitrary workflows in the namespace.
    Activity input/result previews carry query-result rows and activity
    outputs, so they are included only for callers who could also edit the
    query (``scheduled_queries:write``); readers get status/timing/failure
    information without payloads.
    """
    item = await report_store.get_scheduled_query(sq_id)
    if not item:
        raise HTTPException(status_code=404, detail="Scheduled query not found")
    if not workflows.has_code_workflow(item):
        raise HTTPException(status_code=404, detail="Workflow run not found")
    try:
        detail = await temporal_runs.get_workflow_run_detail(
            sq_id,
            workflow_id,
            run_id,
            include_payload_previews=Permission.SCHEDULED_QUERIES_WRITE in current.permissions,
        )
    except temporal_runs.TemporalUnavailableError:
        raise HTTPException(status_code=503, detail="Temporal is unavailable")
    if detail is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return detail


@router.get(
    "/api/v1/scheduled-queries/{sq_id}/versions",
    response_model=ScheduledQueryVersionListResponse,
)
async def list_scheduled_query_versions(
    sq_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_READ)),
) -> ScheduledQueryVersionListResponse:
    """List all versions of a scheduled query."""
    item = await report_store.get_scheduled_query(sq_id)
    if not item:
        raise HTTPException(status_code=404, detail="Scheduled query not found")
    versions = await report_store.list_scheduled_query_versions(sq_id)
    return ScheduledQueryVersionListResponse(
        versions=[
            workflows.project_legacy_version(version) for version in versions if workflows.legacy_representable(version)
        ]
    )


@router.get(
    "/api/v1/scheduled-queries/{sq_id}/versions/{version}",
    response_model=ScheduledQueryVersion,
)
async def get_scheduled_query_version(
    sq_id: str,
    version: int,
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_READ)),
) -> ScheduledQueryVersion:
    """Return a specific version of a scheduled query."""
    v = await report_store.get_scheduled_query_version(sq_id, version)
    if not v:
        raise HTTPException(status_code=404, detail="Scheduled query version not found")
    if not workflows.legacy_representable(v):
        raise HTTPException(status_code=404, detail="Scheduled query version not found")
    return workflows.project_legacy_version(v)


@router.delete(
    "/api/v1/scheduled-queries/{sq_id}",
    response_model=ScheduledQueryIdResponse,
)
async def delete_scheduled_query(
    sq_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_DELETE)),
) -> ScheduledQueryIdResponse:
    """Delete a scheduled query."""
    ok = await report_store.delete_scheduled_query(sq_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Scheduled query not found")
    await workflow_schedules.delete_schedule(sq_id)
    return ScheduledQueryIdResponse(scheduled_query_id=sq_id)
