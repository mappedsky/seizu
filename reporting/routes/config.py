from fastapi import APIRouter

from reporting import scheduled_query_modules, settings, temporal_workflows

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
    # Per-action-type dependent sub-schemas: extra fields rendered once the
    # discriminator field takes one of the keyed values (today only the
    # temporal action's per-workflow config_fields).
    dependent_schemas = {}
    workflow_schemas = {
        name: [f.model_dump() for f in fields] for name, fields in temporal_workflows.workflow_config_schemas().items()
    }
    if workflow_schemas:
        dependent_schemas["temporal"] = {"discriminator": "workflow", "schemas": workflow_schemas}
    return {
        "auth_required": settings.DEVELOPMENT_ONLY_REQUIRE_AUTH,
        "oidc": oidc_config,
        "scheduled_query_action_types": scheduled_query_modules.get_configured_action_names(),
        "scheduled_query_action_schemas": action_schemas,
        "scheduled_query_action_dependent_schemas": dependent_schemas,
        # Feature flags consumed by the frontend to show/hide whole features.
        "features": {
            "chat": settings.CHAT_ENABLED,
            "chat_schedules": settings.CHAT_ENABLED and settings.CHAT_SCHEDULES_ENABLED,
        },
        "config": {},
    }
