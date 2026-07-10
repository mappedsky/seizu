import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, TypeVar

import boto3
import botocore.config
import botocore.exceptions
from snowflake import SnowflakeGenerator

from reporting import settings
from reporting.schema.chat import ChatSessionItem, ScheduledChatItem, ScheduledChatVersion
from reporting.schema.confirmations import ActionConfirmation, ConfirmationDecision, ConfirmationSource
from reporting.schema.mcp_config import (
    SkillItem,
    SkillsetListItem,
    SkillsetVersion,
    SkillVersion,
    ToolItem,
    ToolParamDef,
    ToolsetListItem,
    ToolsetVersion,
    ToolVersion,
)
from reporting.schema.rbac import RoleItem, RoleVersion
from reporting.schema.report_config import (
    QueryHistoryItem,
    ReportAccess,
    ReportListItem,
    ReportVersion,
    ScheduledQueryItem,
    ScheduledQueryVersion,
    User,
)
from reporting.services.report_store.base import ReportStore, initial_report_config

logger = logging.getLogger(__name__)

# Module-level snowflake generator; lazily initialised so the machine ID
# setting is read after the module is imported.
_snowflake_gen: SnowflakeGenerator | None = None

# DynamoDB key constants
_PK_REPORT_LIST = "REPORT_LIST"
# The latest-version pointer uses a '#' prefix so it sorts before all
# "VERSION#…" sort keys and is never returned by begins_with("VERSION#") queries.
_SK_LATEST = "#LATEST"
_SK_METADATA = "#METADATA"
_SK_VERSION_PREFIX = "VERSION#"
# Dashboard pointer — a single item that records which report is the default dashboard.
_PK_DASHBOARD = "#DASHBOARD"
_SK_DASHBOARD_POINTER = "#POINTER"
# User lookup — PK is shared; SK encodes iss + sub for lookup by external identity.
_PK_USER_LOOKUP = "USER_LOOKUP"
# Scheduled queries — list index PK for listing all scheduled queries.
_PK_SCHEDULED_QUERY_LIST = "SCHEDULED_QUERY_LIST"
_PK_SCHEDULED_CHAT_LIST = "SCHEDULED_CHAT_LIST"
# Toolsets — list index PK for listing all toolsets.
_PK_TOOLSET_LIST = "TOOLSET_LIST"
# Skillsets — list index PK for listing all skillsets.
_PK_SKILLSET_LIST = "SKILLSET_LIST"
# Roles — list index PK for listing all user-defined roles.
_PK_ROLE_LIST = "ROLE_LIST"
# Chat sessions — metadata items are keyed by user/thread; list items are keyed
# by user and sorted by updated_at for the sidebar.
_PK_CHAT_SESSION_METADATA_PREFIX = "CHAT_SESSION#"
_PK_CHAT_SESSION_LIST_PREFIX = "CHAT_SESSION_LIST#"
_PK_ACTION_CONFIRMATION_PREFIX = "ACTION_CONFIRMATION#"
# Group mappings — list index PK for listing all group-to-role mappings.
# Query history — per-user SK prefix; items sorted newest-first by snowflake ID.
_SK_QUERY_HISTORY_PREFIX = "HISTORY#"
# Maximum history items fetched from DynamoDB per user (caps scan size).
_QUERY_HISTORY_MAX = 500
_CHAT_SESSION_LIST_MAX = 500
_CHAT_SESSION_TRANSACTION_RETRIES = 3
_ACTION_CONFIRMATION_SESSION_QUERY_MAX = 500
_T = TypeVar("_T")


@dataclass(frozen=True)
class ChatSessionUpdate:
    item: dict[str, Any]
    expression: str
    values: dict[str, Any]
    names: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _get_snowflake_gen() -> SnowflakeGenerator:
    global _snowflake_gen
    if _snowflake_gen is None:
        _snowflake_gen = SnowflakeGenerator(settings.SNOWFLAKE_MACHINE_ID)
    return _snowflake_gen


