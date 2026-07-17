import logging
from typing import Any

from reporting.schema.report_config import ActionConfigFieldDef
from reporting.schema.reporting_config import ScheduledQueryAction

logger = logging.getLogger(__name__)


def action_name() -> str:
    return "log"


def activity_description() -> str:
    return "Writes selected fields from each input row to the application log."


def activity_input_type() -> Any:
    return list[dict[str, Any]]


def activity_output_type() -> Any:
    return dict[str, int]


def action_config_schema() -> list[ActionConfigFieldDef]:
    return [
        ActionConfigFieldDef(
            name="log_attrs",
            label="Log attributes",
            type="string_list",
            required=True,
            description="Attributes from each query result row to include in the log entry.",
        ),
        ActionConfigFieldDef(
            name="query_return_attribute",
            label="Query return attribute",
            type="string",
            required=False,
            description="Top-level attribute of each result row that contains the data map.",
            default="details",
        ),
        ActionConfigFieldDef(
            name="message",
            label="Message",
            type="string",
            required=False,
            description="Log message. Defaults to the scheduled query ID.",
        ),
        ActionConfigFieldDef(
            name="level",
            label="Log level",
            type="select",
            required=False,
            default="info",
            options=["debug", "info", "warning", "error"],
        ),
    ]


async def setup() -> None:
    return


def handle_results(
    scheduled_query_id: str, action: ScheduledQueryAction, results: list[dict[str, Any]]
) -> dict[str, int]:
    if not results:
        return {"rows_logged": 0}

    attr = action.action_config.get("query_return_attribute", "details")
    message = action.action_config.get("message", f"Result for {scheduled_query_id}")
    level = action.action_config.get("level", "info")
    log_attrs = action.action_config.get("log_attrs", [])
    if not log_attrs:
        logger.error(f"{scheduled_query_id} is missing log_attrs in action_config.")
        return {"rows_logged": 0}

    logger.info(
        "Sending results for query",
        extra={
            "result_count": len(results),
            "scheduled_query_id": scheduled_query_id,
        },
    )
    for result in results:
        data = {}
        for key, val in result[attr].items():
            if key in log_attrs:
                data[key] = val
        level_ref = getattr(logging, level.upper())
        logger.log(level_ref, message, extra=data)
    return {"rows_logged": len(results)}
