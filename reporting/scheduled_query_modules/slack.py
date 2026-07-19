import csv
import io
import logging
from typing import Any

from slack_sdk import WebClient

from reporting import settings
from reporting.schema.report_config import ActionConfigFieldDef
from reporting.schema.reporting_config import ScheduledQueryAction
from reporting.utils.settings import str_env

logger = logging.getLogger(__name__)

# Bot token from a slack app's settings
_SLACK_OAUTH_BOT_TOKEN = str_env("SLACK_OAUTH_BOT_TOKEN")

_CLIENT = None


def _get_client() -> Any:
    global _CLIENT

    if _CLIENT is None:
        _CLIENT = WebClient(token=_SLACK_OAUTH_BOT_TOKEN, timeout=settings.SLACK_TIMEOUT)
    return _CLIENT


def action_name() -> str:
    return "slack"


def activity_description() -> str:
    return "Uploads the input rows as a CSV file to Slack."


def activity_input_type() -> Any:
    return list[dict[str, Any]]


def activity_output_type() -> Any:
    return dict[str, int]


def action_config_schema() -> list[ActionConfigFieldDef]:
    return [
        ActionConfigFieldDef(
            name="channels",
            label="Channels",
            type="string_list",
            required=True,
            description="Slack channel IDs to post results to.",
        ),
        ActionConfigFieldDef(
            name="title",
            label="Title",
            type="string",
            required=True,
            description="Title of the uploaded CSV file.",
        ),
        ActionConfigFieldDef(
            name="initial_comment",
            label="Initial comment",
            type="string",
            required=True,
            description="Message posted alongside the file upload.",
        ),
        ActionConfigFieldDef(
            name="query_return_attribute",
            label="Query return attribute",
            type="string",
            required=False,
            description="Top-level attribute of each result row that contains the data map.",
            default="details",
        ),
    ]


async def setup() -> None:
    return


def handle_results(
    scheduled_query_id: str, action: ScheduledQueryAction, results: list[dict[str, Any]]
) -> dict[str, int]:
    if not results:
        return {"rows_uploaded": 0, "channels": 0}

    slack_client = _get_client()

    attr = action.action_config.get("query_return_attribute", "details")
    logger.info(
        "Sending results for query",
        extra={
            "result_count": len(results),
            "scheduled_query_id": scheduled_query_id,
        },
    )
    for key in ["channels", "initial_comment", "title"]:
        if not action.action_config.get(key):
            logger.error(
                "Skipping misconfigured scheduled query",
                extra={
                    "scheduled_query_id": scheduled_query_id,
                    "action_type": action.action_type,
                    "misconfiguration": f"missing {key}",
                },
            )

    content = io.StringIO()
    fieldnames = results[0][attr].keys()
    writer = csv.DictWriter(content, fieldnames=fieldnames)
    writer.writeheader()
    for result in results:
        writer.writerow(result[attr])

    for channel in action.action_config["channels"]:
        slack_client.conversations_join(channel=channel)
    slack_client.files_upload(
        content=content.getvalue(),
        filename=f"{attr}.csv",
        channels=action.action_config["channels"],
        title=action.action_config["title"],
        initial_comment=action.action_config["initial_comment"],
    )
    return {"rows_uploaded": len(results), "channels": len(action.action_config["channels"])}