def _floats_to_decimal(value: Any) -> Any:
    """Recursively convert float to Decimal for DynamoDB compatibility.

    DynamoDB's boto3 resource rejects Python float values; all numbers must
    be stored as Decimal.  We convert via str() to avoid floating-point
    precision artefacts (e.g. Decimal(str(2.0)) == Decimal('2.0')).
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _floats_to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_floats_to_decimal(v) for v in value]
    return value


def _report_pk(report_id: str) -> str:
    return f"REPORT#{report_id}"


def _version_sk(version: int) -> str:
    """Zero-pad version numbers so lexicographic sort matches numeric sort."""
    return f"{_SK_VERSION_PREFIX}{version:010d}"  # noqa: E231


def generate_report_id() -> str:
    """Return a new unique snowflake ID string."""
    return str(next(_get_snowflake_gen()))


def _query_history_pk(user_id: str) -> str:
    return f"QUERY_HISTORY#{user_id}"


def _query_history_sk(history_id: str) -> str:
    """Pad the snowflake ID so lexicographic order matches time order."""
    return f"{_SK_QUERY_HISTORY_PREFIX}{history_id.zfill(20)}"


def _chat_session_metadata_pk(user_id: str) -> str:
    return f"{_PK_CHAT_SESSION_METADATA_PREFIX}{user_id}"


def _chat_session_list_pk(user_id: str) -> str:
    return f"{_PK_CHAT_SESSION_LIST_PREFIX}{user_id}"


def _chat_session_list_sk(updated_at: str, thread_id: str) -> str:
    return f"UPDATED#{updated_at}#THREAD#{thread_id}"


def _chat_session_from_item(item: dict[str, Any]) -> ChatSessionItem:
    origin = item.get("origin", "interactive")
    return ChatSessionItem(
        thread_id=item["thread_id"],
        title=item.get("title", ""),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        origin=origin if origin in ("interactive", "scheduled", "workflow") else "interactive",
        scheduled_chat_id=item.get("scheduled_chat_id"),
        run_status=item.get("run_status"),
        run_errors=item.get("run_errors", []),
    )


def _action_confirmation_pk(confirmation_id: str) -> str:
    return f"{_PK_ACTION_CONFIRMATION_PREFIX}{confirmation_id}"


def _action_confirmation_list_sk(created_at: str, confirmation_id: str) -> str:
    return f"CREATED#{created_at}#CONFIRMATION#{confirmation_id}"


def _action_confirmation_session_list_pk(user_id: str, source: str, session_key: str) -> str:
    return f"ACTION_CONFIRMATION_SESSION_LIST#{user_id}#SOURCE#{source}#SESSION#{session_key}"


def _action_confirmation_session_list_sk(status: str, created_at: str, confirmation_id: str) -> str:
    return f"STATUS#{status}#CREATED#{created_at}#CONFIRMATION#{confirmation_id}"


def _action_confirmation_batch_pk(user_id: str, batch_id: str) -> str:
    return f"ACTION_CONFIRMATION_BATCH#{user_id}#BATCH#{batch_id}"


def _action_confirmation_dedup_sk(data: dict[str, Any]) -> str:
    """Deterministic SK for the pending dedup sentinel; encodes the full action scope."""
    key = "|".join(
        [
            str(data["tool_name"]),
            str(data["action"]),
            str(data["resource_type"]),
            str(data["resource_id"]),
            str(data["arguments_hash"]),
        ]
    )
    return f"PENDING_DEDUP#{hashlib.sha256(key.encode()).hexdigest()}"


def _action_confirmation_from_item(item: dict[str, Any]) -> ActionConfirmation:
    return ActionConfirmation.model_validate(item)


def _action_confirmation_data(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in {"PK", "SK"}}


def _action_confirmation_metadata_item(data: dict[str, Any]) -> dict[str, Any]:
    return {
        **_floats_to_decimal(data),
        "PK": _action_confirmation_pk(str(data["confirmation_id"])),
        "SK": _SK_METADATA,
    }


def _action_confirmation_session_list_item(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "PK": _action_confirmation_session_list_pk(
            str(data["user_id"]),
            str(data["source"]),
            str(data["session_key"]),
        ),
        "SK": _action_confirmation_session_list_sk(
            str(data["status"]),
            str(data["created_at"]),
            str(data["confirmation_id"]),
        ),
        "confirmation_id": str(data["confirmation_id"]),
    }


def _action_confirmation_batch_list_item(data: dict[str, Any]) -> dict[str, Any] | None:
    batch_id = data.get("batch_id")
    if not batch_id:
        return None
    return {
        "PK": _action_confirmation_batch_pk(str(data["user_id"]), str(batch_id)),
        "SK": _action_confirmation_list_sk(str(data["created_at"]), str(data["confirmation_id"])),
        "confirmation_id": str(data["confirmation_id"]),
    }


def _action_confirmation_dedup_sentinel_item(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "PK": _action_confirmation_session_list_pk(str(data["user_id"]), str(data["source"]), str(data["session_key"])),
        "SK": _action_confirmation_dedup_sk(data),
        "confirmation_id": str(data["confirmation_id"]),
    }


def _action_confirmation_put_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = [
        _action_confirmation_metadata_item(data),
        _action_confirmation_session_list_item(data),
    ]
    batch_item = _action_confirmation_batch_list_item(data)
    if batch_item is not None:
        items.append(batch_item)
    return items


async def _hydrate_action_confirmations(
    confirmation_ids: list[str],
    get_confirmation: Callable[[str], Awaitable[ActionConfirmation | None]],
) -> list[ActionConfirmation]:
    confirmations = await asyncio.gather(*(get_confirmation(confirmation_id) for confirmation_id in confirmation_ids))
    return [confirmation for confirmation in confirmations if confirmation is not None]


def _transaction_cancelled(exc: botocore.exceptions.ClientError) -> bool:
    return exc.response["Error"]["Code"] == "TransactionCanceledException"


def _chat_session_update_transactions(
    metadata_pk: str,
    list_pk: str,
    thread_id: str,
    existing: dict[str, Any],
    updated: dict[str, Any],
    update_expression: str,
    expression_attribute_values: dict[str, Any],
    expression_attribute_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if ":old_updated_at" in expression_attribute_values:
        raise ValueError("expression_attribute_values must not define :old_updated_at")
    update_op: dict[str, Any] = {
        "TableName": settings.DYNAMODB_TABLE_NAME,
        "Key": {"PK": metadata_pk, "SK": thread_id},
        "UpdateExpression": update_expression,
        "ExpressionAttributeValues": {
            **expression_attribute_values,
            ":old_updated_at": existing["updated_at"],
        },
        "ConditionExpression": "updated_at = :old_updated_at",
    }
    if expression_attribute_names:
        update_op["ExpressionAttributeNames"] = expression_attribute_names
    return [
        {"Update": update_op},
        {
            "Delete": {
                "TableName": settings.DYNAMODB_TABLE_NAME,
                "Key": {
                    "PK": list_pk,
                    "SK": _chat_session_list_sk(existing["updated_at"], thread_id),
                },
            },
        },
        {
            "Put": {
                "TableName": settings.DYNAMODB_TABLE_NAME,
                "Item": {
                    **updated,
                    "PK": list_pk,
                    "SK": _chat_session_list_sk(updated["updated_at"], thread_id),
                },
            },
        },
    ]


def _retry_chat_session_transaction(  # noqa: UP047 - mypy in this repo does not yet accept PEP 695 generics here.
    table: Any,
    metadata_pk: str,
    thread_id: str,
    build_transaction: Callable[[dict[str, Any]], tuple[_T, list[dict[str, Any]]]],
) -> _T | None:
    attempt = 0
    while True:
        existing_resp = table.get_item(Key={"PK": metadata_pk, "SK": thread_id})
        existing = existing_resp.get("Item")
        if existing is None:
            return None
        result, transact_items = build_transaction(existing)
        try:
            table.meta.client.transact_write_items(TransactItems=transact_items)
            return result
        except botocore.exceptions.ClientError as exc:
            if (
                exc.response["Error"]["Code"] == "TransactionCanceledException"
                and attempt < _CHAT_SESSION_TRANSACTION_RETRIES - 1
            ):
                attempt += 1
                continue
            raise


def _retry_chat_session_update(
    table: Any,
    metadata_pk: str,
    list_pk: str,
    thread_id: str,
    build_update: Callable[[dict[str, Any], str], ChatSessionUpdate],
) -> ChatSessionItem | None:
    def _build_transaction(existing: dict[str, Any]) -> tuple[ChatSessionItem, list[dict[str, Any]]]:
        now = datetime.now(tz=UTC).isoformat()
        updated = build_update(existing, now)
        return _chat_session_from_item(updated.item), _chat_session_update_transactions(
            metadata_pk=metadata_pk,
            list_pk=list_pk,
            thread_id=thread_id,
            existing=existing,
            updated=updated.item,
            update_expression=updated.expression,
            expression_attribute_values=updated.values,
            expression_attribute_names=updated.names,
        )

    return _retry_chat_session_transaction(table, metadata_pk, thread_id, _build_transaction)


def _retry_chat_session_delete(
    table: Any,
    metadata_pk: str,
    list_pk: str,
    thread_id: str,
) -> bool:
    def _build_transaction(existing: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
        return True, [
            {
                "Delete": {
                    "TableName": settings.DYNAMODB_TABLE_NAME,
                    "Key": {"PK": metadata_pk, "SK": thread_id},
                    "ConditionExpression": "updated_at = :old_updated_at",
                    "ExpressionAttributeValues": {
                        ":old_updated_at": existing["updated_at"],
                    },
                },
            },
            {
                "Delete": {
                    "TableName": settings.DYNAMODB_TABLE_NAME,
                    "Key": {
                        "PK": list_pk,
                        "SK": _chat_session_list_sk(existing["updated_at"], thread_id),
                    },
                },
            },
        ]

    return _retry_chat_session_transaction(table, metadata_pk, thread_id, _build_transaction) or False


def _role_pk(role_id: str) -> str:
    return f"ROLE#{role_id}"


def _role_list_sk(role_id: str) -> str:
    return f"ROLE#{role_id}"


def _role_from_item(item: dict) -> RoleItem:
    return RoleItem(
        role_id=item["role_id"],
        name=item["name"],
        description=item.get("description", ""),
        permissions=item.get("permissions", []),
        current_version=item.get("current_version", 0),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        created_by=item["created_by"],
        updated_by=item.get("updated_by"),
    )


def _report_visible_to_user(item: dict[str, Any], user_id: str | None) -> bool:
    if user_id is None:
        return True
    access = item["access"]
    return access["scope"] == "public" or item["created_by"] == user_id


def _report_list_item_from_item(item: dict[str, Any]) -> ReportListItem:
    return ReportListItem(
        report_id=item["report_id"],
        name=item["name"],
        current_version=item["current_version"],
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        created_by=item["created_by"],
        updated_by=item["updated_by"],
        access=item["access"],
        pinned=bool(item.get("pinned", False)),
    )


def _report_version_from_item(item: dict[str, Any], meta: dict[str, Any]) -> ReportVersion:
    return ReportVersion(
        report_id=item["report_id"],
        name=item["name"],
        version=item["version"],
        config=item["config"],
        created_at=item["created_at"],
        created_by=item["created_by"],
        comment=item.get("comment"),
        report_created_by=meta["created_by"],
        report_updated_by=meta["updated_by"],
        access=meta["access"],
    )


def _role_version_from_item(item: dict) -> RoleVersion:
    return RoleVersion(
        role_id=item["role_id"],
        name=item["name"],
        description=item.get("description", ""),
        permissions=item.get("permissions", []),
        version=item["version"],
        created_at=item["created_at"],
        created_by=item["created_by"],
        comment=item.get("comment"),
    )


def _sq_pk(sq_id: str) -> str:
    return f"SQ#{sq_id}"


def _scheduled_chat_pk(sc_id: str) -> str:
    return f"SCHEDULED_CHAT#{sc_id}"


def _scheduled_chat_from_item(item: dict) -> ScheduledChatItem:
    return ScheduledChatItem(
        scheduled_chat_id=item["scheduled_chat_id"],
        name=item["name"],
        prompt=item["prompt"],
        schedule=item.get("schedule"),
        watch_scans=item.get("watch_scans", []),
        enabled=item.get("enabled", True),
        current_version=item.get("current_version", 0),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        created_by=item["created_by"],
        updated_by=item.get("updated_by"),
        last_run_status=item.get("last_run_status"),
        last_run_at=item.get("last_run_at"),
        last_errors=item.get("last_errors", []),
        last_scheduled_at=item.get("last_scheduled_at"),
        run_requested_at=item.get("run_requested_at"),
    )


def _scheduled_chat_version_from_item(item: dict) -> ScheduledChatVersion:
    return ScheduledChatVersion(
        scheduled_chat_id=item["scheduled_chat_id"],
        version=item["version"],
        name=item["name"],
        prompt=item["prompt"],
        schedule=item.get("schedule"),
        watch_scans=item.get("watch_scans", []),
        enabled=item.get("enabled", True),
        created_at=item["created_at"],
        created_by=item["created_by"],
        comment=item.get("comment"),
    )


def _toolset_pk(toolset_id: str) -> str:
    return f"TOOLSET#{toolset_id}"


def _toolset_list_sk(toolset_id: str) -> str:
    return f"TOOLSET#{toolset_id}"


def _tool_pk(tool_id: str) -> str:
    return f"TOOL#{tool_id}"


def _tool_list_pk(toolset_id: str) -> str:
    return f"TOOL_LIST#{toolset_id}"


def _tool_list_sk(tool_id: str) -> str:
    return f"TOOL#{tool_id}"


def _skillset_pk(skillset_id: str) -> str:
    return f"SKILLSET#{skillset_id}"


def _skillset_list_sk(skillset_id: str) -> str:
    return f"SKILLSET#{skillset_id}"


def _skill_pk(skill_id: str) -> str:
    return f"SKILL#{skill_id}"


def _skill_list_pk(skillset_id: str) -> str:
    return f"SKILL_LIST#{skillset_id}"


def _skill_list_sk(skill_id: str) -> str:
    return f"SKILL#{skill_id}"


def _toolset_from_item(item: dict) -> ToolsetListItem:
    return ToolsetListItem(
        toolset_id=item["toolset_id"],
        name=item["name"],
        description=item.get("description", ""),
        enabled=item.get("enabled", True),
        current_version=item.get("current_version", 0),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        created_by=item["created_by"],
        updated_by=item.get("updated_by"),
    )


def _toolset_version_from_item(item: dict) -> ToolsetVersion:
    return ToolsetVersion(
        toolset_id=item["toolset_id"],
        name=item["name"],
        description=item.get("description", ""),
        enabled=item.get("enabled", True),
        version=item["version"],
        created_at=item["created_at"],
        created_by=item["created_by"],
        comment=item.get("comment"),
    )


def _tool_from_item(item: dict) -> ToolItem:
    return ToolItem(
        tool_id=item["tool_id"],
        toolset_id=item["toolset_id"],
        name=item["name"],
        description=item.get("description", ""),
        cypher=item["cypher"],
        parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in item.get("parameters", [])],
        enabled=item.get("enabled", True),
        current_version=item.get("current_version", 0),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        created_by=item["created_by"],
        updated_by=item.get("updated_by"),
    )


def _tool_version_from_item(item: dict) -> ToolVersion:
    return ToolVersion(
        tool_id=item["tool_id"],
        toolset_id=item["toolset_id"],
        name=item["name"],
        description=item.get("description", ""),
        cypher=item["cypher"],
        parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in item.get("parameters", [])],
        enabled=item.get("enabled", True),
        version=item["version"],
        created_at=item["created_at"],
        created_by=item["created_by"],
        comment=item.get("comment"),
    )


def _skillset_from_item(item: dict) -> SkillsetListItem:
    return SkillsetListItem(
        skillset_id=item["skillset_id"],
        name=item["name"],
        description=item.get("description", ""),
        enabled=item.get("enabled", True),
        current_version=item.get("current_version", 0),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        created_by=item["created_by"],
        updated_by=item.get("updated_by"),
    )


def _skillset_version_from_item(item: dict) -> SkillsetVersion:
    return SkillsetVersion(
        skillset_id=item["skillset_id"],
        name=item["name"],
        description=item.get("description", ""),
        enabled=item.get("enabled", True),
        version=item["version"],
        created_at=item["created_at"],
        created_by=item["created_by"],
        comment=item.get("comment"),
    )


def _skill_from_item(item: dict) -> SkillItem:
    return SkillItem(
        skill_id=item["skill_id"],
        skillset_id=item["skillset_id"],
        name=item["name"],
        description=item.get("description", ""),
        template=item["template"],
        parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in item.get("parameters", [])],
        triggers=item.get("triggers", []),
        tools_required=item.get("tools_required", []),
        enabled=item.get("enabled", True),
        current_version=item.get("current_version", 0),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        created_by=item["created_by"],
        updated_by=item.get("updated_by"),
    )


def _skill_version_from_item(item: dict) -> SkillVersion:
    return SkillVersion(
        skill_id=item["skill_id"],
        skillset_id=item["skillset_id"],
        name=item["name"],
        description=item.get("description", ""),
        template=item["template"],
        parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in item.get("parameters", [])],
        triggers=item.get("triggers", []),
        tools_required=item.get("tools_required", []),
        enabled=item.get("enabled", True),
        version=item["version"],
        created_at=item["created_at"],
        created_by=item["created_by"],
        comment=item.get("comment"),
    )


def _sq_version_sk(version: int) -> str:
    """Zero-pad version numbers so lexicographic sort matches numeric sort."""
    return f"{_SK_VERSION_PREFIX}{version:010d}"  # noqa: E231


def _sq_from_item(item: dict) -> ScheduledQueryItem:
    return ScheduledQueryItem(
        scheduled_query_id=item["scheduled_query_id"],
        name=item["name"],
        cypher=item["cypher"],
        params=item.get("params", []),
        frequency=item.get("frequency"),
        schedule=item.get("schedule"),
        watch_scans=item.get("watch_scans", []),
        enabled=item.get("enabled", True),
        actions=item.get("actions", []),
        current_version=item.get("current_version", 0),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        created_by=item["created_by"],
        updated_by=item.get("updated_by"),
        last_run_status=item.get("last_run_status"),
        last_run_at=item.get("last_run_at"),
        last_errors=item.get("last_errors", []),
        last_scheduled_at=item.get("last_scheduled_at"),
        run_requested_at=item.get("run_requested_at"),
    )


def _sq_version_from_item(item: dict) -> ScheduledQueryVersion:
    return ScheduledQueryVersion(
        scheduled_query_id=item["scheduled_query_id"],
        name=item["name"],
        version=item["version"],
        cypher=item["cypher"],
        params=item.get("params", []),
        frequency=item.get("frequency"),
        schedule=item.get("schedule"),
        watch_scans=item.get("watch_scans", []),
        enabled=item.get("enabled", True),
        actions=item.get("actions", []),
        created_at=item["created_at"],
        created_by=item["created_by"],
        comment=item.get("comment"),
    )


def _user_pk(user_id: str) -> str:
    return f"USER#{user_id}"


def _user_lookup_sk(iss: str, sub: str) -> str:
    return f"{iss}#{sub}"


def _user_from_item(item: dict) -> User:
    return User(
        user_id=item["user_id"],
        sub=item["sub"],
        iss=item["iss"],
        email=item.get("email"),
        display_name=item.get("display_name"),
        preferred_username=item.get("preferred_username"),
        created_at=item["created_at"],
        last_login=item.get("last_login", item.get("last_seen_at", "")),
        archived_at=item.get("archived_at"),
        role=item.get("role"),
    )


def _sync_user_role(table: Any, user_id: str, role: str | None) -> User:
    """Write the observed role claim to the user profile and return the user.

    ``role`` is reserved in DynamoDB expressions, hence the alias.
    """
    if role is None:
        update = "REMOVE #role"
        kwargs: dict[str, Any] = {}
    else:
        update = "SET #role = :r"
        kwargs = {"ExpressionAttributeValues": {":r": role}}
    resp = table.update_item(
        Key={"PK": _user_pk(user_id), "SK": _SK_METADATA},
        UpdateExpression=update,
        ExpressionAttributeNames={"#role": "role"},
        ReturnValues="ALL_NEW",
        **kwargs,
    )
    return _user_from_item(resp["Attributes"])


def _strip_none(value: Any) -> Any:
    """Recursively remove None values from dicts and lists.

    DynamoDB Local rejects the NULL type in TransactWriteItems, even when
    nested inside a Map or List attribute.  Omitting None entirely is
    equivalent — a missing attribute reads back as None on deserialisation.
    """
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value if v is not None]
    return value


def get_boto_resource() -> Any:
    """Return a sync boto3 DynamoDB service resource."""
    return boto3.resource(
        "dynamodb",
        region_name=settings.DYNAMODB_REGION,
        endpoint_url=settings.DYNAMODB_ENDPOINT_URL or None,
        config=botocore.config.Config(
            connect_timeout=settings.AWS_CONNECT_TIMEOUT,
            read_timeout=settings.AWS_READ_TIMEOUT,
        ),
    )


def _get_table() -> Any:
    """Return a sync boto3 DynamoDB Table object."""
    return get_boto_resource().Table(settings.DYNAMODB_TABLE_NAME)


def _transact_put_sync(table: Any, *items: dict[str, Any]) -> None:
    """Write one or more items atomically via TransactWriteItems."""
    table.meta.client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": settings.DYNAMODB_TABLE_NAME,
                    "Item": _strip_none(item),
                }
            }
            for item in items
        ]
    )


def _update_user_profile_internal(
    table: Any,
    user_id: str,
    email: str | None,
    display_name: str | None,
    preferred_username: str | None,
    token_iat: datetime | None,
) -> User:
    """Apply an email/display_name update and conditionally advance last_login."""
    update_parts: list[str] = []
    exp_values: dict = {}
    if email is not None:
        update_parts.append("email = :e")
        exp_values[":e"] = email
    if display_name is not None:
        update_parts.append("display_name = :d")
        exp_values[":d"] = display_name
    if preferred_username is not None:
        update_parts.append("preferred_username = :u")
        exp_values[":u"] = preferred_username
    if token_iat is not None:
        iat_str = token_iat.isoformat()
        update_parts.append("last_login = :t")
        exp_values[":t"] = iat_str
        try:
            resp = table.update_item(
                Key={"PK": _user_pk(user_id), "SK": _SK_METADATA},
                UpdateExpression="SET " + ", ".join(update_parts),
                ConditionExpression="last_login < :t",
                ExpressionAttributeValues=exp_values,
                ReturnValues="ALL_NEW",
            )
            return _user_from_item(resp["Attributes"])
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            update_parts = [part for part in update_parts if part != "last_login = :t"]
            exp_values.pop(":t", None)
            if not update_parts:
                profile_resp = table.get_item(
                    Key={"PK": _user_pk(user_id), "SK": _SK_METADATA},
                )
                return _user_from_item(profile_resp["Item"])
    resp = table.update_item(
        Key={"PK": _user_pk(user_id), "SK": _SK_METADATA},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeValues=exp_values,
        ReturnValues="ALL_NEW",
    )
    return _user_from_item(resp["Attributes"])


# ---------------------------------------------------------------------------
# DynamoDB backend implementation
# ---------------------------------------------------------------------------


class DynamoDBReportStore(ReportStore):
    """ReportStore implementation backed by Amazon DynamoDB."""

    def generate_id(self) -> str:
        return generate_report_id()

    async def initialize(self) -> None:
        """Create the DynamoDB table if it does not already exist.

        Intended for development / local DynamoDB setups; enabled via
        DYNAMODB_CREATE_TABLE=true.
        """

        def _op() -> None:
            dynamodb = get_boto_resource()
            existing_names = [t.name for t in dynamodb.tables.all()]
            if settings.DYNAMODB_TABLE_NAME in existing_names:
                return
            try:
                dynamodb.create_table(
                    TableName=settings.DYNAMODB_TABLE_NAME,
                    KeySchema=[
                        {"AttributeName": "PK", "KeyType": "HASH"},
                        {"AttributeName": "SK", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "PK", "AttributeType": "S"},
                        {"AttributeName": "SK", "AttributeType": "S"},
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )
                logger.info(
                    "Created DynamoDB table",
                    extra={"table": settings.DYNAMODB_TABLE_NAME},
                )
            except dynamodb.meta.client.exceptions.ResourceInUseException:
                logger.info(
                    "DynamoDB table already exists (created by another worker)",
                    extra={"table": settings.DYNAMODB_TABLE_NAME},
                )

        await asyncio.to_thread(_op)

    async def list_reports(self, user_id: str | None = None) -> list[ReportListItem]:
        """Return lightweight metadata for all reports."""

        def _op() -> list[ReportListItem]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _PK_REPORT_LIST},
            )
            items = resp.get("Items", [])
            return [_report_list_item_from_item(item) for item in items if _report_visible_to_user(item, user_id)]

        return await asyncio.to_thread(_op)

    async def get_report_metadata(
        self,
        report_id: str,
        user_id: str | None = None,
    ) -> ReportListItem | None:
        def _op() -> ReportListItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _report_pk(report_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item or not _report_visible_to_user(item, user_id):
                return None
            return _report_list_item_from_item(item)

        return await asyncio.to_thread(_op)

    async def get_report_latest(
        self,
        report_id: str,
        user_id: str | None = None,
    ) -> ReportVersion | None:
        """Return the latest version of a report config, or None if not found."""

        def _op() -> ReportVersion | None:
            table = _get_table()
            meta_resp = table.get_item(Key={"PK": _report_pk(report_id), "SK": _SK_METADATA})
            meta = meta_resp.get("Item")
            if not meta or not _report_visible_to_user(meta, user_id):
                return None
            resp = table.get_item(
                Key={"PK": _report_pk(report_id), "SK": _SK_LATEST},
            )
            item = resp.get("Item")
            if not item:
                return None
            return _report_version_from_item(item, meta)

        return await asyncio.to_thread(_op)

    async def get_report_version(
        self,
        report_id: str,
        version: int,
        user_id: str | None = None,
    ) -> ReportVersion | None:
        """Return a specific version of a report config, or None if not found."""

        def _op() -> ReportVersion | None:
            table = _get_table()
            meta_resp = table.get_item(Key={"PK": _report_pk(report_id), "SK": _SK_METADATA})
            meta = meta_resp.get("Item")
            if not meta or not _report_visible_to_user(meta, user_id):
                return None
            resp = table.get_item(
                Key={"PK": _report_pk(report_id), "SK": _version_sk(version)},
            )
            item = resp.get("Item")
            if not item:
                return None
            return _report_version_from_item(item, meta)

        return await asyncio.to_thread(_op)

    async def list_report_versions(
        self,
        report_id: str,
        user_id: str | None = None,
    ) -> list[ReportVersion]:
        """Return all stored versions for a report, newest first."""

        def _op() -> list[ReportVersion]:
            table = _get_table()
            meta_resp = table.get_item(Key={"PK": _report_pk(report_id), "SK": _SK_METADATA})
            meta = meta_resp.get("Item")
            if not meta or not _report_visible_to_user(meta, user_id):
                return []
            resp = table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": _report_pk(report_id),
                    ":prefix": _SK_VERSION_PREFIX,
                },
                ScanIndexForward=False,
            )
            return [_report_version_from_item(item, meta) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def create_report(
        self,
        name: str,
        created_by: str,
        access: ReportAccess | None = None,
    ) -> ReportListItem:
        """Create a report and its initial renderable version atomically."""
        report_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        report_access = access or ReportAccess(scope="private")
        config = initial_report_config(name)

        metadata_item = {
            "PK": _report_pk(report_id),
            "SK": _SK_METADATA,
            "report_id": report_id,
            "name": name,
            "current_version": 1,
            "created_at": now,
            "updated_at": now,
            "created_by": created_by,
            "updated_by": created_by,
            "access": report_access.model_dump(),
            "pinned": False,
        }
        list_item = {
            "PK": _PK_REPORT_LIST,
            "SK": f"REPORT#{report_id}",
            "report_id": report_id,
            "name": name,
            "current_version": 1,
            "created_at": now,
            "updated_at": now,
            "created_by": created_by,
            "updated_by": created_by,
            "access": report_access.model_dump(),
            "pinned": False,
        }
        version_item: dict[str, Any] = {
            "PK": _report_pk(report_id),
            "SK": _version_sk(1),
            "report_id": report_id,
            "name": name,
            "version": 1,
            "config": config,
            "created_at": now,
            "created_by": created_by,
            "comment": "Initial version",
        }
        latest_item: dict[str, Any] = {**version_item, "SK": _SK_LATEST}

        def _op() -> None:
            table = _get_table()
            _transact_put_sync(table, metadata_item, list_item, version_item, latest_item)

        await asyncio.to_thread(_op)
        return ReportListItem(
            report_id=report_id,
            name=name,
            current_version=1,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            updated_by=created_by,
            access=report_access,
            pinned=False,
        )

    async def save_report_version(
        self,
        report_id: str,
        config: dict[str, Any],
        created_by: str,
        comment: str | None = None,
        user_id: str | None = None,
    ) -> ReportVersion | None:
        """Append a new version to an existing report and return it."""

        def _op() -> ReportVersion | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _report_pk(report_id), "SK": _SK_METADATA})
            meta = resp.get("Item")
            if not meta or not _report_visible_to_user(meta, user_id):
                return None

            version = int(meta["current_version"]) + 1
            config_name = config.get("name")
            if isinstance(config_name, str) and config_name.strip():
                report_name = config_name.strip()
            else:
                report_name = meta["name"]
            stored_config = {**config, "name": report_name}
            pinned = bool(meta.get("pinned", False))
            now = datetime.now(tz=UTC).isoformat()

            version_item = {
                "PK": _report_pk(report_id),
                "SK": _version_sk(version),
                "report_id": report_id,
                "name": report_name,
                "version": version,
                "config": _floats_to_decimal(stored_config),
                "created_at": now,
                "created_by": created_by,
                "comment": comment,
            }
            latest_item = {**version_item, "SK": _SK_LATEST}
            metadata_item = {
                "PK": _report_pk(report_id),
                "SK": _SK_METADATA,
                "report_id": report_id,
                "name": report_name,
                "current_version": version,
                "created_at": meta["created_at"],
                "updated_at": now,
                "created_by": meta["created_by"],
                "updated_by": created_by,
                "access": meta["access"],
                "pinned": pinned,
            }
            list_item = {
                "PK": _PK_REPORT_LIST,
                "SK": f"REPORT#{report_id}",
                "report_id": report_id,
                "name": report_name,
                "current_version": version,
                "created_at": meta["created_at"],
                "updated_at": now,
                "created_by": meta["created_by"],
                "updated_by": created_by,
                "access": meta["access"],
                "pinned": pinned,
            }

            _transact_put_sync(
                table,
                version_item,
                latest_item,
                metadata_item,
                list_item,
            )
            return _report_version_from_item(version_item, metadata_item)

        return await asyncio.to_thread(_op)

    async def update_report_visibility(
        self,
        report_id: str,
        updated_by: str,
        access: ReportAccess | None = None,
    ) -> ReportListItem | None:
        def _op() -> ReportListItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _report_pk(report_id), "SK": _SK_METADATA})
            meta = resp.get("Item")
            if not meta:
                return None

            now = datetime.now(tz=UTC).isoformat()
            new_access = access.model_dump() if access is not None else meta["access"]
            updated = {
                **meta,
                "updated_at": now,
                "updated_by": updated_by,
                "access": new_access,
            }
            list_item = {
                "PK": _PK_REPORT_LIST,
                "SK": f"REPORT#{report_id}",
                "report_id": report_id,
                "name": meta["name"],
                "current_version": meta["current_version"],
                "created_at": meta["created_at"],
                "updated_at": now,
                "created_by": meta["created_by"],
                "updated_by": updated_by,
                "access": new_access,
                "pinned": bool(meta.get("pinned", False)),
            }
            _transact_put_sync(table, updated, list_item)
            return _report_list_item_from_item(list_item)

        return await asyncio.to_thread(_op)

    async def delete_report(self, report_id: str, user_id: str | None = None) -> bool:
        """Delete a report and all its versions."""

        def _op() -> bool:
            table = _get_table()
            resp = table.get_item(Key={"PK": _report_pk(report_id), "SK": _SK_METADATA})
            meta = resp.get("Item")
            if not meta or not _report_visible_to_user(meta, user_id):
                return False

            items_resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _report_pk(report_id)},
                ProjectionExpression="PK, SK",
            )
            keys_to_delete = [{"PK": item["PK"], "SK": item["SK"]} for item in items_resp.get("Items", [])]
            keys_to_delete.append({"PK": _PK_REPORT_LIST, "SK": f"REPORT#{report_id}"})

            dashboard_resp = table.get_item(Key={"PK": _PK_DASHBOARD, "SK": _SK_DASHBOARD_POINTER})
            dashboard_item = dashboard_resp.get("Item")
            if dashboard_item and dashboard_item.get("report_id") == report_id:
                keys_to_delete.append({"PK": _PK_DASHBOARD, "SK": _SK_DASHBOARD_POINTER})

            with table.batch_writer() as batch:
                for key in keys_to_delete:
                    batch.delete_item(Key=key)

            return True

        return await asyncio.to_thread(_op)

    async def pin_report(
        self,
        report_id: str,
        pinned: bool,
        updated_by: str,
        user_id: str | None = None,
    ) -> bool:
        """Set or clear the pinned flag on a report."""

        def _op() -> bool:
            table = _get_table()
            resp = table.get_item(Key={"PK": _report_pk(report_id), "SK": _SK_METADATA})
            meta = resp.get("Item")
            if not meta or not _report_visible_to_user(meta, user_id):
                return False
            now = datetime.now(tz=UTC).isoformat()
            table.update_item(
                Key={"PK": _report_pk(report_id), "SK": _SK_METADATA},
                UpdateExpression="SET pinned = :pinned, updated_at = :updated_at, updated_by = :updated_by",
                ExpressionAttributeValues={":pinned": pinned, ":updated_at": now, ":updated_by": updated_by},
            )
            table.update_item(
                Key={"PK": _PK_REPORT_LIST, "SK": f"REPORT#{report_id}"},
                UpdateExpression="SET pinned = :pinned, updated_at = :updated_at, updated_by = :updated_by",
                ExpressionAttributeValues={":pinned": pinned, ":updated_at": now, ":updated_by": updated_by},
            )
            return True

        return await asyncio.to_thread(_op)

    async def get_dashboard_report_id(self) -> str | None:
        """Return the report_id of the current dashboard report, or None if not set."""

        def _op() -> str | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _PK_DASHBOARD, "SK": _SK_DASHBOARD_POINTER})
            item = resp.get("Item")
            if not item:
                return None
            return item.get("report_id")

        return await asyncio.to_thread(_op)

    async def set_dashboard_report(self, report_id: str) -> bool:
        """Point the dashboard pointer at the given report."""

        def _op() -> bool:
            table = _get_table()
            resp = table.get_item(Key={"PK": _report_pk(report_id), "SK": _SK_METADATA})
            meta = resp.get("Item")
            if not meta:
                return False
            if meta["access"]["scope"] != "public":
                return False
            table.put_item(
                Item={
                    "PK": _PK_DASHBOARD,
                    "SK": _SK_DASHBOARD_POINTER,
                    "report_id": report_id,
                    "updated_at": datetime.now(tz=UTC).isoformat(),
                }
            )
            return True

        return await asyncio.to_thread(_op)

    async def get_dashboard_report(self) -> ReportVersion | None:
        """Return the latest version of the dashboard report, or None if not set."""
        report_id = await self.get_dashboard_report_id()
        if not report_id:
            return None
        report = await self.get_report_latest(report_id)
        if report and report.access.scope == "public":
            return report
        return None

    async def list_scheduled_queries(self) -> list[ScheduledQueryItem]:
        def _op() -> list[ScheduledQueryItem]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _PK_SCHEDULED_QUERY_LIST},
            )
            return [_sq_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def get_scheduled_query(self, sq_id: str) -> ScheduledQueryItem | None:
        def _op() -> ScheduledQueryItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _sq_pk(sq_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return None
            return _sq_from_item(item)

        return await asyncio.to_thread(_op)

    async def create_scheduled_query(
        self,
        name: str,
        cypher: str,
        params: list[dict[str, Any]],
        frequency: int | None,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        actions: list[dict[str, Any]],
        created_by: str,
    ) -> ScheduledQueryItem:
        sq_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        version = 1
        base = _strip_none(
            {
                "scheduled_query_id": sq_id,
                "name": name,
                "cypher": cypher,
                "params": _floats_to_decimal(params),
                "frequency": frequency,
                "schedule": _floats_to_decimal(schedule),
                "watch_scans": _floats_to_decimal(watch_scans),
                "enabled": enabled,
                "actions": _floats_to_decimal(actions),
                "current_version": version,
                "created_at": now,
                "updated_at": now,
                "created_by": created_by,
                "updated_by": created_by,
            }
        )
        metadata_item = {"PK": _sq_pk(sq_id), "SK": _SK_METADATA, **base}
        list_item = {
            "PK": _PK_SCHEDULED_QUERY_LIST,
            "SK": _sq_pk(sq_id),
            **base,
        }
        version_item = _strip_none(
            {
                "PK": _sq_pk(sq_id),
                "SK": _sq_version_sk(version),
                "scheduled_query_id": sq_id,
                "name": name,
                "version": version,
                "cypher": cypher,
                "params": _floats_to_decimal(params),
                "frequency": frequency,
                "schedule": _floats_to_decimal(schedule),
                "watch_scans": _floats_to_decimal(watch_scans),
                "enabled": enabled,
                "actions": _floats_to_decimal(actions),
                "created_at": now,
                "created_by": created_by,
                "comment": None,
            }
        )

        def _op() -> None:
            table = _get_table()
            _transact_put_sync(table, metadata_item, list_item, version_item)

        await asyncio.to_thread(_op)
        return _sq_from_item(base)

    async def update_scheduled_query(
        self,
        sq_id: str,
        name: str,
        cypher: str,
        params: list[dict[str, Any]],
        frequency: int | None,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        actions: list[dict[str, Any]],
        updated_by: str,
        comment: str | None = None,
    ) -> ScheduledQueryItem | None:
        def _op() -> ScheduledQueryItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _sq_pk(sq_id), "SK": _SK_METADATA})
            if not resp.get("Item"):
                return None
            existing = resp["Item"]
            current_version = int(existing.get("current_version", 0))
            version = current_version + 1
            now = datetime.now(tz=UTC).isoformat()
            base = _strip_none(
                {
                    "scheduled_query_id": sq_id,
                    "name": name,
                    "cypher": cypher,
                    "params": _floats_to_decimal(params),
                    "frequency": frequency,
                    "schedule": _floats_to_decimal(schedule),
                    "watch_scans": _floats_to_decimal(watch_scans),
                    "enabled": enabled,
                    "actions": _floats_to_decimal(actions),
                    "current_version": version,
                    "created_at": existing["created_at"],
                    "updated_at": now,
                    "created_by": existing["created_by"],
                    "updated_by": updated_by,
                }
            )
            metadata_item = {"PK": _sq_pk(sq_id), "SK": _SK_METADATA, **base}
            list_item = {
                "PK": _PK_SCHEDULED_QUERY_LIST,
                "SK": _sq_pk(sq_id),
                **base,
            }
            version_item = _strip_none(
                {
                    "PK": _sq_pk(sq_id),
                    "SK": _sq_version_sk(version),
                    "scheduled_query_id": sq_id,
                    "name": name,
                    "version": version,
                    "cypher": cypher,
                    "params": _floats_to_decimal(params),
                    "frequency": frequency,
                    "schedule": _floats_to_decimal(schedule),
                    "watch_scans": _floats_to_decimal(watch_scans),
                    "enabled": enabled,
                    "actions": _floats_to_decimal(actions),
                    "created_at": now,
                    "created_by": updated_by,
                    "comment": comment,
                }
            )
            _transact_put_sync(table, metadata_item, list_item, version_item)
            return _sq_from_item(base)

        return await asyncio.to_thread(_op)

    async def list_scheduled_query_versions(self, sq_id: str) -> list[ScheduledQueryVersion]:
        def _op() -> list[ScheduledQueryVersion]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": _sq_pk(sq_id),
                    ":prefix": _SK_VERSION_PREFIX,
                },
                ScanIndexForward=False,
            )
            return [_sq_version_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def get_scheduled_query_version(self, sq_id: str, version: int) -> ScheduledQueryVersion | None:
        def _op() -> ScheduledQueryVersion | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _sq_pk(sq_id), "SK": _sq_version_sk(version)})
            item = resp.get("Item")
            if not item:
                return None
            return _sq_version_from_item(item)

        return await asyncio.to_thread(_op)

    async def delete_scheduled_query(self, sq_id: str) -> bool:
        def _op() -> bool:
            table = _get_table()
            resp = table.get_item(Key={"PK": _sq_pk(sq_id), "SK": _SK_METADATA})
            if not resp.get("Item"):
                return False
            items_resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _sq_pk(sq_id)},
                ProjectionExpression="PK, SK",
            )
            keys_to_delete = [{"PK": item["PK"], "SK": item["SK"]} for item in items_resp.get("Items", [])]
            keys_to_delete.append({"PK": _PK_SCHEDULED_QUERY_LIST, "SK": _sq_pk(sq_id)})
            with table.batch_writer() as batch:
                for key in keys_to_delete:
                    batch.delete_item(Key=key)
            return True

        return await asyncio.to_thread(_op)

    async def acquire_scheduled_query_lock(self, sq_id: str, expected_last_scheduled_at: str | None) -> bool:
        def _op() -> bool:
            table = _get_table()
            now = datetime.now(tz=UTC).isoformat()
            update_expr = "SET last_scheduled_at = :new_val"
            expr_values: dict[str, Any] = {":new_val": now}

            if expected_last_scheduled_at is None:
                condition = "attribute_not_exists(last_scheduled_at)"
            else:
                condition = "last_scheduled_at = :expected"
                expr_values[":expected"] = expected_last_scheduled_at

            try:
                table.update_item(
                    Key={"PK": _sq_pk(sq_id), "SK": _SK_METADATA},
                    UpdateExpression=update_expr,
                    ExpressionAttributeValues=expr_values,
                    ConditionExpression=condition,
                )
            except botocore.exceptions.ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    return False
                raise
            # Update the list item (best-effort, no condition needed)
            table.update_item(
                Key={"PK": _PK_SCHEDULED_QUERY_LIST, "SK": _sq_pk(sq_id)},
                UpdateExpression=update_expr,
                ExpressionAttributeValues={":new_val": now},
            )
            return True

        return await asyncio.to_thread(_op)

    async def record_scheduled_query_result(self, sq_id: str, status: str, error: str | None = None) -> None:
        def _op() -> None:
            table = _get_table()
            now = datetime.now(tz=UTC).isoformat()

            # Read current metadata to get existing last_errors
            resp = table.get_item(Key={"PK": _sq_pk(sq_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return

            if status == "failure" and error:
                errors = list(item.get("last_errors", []))
                errors.insert(0, {"timestamp": now, "error": error})
                errors = errors[:5]
            elif status == "success":
                errors = []
            else:
                errors = list(item.get("last_errors", []))

            update_expr = "SET last_run_status = :status, last_run_at = :now, last_errors = :errors"
            expr_values = {":status": status, ":now": now, ":errors": errors}

            # Update both metadata and list items
            table.update_item(
                Key={"PK": _sq_pk(sq_id), "SK": _SK_METADATA},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
            )
            table.update_item(
                Key={"PK": _PK_SCHEDULED_QUERY_LIST, "SK": _sq_pk(sq_id)},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
            )

        await asyncio.to_thread(_op)

    async def request_scheduled_query_run(self, sq_id: str) -> str | None:
        def _op() -> str | None:
            table = _get_table()
            now = datetime.now(tz=UTC).isoformat()
            try:
                table.update_item(
                    Key={"PK": _sq_pk(sq_id), "SK": _SK_METADATA},
                    UpdateExpression="SET run_requested_at = :now",
                    ExpressionAttributeValues={":now": now},
                    ConditionExpression="attribute_exists(PK)",
                )
            except botocore.exceptions.ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    return None
                raise
            # Update the list item too; the worker reads from the list.
            table.update_item(
                Key={"PK": _PK_SCHEDULED_QUERY_LIST, "SK": _sq_pk(sq_id)},
                UpdateExpression="SET run_requested_at = :now",
                ExpressionAttributeValues={":now": now},
            )
            return now

        return await asyncio.to_thread(_op)

    async def list_scheduled_chats(self, user_id: str | None = None) -> list[ScheduledChatItem]:
        def _op() -> list[ScheduledChatItem]:
            table = _get_table()
            items: list[ScheduledChatItem] = []
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": "PK = :pk",
                "ExpressionAttributeValues": {":pk": _PK_SCHEDULED_CHAT_LIST},
            }
            while True:
                resp = table.query(**kwargs)
                items.extend(_scheduled_chat_from_item(item) for item in resp.get("Items", []))
                last_key = resp.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key
            if user_id is not None:
                items = [item for item in items if item.created_by == user_id]
            return items

        return await asyncio.to_thread(_op)

    async def get_scheduled_chat(self, sc_id: str) -> ScheduledChatItem | None:
        def _op() -> ScheduledChatItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _scheduled_chat_pk(sc_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return None
            return _scheduled_chat_from_item(item)

        return await asyncio.to_thread(_op)

    async def create_scheduled_chat(
        self,
        name: str,
        prompt: str,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        created_by: str,
    ) -> ScheduledChatItem:
        sc_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        version = 1
        base = _strip_none(
            {
                "scheduled_chat_id": sc_id,
                "name": name,
                "prompt": prompt,
                "schedule": _floats_to_decimal(schedule) if schedule else None,
                "watch_scans": _floats_to_decimal(watch_scans),
                "enabled": enabled,
                "current_version": version,
                "created_at": now,
                "updated_at": now,
                "created_by": created_by,
                "updated_by": created_by,
            }
        )
        version_item = _strip_none(
            {
                "PK": _scheduled_chat_pk(sc_id),
                "SK": _sq_version_sk(version),
                "scheduled_chat_id": sc_id,
                "version": version,
                "name": name,
                "prompt": prompt,
                "schedule": _floats_to_decimal(schedule) if schedule else None,
                "watch_scans": _floats_to_decimal(watch_scans),
                "enabled": enabled,
                "created_at": now,
                "created_by": created_by,
            }
        )

        def _op() -> ScheduledChatItem:
            table = _get_table()
            table.put_item(Item={"PK": _scheduled_chat_pk(sc_id), "SK": _SK_METADATA, **base})
            table.put_item(Item={"PK": _PK_SCHEDULED_CHAT_LIST, "SK": _scheduled_chat_pk(sc_id), **base})
            table.put_item(Item=version_item)
            return _scheduled_chat_from_item(base)

        return await asyncio.to_thread(_op)

    async def update_scheduled_chat(
        self,
        sc_id: str,
        name: str,
        prompt: str,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> ScheduledChatItem | None:
        now = datetime.now(tz=UTC).isoformat()

        def _op() -> ScheduledChatItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _scheduled_chat_pk(sc_id), "SK": _SK_METADATA})
            existing = resp.get("Item")
            if not existing:
                return None
            version = int(existing.get("current_version", 0)) + 1
            base = _strip_none(
                {
                    **{k: v for k, v in existing.items() if k not in ("PK", "SK")},
                    "name": name,
                    "prompt": prompt,
                    "schedule": _floats_to_decimal(schedule) if schedule else None,
                    "watch_scans": _floats_to_decimal(watch_scans),
                    "enabled": enabled,
                    "current_version": version,
                    "updated_at": now,
                    "updated_by": updated_by,
                }
            )
            if schedule is None:
                base.pop("schedule", None)
            version_item = _strip_none(
                {
                    "PK": _scheduled_chat_pk(sc_id),
                    "SK": _sq_version_sk(version),
                    "scheduled_chat_id": sc_id,
                    "version": version,
                    "name": name,
                    "prompt": prompt,
                    "schedule": _floats_to_decimal(schedule) if schedule else None,
                    "watch_scans": _floats_to_decimal(watch_scans),
                    "enabled": enabled,
                    "created_at": now,
                    "created_by": updated_by,
                    "comment": comment,
                }
            )
            table.put_item(Item={"PK": _scheduled_chat_pk(sc_id), "SK": _SK_METADATA, **base})
            table.put_item(Item={"PK": _PK_SCHEDULED_CHAT_LIST, "SK": _scheduled_chat_pk(sc_id), **base})
            table.put_item(Item=version_item)
            return _scheduled_chat_from_item(base)

        return await asyncio.to_thread(_op)

    async def list_scheduled_chat_versions(self, sc_id: str) -> list[ScheduledChatVersion]:
        def _op() -> list[ScheduledChatVersion]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={":pk": _scheduled_chat_pk(sc_id), ":prefix": _SK_VERSION_PREFIX},
                ScanIndexForward=False,
            )
            return [_scheduled_chat_version_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def get_scheduled_chat_version(self, sc_id: str, version: int) -> ScheduledChatVersion | None:
        def _op() -> ScheduledChatVersion | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _scheduled_chat_pk(sc_id), "SK": _sq_version_sk(version)})
            item = resp.get("Item")
            if not item:
                return None
            return _scheduled_chat_version_from_item(item)

        return await asyncio.to_thread(_op)

    async def delete_scheduled_chat(self, sc_id: str) -> bool:
        def _op() -> bool:
            table = _get_table()
            resp = table.get_item(Key={"PK": _scheduled_chat_pk(sc_id), "SK": _SK_METADATA})
            metadata = resp.get("Item")
            if not metadata:
                return False
            keys_to_delete: list[dict[str, str]] = []
            schedule_kwargs: dict[str, Any] = {
                "KeyConditionExpression": "PK = :pk",
                "ExpressionAttributeValues": {":pk": _scheduled_chat_pk(sc_id)},
                "ProjectionExpression": "PK, SK",
            }
            while True:
                items_resp = table.query(**schedule_kwargs)
                keys_to_delete.extend({"PK": item["PK"], "SK": item["SK"]} for item in items_resp.get("Items", []))
                last_key = items_resp.get("LastEvaluatedKey")
                if not last_key:
                    break
                schedule_kwargs["ExclusiveStartKey"] = last_key

            owner_id = metadata["created_by"]
            session_kwargs: dict[str, Any] = {
                "KeyConditionExpression": "PK = :pk",
                "FilterExpression": "scheduled_chat_id = :sc_id",
                "ExpressionAttributeValues": {
                    ":pk": _chat_session_list_pk(owner_id),
                    ":sc_id": sc_id,
                },
                "ProjectionExpression": "PK, SK, thread_id",
            }
            while True:
                sessions_resp = table.query(**session_kwargs)
                for item in sessions_resp.get("Items", []):
                    keys_to_delete.append({"PK": item["PK"], "SK": item["SK"]})
                    keys_to_delete.append(
                        {
                            "PK": _chat_session_metadata_pk(owner_id),
                            "SK": item["thread_id"],
                        }
                    )
                last_key = sessions_resp.get("LastEvaluatedKey")
                if not last_key:
                    break
                session_kwargs["ExclusiveStartKey"] = last_key

            keys_to_delete.append({"PK": _PK_SCHEDULED_CHAT_LIST, "SK": _scheduled_chat_pk(sc_id)})
            with table.batch_writer() as batch:
                for key in keys_to_delete:
                    batch.delete_item(Key=key)
            return True

        return await asyncio.to_thread(_op)

    async def acquire_scheduled_chat_lock(self, sc_id: str, expected_last_scheduled_at: str | None) -> bool:
        def _op() -> bool:
            table = _get_table()
            now = datetime.now(tz=UTC).isoformat()
            update_expr = "SET last_scheduled_at = :new_val"
            expr_values: dict[str, Any] = {":new_val": now}

            if expected_last_scheduled_at is None:
                condition = "attribute_not_exists(last_scheduled_at)"
            else:
                condition = "last_scheduled_at = :expected"
                expr_values[":expected"] = expected_last_scheduled_at

            try:
                table.update_item(
                    Key={"PK": _scheduled_chat_pk(sc_id), "SK": _SK_METADATA},
                    UpdateExpression=update_expr,
                    ExpressionAttributeValues=expr_values,
                    ConditionExpression=condition,
                )
            except botocore.exceptions.ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    return False
                raise
            table.update_item(
                Key={"PK": _PK_SCHEDULED_CHAT_LIST, "SK": _scheduled_chat_pk(sc_id)},
                UpdateExpression=update_expr,
                ExpressionAttributeValues={":new_val": now},
            )
            return True

        return await asyncio.to_thread(_op)

    async def record_scheduled_chat_result(self, sc_id: str, status: str, error: str | None = None) -> None:
        def _op() -> None:
            table = _get_table()
            now = datetime.now(tz=UTC).isoformat()

            resp = table.get_item(Key={"PK": _scheduled_chat_pk(sc_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return

            if status == "failure" and error:
                errors = list(item.get("last_errors", []))
                errors.insert(0, {"timestamp": now, "error": error})
                errors = errors[:5]
            elif status in {"success", "partial", "budget_exhausted"}:
                errors = []
            else:
                errors = list(item.get("last_errors", []))

            update_expr = "SET last_run_status = :status, last_run_at = :now, last_errors = :errors"
            expr_values = {":status": status, ":now": now, ":errors": errors}

            table.update_item(
                Key={"PK": _scheduled_chat_pk(sc_id), "SK": _SK_METADATA},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
            )
            table.update_item(
                Key={"PK": _PK_SCHEDULED_CHAT_LIST, "SK": _scheduled_chat_pk(sc_id)},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
            )

        await asyncio.to_thread(_op)

    async def request_scheduled_chat_run(self, sc_id: str) -> str | None:
        def _op() -> str | None:
            table = _get_table()
            now = datetime.now(tz=UTC).isoformat()
            try:
                table.update_item(
                    Key={"PK": _scheduled_chat_pk(sc_id), "SK": _SK_METADATA},
                    UpdateExpression="SET run_requested_at = :now",
                    ExpressionAttributeValues={":now": now},
                    ConditionExpression="attribute_exists(PK)",
                )
            except botocore.exceptions.ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    return None
                raise
            # Update the list item too; the worker reads from the list.
            table.update_item(
                Key={"PK": _PK_SCHEDULED_CHAT_LIST, "SK": _scheduled_chat_pk(sc_id)},
                UpdateExpression="SET run_requested_at = :now",
                ExpressionAttributeValues={":now": now},
            )
            return now

        return await asyncio.to_thread(_op)

    async def get_or_create_user(
        self,
        sub: str,
        iss: str,
        email: str | None = None,
        display_name: str | None = None,
        preferred_username: str | None = None,
        role: str | None = None,
    ) -> User:
        """Get an existing user by (iss, sub), or create one on first login."""

        def _op() -> User:
            table = _get_table()
            now = datetime.now(tz=UTC).isoformat()
            lookup_sk = _user_lookup_sk(iss, sub)

            lookup_resp = table.get_item(
                Key={"PK": _PK_USER_LOOKUP, "SK": lookup_sk},
            )
            lookup_item = lookup_resp.get("Item")

            if lookup_item:
                user_id = lookup_item["user_id"]
                profile_resp = table.get_item(
                    Key={"PK": _user_pk(user_id), "SK": _SK_METADATA},
                )
                user = _user_from_item(profile_resp["Item"])
                if user.role != role:
                    user = _sync_user_role(table, user_id, role)
                return user

            user_id = generate_report_id()
            profile_item = _strip_none(
                {
                    "PK": _user_pk(user_id),
                    "SK": _SK_METADATA,
                    "user_id": user_id,
                    "sub": sub,
                    "iss": iss,
                    "email": email,
                    "display_name": display_name,
                    "preferred_username": preferred_username,
                    "created_at": now,
                    "last_login": now,
                    "archived_at": None,
                    "role": role,
                }
            )
            new_lookup_item = {
                "PK": _PK_USER_LOOKUP,
                "SK": lookup_sk,
                "user_id": user_id,
            }

            try:
                table.put_item(
                    Item=new_lookup_item,
                    ConditionExpression="attribute_not_exists(PK)",
                )
                table.put_item(Item=profile_item)
            except table.meta.client.exceptions.ConditionalCheckFailedException:
                lookup_resp2 = table.get_item(
                    Key={"PK": _PK_USER_LOOKUP, "SK": lookup_sk},
                )
                user_id = lookup_resp2["Item"]["user_id"]
                profile_resp = table.get_item(
                    Key={"PK": _user_pk(user_id), "SK": _SK_METADATA},
                )
                return _user_from_item(profile_resp["Item"])

            return User(
                user_id=user_id,
                sub=sub,
                iss=iss,
                email=email,
                display_name=display_name,
                preferred_username=preferred_username,
                created_at=now,
                last_login=now,
                archived_at=None,
                role=role,
            )

        return await asyncio.to_thread(_op)

    async def update_user_profile(
        self,
        user_id: str,
        email: str | None = None,
        display_name: str | None = None,
        preferred_username: str | None = None,
        token_iat: datetime | None = None,
    ) -> User:
        """Sync mutable profile fields, writing only what has changed."""

        def _op() -> User:
            table = _get_table()
            profile_resp = table.get_item(
                Key={"PK": _user_pk(user_id), "SK": _SK_METADATA},
            )
            item = profile_resp.get("Item")
            if not item:
                raise ValueError(f"User {user_id!r} not found")
            stored_user = _user_from_item(item)

            email_changed = email is not None and stored_user.email != email
            name_changed = display_name is not None and stored_user.display_name != display_name
            username_changed = preferred_username is not None and stored_user.preferred_username != preferred_username
            iat_newer = token_iat is not None and token_iat > datetime.fromisoformat(stored_user.last_login)

            if not (email_changed or name_changed or username_changed or iat_newer):
                return stored_user

            return _update_user_profile_internal(
                table,
                user_id,
                email if email_changed else None,
                display_name if name_changed else None,
                preferred_username if username_changed else None,
                token_iat if iat_newer else None,
            )

        return await asyncio.to_thread(_op)

    async def get_user(self, user_id: str) -> User | None:
        """Return a user by their internal user_id, or None if not found."""

        def _op() -> User | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _user_pk(user_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return None
            return _user_from_item(item)

        return await asyncio.to_thread(_op)

    async def archive_user(self, user_id: str) -> bool:
        """Soft-delete a user by setting archived_at."""

        def _op() -> bool:
            table = _get_table()
            resp = table.get_item(Key={"PK": _user_pk(user_id), "SK": _SK_METADATA})
            if not resp.get("Item"):
                return False
            now = datetime.now(tz=UTC).isoformat()
            table.update_item(
                Key={"PK": _user_pk(user_id), "SK": _SK_METADATA},
                UpdateExpression="SET archived_at = :t",
                ExpressionAttributeValues={":t": now},
            )
            return True

        return await asyncio.to_thread(_op)

    # ------------------------------------------------------------------
    # Toolsets
    # ------------------------------------------------------------------

    async def list_toolsets(self) -> list[ToolsetListItem]:
        def _op() -> list[ToolsetListItem]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _PK_TOOLSET_LIST},
            )
            return [_toolset_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def get_toolset(self, toolset_id: str) -> ToolsetListItem | None:
        def _op() -> ToolsetListItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _toolset_pk(toolset_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return None
            return _toolset_from_item(item)

        return await asyncio.to_thread(_op)

    async def create_toolset(
        self,
        toolset_id: str,
        name: str,
        description: str,
        enabled: bool,
        created_by: str,
    ) -> ToolsetListItem:
        now = datetime.now(tz=UTC).isoformat()
        version = 1
        base = _strip_none(
            {
                "toolset_id": toolset_id,
                "name": name,
                "description": description,
                "enabled": enabled,
                "current_version": version,
                "created_at": now,
                "updated_at": now,
                "created_by": created_by,
                "updated_by": created_by,
            }
        )
        metadata_item = {"PK": _toolset_pk(toolset_id), "SK": _SK_METADATA, **base}
        list_item = {
            "PK": _PK_TOOLSET_LIST,
            "SK": _toolset_list_sk(toolset_id),
            **base,
        }
        version_item = _strip_none(
            {
                "PK": _toolset_pk(toolset_id),
                "SK": _version_sk(version),
                "toolset_id": toolset_id,
                "name": name,
                "description": description,
                "enabled": enabled,
                "version": version,
                "created_at": now,
                "created_by": created_by,
                "comment": None,
            }
        )

        def _op() -> None:
            table = _get_table()
            _transact_put_sync(table, metadata_item, list_item, version_item)

        await asyncio.to_thread(_op)
        return _toolset_from_item(base)

    async def update_toolset(
        self,
        toolset_id: str,
        name: str,
        description: str,
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> ToolsetListItem | None:
        def _op() -> ToolsetListItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _toolset_pk(toolset_id), "SK": _SK_METADATA})
            if not resp.get("Item"):
                return None
            existing = resp["Item"]
            current_version = int(existing.get("current_version", 0))
            version = current_version + 1
            now = datetime.now(tz=UTC).isoformat()
            base = _strip_none(
                {
                    "toolset_id": toolset_id,
                    "name": name,
                    "description": description,
                    "enabled": enabled,
                    "current_version": version,
                    "created_at": existing["created_at"],
                    "updated_at": now,
                    "created_by": existing["created_by"],
                    "updated_by": updated_by,
                }
            )
            metadata_item = {
                "PK": _toolset_pk(toolset_id),
                "SK": _SK_METADATA,
                **base,
            }
            list_item = {
                "PK": _PK_TOOLSET_LIST,
                "SK": _toolset_list_sk(toolset_id),
                **base,
            }
            version_item = _strip_none(
                {
                    "PK": _toolset_pk(toolset_id),
                    "SK": _version_sk(version),
                    "toolset_id": toolset_id,
                    "name": name,
                    "description": description,
                    "enabled": enabled,
                    "version": version,
                    "created_at": now,
                    "created_by": updated_by,
                    "comment": comment,
                }
            )
            _transact_put_sync(table, metadata_item, list_item, version_item)
            return _toolset_from_item(base)

        return await asyncio.to_thread(_op)

    async def delete_toolset(self, toolset_id: str) -> bool:
        def _op() -> bool:
            table = _get_table()
            resp = table.get_item(Key={"PK": _toolset_pk(toolset_id), "SK": _SK_METADATA})
            if not resp.get("Item"):
                return False

            # Find and delete all tools in this toolset first
            tool_list_resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _tool_list_pk(toolset_id)},
                ProjectionExpression="SK",
            )
            tool_ids = [item["SK"].replace("TOOL#", "") for item in tool_list_resp.get("Items", [])]
            for tool_id in tool_ids:
                tool_items_resp = table.query(
                    KeyConditionExpression="PK = :pk",
                    ExpressionAttributeValues={":pk": _tool_pk(tool_id)},
                    ProjectionExpression="PK, SK",
                )
                keys_to_delete = [{"PK": i["PK"], "SK": i["SK"]} for i in tool_items_resp.get("Items", [])]
                with table.batch_writer() as batch:
                    for key in keys_to_delete:
                        batch.delete_item(Key=key)

            # Delete the tool list partition for this toolset
            tool_list_keys = [{"PK": _tool_list_pk(toolset_id), "SK": _tool_list_sk(tool_id)} for tool_id in tool_ids]

            # Delete all toolset items
            items_resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _toolset_pk(toolset_id)},
                ProjectionExpression="PK, SK",
            )
            keys_to_delete = [{"PK": item["PK"], "SK": item["SK"]} for item in items_resp.get("Items", [])]
            keys_to_delete.append({"PK": _PK_TOOLSET_LIST, "SK": _toolset_list_sk(toolset_id)})
            keys_to_delete.extend(tool_list_keys)

            with table.batch_writer() as batch:
                for key in keys_to_delete:
                    batch.delete_item(Key=key)

            return True

        return await asyncio.to_thread(_op)

    async def list_toolset_versions(self, toolset_id: str) -> list[ToolsetVersion]:
        def _op() -> list[ToolsetVersion]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": _toolset_pk(toolset_id),
                    ":prefix": _SK_VERSION_PREFIX,
                },
                ScanIndexForward=False,
            )
            return [_toolset_version_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def get_toolset_version(self, toolset_id: str, version: int) -> ToolsetVersion | None:
        def _op() -> ToolsetVersion | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _toolset_pk(toolset_id), "SK": _version_sk(version)})
            item = resp.get("Item")
            if not item:
                return None
            return _toolset_version_from_item(item)

        return await asyncio.to_thread(_op)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    async def list_tools(self, toolset_id: str) -> list[ToolItem]:
        def _op() -> list[ToolItem]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _tool_list_pk(toolset_id)},
            )
            tool_ids = [item["SK"].replace("TOOL#", "") for item in resp.get("Items", [])]
            tools = []
            for tool_id in tool_ids:
                tool_resp = table.get_item(Key={"PK": _tool_pk(tool_id), "SK": _SK_METADATA})
                item = tool_resp.get("Item")
                if item:
                    tools.append(_tool_from_item(item))
            return tools

        return await asyncio.to_thread(_op)

    async def get_tool(self, tool_id: str) -> ToolItem | None:
        def _op() -> ToolItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _tool_pk(tool_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return None
            return _tool_from_item(item)

        return await asyncio.to_thread(_op)

    async def create_tool(
        self,
        toolset_id: str,
        tool_id: str,
        name: str,
        description: str,
        cypher: str,
        parameters: list[dict[str, Any]],
        enabled: bool,
        created_by: str,
    ) -> ToolItem | None:
        def _op() -> ToolItem | None:
            table = _get_table()
            # Verify toolset exists
            ts_resp = table.get_item(Key={"PK": _toolset_pk(toolset_id), "SK": _SK_METADATA})
            if not ts_resp.get("Item"):
                return None

            now = datetime.now(tz=UTC).isoformat()
            version = 1
            base = _strip_none(
                {
                    "tool_id": tool_id,
                    "toolset_id": toolset_id,
                    "name": name,
                    "description": description,
                    "cypher": cypher,
                    "parameters": _floats_to_decimal(parameters),
                    "enabled": enabled,
                    "current_version": version,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": created_by,
                    "updated_by": created_by,
                }
            )
            metadata_item = {"PK": _tool_pk(tool_id), "SK": _SK_METADATA, **base}
            list_item = {
                "PK": _tool_list_pk(toolset_id),
                "SK": _tool_list_sk(tool_id),
                **base,
            }
            version_item = _strip_none(
                {
                    "PK": _tool_pk(tool_id),
                    "SK": _version_sk(version),
                    "tool_id": tool_id,
                    "toolset_id": toolset_id,
                    "name": name,
                    "description": description,
                    "cypher": cypher,
                    "parameters": _floats_to_decimal(parameters),
                    "enabled": enabled,
                    "version": version,
                    "created_at": now,
                    "created_by": created_by,
                    "comment": None,
                }
            )
            _transact_put_sync(table, metadata_item, list_item, version_item)
            return _tool_from_item(base)

        return await asyncio.to_thread(_op)

    async def update_tool(
        self,
        tool_id: str,
        name: str,
        description: str,
        cypher: str,
        parameters: list[dict[str, Any]],
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> ToolItem | None:
        def _op() -> ToolItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _tool_pk(tool_id), "SK": _SK_METADATA})
            if not resp.get("Item"):
                return None
            existing = resp["Item"]
            toolset_id = existing["toolset_id"]
            current_version = int(existing.get("current_version", 0))
            version = current_version + 1
            now = datetime.now(tz=UTC).isoformat()
            base = _strip_none(
                {
                    "tool_id": tool_id,
                    "toolset_id": toolset_id,
                    "name": name,
                    "description": description,
                    "cypher": cypher,
                    "parameters": _floats_to_decimal(parameters),
                    "enabled": enabled,
                    "current_version": version,
                    "created_at": existing["created_at"],
                    "updated_at": now,
                    "created_by": existing["created_by"],
                    "updated_by": updated_by,
                }
            )
            metadata_item = {"PK": _tool_pk(tool_id), "SK": _SK_METADATA, **base}
            list_item = {
                "PK": _tool_list_pk(toolset_id),
                "SK": _tool_list_sk(tool_id),
                **base,
            }
            version_item = _strip_none(
                {
                    "PK": _tool_pk(tool_id),
                    "SK": _version_sk(version),
                    "tool_id": tool_id,
                    "toolset_id": toolset_id,
                    "name": name,
                    "description": description,
                    "cypher": cypher,
                    "parameters": _floats_to_decimal(parameters),
                    "enabled": enabled,
                    "version": version,
                    "created_at": now,
                    "created_by": updated_by,
                    "comment": comment,
                }
            )
            _transact_put_sync(table, metadata_item, list_item, version_item)
            return _tool_from_item(base)

        return await asyncio.to_thread(_op)

    async def delete_tool(self, tool_id: str) -> bool:
        def _op() -> bool:
            table = _get_table()
            resp = table.get_item(Key={"PK": _tool_pk(tool_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return False
            toolset_id = item["toolset_id"]
            items_resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _tool_pk(tool_id)},
                ProjectionExpression="PK, SK",
            )
            keys_to_delete = [{"PK": i["PK"], "SK": i["SK"]} for i in items_resp.get("Items", [])]
            keys_to_delete.append({"PK": _tool_list_pk(toolset_id), "SK": _tool_list_sk(tool_id)})
            with table.batch_writer() as batch:
                for key in keys_to_delete:
                    batch.delete_item(Key=key)
            return True

        return await asyncio.to_thread(_op)

    async def list_tool_versions(self, tool_id: str) -> list[ToolVersion]:
        def _op() -> list[ToolVersion]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": _tool_pk(tool_id),
                    ":prefix": _SK_VERSION_PREFIX,
                },
                ScanIndexForward=False,
            )
            return [_tool_version_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def get_tool_version(self, tool_id: str, version: int) -> ToolVersion | None:
        def _op() -> ToolVersion | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _tool_pk(tool_id), "SK": _version_sk(version)})
            item = resp.get("Item")
            if not item:
                return None
            return _tool_version_from_item(item)

        return await asyncio.to_thread(_op)

    async def list_enabled_tools(self) -> list[ToolItem]:
        def _op() -> list[ToolItem]:
            table = _get_table()
            ts_resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _PK_TOOLSET_LIST},
            )
            enabled_toolset_ids = [item["toolset_id"] for item in ts_resp.get("Items", []) if item.get("enabled", True)]
            tools = []
            for toolset_id in enabled_toolset_ids:
                tool_list_resp = table.query(
                    KeyConditionExpression="PK = :pk",
                    ExpressionAttributeValues={":pk": _tool_list_pk(toolset_id)},
                )
                for list_item in tool_list_resp.get("Items", []):
                    if list_item.get("enabled", True):
                        tool_resp = table.get_item(
                            Key={
                                "PK": _tool_pk(list_item["tool_id"]),
                                "SK": _SK_METADATA,
                            }
                        )
                        item = tool_resp.get("Item")
                        if item and item.get("enabled", True):
                            tools.append(_tool_from_item(item))
            return tools

        return await asyncio.to_thread(_op)

    async def get_enabled_tool(self, toolset_id: str, tool_id: str) -> ToolItem | None:
        def _op() -> ToolItem | None:
            table = _get_table()
            tool_resp = table.get_item(Key={"PK": _tool_pk(tool_id), "SK": _SK_METADATA})
            tool_item = tool_resp.get("Item")
            if not tool_item or tool_item.get("toolset_id") != toolset_id or not tool_item.get("enabled", True):
                return None

            toolset_resp = table.get_item(Key={"PK": _toolset_pk(toolset_id), "SK": _SK_METADATA})
            toolset_item = toolset_resp.get("Item")
            if not toolset_item or not toolset_item.get("enabled", True):
                return None
            return _tool_from_item(tool_item)

        return await asyncio.to_thread(_op)

    # ------------------------------------------------------------------
    # Skillsets
    # ------------------------------------------------------------------

    async def list_skillsets(self) -> list[SkillsetListItem]:
        def _op() -> list[SkillsetListItem]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _PK_SKILLSET_LIST},
            )
            return [_skillset_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def get_skillset(self, skillset_id: str) -> SkillsetListItem | None:
        def _op() -> SkillsetListItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _skillset_pk(skillset_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return None
            return _skillset_from_item(item)

        return await asyncio.to_thread(_op)

    async def create_skillset(
        self,
        skillset_id: str,
        name: str,
        description: str,
        enabled: bool,
        created_by: str,
    ) -> SkillsetListItem:
        now = datetime.now(tz=UTC).isoformat()
        version = 1
        base = _strip_none(
            {
                "skillset_id": skillset_id,
                "name": name,
                "description": description,
                "enabled": enabled,
                "current_version": version,
                "created_at": now,
                "updated_at": now,
                "created_by": created_by,
                "updated_by": created_by,
            }
        )
        metadata_item = {"PK": _skillset_pk(skillset_id), "SK": _SK_METADATA, **base}
        list_item = {
            "PK": _PK_SKILLSET_LIST,
            "SK": _skillset_list_sk(skillset_id),
            **base,
        }
        version_item = _strip_none(
            {
                "PK": _skillset_pk(skillset_id),
                "SK": _version_sk(version),
                "skillset_id": skillset_id,
                "name": name,
                "description": description,
                "enabled": enabled,
                "version": version,
                "created_at": now,
                "created_by": created_by,
                "comment": None,
            }
        )

        def _op() -> None:
            table = _get_table()
            _transact_put_sync(table, metadata_item, list_item, version_item)

        await asyncio.to_thread(_op)
        return _skillset_from_item(base)

    async def update_skillset(
        self,
        skillset_id: str,
        name: str,
        description: str,
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> SkillsetListItem | None:
        def _op() -> SkillsetListItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _skillset_pk(skillset_id), "SK": _SK_METADATA})
            if not resp.get("Item"):
                return None
            existing = resp["Item"]
            current_version = int(existing.get("current_version", 0))
            version = current_version + 1
            now = datetime.now(tz=UTC).isoformat()
            base = _strip_none(
                {
                    "skillset_id": skillset_id,
                    "name": name,
                    "description": description,
                    "enabled": enabled,
                    "current_version": version,
                    "created_at": existing["created_at"],
                    "updated_at": now,
                    "created_by": existing["created_by"],
                    "updated_by": updated_by,
                }
            )
            metadata_item = {"PK": _skillset_pk(skillset_id), "SK": _SK_METADATA, **base}
            list_item = {"PK": _PK_SKILLSET_LIST, "SK": _skillset_list_sk(skillset_id), **base}
            version_item = _strip_none(
                {
                    "PK": _skillset_pk(skillset_id),
                    "SK": _version_sk(version),
                    "skillset_id": skillset_id,
                    "name": name,
                    "description": description,
                    "enabled": enabled,
                    "version": version,
                    "created_at": now,
                    "created_by": updated_by,
                    "comment": comment,
                }
            )
            _transact_put_sync(table, metadata_item, list_item, version_item)
            return _skillset_from_item(base)

        return await asyncio.to_thread(_op)

    async def delete_skillset(self, skillset_id: str) -> bool:
        def _op() -> bool:
            table = _get_table()
            resp = table.get_item(Key={"PK": _skillset_pk(skillset_id), "SK": _SK_METADATA})
            if not resp.get("Item"):
                return False

            skill_list_resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _skill_list_pk(skillset_id)},
                ProjectionExpression="SK",
            )
            skill_ids = [item["SK"].replace("SKILL#", "") for item in skill_list_resp.get("Items", [])]
            for skill_id in skill_ids:
                skill_items_resp = table.query(
                    KeyConditionExpression="PK = :pk",
                    ExpressionAttributeValues={":pk": _skill_pk(skill_id)},
                    ProjectionExpression="PK, SK",
                )
                keys_to_delete = [{"PK": i["PK"], "SK": i["SK"]} for i in skill_items_resp.get("Items", [])]
                with table.batch_writer() as batch:
                    for key in keys_to_delete:
                        batch.delete_item(Key=key)

            skill_list_keys = [
                {"PK": _skill_list_pk(skillset_id), "SK": _skill_list_sk(skill_id)} for skill_id in skill_ids
            ]
            items_resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _skillset_pk(skillset_id)},
                ProjectionExpression="PK, SK",
            )
            keys_to_delete = [{"PK": item["PK"], "SK": item["SK"]} for item in items_resp.get("Items", [])]
            keys_to_delete.append({"PK": _PK_SKILLSET_LIST, "SK": _skillset_list_sk(skillset_id)})
            keys_to_delete.extend(skill_list_keys)

            with table.batch_writer() as batch:
                for key in keys_to_delete:
                    batch.delete_item(Key=key)

            return True

        return await asyncio.to_thread(_op)

    async def list_skillset_versions(self, skillset_id: str) -> list[SkillsetVersion]:
        def _op() -> list[SkillsetVersion]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": _skillset_pk(skillset_id),
                    ":prefix": _SK_VERSION_PREFIX,
                },
                ScanIndexForward=False,
            )
            return [_skillset_version_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def get_skillset_version(self, skillset_id: str, version: int) -> SkillsetVersion | None:
        def _op() -> SkillsetVersion | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _skillset_pk(skillset_id), "SK": _version_sk(version)})
            item = resp.get("Item")
            if not item:
                return None
            return _skillset_version_from_item(item)

        return await asyncio.to_thread(_op)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    async def list_skills(self, skillset_id: str) -> list[SkillItem]:
        def _op() -> list[SkillItem]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _skill_list_pk(skillset_id)},
            )
            skill_ids = [item["SK"].replace("SKILL#", "") for item in resp.get("Items", [])]
            skills = []
            for skill_id in skill_ids:
                skill_resp = table.get_item(Key={"PK": _skill_pk(skill_id), "SK": _SK_METADATA})
                item = skill_resp.get("Item")
                if item:
                    skills.append(_skill_from_item(item))
            return skills

        return await asyncio.to_thread(_op)

    async def get_skill(self, skill_id: str) -> SkillItem | None:
        def _op() -> SkillItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _skill_pk(skill_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return None
            return _skill_from_item(item)

        return await asyncio.to_thread(_op)

    async def create_skill(
        self,
        skillset_id: str,
        skill_id: str,
        name: str,
        description: str,
        template: str,
        parameters: list[dict[str, Any]],
        triggers: list[str],
        tools_required: list[str],
        enabled: bool,
        created_by: str,
    ) -> SkillItem | None:
        def _op() -> SkillItem | None:
            table = _get_table()
            ss_resp = table.get_item(Key={"PK": _skillset_pk(skillset_id), "SK": _SK_METADATA})
            if not ss_resp.get("Item"):
                return None

            now = datetime.now(tz=UTC).isoformat()
            version = 1
            base = _strip_none(
                {
                    "skill_id": skill_id,
                    "skillset_id": skillset_id,
                    "name": name,
                    "description": description,
                    "template": template,
                    "parameters": _floats_to_decimal(parameters),
                    "triggers": triggers,
                    "tools_required": tools_required,
                    "enabled": enabled,
                    "current_version": version,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": created_by,
                    "updated_by": created_by,
                }
            )
            metadata_item = {"PK": _skill_pk(skill_id), "SK": _SK_METADATA, **base}
            list_item = {"PK": _skill_list_pk(skillset_id), "SK": _skill_list_sk(skill_id), **base}
            version_item = _strip_none(
                {
                    "PK": _skill_pk(skill_id),
                    "SK": _version_sk(version),
                    "skill_id": skill_id,
                    "skillset_id": skillset_id,
                    "name": name,
                    "description": description,
                    "template": template,
                    "parameters": _floats_to_decimal(parameters),
                    "triggers": triggers,
                    "tools_required": tools_required,
                    "enabled": enabled,
                    "version": version,
                    "created_at": now,
                    "created_by": created_by,
                    "comment": None,
                }
            )
            _transact_put_sync(table, metadata_item, list_item, version_item)
            return _skill_from_item(base)

        return await asyncio.to_thread(_op)

    async def update_skill(
        self,
        skill_id: str,
        name: str,
        description: str,
        template: str,
        parameters: list[dict[str, Any]],
        triggers: list[str],
        tools_required: list[str],
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> SkillItem | None:
        def _op() -> SkillItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _skill_pk(skill_id), "SK": _SK_METADATA})
            if not resp.get("Item"):
                return None
            existing = resp["Item"]
            skillset_id = existing["skillset_id"]
            current_version = int(existing.get("current_version", 0))
            version = current_version + 1
            now = datetime.now(tz=UTC).isoformat()
            base = _strip_none(
                {
                    "skill_id": skill_id,
                    "skillset_id": skillset_id,
                    "name": name,
                    "description": description,
                    "template": template,
                    "parameters": _floats_to_decimal(parameters),
                    "triggers": triggers,
                    "tools_required": tools_required,
                    "enabled": enabled,
                    "current_version": version,
                    "created_at": existing["created_at"],
                    "updated_at": now,
                    "created_by": existing["created_by"],
                    "updated_by": updated_by,
                }
            )
            metadata_item = {"PK": _skill_pk(skill_id), "SK": _SK_METADATA, **base}
            list_item = {"PK": _skill_list_pk(skillset_id), "SK": _skill_list_sk(skill_id), **base}
            version_item = _strip_none(
                {
                    "PK": _skill_pk(skill_id),
                    "SK": _version_sk(version),
                    "skill_id": skill_id,
                    "skillset_id": skillset_id,
                    "name": name,
                    "description": description,
                    "template": template,
                    "parameters": _floats_to_decimal(parameters),
                    "triggers": triggers,
                    "tools_required": tools_required,
                    "enabled": enabled,
                    "version": version,
                    "created_at": now,
                    "created_by": updated_by,
                    "comment": comment,
                }
            )
            _transact_put_sync(table, metadata_item, list_item, version_item)
            return _skill_from_item(base)

        return await asyncio.to_thread(_op)

    async def delete_skill(self, skill_id: str) -> bool:
        def _op() -> bool:
            table = _get_table()
            resp = table.get_item(Key={"PK": _skill_pk(skill_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return False
            skillset_id = item["skillset_id"]
            items_resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _skill_pk(skill_id)},
                ProjectionExpression="PK, SK",
            )
            keys_to_delete = [{"PK": i["PK"], "SK": i["SK"]} for i in items_resp.get("Items", [])]
            keys_to_delete.append({"PK": _skill_list_pk(skillset_id), "SK": _skill_list_sk(skill_id)})
            with table.batch_writer() as batch:
                for key in keys_to_delete:
                    batch.delete_item(Key=key)
            return True

        return await asyncio.to_thread(_op)

    async def list_skill_versions(self, skill_id: str) -> list[SkillVersion]:
        def _op() -> list[SkillVersion]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": _skill_pk(skill_id),
                    ":prefix": _SK_VERSION_PREFIX,
                },
                ScanIndexForward=False,
            )
            return [_skill_version_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def get_skill_version(self, skill_id: str, version: int) -> SkillVersion | None:
        def _op() -> SkillVersion | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _skill_pk(skill_id), "SK": _version_sk(version)})
            item = resp.get("Item")
            if not item:
                return None
            return _skill_version_from_item(item)

        return await asyncio.to_thread(_op)

    async def list_enabled_skills(self) -> list[SkillItem]:
        def _op() -> list[SkillItem]:
            table = _get_table()
            ss_resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _PK_SKILLSET_LIST},
            )
            enabled_skillset_ids = [
                item["skillset_id"] for item in ss_resp.get("Items", []) if item.get("enabled", True)
            ]
            skills = []
            for skillset_id in enabled_skillset_ids:
                skill_list_resp = table.query(
                    KeyConditionExpression="PK = :pk",
                    ExpressionAttributeValues={":pk": _skill_list_pk(skillset_id)},
                )
                for list_item in skill_list_resp.get("Items", []):
                    if list_item.get("enabled", True):
                        skill_resp = table.get_item(
                            Key={
                                "PK": _skill_pk(list_item["skill_id"]),
                                "SK": _SK_METADATA,
                            }
                        )
                        item = skill_resp.get("Item")
                        if item and item.get("enabled", True):
                            skills.append(_skill_from_item(item))
            return skills

        return await asyncio.to_thread(_op)

    async def get_enabled_skill(self, skillset_id: str, skill_id: str) -> SkillItem | None:
        def _op() -> SkillItem | None:
            table = _get_table()
            skill_resp = table.get_item(Key={"PK": _skill_pk(skill_id), "SK": _SK_METADATA})
            skill_item = skill_resp.get("Item")
            if not skill_item or skill_item.get("skillset_id") != skillset_id or not skill_item.get("enabled", True):
                return None

            skillset_resp = table.get_item(Key={"PK": _skillset_pk(skillset_id), "SK": _SK_METADATA})
            skillset_item = skillset_resp.get("Item")
            if not skillset_item or not skillset_item.get("enabled", True):
                return None
            return _skill_from_item(skill_item)

        return await asyncio.to_thread(_op)

    async def get_query_history_item(self, user_id: str, history_id: str) -> QueryHistoryItem | None:
        def _op() -> QueryHistoryItem | None:
            table = _get_table()
            resp = table.get_item(
                Key={
                    "PK": _query_history_pk(user_id),
                    "SK": _query_history_sk(history_id),
                }
            )
            item = resp.get("Item")
            if item is None:
                return None
            return QueryHistoryItem(
                history_id=item["history_id"],
                user_id=item["user_id"],
                query=item["query"],
                executed_at=item["executed_at"],
            )

        return await asyncio.to_thread(_op)

    # ------------------------------------------------------------------
    # Roles (user-defined, versioned)
    # ------------------------------------------------------------------

    async def list_roles(self) -> list[RoleItem]:
        def _op() -> list[RoleItem]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _PK_ROLE_LIST},
            )
            return [_role_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def get_role(self, role_id: str) -> RoleItem | None:
        def _op() -> RoleItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _role_pk(role_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item:
                return None
            return _role_from_item(item)

        return await asyncio.to_thread(_op)

    async def get_role_by_name(self, name: str) -> RoleItem | None:
        def _op() -> RoleItem | None:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk",
                FilterExpression="#n = :name",
                ExpressionAttributeNames={"#n": "name"},
                ExpressionAttributeValues={":pk": _PK_ROLE_LIST, ":name": name},
            )
            items = resp.get("Items", [])
            if not items:
                return None
            return _role_from_item(items[0])

        return await asyncio.to_thread(_op)

    async def create_role(
        self,
        name: str,
        description: str,
        permissions: list[str],
        created_by: str,
    ) -> RoleItem:
        role_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        version = 1
        base = _strip_none(
            {
                "role_id": role_id,
                "name": name,
                "description": description,
                "permissions": permissions,
                "current_version": version,
                "created_at": now,
                "updated_at": now,
                "created_by": created_by,
                "updated_by": created_by,
            }
        )
        metadata_item = {"PK": _role_pk(role_id), "SK": _SK_METADATA, **base}
        list_item = {"PK": _PK_ROLE_LIST, "SK": _role_list_sk(role_id), **base}
        version_item = _strip_none(
            {
                "PK": _role_pk(role_id),
                "SK": _version_sk(version),
                "role_id": role_id,
                "name": name,
                "description": description,
                "permissions": permissions,
                "version": version,
                "created_at": now,
                "created_by": created_by,
                "comment": None,
            }
        )

        def _op() -> None:
            table = _get_table()
            _transact_put_sync(table, metadata_item, list_item, version_item)

        await asyncio.to_thread(_op)
        return _role_from_item(base)

    async def update_role(
        self,
        role_id: str,
        name: str,
        description: str,
        permissions: list[str],
        updated_by: str,
        comment: str | None = None,
    ) -> RoleItem | None:
        def _op() -> RoleItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _role_pk(role_id), "SK": _SK_METADATA})
            if not resp.get("Item"):
                return None
            existing = resp["Item"]
            current_version = int(existing.get("current_version", 0))
            version = current_version + 1
            now = datetime.now(tz=UTC).isoformat()
            base = _strip_none(
                {
                    "role_id": role_id,
                    "name": name,
                    "description": description,
                    "permissions": permissions,
                    "current_version": version,
                    "created_at": existing["created_at"],
                    "updated_at": now,
                    "created_by": existing["created_by"],
                    "updated_by": updated_by,
                }
            )
            metadata_item = {"PK": _role_pk(role_id), "SK": _SK_METADATA, **base}
            list_item = {"PK": _PK_ROLE_LIST, "SK": _role_list_sk(role_id), **base}
            version_item = _strip_none(
                {
                    "PK": _role_pk(role_id),
                    "SK": _version_sk(version),
                    "role_id": role_id,
                    "name": name,
                    "description": description,
                    "permissions": permissions,
                    "version": version,
                    "created_at": now,
                    "created_by": updated_by,
                    "comment": comment,
                }
            )
            _transact_put_sync(table, metadata_item, list_item, version_item)
            return _role_from_item(base)

        return await asyncio.to_thread(_op)

    async def delete_role(self, role_id: str) -> bool:
        def _op() -> bool:
            table = _get_table()
            resp = table.get_item(Key={"PK": _role_pk(role_id), "SK": _SK_METADATA})
            if not resp.get("Item"):
                return False
            items_resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _role_pk(role_id)},
                ProjectionExpression="PK, SK",
            )
            keys_to_delete = [{"PK": item["PK"], "SK": item["SK"]} for item in items_resp.get("Items", [])]
            keys_to_delete.append({"PK": _PK_ROLE_LIST, "SK": _role_list_sk(role_id)})
            with table.batch_writer() as batch:
                for key in keys_to_delete:
                    batch.delete_item(Key=key)
            return True

        return await asyncio.to_thread(_op)

    async def list_role_versions(self, role_id: str) -> list[RoleVersion]:
        def _op() -> list[RoleVersion]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": _role_pk(role_id),
                    ":prefix": _SK_VERSION_PREFIX,
                },
                ScanIndexForward=False,
            )
            return [_role_version_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def get_role_version(self, role_id: str, version: int) -> RoleVersion | None:
        def _op() -> RoleVersion | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _role_pk(role_id), "SK": _version_sk(version)})
            item = resp.get("Item")
            if not item:
                return None
            return _role_version_from_item(item)

        return await asyncio.to_thread(_op)

    # ------------------------------------------------------------------
    # Query history
    # ------------------------------------------------------------------

    async def save_query_history(self, user_id: str, query: str) -> QueryHistoryItem:
        """Append a query execution to the user's history."""
        history_id = str(next(_get_snowflake_gen()))
        now = datetime.now(tz=UTC).isoformat()
        item = {
            "PK": _query_history_pk(user_id),
            "SK": _query_history_sk(history_id),
            "history_id": history_id,
            "user_id": user_id,
            "query": query,
            "executed_at": now,
        }

        def _op() -> None:
            table = _get_table()
            table.put_item(Item=_strip_none(item))

        await asyncio.to_thread(_op)
        return QueryHistoryItem(
            history_id=history_id,
            user_id=user_id,
            query=query,
            executed_at=now,
        )

    async def list_query_history(self, user_id: str, page: int, per_page: int) -> tuple[list[QueryHistoryItem], int]:
        """Return a paginated page of query history (newest first) and the total count."""

        def _op() -> tuple[list[QueryHistoryItem], int]:
            table = _get_table()
            pk = _query_history_pk(user_id)

            # Count (capped at _QUERY_HISTORY_MAX to bound the scan).
            count_resp = table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": pk,
                    ":prefix": _SK_QUERY_HISTORY_PREFIX,
                },
                Select="COUNT",
                Limit=_QUERY_HISTORY_MAX,
            )
            total = count_resp.get("Count", 0)
            if total == 0:
                return [], 0

            # Fetch only as many items as needed to reach the end of the requested
            # page (DynamoDB has no OFFSET, so we scan from the start and slice).
            fetch_limit = page * per_page
            resp = table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": pk,
                    ":prefix": _SK_QUERY_HISTORY_PREFIX,
                },
                ScanIndexForward=False,
                Limit=fetch_limit,
            )
            items = resp.get("Items", [])
            offset = (page - 1) * per_page
            paged = items[offset : offset + per_page]  # noqa: E203
            return [
                QueryHistoryItem(
                    history_id=it["history_id"],
                    user_id=it["user_id"],
                    query=it["query"],
                    executed_at=it["executed_at"],
                )
                for it in paged
            ], total

        return await asyncio.to_thread(_op)

    # ------------------------------------------------------------------
    # Chat sessions
    # ------------------------------------------------------------------

    async def list_chat_sessions(self, user_id: str, limit: int) -> list[ChatSessionItem]:
        pk = _chat_session_list_pk(user_id)
        bounded_limit = max(1, min(limit, _CHAT_SESSION_LIST_MAX))

        def _op() -> list[ChatSessionItem]:
            table = _get_table()
            # Scheduled-run sessions are hidden from the interactive session
            # list. The filter runs after Limit evaluates items, so a page mixed
            # with scheduled sessions can return fewer than `limit` rows; that
            # under-fill is acceptable for the sidebar list.
            resp = table.query(
                KeyConditionExpression="PK = :pk",
                FilterExpression="attribute_not_exists(origin) OR origin = :interactive",
                ExpressionAttributeValues={":pk": pk, ":interactive": "interactive"},
                Limit=bounded_limit,
                ScanIndexForward=False,
            )
            return [_chat_session_from_item(item) for item in resp.get("Items", [])]

        return await asyncio.to_thread(_op)

    async def list_scheduled_chat_sessions(
        self,
        user_id: str,
        scheduled_chat_id: str,
        limit: int,
    ) -> list[ChatSessionItem]:
        pk = _chat_session_list_pk(user_id)
        bounded_limit = max(1, min(limit, _CHAT_SESSION_LIST_MAX))

        def _op() -> list[ChatSessionItem]:
            table = _get_table()
            sessions: list[ChatSessionItem] = []
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": "PK = :pk",
                "FilterExpression": "scheduled_chat_id = :sc_id",
                "ExpressionAttributeValues": {":pk": pk, ":sc_id": scheduled_chat_id},
                "ScanIndexForward": False,
            }
            while len(sessions) < bounded_limit:
                resp = table.query(**kwargs)
                sessions.extend(_chat_session_from_item(item) for item in resp.get("Items", []))
                last_key = resp.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key
            return sessions[:bounded_limit]

        return await asyncio.to_thread(_op)

    async def get_chat_session(self, user_id: str, thread_id: str) -> ChatSessionItem | None:
        def _op() -> ChatSessionItem | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _chat_session_metadata_pk(user_id), "SK": thread_id})
            item = resp.get("Item")
            return _chat_session_from_item(item) if item else None

        return await asyncio.to_thread(_op)

    async def create_chat_session(
        self,
        user_id: str,
        title: str,
        origin: str = "interactive",
        scheduled_chat_id: str | None = None,
    ) -> ChatSessionItem:
        thread_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        metadata_pk = _chat_session_metadata_pk(user_id)
        list_pk = _chat_session_list_pk(user_id)
        metadata_item = _strip_none(
            {
                "PK": metadata_pk,
                "SK": thread_id,
                "thread_id": thread_id,
                "user_id": user_id,
                "title": title,
                "created_at": now,
                "updated_at": now,
                "origin": origin,
                "scheduled_chat_id": scheduled_chat_id,
                "run_status": "running" if origin != "interactive" else None,
                "run_errors": [],
            }
        )
        list_item = {**metadata_item, "PK": list_pk, "SK": _chat_session_list_sk(now, thread_id)}

        def _op() -> ChatSessionItem:
            table = _get_table()
            table.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": settings.DYNAMODB_TABLE_NAME,
                            "Item": metadata_item,
                            "ConditionExpression": "attribute_not_exists(PK)",
                        },
                    },
                    {
                        "Put": {
                            "TableName": settings.DYNAMODB_TABLE_NAME,
                            "Item": list_item,
                        },
                    },
                ]
            )
            return _chat_session_from_item(metadata_item)

        return await asyncio.to_thread(_op)

    async def touch_chat_session(self, user_id: str, thread_id: str) -> ChatSessionItem | None:
        metadata_pk = _chat_session_metadata_pk(user_id)
        list_pk = _chat_session_list_pk(user_id)

        def _op() -> ChatSessionItem | None:
            table = _get_table()
            return _retry_chat_session_update(
                table,
                metadata_pk,
                list_pk,
                thread_id,
                lambda existing, now: ChatSessionUpdate(
                    item={**existing, "updated_at": now},
                    expression="SET updated_at = :updated_at",
                    values={":updated_at": now},
                ),
            )

        return await asyncio.to_thread(_op)

    async def complete_chat_session_run(
        self,
        user_id: str,
        thread_id: str,
        status: str,
        errors: list[str],
    ) -> ChatSessionItem | None:
        metadata_pk = _chat_session_metadata_pk(user_id)
        list_pk = _chat_session_list_pk(user_id)

        def _op() -> ChatSessionItem | None:
            table = _get_table()
            return _retry_chat_session_update(
                table,
                metadata_pk,
                list_pk,
                thread_id,
                lambda existing, now: ChatSessionUpdate(
                    item={
                        **existing,
                        "updated_at": now,
                        "run_status": status,
                        "run_errors": list(errors),
                    },
                    expression=("SET updated_at = :updated_at, run_status = :run_status, run_errors = :run_errors"),
                    values={
                        ":updated_at": now,
                        ":run_status": status,
                        ":run_errors": list(errors),
                    },
                ),
            )

        return await asyncio.to_thread(_op)

    async def update_chat_session_title(self, user_id: str, thread_id: str, title: str) -> ChatSessionItem | None:
        metadata_pk = _chat_session_metadata_pk(user_id)
        list_pk = _chat_session_list_pk(user_id)

        def _op() -> ChatSessionItem | None:
            table = _get_table()
            return _retry_chat_session_update(
                table,
                metadata_pk,
                list_pk,
                thread_id,
                lambda existing, now: ChatSessionUpdate(
                    item={**existing, "title": title, "updated_at": now},
                    expression="SET #t = :title, updated_at = :updated_at",
                    values={":title": title, ":updated_at": now},
                    names={"#t": "title"},
                ),
            )

        return await asyncio.to_thread(_op)

    async def delete_chat_session(self, user_id: str, thread_id: str) -> bool:
        metadata_pk = _chat_session_metadata_pk(user_id)
        list_pk = _chat_session_list_pk(user_id)

        def _op() -> bool:
            table = _get_table()
            return _retry_chat_session_delete(table, metadata_pk, list_pk, thread_id)

        return await asyncio.to_thread(_op)

    # ------------------------------------------------------------------
    # Action confirmations
    # ------------------------------------------------------------------

    async def create_action_confirmation(self, confirmation: ActionConfirmation) -> ActionConfirmation:
        data = confirmation.model_dump()
        dedup_item = _action_confirmation_dedup_sentinel_item(data)

        def _op() -> ActionConfirmation | None:
            table = _get_table()
            try:
                table.meta.client.transact_write_items(
                    TransactItems=[
                        {
                            "Put": {
                                "TableName": settings.DYNAMODB_TABLE_NAME,
                                "Item": _action_confirmation_metadata_item(data),
                                "ConditionExpression": "attribute_not_exists(PK)",
                            },
                        },
                        *[
                            {
                                "Put": {
                                    "TableName": settings.DYNAMODB_TABLE_NAME,
                                    "Item": item,
                                },
                            }
                            for item in _action_confirmation_put_items(data)[1:]
                        ],
                        {
                            "Put": {
                                "TableName": settings.DYNAMODB_TABLE_NAME,
                                "Item": dedup_item,
                                "ConditionExpression": "attribute_not_exists(PK)",
                            },
                        },
                    ]
                )
                return confirmation
            except botocore.exceptions.ClientError as exc:
                if _transaction_cancelled(exc):
                    # Either duplicate confirmation_id (effectively impossible) or the
                    # dedup sentinel already exists — a concurrent call won the race.
                    return None
                raise

        for attempt in range(2):
            result = await asyncio.to_thread(_op)
            if result is not None:
                return result
            # Transaction cancelled — a concurrent caller won the dedup race.
            # Search across all statuses so we return the correct record even if
            # the winning confirmation was decided (approved/denied/executed) in
            # the window between our transaction failure and this re-fetch.
            existing = await self.find_action_confirmation_grant(
                user_id=confirmation.user_id,
                source=confirmation.source,
                session_key=confirmation.session_key,
                tool_name=confirmation.tool_name,
                action=confirmation.action,
                resource_type=confirmation.resource_type,
                resource_id=confirmation.resource_id,
                arguments_hash=confirmation.arguments_hash,
                statuses=("pending", "approved", "denied", "executed"),
            )
            if existing is not None:
                return existing
            if attempt == 0:
                await self._expire_matching_pending_action_confirmation(confirmation)
                continue
            break
        return confirmation

    async def _expire_matching_pending_action_confirmation(self, confirmation: ActionConfirmation) -> bool:
        now = datetime.now(tz=UTC)
        pk = _action_confirmation_session_list_pk(
            confirmation.user_id,
            confirmation.source,
            confirmation.session_key,
        )

        def _op() -> bool:
            table = _get_table()
            last_key: dict[str, Any] | None = None
            while True:
                kwargs: dict[str, Any] = {
                    "KeyConditionExpression": "PK = :pk AND begins_with(SK, :status_prefix)",
                    "ExpressionAttributeValues": {
                        ":pk": pk,
                        ":status_prefix": "STATUS#pending#",
                    },
                    "ScanIndexForward": False,
                    "Limit": _ACTION_CONFIRMATION_SESSION_QUERY_MAX,
                }
                if last_key is not None:
                    kwargs["ExclusiveStartKey"] = last_key
                resp = table.query(**kwargs)
                for raw_item in resp.get("Items", []):
                    cid = raw_item.get("confirmation_id")
                    if not isinstance(cid, str) or not cid:
                        continue
                    meta_resp = table.get_item(Key={"PK": _action_confirmation_pk(cid), "SK": _SK_METADATA})
                    meta_item = meta_resp.get("Item")
                    if not meta_item:
                        continue
                    item = _action_confirmation_from_item(meta_item)
                    if (
                        item.tool_name != confirmation.tool_name
                        or item.action != confirmation.action
                        or item.resource_type != confirmation.resource_type
                        or item.resource_id != confirmation.resource_id
                        or item.arguments_hash != confirmation.arguments_hash
                        or datetime.fromisoformat(item.expires_at) > now
                    ):
                        continue
                    data = _action_confirmation_data(meta_item)
                    updated = {**data, "status": "expired", "decided_at": now.isoformat()}
                    transact_items: list[dict[str, Any]] = [
                        {
                            "Update": {
                                "TableName": settings.DYNAMODB_TABLE_NAME,
                                "Key": {"PK": _action_confirmation_pk(cid), "SK": _SK_METADATA},
                                "UpdateExpression": "SET #s = :expired, decided_at = :now",
                                "ConditionExpression": "#s = :pending AND user_id = :user AND expires_at <= :now",
                                "ExpressionAttributeNames": {"#s": "status"},
                                "ExpressionAttributeValues": {
                                    ":expired": "expired",
                                    ":pending": "pending",
                                    ":user": confirmation.user_id,
                                    ":now": now.isoformat(),
                                },
                            }
                        },
                        {
                            "Delete": {
                                "TableName": settings.DYNAMODB_TABLE_NAME,
                                "Key": {
                                    "PK": pk,
                                    "SK": _action_confirmation_session_list_sk(
                                        "pending",
                                        str(data["created_at"]),
                                        cid,
                                    ),
                                },
                            },
                        },
                        {
                            "Put": {
                                "TableName": settings.DYNAMODB_TABLE_NAME,
                                "Item": _action_confirmation_session_list_item(updated),
                            },
                        },
                        {
                            "Delete": {
                                "TableName": settings.DYNAMODB_TABLE_NAME,
                                "Key": {"PK": pk, "SK": _action_confirmation_dedup_sk(data)},
                            },
                        },
                    ]
                    try:
                        table.meta.client.transact_write_items(TransactItems=transact_items)
                        return True
                    except botocore.exceptions.ClientError as exc:
                        if _transaction_cancelled(exc):
                            return False
                        raise
                last_key = resp.get("LastEvaluatedKey")
                if not last_key:
                    return False

        return await asyncio.to_thread(_op)

    async def get_action_confirmation(
        self,
        confirmation_id: str,
        user_id: str | None = None,
    ) -> ActionConfirmation | None:
        def _op() -> ActionConfirmation | None:
            table = _get_table()
            resp = table.get_item(Key={"PK": _action_confirmation_pk(confirmation_id), "SK": _SK_METADATA})
            item = resp.get("Item")
            if not item or (user_id is not None and item.get("user_id") != user_id):
                return None
            return _action_confirmation_from_item(item)

        return await asyncio.to_thread(_op)

    async def list_action_confirmations(
        self,
        user_id: str,
        source: ConfirmationSource,
        session_key: str,
        status: str | None = None,
    ) -> list[ActionConfirmation]:
        def _op() -> list[str]:
            table = _get_table()
            values = {":pk": _action_confirmation_session_list_pk(user_id, source, session_key)}
            key_expression = "PK = :pk"
            if status is not None:
                values[":status_prefix"] = f"STATUS#{status}#"
                key_expression = "PK = :pk AND begins_with(SK, :status_prefix)"
            resp = table.query(
                KeyConditionExpression=key_expression,
                ExpressionAttributeValues=values,
                ScanIndexForward=False,
                Limit=_ACTION_CONFIRMATION_SESSION_QUERY_MAX,
            )
            return [
                str(item["confirmation_id"])
                for item in resp.get("Items", [])
                if isinstance(item.get("confirmation_id"), str)
            ]

        confirmation_ids = await asyncio.to_thread(_op)
        return await _hydrate_action_confirmations(
            confirmation_ids,
            lambda confirmation_id: self.get_action_confirmation(confirmation_id, user_id=user_id),
        )

    async def list_batch_action_confirmations(self, user_id: str, batch_id: str) -> list[ActionConfirmation]:
        def _op() -> list[str]:
            table = _get_table()
            resp = table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": _action_confirmation_batch_pk(user_id, batch_id)},
                ScanIndexForward=True,
                Limit=_ACTION_CONFIRMATION_SESSION_QUERY_MAX,
            )
            return [
                str(item["confirmation_id"])
                for item in resp.get("Items", [])
                if isinstance(item.get("confirmation_id"), str)
            ]

        confirmation_ids = await asyncio.to_thread(_op)
        return await _hydrate_action_confirmations(
            confirmation_ids,
            lambda confirmation_id: self.get_action_confirmation(confirmation_id, user_id=user_id),
        )

    async def decide_action_confirmation(
        self,
        confirmation_id: str,
        user_id: str,
        decision: ConfirmationDecision,
    ) -> ActionConfirmation | None:
        now = datetime.now(tz=UTC).isoformat()

        def _op() -> ActionConfirmation | None:
            table = _get_table()
            existing_resp = table.get_item(Key={"PK": _action_confirmation_pk(confirmation_id), "SK": _SK_METADATA})
            existing = existing_resp.get("Item")
            if (
                not existing
                or existing.get("user_id") != user_id
                or existing.get("status") != "pending"
                or existing.get("expires_at", "") <= now
            ):
                return None
            data = _action_confirmation_data(existing)
            updated = {
                **data,
                "status": decision,
                "decided_at": now,
                "decided_by": user_id,
            }
            transact_items: list[dict[str, Any]] = [
                {
                    "Update": {
                        "TableName": settings.DYNAMODB_TABLE_NAME,
                        "Key": {"PK": _action_confirmation_pk(confirmation_id), "SK": _SK_METADATA},
                        "UpdateExpression": "SET #s = :decision, decided_at = :now, decided_by = :user",
                        "ConditionExpression": "#s = :pending AND user_id = :user AND expires_at > :now",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {
                            ":decision": decision,
                            ":now": now,
                            ":user": user_id,
                            ":pending": "pending",
                        },
                    }
                },
                {
                    "Delete": {
                        "TableName": settings.DYNAMODB_TABLE_NAME,
                        "Key": {
                            "PK": _action_confirmation_session_list_pk(
                                user_id, str(data["source"]), str(data["session_key"])
                            ),
                            "SK": _action_confirmation_session_list_sk(
                                "pending", str(data["created_at"]), confirmation_id
                            ),
                        },
                    },
                },
                {
                    "Put": {
                        "TableName": settings.DYNAMODB_TABLE_NAME,
                        "Item": _action_confirmation_session_list_item(updated),
                    },
                },
                {
                    "Delete": {
                        "TableName": settings.DYNAMODB_TABLE_NAME,
                        "Key": {
                            "PK": _action_confirmation_session_list_pk(
                                user_id, str(data["source"]), str(data["session_key"])
                            ),
                            "SK": _action_confirmation_dedup_sk(data),
                        },
                    },
                },
            ]
            try:
                table.meta.client.transact_write_items(TransactItems=transact_items)
            except botocore.exceptions.ClientError as exc:
                if _transaction_cancelled(exc):
                    return None
                raise
            return _action_confirmation_from_item(updated)

        return await asyncio.to_thread(_op)

    async def claim_action_confirmation_for_execution(
        self,
        confirmation_id: str,
        user_id: str,
    ) -> ActionConfirmation | None:
        def _op() -> ActionConfirmation | None:
            table = _get_table()
            existing_resp = table.get_item(Key={"PK": _action_confirmation_pk(confirmation_id), "SK": _SK_METADATA})
            existing = existing_resp.get("Item")
            now = datetime.now(tz=UTC).isoformat()
            if (
                not existing
                or existing.get("user_id") != user_id
                or existing.get("status") != "approved"
                or existing.get("expires_at", "") <= now
            ):
                return None
            data = _action_confirmation_data(existing)
            updated = {**data, "status": "executed"}
            transact_items: list[dict[str, Any]] = [
                {
                    "Update": {
                        "TableName": settings.DYNAMODB_TABLE_NAME,
                        "Key": {"PK": _action_confirmation_pk(confirmation_id), "SK": _SK_METADATA},
                        "UpdateExpression": "SET #s = :executed",
                        "ConditionExpression": "#s = :approved AND user_id = :user AND expires_at > :now",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {
                            ":executed": "executed",
                            ":approved": "approved",
                            ":user": user_id,
                            ":now": now,
                        },
                    }
                },
                {
                    "Delete": {
                        "TableName": settings.DYNAMODB_TABLE_NAME,
                        "Key": {
                            "PK": _action_confirmation_session_list_pk(
                                user_id, str(data["source"]), str(data["session_key"])
                            ),
                            "SK": _action_confirmation_session_list_sk(
                                "approved", str(data["created_at"]), confirmation_id
                            ),
                        },
                    },
                },
                {
                    "Put": {
                        "TableName": settings.DYNAMODB_TABLE_NAME,
                        "Item": _action_confirmation_session_list_item(updated),
                    },
                },
            ]
            # The dedup sentinel is deleted unconditionally; if the confirmation
            # was created before this code deployed the sentinel won't exist and
            # the delete is a no-op.
            transact_items.append(
                {
                    "Delete": {
                        "TableName": settings.DYNAMODB_TABLE_NAME,
                        "Key": {
                            "PK": _action_confirmation_session_list_pk(
                                user_id, str(data["source"]), str(data["session_key"])
                            ),
                            "SK": _action_confirmation_dedup_sk(data),
                        },
                    },
                }
            )
            try:
                table.meta.client.transact_write_items(TransactItems=transact_items)
            except botocore.exceptions.ClientError as exc:
                if _transaction_cancelled(exc):
                    return None
                raise
            return _action_confirmation_from_item(updated)

        return await asyncio.to_thread(_op)

    async def find_action_confirmation_grant(
        self,
        user_id: str,
        source: ConfirmationSource,
        session_key: str,
        tool_name: str,
        action: str,
        resource_type: str,
        resource_id: str,
        arguments_hash: str,
        statuses: tuple[str, ...] = ("approved", "denied"),
    ) -> ActionConfirmation | None:
        now = datetime.now(tz=UTC)
        pk = _action_confirmation_session_list_pk(user_id, source, session_key)

        def _op() -> ActionConfirmation | None:
            table = _get_table()
            newest: ActionConfirmation | None = None
            for status in statuses:
                last_key: dict[str, Any] | None = None
                while True:
                    kwargs: dict[str, Any] = {
                        "KeyConditionExpression": "PK = :pk AND begins_with(SK, :status_prefix)",
                        "ExpressionAttributeValues": {
                            ":pk": pk,
                            ":status_prefix": f"STATUS#{status}#",
                        },
                        "ScanIndexForward": False,
                        "Limit": _ACTION_CONFIRMATION_SESSION_QUERY_MAX,
                    }
                    if last_key is not None:
                        kwargs["ExclusiveStartKey"] = last_key
                    resp = table.query(**kwargs)
                    for raw_item in resp.get("Items", []):
                        # Session-list items are pointer-only ({PK, SK, confirmation_id}).
                        # Hydrate the full record from the metadata item before comparing.
                        cid = raw_item.get("confirmation_id")
                        if not isinstance(cid, str) or not cid:
                            continue
                        meta_resp = table.get_item(Key={"PK": _action_confirmation_pk(cid), "SK": _SK_METADATA})
                        meta_item = meta_resp.get("Item")
                        if not meta_item:
                            continue
                        item = _action_confirmation_from_item(meta_item)
                        if (
                            item.tool_name == tool_name
                            and item.action == action
                            and item.resource_type == resource_type
                            and item.resource_id == resource_id
                            and item.arguments_hash == arguments_hash
                            and datetime.fromisoformat(item.expires_at) > now
                        ):
                            if newest is None or (item.decided_at or item.created_at) > (
                                newest.decided_at or newest.created_at
                            ):
                                newest = item
                    last_key = resp.get("LastEvaluatedKey")
                    if not last_key:
                        break
            return newest

        return await asyncio.to_thread(_op)
