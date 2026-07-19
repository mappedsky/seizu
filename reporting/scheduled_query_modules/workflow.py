"""Code-defined workflow activity.

This is the canonical name for the former ``temporal`` scheduled-query action.
The handler remains available for legacy worker compatibility; configurable
Temporal workflows execute this activity as an awaited child workflow.
"""

from reporting.scheduled_query_modules.temporal import (
    action_config_schema as action_config_schema,
)
from reporting.scheduled_query_modules.temporal import handle_results as handle_results
from reporting.scheduled_query_modules.temporal import setup as setup
from reporting.scheduled_query_modules.temporal import (
    validate_action_config as validate_action_config,
)


def action_name() -> str:
    return "workflow"
