from fastapi import APIRouter

from reporting import scheduled_query_modules, settings
from reporting.services import workflows

router = APIRouter()


@router.get("/api/v1/config", include_in_schema=False)
async def get_config() -> dict:
    """Get frontend configuration."""
    oidc_config = None
    if settings.OIDC_AUTHORITY:
        oidc_config = {
            "authority": settings.OIDC_AUTHORITY,
            "client_id": settings.OIDC_CLIENT_ID,
            "redirect_uri": settings.OIDC_REDIRECT_URI,
            "scope": settings.OIDC_SCOPE,
        }
    action_schemas = {
        name: [f.model_dump() for f in fields] for name, fields in scheduled_query_modules.get_action_schemas().items()
    }
    workflow_activity_schemas = {
        name: [field.model_dump() for field in fields] for name, fields in workflows.activity_schemas().items()
    }
    workflow_activity_definitions = workflows.activity_definitions()
    return {
        "auth_required": settings.DEVELOPMENT_ONLY_REQUIRE_AUTH,
        "oidc": oidc_config,
        "scheduled_query_action_types": sorted(action_schemas),
        "scheduled_query_action_schemas": action_schemas,
        "scheduled_query_action_dependent_schemas": {},
        "workflow_activity_types": sorted(workflow_activity_schemas),
        "workflow_activity_schemas": workflow_activity_schemas,
        "workflow_activity_definitions": workflow_activity_definitions,
        # Feature flags consumed by the frontend to show/hide whole features.
        "features": {
            "chat": settings.CHAT_ENABLED,
            "chat_schedules": settings.CHAT_ENABLED and settings.CHAT_SCHEDULES_ENABLED,
        },
        "config": {},
    }
