import json
import logging
from typing import Any

import botocore.config

from reporting import settings
from reporting.schema.report_config import ActionConfigFieldDef
from reporting.schema.reporting_config import ScheduledQueryAction
from reporting.services import get_boto_client, report_store
from reporting.utils.settings import bool_env, str_env

logger = logging.getLogger(__name__)

# Whether or not to create the queues referenced in the scheduled query sqs actions (meant for dev)
_SQS_CREATE_SCHEDULED_QUERY_QUEUES = bool_env("SQS_CREATE_SCHEDULED_QUERY_QUEUES", False)
# URL for the SQS server, for use in dev when pointing at a fake SQS
_SQS_URL = str_env("SQS_URL")


def _get_client() -> Any:
    config = botocore.config.Config(
        connect_timeout=settings.AWS_CONNECT_TIMEOUT,
        read_timeout=settings.AWS_READ_TIMEOUT,
    )
    if _SQS_URL:
        return get_boto_client("sqs", endpoint_url=_SQS_URL, config=config)
    else:
        return get_boto_client("sqs", config=config)


def action_name() -> str:
    return "sqs"


def activity_description() -> str:
    return "Sends one SQS message for each input row."


def activity_input_type() -> Any:
    return list[dict[str, Any]]


def activity_output_type() -> Any:
    return dict[str, int]


def action_config_schema() -> list[ActionConfigFieldDef]:
    return [
        ActionConfigFieldDef(
            name="sqs_queue",
            label="SQS queue name",
            type="string",
            required=True,
            description="Name of the SQS queue to send result messages to.",
        ),
        ActionConfigFieldDef(
            name="query_return_attribute",
            label="Query return attribute",
            type="string",
            required=False,
            description="Top-level attribute of each result row to send as the message body.",
            default="details",
        ),
    ]


async def setup() -> None:
    if not _SQS_CREATE_SCHEDULED_QUERY_QUEUES:
        return
    queue_names: set[str] = set()
    for item in await report_store.list_scheduled_queries():
        for action in item.actions:
            if action.get("action_type") == "sqs":
                queue_name = (action.get("action_config") or {}).get("sqs_queue")
                if isinstance(queue_name, str) and queue_name:
                    queue_names.add(queue_name)
        for stage in item.stages or []:
            for activity in stage.get("activities", []):
                if activity.get("type") != "sqs":
                    continue
                queue_name = (activity.get("parameters") or {}).get("sqs_queue")
                if isinstance(queue_name, str) and queue_name:
                    queue_names.add(queue_name)
    if queue_names:
        sqs_client = _get_client()
        for queue_name in sorted(queue_names):
            sqs_client.create_queue(QueueName=queue_name)


def handle_results(
    scheduled_query_id: str,
    action: ScheduledQueryAction,
    results: list[dict[str, Any]],
) -> dict[str, int]:
    if not results:
        return {"messages_sent": 0}

    sqs_client = _get_client()
    q_url = sqs_client.get_queue_url(QueueName=action.action_config["sqs_queue"])["QueueUrl"]
    attr = action.action_config.get("query_return_attribute", "details")
    logger.info(
        "Sending results for query",
        extra={
            "result_count": len(results),
            "scheduled_query_id": scheduled_query_id,
        },
    )
    for result in results:
        body = json.dumps(result[attr])
        sqs_client.send_message(
            QueueUrl=q_url,
            MessageBody=body,
            MessageAttributes={
                "type": {
                    "DataType": "String",
                    "StringValue": scheduled_query_id,
                },
                "source": {
                    "DataType": "String",
                    "StringValue": "seizu",
                },
            },
        )
    return {"messages_sent": len(results)}
