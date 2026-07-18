"""Shared validation helpers for scheduled query create/update flows."""

from typing import Any

from reporting import scheduled_query_modules
from reporting.services.activity_config import validate_config


def _config_fields(action_type: str, action_config: dict[str, Any], fields: list[Any]) -> list[Any]:
    merged = list(fields)
    if action_type not in ("temporal", "workflow"):
        return merged
    from reporting.temporal_workflows import get_enabled_workflow_spec

    workflow_name = action_config.get("workflow")
    spec = get_enabled_workflow_spec(workflow_name) if isinstance(workflow_name, str) else None
    if spec is not None and spec.config_fields is not None:
        known = {field.name for field in merged}
        merged.extend(field for field in spec.config_fields() if field.name not in known)
    return merged


def validate_action_configs(actions: list[dict[str, Any]]) -> str | None:
    """Validate each action's config against the module's declared schema.

    Returns an error message string if validation fails, or None if valid.
    """
    schemas = scheduled_query_modules.get_action_schemas()
    validators = scheduled_query_modules.get_action_validators()
    for action in actions:
        action_type = action.get("action_type", "")
        action_config = action.get("action_config", {})
        if action_type not in schemas:
            return f"Unknown action type '{action_type}'. Valid types: {sorted(schemas)}."
        fields = _config_fields(action_type, action_config, schemas[action_type])
        if error := validate_config(action_config, fields, name=f"{action_type.title()}Config"):
            return f"Action type '{action_type}': {error}"
        validator = validators.get(action_type)
        if validator is not None:
            error = validator(action_config)
            if error is not None:
                return f"Action type '{action_type}': {error}"
    return None
