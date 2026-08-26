from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from reporting import settings
from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.model_profiles import (
    CreateModelProfileRequest,
    ModelProfileIdResponse,
    ModelProfileItem,
    ModelProfileListResponse,
    ModelProfileVersion,
    ModelProfileVersionListResponse,
    SelectableModelProfilesResponse,
    UpdateModelProfileRequest,
)
from reporting.services import model_profiles, report_store

router = APIRouter()


@router.get("/api/v1/chat/model-profiles", response_model=SelectableModelProfilesResponse)
async def list_selectable_model_profiles(
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> SelectableModelProfilesResponse:
    profiles = await model_profiles.selectable_profiles()
    default = next((profile.profile_id for profile in profiles if profile.is_default), None)
    return SelectableModelProfilesResponse(profiles=profiles, default_profile_id=default)


@router.get("/api/v1/model-profiles", response_model=ModelProfileListResponse)
async def list_model_profiles(
    current: CurrentUser = Depends(require_permission(Permission.MODEL_PROFILES_READ)),
) -> ModelProfileListResponse:
    return ModelProfileListResponse(
        profiles=await report_store.list_model_profiles(),
        global_run_cost_budget_usd=max(0.0, settings.CHAT_RUN_COST_BUDGET_USD),
    )


@router.post("/api/v1/model-profiles", response_model=ModelProfileItem, status_code=201)
async def create_model_profile(
    body: CreateModelProfileRequest,
    current: CurrentUser = Depends(require_permission(Permission.MODEL_PROFILES_WRITE)),
) -> ModelProfileItem:
    try:
        return await report_store.create_model_profile(body.model_dump(mode="json"), current.user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/v1/model-profiles/{profile_id}", response_model=ModelProfileItem)
async def get_model_profile(
    profile_id: str,
    current: CurrentUser = Depends(require_permission(Permission.MODEL_PROFILES_READ)),
) -> ModelProfileItem:
    profile = await report_store.get_model_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Model profile not found")
    return profile


@router.put("/api/v1/model-profiles/{profile_id}", response_model=ModelProfileItem)
async def update_model_profile(
    profile_id: str,
    body: UpdateModelProfileRequest,
    current: CurrentUser = Depends(require_permission(Permission.MODEL_PROFILES_WRITE)),
) -> Any:
    data = body.model_dump(mode="json", exclude={"comment"})
    try:
        profile = await report_store.update_model_profile(profile_id, data, current.user.user_id, body.comment)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if profile is None:
        raise HTTPException(status_code=404, detail="Model profile not found")
    return profile


@router.delete("/api/v1/model-profiles/{profile_id}", response_model=ModelProfileIdResponse)
async def delete_model_profile(
    profile_id: str,
    current: CurrentUser = Depends(require_permission(Permission.MODEL_PROFILES_DELETE)),
) -> ModelProfileIdResponse:
    try:
        deleted = await report_store.delete_model_profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Model profile not found")
    return ModelProfileIdResponse(profile_id=profile_id)


@router.get("/api/v1/model-profiles/{profile_id}/versions", response_model=ModelProfileVersionListResponse)
async def list_model_profile_versions(
    profile_id: str,
    current: CurrentUser = Depends(require_permission(Permission.MODEL_PROFILES_READ)),
) -> ModelProfileVersionListResponse:
    if await report_store.get_model_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail="Model profile not found")
    return ModelProfileVersionListResponse(versions=await report_store.list_model_profile_versions(profile_id))


@router.get("/api/v1/model-profiles/{profile_id}/versions/{version}", response_model=ModelProfileVersion)
async def get_model_profile_version(
    profile_id: str,
    version: int,
    current: CurrentUser = Depends(require_permission(Permission.MODEL_PROFILES_READ)),
) -> ModelProfileVersion:
    item = await report_store.get_model_profile_version(profile_id, version)
    if item is None:
        raise HTTPException(status_code=404, detail="Model profile version not found")
    return item
