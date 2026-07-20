"""Shared validation helpers for scheduled query create/update flows."""

from typing import Any

from reporting import scheduled_query_modules
from reporting.services.activity_config import validate_config


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
        if error := validate_config(action_config, schemas[action_type], name=f"{action_type.title()}Config"):
            return f"Action type '{action_type}': {error}"
        validator = validators.get(action_type)
        if validator is not None:
            error = validator(action_config)
            if error is not None:
                return f"Action type '{action_type}': {error}"
    return None
