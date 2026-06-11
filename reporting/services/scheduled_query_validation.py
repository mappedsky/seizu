"""Shared validation helpers for scheduled query create/update flows."""

from typing import Any

from reporting import scheduled_query_modules


def validate_action_configs(actions: list[dict[str, Any]], permissions: frozenset[str] | None = None) -> str | None:
    """Validate each action's config against the module's declared schema.

    When ``permissions`` is provided, action types whose module declares a
    ``required_permission`` are rejected unless the permission is held (e.g.
    ``agent_chat`` requires ``chat:bypass_permissions``).

    Returns an error message string if validation fails, or None if valid.
    """
    schemas = scheduled_query_modules.get_action_schemas()
    action_permissions = scheduled_query_modules.get_action_permissions()
    for action in actions:
        action_type = action.get("action_type", "")
        action_config = action.get("action_config", {})
        if action_type not in schemas:
            return f"Unknown action type '{action_type}'. Valid types: {sorted(schemas)}."
        required = action_permissions.get(action_type)
        if required and permissions is not None and required not in permissions:
            return f"Action type '{action_type}' requires the '{required}' permission."
        for field in schemas[action_type]:
            if not field.required:
                continue
            value = action_config.get(field.name)
            if value is None or value == "" or value == []:
                return f"Action type '{action_type}' is missing required field '{field.name}'."
            # A required boolean is an explicit acknowledgement (e.g. the
            # temporal action's confirmation-bypass acceptance): it must be
            # checked, not merely present.
            if field.type == "boolean" and value is not True:
                return f"Action type '{action_type}' requires '{field.name}' to be accepted."
    return None
