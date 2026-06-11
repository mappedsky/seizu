import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.report_config import (
    CreateScheduledQueryRequest,
    ScheduledQueryIdResponse,
    ScheduledQueryItem,
    ScheduledQueryListResponse,
    ScheduledQueryVersion,
    ScheduledQueryVersionListResponse,
)
from reporting.services import report_store
from reporting.services.query_validator import validate_query
from reporting.services.scheduled_query_validation import validate_action_configs

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/v1/scheduled-queries", response_model=ScheduledQueryListResponse)
async def list_scheduled_queries(
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_READ)),
) -> ScheduledQueryListResponse:
    """List all scheduled queries."""
    return ScheduledQueryListResponse(scheduled_queries=await report_store.list_scheduled_queries())


@router.get("/api/v1/scheduled-queries/{sq_id}", response_model=ScheduledQueryItem)
async def get_scheduled_query(
    sq_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SCHEDULED_QUERIES_READ)),
) -> ScheduledQueryItem:
    """Return a scheduled query by ID."""
    item = await report_store.get_scheduled_query(sq_id)
    if not item:
        raise HTTPException(status_code=404, detail="Scheduled query not found")
    return item


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
    return await report_store.create_scheduled_query(
        name=body.name,
        cypher=body.cypher,
        params=body.params,
        frequency=body.frequency,
        watch_scans=body.watch_scans,
        enabled=body.enabled,
        actions=body.actions,
        created_by=current.user.user_id,
    )


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
        watch_scans=body.watch_scans,
        enabled=body.enabled,
        actions=body.actions,
        updated_by=current.user.user_id,
        comment=body.comment,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Scheduled query not found")
    return item


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
    return ScheduledQueryVersionListResponse(versions=versions)


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
    return v


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
    return ScheduledQueryIdResponse(scheduled_query_id=sq_id)
