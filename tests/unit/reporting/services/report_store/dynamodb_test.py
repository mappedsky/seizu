import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import botocore.exceptions
import pytest

from reporting.schema.chat import CHAT_TURN_MAX_BATCH_BYTES, ChatTurnCanceledError, ChatTurnConflictError
from reporting.schema.confirmations import ActionConfirmation
from reporting.schema.mcp_config import SkillItem, SkillsetListItem, SkillsetVersion, SkillVersion
from reporting.schema.report_config import ReportAccess, ReportListItem, ReportVersion
from reporting.schema.space_config import SpaceConflictError, SpaceDeleteResult
from reporting.services.report_store import dynamodb as dynamodb_module
from reporting.services.report_store.dynamodb import DynamoDBReportStore, _action_confirmation_dedup_sk

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_snowflake_gen():
    """Reset the module-level snowflake generator between tests."""
    original = dynamodb_module._snowflake_gen
    dynamodb_module._snowflake_gen = None
    yield
    dynamodb_module._snowflake_gen = original


def _force_index_fallback():
    """Pin the store to the no-GSI path.

    Both fields are needed: a negative probe is only cached for
    _SPACE_INDEX_RETRY_SECONDS, so without a fresh timestamp the store
    immediately re-probes.
    """
    dynamodb_module._space_reports_index_available = False
    dynamodb_module._space_reports_index_checked_at = time.monotonic()


@pytest.fixture(autouse=True)
def reset_space_index_probe():
    """Reset the cached "is the space GSI usable" flag between tests."""
    original = dynamodb_module._space_reports_index_available
    original_at = dynamodb_module._space_reports_index_checked_at
    dynamodb_module._space_reports_index_available = None
    dynamodb_module._space_reports_index_checked_at = 0.0
    yield
    dynamodb_module._space_reports_index_available = original
    dynamodb_module._space_reports_index_checked_at = original_at


@pytest.fixture()
def mock_table():
    return MagicMock()


@pytest.fixture()
def patch_table(mock_table):
    with patch(
        "reporting.services.report_store.dynamodb._get_table",
        return_value=mock_table,
    ):
        yield mock_table


@pytest.fixture()
def store():
    return DynamoDBReportStore()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_get_boto_resource_uses_configured_timeouts(mocker):
    resource_mock = mocker.MagicMock()
    resource_ctor = mocker.patch("reporting.services.report_store.dynamodb.boto3.resource", return_value=resource_mock)
    mocker.patch("reporting.settings.DYNAMODB_REGION", "us-west-2")
    mocker.patch("reporting.settings.DYNAMODB_ENDPOINT_URL", "http://dynamodb.local")
    mocker.patch("reporting.settings.AWS_CONNECT_TIMEOUT", 7)
    mocker.patch("reporting.settings.AWS_READ_TIMEOUT", 29)

    result = dynamodb_module.get_boto_resource()

    assert result == resource_mock
    assert resource_ctor.call_args.args == ("dynamodb",)
    config = resource_ctor.call_args.kwargs["config"]
    assert resource_ctor.call_args.kwargs["region_name"] == "us-west-2"
    assert resource_ctor.call_args.kwargs["endpoint_url"] == "http://dynamodb.local"
    assert config.connect_timeout == 7
    assert config.read_timeout == 29


def _version_item(report_id="123", version=1):
    return {
        "PK": f"REPORT#{report_id}",
        "SK": f"VERSION#{version:010d}",  # noqa: E231
        "report_id": report_id,
        "name": "My Report",
        "version": version,
        "config": {"rows": []},
        "created_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "updated_by": "user@example.com",
        "access": {"scope": "public"},
        "comment": None,
    }


def _latest_item(report_id="123", version=1):
    return {
        "PK": f"REPORT#{report_id}",
        "SK": "#LATEST",
        "report_id": report_id,
        "name": "My Report",
        "version": version,
        "config": {"rows": []},
        "created_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "updated_by": "user@example.com",
        "access": {"scope": "public"},
        "comment": None,
    }


def _metadata_item(report_id="123", current_version=1):
    return {
        "PK": f"REPORT#{report_id}",
        "SK": "#METADATA",
        "report_id": report_id,
        "name": "My Report",
        "current_version": current_version,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "updated_by": "user@example.com",
        "access": {"scope": "public"},
    }


def _skillset_metadata_item(skillset_id="ss1", current_version=1, enabled=True):
    return {
        "PK": f"SKILLSET#{skillset_id}",
        "SK": "#METADATA",
        "skillset_id": skillset_id,
        "name": "Skillset",
        "description": "desc",
        "enabled": enabled,
        "current_version": current_version,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": "u1",
        "updated_by": "u1",
    }


def _skillset_version_item(skillset_id="ss1", version=1):
    return {
        "PK": f"SKILLSET#{skillset_id}",
        "SK": f"VERSION#{version:010d}",  # noqa: E231
        "skillset_id": skillset_id,
        "name": "Skillset",
        "description": "desc",
        "enabled": True,
        "version": version,
        "created_at": "2024-01-01T00:00:00+00:00",
        "created_by": "u1",
        "comment": None,
    }


def _skill_metadata_item(skill_id="sk1", skillset_id="ss1", current_version=1, enabled=True):
    return {
        "PK": f"SKILL#{skill_id}",
        "SK": "#METADATA",
        "skill_id": skill_id,
        "skillset_id": skillset_id,
        "name": "Skill",
        "description": "desc",
        "template": "Hello {{topic}}",
        "parameters": [{"name": "topic", "type": "string", "required": True, "default": None}],
        "triggers": ["say hello"],
        "tools_required": ["toolset__tool"],
        "enabled": enabled,
        "current_version": current_version,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": "u1",
        "updated_by": "u1",
    }


def _skill_version_item(skill_id="sk1", skillset_id="ss1", version=1):
    return {
        "PK": f"SKILL#{skill_id}",
        "SK": f"VERSION#{version:010d}",  # noqa: E231
        "skill_id": skill_id,
        "skillset_id": skillset_id,
        "name": "Skill",
        "description": "desc",
        "template": "Hello {{topic}}",
        "parameters": [{"name": "topic", "type": "string", "required": True, "default": None}],
        "triggers": [],
        "tools_required": [],
        "enabled": True,
        "version": version,
        "created_at": "2024-01-01T00:00:00+00:00",
        "created_by": "u1",
        "comment": None,
    }


def _action_confirmation_item(
    confirmation_id: str = "confirm-1",
    status: str = "pending",
    created_at: str = "2024-01-01T00:00:00+00:00",
    expires_at: str = "2099-01-01T00:30:00+00:00",
) -> dict[str, object]:
    return {
        "confirmation_id": confirmation_id,
        "user_id": "user-1",
        "source": "mcp",
        "session_key": "session-1",
        "tool_name": "reports__delete",
        "action": "delete",
        "resource_type": "report",
        "resource_id": "report-1",
        "arguments": {"report_id": "report-1"},
        "arguments_hash": "hash-1",
        "status": status,
        "created_at": created_at,
        "expires_at": expires_at,
    }


# ---------------------------------------------------------------------------
# list_reports
# ---------------------------------------------------------------------------


async def test_list_reports_returns_items(patch_table, store):
    patch_table.query.return_value = {
        "Items": [
            {
                "PK": "REPORT_LIST",
                "SK": "REPORT#123",
                "report_id": "123",
                "name": "My Report",
                "current_version": 1,
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "created_by": "user@example.com",
                "updated_by": "user@example.com",
                "access": {"scope": "public"},
            }
        ]
    }
    result = await store.list_reports()
    assert len(result) == 1
    assert isinstance(result[0], ReportListItem)
    assert result[0].report_id == "123"
    assert result[0].name == "My Report"
    assert result[0].current_version == 1


async def test_list_reports_empty(patch_table, store):
    patch_table.query.return_value = {"Items": []}
    result = await store.list_reports()
    assert result == []


async def test_list_reports_follows_pagination(patch_table, store):
    # DynamoDB caps a Query response at 1 MB; without following
    # LastEvaluatedKey the report list silently truncates.
    def _list_item(report_id: str) -> dict:
        return {
            "PK": "REPORT_LIST",
            "SK": f"REPORT#{report_id}",
            "report_id": report_id,
            "name": f"Report {report_id}",
            "current_version": 1,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "created_by": "user@example.com",
            "updated_by": "user@example.com",
            "access": {"scope": "public"},
        }

    patch_table.query.side_effect = [
        {"Items": [_list_item("1")], "LastEvaluatedKey": {"PK": "REPORT_LIST", "SK": "REPORT#1"}},
        {"Items": [_list_item("2")]},
    ]
    result = await store.list_reports()
    assert [item.report_id for item in result] == ["1", "2"]
    assert patch_table.query.call_count == 2
    assert patch_table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {
        "PK": "REPORT_LIST",
        "SK": "REPORT#1",
    }


async def test_list_reports_coerces_decimal(patch_table, store):
    patch_table.query.return_value = {
        "Items": [
            {
                "PK": "REPORT_LIST",
                "SK": "REPORT#123",
                "report_id": "123",
                "name": "My Report",
                "current_version": Decimal("3"),
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "created_by": "user@example.com",
                "updated_by": "user@example.com",
                "access": {"scope": "public"},
            }
        ]
    }
    result = await store.list_reports()
    assert result[0].current_version == 3
    assert isinstance(result[0].current_version, int)


# ---------------------------------------------------------------------------
# get_report_latest
# ---------------------------------------------------------------------------


async def test_get_report_latest_found(patch_table, store):
    patch_table.get_item.return_value = {"Item": _version_item()}
    result = await store.get_report_latest("123")
    assert isinstance(result, ReportVersion)
    assert result.version == 1


async def test_get_report_latest_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.get_report_latest("missing")
    assert result is None


async def test_get_report_latest_queries_correct_sk(patch_table, store):
    patch_table.get_item.side_effect = [{"Item": _metadata_item(report_id="abc")}, {}]
    await store.get_report_latest("abc")
    assert patch_table.get_item.call_args_list[-1].kwargs == {"Key": {"PK": "REPORT#abc", "SK": "#LATEST"}}


# ---------------------------------------------------------------------------
# get_report_version
# ---------------------------------------------------------------------------


async def test_get_report_version_found(patch_table, store):
    patch_table.get_item.return_value = {"Item": _version_item(version=2)}
    result = await store.get_report_version("123", 2)
    assert isinstance(result, ReportVersion)
    assert result.version == 2


async def test_get_report_version_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.get_report_version("123", 99)
    assert result is None


async def test_get_report_version_uses_zero_padded_sk(patch_table, store):
    patch_table.get_item.side_effect = [{"Item": _metadata_item(report_id="abc")}, {}]
    await store.get_report_version("abc", 5)
    assert patch_table.get_item.call_args_list[-1].kwargs == {"Key": {"PK": "REPORT#abc", "SK": "VERSION#0000000005"}}


# ---------------------------------------------------------------------------
# list_report_versions
# ---------------------------------------------------------------------------


async def test_list_report_versions_returns_items(patch_table, store):
    patch_table.get_item.return_value = {"Item": _metadata_item()}
    patch_table.query.return_value = {"Items": [_version_item(version=2), _version_item(version=1)]}
    result = await store.list_report_versions("123")
    assert len(result) == 2
    assert result[0].version == 2
    assert result[1].version == 1


async def test_list_report_versions_scan_index_forward_false(patch_table, store):
    patch_table.get_item.return_value = {"Item": _metadata_item(report_id="abc")}
    patch_table.query.return_value = {"Items": []}
    await store.list_report_versions("abc")
    call_kwargs = patch_table.query.call_args[1]
    assert call_kwargs.get("ScanIndexForward") is False


# ---------------------------------------------------------------------------
# create_report
# ---------------------------------------------------------------------------


async def test_create_report_returns_list_item(patch_table, store, mocker):
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="snowflake123",
    )
    result = await store.create_report(
        name="My Report",
        created_by="user@example.com",
    )

    assert isinstance(result, ReportListItem)
    assert result.report_id == "snowflake123"
    assert result.name == "My Report"
    assert result.current_version == 1


async def test_create_report_writes_initial_version_transactionally(patch_table, store, mocker):
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="rid",
    )
    await store.create_report(name="My Report", created_by="u@x.com")

    patch_table.meta.client.transact_write_items.assert_called_once()
    items = patch_table.meta.client.transact_write_items.call_args[1]["TransactItems"]
    assert len(items) == 4


async def test_create_report_correct_sks(patch_table, store, mocker):
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="rid",
    )
    await store.create_report(name="My Report", created_by="u@x.com")

    items = patch_table.meta.client.transact_write_items.call_args[1]["TransactItems"]
    sks = [i["Put"]["Item"]["SK"] for i in items]
    assert "#METADATA" in sks
    assert "#LATEST" in sks
    assert "VERSION#0000000001" in sks
    # list item SK is the report_id prefixed with REPORT#
    pks = [i["Put"]["Item"]["PK"] for i in items]
    assert "REPORT_LIST" in pks


# ---------------------------------------------------------------------------
# save_report_version
# ---------------------------------------------------------------------------


async def test_save_report_version_returns_none_when_report_missing(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.save_report_version(
        report_id="missing",
        config={},
        created_by="u@x.com",
    )
    assert result is None


async def test_save_report_version_increments_version(patch_table, store):
    patch_table.get_item.return_value = {"Item": _metadata_item(current_version=3)}

    result = await store.save_report_version(
        report_id="123",
        config={"rows": [{"name": "new"}]},
        created_by="editor@example.com",
        comment="v4",
    )

    assert result.version == 4
    assert result.name == "My Report"
    assert result.config == {"name": "My Report", "rows": [{"name": "new"}]}
    assert result.comment == "v4"


async def test_save_report_version_updates_report_name_from_config(patch_table, store):
    patch_table.get_item.return_value = {"Item": _metadata_item(current_version=1)}

    result = await store.save_report_version(
        report_id="123",
        config={"name": "Renamed Report", "rows": []},
        created_by="editor@example.com",
    )

    assert result.name == "Renamed Report"
    items = patch_table.meta.client.transact_write_items.call_args[1]["TransactItems"]
    stored_items = [item["Put"]["Item"] for item in items]
    metadata_item = next(item for item in stored_items if item["PK"] == "REPORT#123" and item["SK"] == "#METADATA")
    list_item = next(item for item in stored_items if item["PK"] == "REPORT_LIST" and item["SK"] == "REPORT#123")
    version_item = next(
        item for item in stored_items if item["PK"] == "REPORT#123" and item["SK"] == "VERSION#0000000002"
    )

    assert metadata_item["name"] == "Renamed Report"
    assert list_item["name"] == "Renamed Report"
    assert version_item["name"] == "Renamed Report"
    assert version_item["config"]["name"] == "Renamed Report"


async def test_save_report_version_ignores_blank_config_name(patch_table, store):
    patch_table.get_item.return_value = {"Item": _metadata_item(current_version=1)}

    result = await store.save_report_version(
        report_id="123",
        config={"name": "   ", "rows": []},
        created_by="editor@example.com",
    )

    assert result.name == "My Report"


async def test_save_report_version_writes_five_items_transactionally(patch_table, store):
    patch_table.get_item.return_value = {"Item": _metadata_item(current_version=1)}

    await store.save_report_version(report_id="123", config={}, created_by="u@x.com")

    patch_table.meta.client.transact_write_items.assert_called_once()
    items = patch_table.meta.client.transact_write_items.call_args[1]["TransactItems"]
    # version, latest, metadata, list = 4 items
    assert len(items) == 4
    patch_table.update_item.assert_not_called()


# ---------------------------------------------------------------------------
# _floats_to_decimal helper
# ---------------------------------------------------------------------------


def test_floats_to_decimal_converts_float():
    result = dynamodb_module._floats_to_decimal({"size": 2.0, "threshold": 0.5})
    assert result == {"size": Decimal("2.0"), "threshold": Decimal("0.5")}
    assert isinstance(result["size"], Decimal)


def test_floats_to_decimal_handles_nested():
    result = dynamodb_module._floats_to_decimal({"rows": [{"size": 12.0, "nested": {"value": 1.5}}]})
    assert result["rows"][0]["size"] == Decimal("12.0")
    assert result["rows"][0]["nested"]["value"] == Decimal("1.5")


def test_floats_to_decimal_leaves_non_floats_unchanged():
    result = dynamodb_module._floats_to_decimal({"name": "CVEs", "version": 1, "enabled": True, "comment": None})
    assert result == {"name": "CVEs", "version": 1, "enabled": True, "comment": None}


async def test_save_report_version_converts_floats_in_config(patch_table, store):
    patch_table.get_item.return_value = {"Item": _metadata_item(current_version=0)}
    await store.save_report_version(report_id="123", config={"rows": [{"size": 2.0}]}, created_by="u@x.com")

    items = patch_table.meta.client.transact_write_items.call_args[1]["TransactItems"]
    version_item = next(i for i in items if i["Put"]["Item"]["SK"] == "VERSION#0000000001")
    # _floats_to_decimal converts 2.0 → Decimal("2.0") so that the resource
    # layer's TypeSerializer produces a valid N attribute (float is rejected).
    size = version_item["Put"]["Item"]["config"]["rows"][0]["size"]
    assert size == Decimal("2.0")


# ---------------------------------------------------------------------------
# _version_sk helper
# ---------------------------------------------------------------------------


def test_version_sk_zero_pads():
    assert dynamodb_module._version_sk(1) == "VERSION#0000000001"
    assert dynamodb_module._version_sk(999) == "VERSION#0000000999"
    assert dynamodb_module._version_sk(1_000_000_000) == "VERSION#1000000000"


# ---------------------------------------------------------------------------
# initialize (create_table_if_not_exists)
# ---------------------------------------------------------------------------


async def test_initialize_skips_when_table_present(store, mocker):
    mock_resource = MagicMock()
    mock_table = MagicMock()
    mock_table.name = "seizu-reports"
    mock_resource.tables.all.return_value = [mock_table]
    mocker.patch(
        "reporting.services.report_store.dynamodb.get_boto_resource",
        return_value=mock_resource,
    )
    mocker.patch("reporting.settings.DYNAMODB_TABLE_NAME", "seizu-reports")

    await store.initialize()

    mock_resource.create_table.assert_not_called()


async def test_initialize_creates_when_missing(store, mocker):
    mock_resource = MagicMock()
    mock_resource.tables.all.return_value = []
    mocker.patch(
        "reporting.services.report_store.dynamodb.get_boto_resource",
        return_value=mock_resource,
    )
    mocker.patch("reporting.settings.DYNAMODB_TABLE_NAME", "seizu-reports")

    await store.initialize()

    mock_resource.create_table.assert_called_once()
    kwargs = mock_resource.create_table.call_args[1]
    assert kwargs["TableName"] == "seizu-reports"
    assert kwargs["BillingMode"] == "PAY_PER_REQUEST"


async def test_initialize_handles_race_condition(store, mocker):
    mock_resource = MagicMock()
    mock_resource.tables.all.return_value = []
    mock_resource.create_table.side_effect = mock_resource.meta.client.exceptions.ResourceInUseException(
        {"Error": {"Code": "ResourceInUseException", "Message": ""}},
        "CreateTable",
    )
    mocker.patch(
        "reporting.services.report_store.dynamodb.get_boto_resource",
        return_value=mock_resource,
    )
    mocker.patch("reporting.settings.DYNAMODB_TABLE_NAME", "seizu-reports")

    # Should not raise
    await store.initialize()


# ---------------------------------------------------------------------------
# _strip_none helper
# ---------------------------------------------------------------------------


def test_strip_none_removes_top_level_none():
    result = dynamodb_module._strip_none({"a": 1, "b": None, "c": "x"})
    assert result == {"a": 1, "c": "x"}


def test_strip_none_removes_nested_none_in_dict():
    result = dynamodb_module._strip_none({"a": {"b": None, "c": "x"}})
    assert result == {"a": {"c": "x"}}


def test_strip_none_removes_none_in_list():
    result = dynamodb_module._strip_none({"a": [None, "x", None]})
    assert result == {"a": ["x"]}


def test_strip_none_removes_deeply_nested_none():
    result = dynamodb_module._strip_none(
        {
            "rows": [
                {
                    "panels": [
                        {
                            "size": Decimal("2.0"),
                            "threshold": None,
                            "caption": None,
                        }
                    ]
                }
            ]
        }
    )
    assert result == {"rows": [{"panels": [{"size": Decimal("2.0")}]}]}


def test_strip_none_leaves_falsy_non_none_values():
    result = dynamodb_module._strip_none({"a": 0, "b": "", "c": False, "d": []})
    assert result == {"a": 0, "b": "", "c": False, "d": []}


# ---------------------------------------------------------------------------
# _strip_none — no None values after stripping (regression: model_dump() Nones)
# ---------------------------------------------------------------------------


def _contains_none(obj) -> bool:
    """Recursively check if any value in a plain Python dict/list is None."""
    if isinstance(obj, dict):
        return any(v is None or _contains_none(v) for v in obj.values())
    if isinstance(obj, list):
        return any(item is None or _contains_none(item) for item in obj)
    return False


def test_strip_none_removes_nested_nones_from_config():
    item = {
        "PK": "REPORT#123",
        "config": {
            "name": "My Report",
            "rows": [
                {
                    "name": "row1",
                    "panels": [
                        {
                            "type": "count",
                            "size": Decimal("2.4"),
                            "threshold": None,
                            "caption": None,
                            "bar_settings": None,
                        }
                    ],
                }
            ],
        },
    }
    result = dynamodb_module._strip_none(item)
    panel = result["config"]["rows"][0]["panels"][0]
    assert "threshold" not in panel
    assert "caption" not in panel
    assert "bar_settings" not in panel
    assert panel["size"] == Decimal("2.4")


# ---------------------------------------------------------------------------
# get_dashboard_report_id
# ---------------------------------------------------------------------------


async def test_get_dashboard_report_id_returns_none_when_not_set(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.get_dashboard_report_id() is None


async def test_get_dashboard_report_id_returns_report_id(patch_table, store):
    patch_table.get_item.return_value = {"Item": {"PK": "#DASHBOARD", "SK": "#POINTER", "report_id": "abc123"}}
    assert await store.get_dashboard_report_id() == "abc123"


async def test_get_dashboard_report_id_queries_correct_key(patch_table, store):
    patch_table.get_item.return_value = {}
    await store.get_dashboard_report_id()
    patch_table.get_item.assert_called_once_with(Key={"PK": "#DASHBOARD", "SK": "#POINTER"})


# ---------------------------------------------------------------------------
# set_dashboard_report
# ---------------------------------------------------------------------------


async def test_set_dashboard_report_returns_false_when_report_missing(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.set_dashboard_report("nonexistent") is False


async def test_set_dashboard_report_returns_true_when_report_exists(patch_table, store):
    patch_table.get_item.return_value = {"Item": _metadata_item()}
    assert await store.set_dashboard_report("123") is True


async def test_set_dashboard_report_writes_pointer_item(patch_table, store):
    patch_table.get_item.return_value = {"Item": _metadata_item(report_id="rid1")}
    await store.set_dashboard_report("rid1")
    patch_table.put_item.assert_called_once()
    item = patch_table.put_item.call_args[1]["Item"]
    assert item["report_id"] == "rid1"
    assert item["PK"] == "#DASHBOARD"
    assert item["SK"] == "#POINTER"


# ---------------------------------------------------------------------------
# get_dashboard_report
# ---------------------------------------------------------------------------


async def test_get_dashboard_report_returns_none_when_not_set(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.get_dashboard_report() is None


async def test_get_dashboard_report_returns_report_version(patch_table, store):
    patch_table.get_item.side_effect = [
        {"Item": {"PK": "#DASHBOARD", "SK": "#POINTER", "report_id": "123"}},
        {"Item": _metadata_item(report_id="123")},
        {"Item": _latest_item(report_id="123")},
    ]
    result = await store.get_dashboard_report()
    assert isinstance(result, ReportVersion)
    assert result.report_id == "123"


# ---------------------------------------------------------------------------
# save_report_version — correct sort keys
# ---------------------------------------------------------------------------


async def test_save_report_version_correct_sks(patch_table, store):
    patch_table.get_item.return_value = {"Item": _metadata_item(current_version=2)}
    await store.save_report_version(report_id="123", config={}, created_by="u@x.com")
    items = patch_table.meta.client.transact_write_items.call_args[1]["TransactItems"]
    sks = [i["Put"]["Item"]["SK"] for i in items]
    assert "#LATEST" in sks
    assert "VERSION#0000000003" in sks
    assert "REPORT#123" in sks
    assert "#METADATA" in sks


# ---------------------------------------------------------------------------
# create_report — config with nested Nones (model_dump()-style)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# delete_report
# ---------------------------------------------------------------------------


async def test_delete_report_returns_false_when_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.delete_report("missing") is False


async def test_delete_report_returns_true_on_success(patch_table, store):
    patch_table.get_item.side_effect = [
        {"Item": _metadata_item()},  # metadata check
        {},  # dashboard pointer check — not set
    ]
    patch_table.query.return_value = {
        "Items": [
            {"PK": "REPORT#123", "SK": "#METADATA"},
            {"PK": "REPORT#123", "SK": "#LATEST"},
            {"PK": "REPORT#123", "SK": "VERSION#0000000001"},
        ]
    }
    assert await store.delete_report("123") is True


async def test_delete_report_clears_dashboard_pointer(patch_table, store):
    patch_table.get_item.side_effect = [
        {"Item": _metadata_item()},  # metadata check
        {"Item": {"PK": "#DASHBOARD", "SK": "#POINTER", "report_id": "123"}},
    ]
    patch_table.query.return_value = {"Items": [{"PK": "REPORT#123", "SK": "#METADATA"}]}
    await store.delete_report("123")
    # batch_writer context manager calls delete_item; verify it was called
    batch = patch_table.batch_writer.return_value.__enter__.return_value
    deleted_keys = [call[1]["Key"] for call in batch.delete_item.call_args_list]
    assert {"PK": "#DASHBOARD", "SK": "#POINTER"} in deleted_keys


async def test_save_report_version_nested_none_config_produces_no_nones(patch_table, store):
    """Config from Pydantic model_dump() may contain nested None values for
    optional fields; verify _strip_none removes them before they reach DynamoDB
    (which would convert None to {"NULL": True}, rejected by DynamoDB Local)."""
    patch_table.get_item.return_value = {"Item": _metadata_item(current_version=0)}
    config = {
        "inputs": [],
        "rows": [
            {
                "name": "row1",
                "panels": [
                    {
                        "type": "count",
                        "cypher": "cves-total",
                        "details_cypher": None,
                        "params": [
                            {
                                "name": "base_severity",
                                "input_id": None,
                                "value": "CRITICAL",
                            }
                        ],
                        "caption": "Total CVEs",
                        "table_id": None,
                        "markdown": None,
                        "size": 2.4,
                        "threshold": None,
                        "bar_settings": None,
                        "pie_settings": None,
                    }
                ],
            }
        ],
    }
    await store.save_report_version(report_id="123", config=config, created_by="u@x.com")
    items = patch_table.meta.client.transact_write_items.call_args[1]["TransactItems"]
    for item_op in items:
        assert not _contains_none(item_op["Put"]["Item"])


# ---------------------------------------------------------------------------
# get_or_create_user
# ---------------------------------------------------------------------------


def _user_profile_item(user_id="uid1"):
    return {
        "PK": f"USER#{user_id}",
        "SK": "#METADATA",
        "user_id": user_id,
        "sub": "sub123",
        "iss": "https://idp.example.com",
        "email": "alice@example.com",
        "display_name": "Alice",
        "created_at": "2024-01-01T00:00:00+00:00",
        "last_login": "2024-01-01T00:00:00+00:00",
    }


async def test_get_or_create_user_creates_new_user(patch_table, store, mocker):
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="uid1",
    )
    # Lookup returns nothing (new user)
    patch_table.get_item.return_value = {}

    from reporting.schema.report_config import User

    user = await store.get_or_create_user(
        sub="sub123",
        iss="https://idp.example.com",
        email="alice@example.com",
        display_name="Alice",
    )
    assert isinstance(user, User)
    assert user.user_id == "uid1"
    assert user.sub == "sub123"
    assert user.email == "alice@example.com"


async def test_get_or_create_user_creates_lookup_and_profile_items(patch_table, store, mocker):
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="uid1",
    )
    patch_table.get_item.return_value = {}

    await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="alice@example.com")

    # put_item called twice: once for lookup (conditional), once for profile
    assert patch_table.put_item.call_count == 2
    call_items = [c[1]["Item"] for c in patch_table.put_item.call_args_list]
    pks = {item["PK"] for item in call_items}
    assert "USER_LOOKUP" in pks
    assert "USER#uid1" in pks


async def test_get_or_create_user_returns_existing_user_on_lookup_hit(patch_table, store):
    lookup_item = {
        "PK": "USER_LOOKUP",
        "SK": "https://idp.example.com#sub123",
        "user_id": "uid1",
    }
    profile_item = {
        "PK": "USER#uid1",
        "SK": "#METADATA",
        "user_id": "uid1",
        "sub": "sub123",
        "iss": "https://idp.example.com",
        "email": "alice@example.com",
        "created_at": "2024-01-01T00:00:00+00:00",
        "last_login": "2024-01-01T00:00:00+00:00",
    }
    # First call: lookup hit; second call: profile fetch
    patch_table.get_item.side_effect = [
        {"Item": lookup_item},
        {"Item": profile_item},
    ]

    from reporting.schema.report_config import User

    user = await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="alice@example.com")
    assert isinstance(user, User)
    assert user.user_id == "uid1"
    patch_table.put_item.assert_not_called()
    patch_table.update_item.assert_not_called()


# ---------------------------------------------------------------------------
# get_user
# ---------------------------------------------------------------------------


async def test_get_user_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.get_user("nonexistent") is None


async def test_get_user_returns_user(patch_table, store):
    patch_table.get_item.return_value = {"Item": _user_profile_item()}
    from reporting.schema.report_config import User

    user = await store.get_user("uid1")
    assert isinstance(user, User)
    assert user.user_id == "uid1"
    assert user.email == "alice@example.com"


# ---------------------------------------------------------------------------
# archive_user
# ---------------------------------------------------------------------------


async def test_archive_user_returns_false_when_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.archive_user("nonexistent") is False


async def test_archive_user_updates_archived_at(patch_table, store):
    patch_table.get_item.return_value = {"Item": _user_profile_item()}
    result = await store.archive_user("uid1")
    assert result is True
    patch_table.update_item.assert_called_once()
    kwargs = patch_table.update_item.call_args[1]
    assert kwargs["Key"] == {"PK": "USER#uid1", "SK": "#METADATA"}
    assert "archived_at" in kwargs["UpdateExpression"]


# ---------------------------------------------------------------------------
# Scheduled queries
# ---------------------------------------------------------------------------


def _sq_metadata_item(sq_id="sq1", current_version=1):
    return {
        "PK": f"SQ#{sq_id}",
        "SK": "#METADATA",
        "scheduled_query_id": sq_id,
        "name": "My Query",
        "cypher": "MATCH (n) RETURN n",
        "params": [],
        "frequency": 60,
        "watch_scans": [],
        "enabled": True,
        "actions": [{"action_type": "log", "action_config": {}}],
        "current_version": current_version,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "updated_by": "user@example.com",
    }


def _sq_version_dynamo_item(sq_id="sq1", version=1):
    return {
        "PK": f"SQ#{sq_id}",
        "SK": f"VERSION#{version:010d}",  # noqa: E231
        "scheduled_query_id": sq_id,
        "name": "My Query",
        "version": version,
        "cypher": "MATCH (n) RETURN n",
        "params": [],
        "frequency": 60,
        "watch_scans": [],
        "enabled": True,
        "actions": [{"action_type": "log", "action_config": {}}],
        "created_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "comment": None,
    }


def _scheduled_chat_item(sc_id="sc1", created_by="user-1"):
    return {
        "PK": "SCHEDULED_CHAT_LIST",
        "SK": f"SCHEDULED_CHAT#{sc_id}",
        "scheduled_chat_id": sc_id,
        "name": f"Chat {sc_id}",
        "prompt": "Summarize",
        "schedule": {"type": "hourly", "interval_hours": 1},
        "watch_scans": [],
        "enabled": True,
        "current_version": 1,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": created_by,
        "updated_by": created_by,
        "last_errors": [],
    }


async def test_list_scheduled_chats_paginates(patch_table, store):
    patch_table.query.side_effect = [
        {
            "Items": [_scheduled_chat_item("sc1")],
            "LastEvaluatedKey": {"PK": "SCHEDULED_CHAT_LIST", "SK": "SCHEDULED_CHAT#sc1"},
        },
        {"Items": [_scheduled_chat_item("sc2")]},
    ]

    result = await store.list_scheduled_chats()

    assert [item.scheduled_chat_id for item in result] == ["sc1", "sc2"]
    assert patch_table.query.call_count == 2
    assert patch_table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {
        "PK": "SCHEDULED_CHAT_LIST",
        "SK": "SCHEDULED_CHAT#sc1",
    }


async def test_delete_scheduled_chat_removes_paginated_sessions(patch_table, store):
    patch_table.get_item.return_value = {
        "Item": {
            **_scheduled_chat_item(),
            "PK": "SCHEDULED_CHAT#sc1",
            "SK": "#METADATA",
        }
    }

    def query_side_effect(**kwargs):
        pk = kwargs["ExpressionAttributeValues"][":pk"]
        start = kwargs.get("ExclusiveStartKey")
        if pk == "SCHEDULED_CHAT#sc1":
            if start:
                return {"Items": [{"PK": pk, "SK": "VERSION#0000000001"}]}
            return {
                "Items": [{"PK": pk, "SK": "#METADATA"}],
                "LastEvaluatedKey": {"PK": pk, "SK": "#METADATA"},
            }
        if pk == "CHAT_SESSION_LIST#user-1":
            if start:
                return {
                    "Items": [
                        {
                            "PK": pk,
                            "SK": "UPDATED#2#THREAD#t2",
                            "thread_id": "t2",
                        }
                    ]
                }
            return {
                "Items": [
                    {
                        "PK": pk,
                        "SK": "UPDATED#1#THREAD#t1",
                        "thread_id": "t1",
                    }
                ],
                "LastEvaluatedKey": {"PK": pk, "SK": "UPDATED#1#THREAD#t1"},
            }
        raise AssertionError(f"Unexpected partition: {pk}")

    patch_table.query.side_effect = query_side_effect
    batch = MagicMock()
    patch_table.batch_writer.return_value.__enter__ = MagicMock(return_value=batch)
    patch_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)

    assert await store.delete_scheduled_chat("sc1") is True

    deleted = [call.kwargs["Key"] for call in batch.delete_item.call_args_list]
    assert {"PK": "CHAT_SESSION#user-1", "SK": "t1"} in deleted
    assert {"PK": "CHAT_SESSION#user-1", "SK": "t2"} in deleted
    assert {"PK": "CHAT_SESSION_LIST#user-1", "SK": "UPDATED#1#THREAD#t1"} in deleted
    assert {"PK": "CHAT_SESSION_LIST#user-1", "SK": "UPDATED#2#THREAD#t2"} in deleted


async def test_partial_scheduled_chat_result_clears_stale_errors(patch_table, store):
    patch_table.get_item.return_value = {
        "Item": {
            **_scheduled_chat_item(),
            "last_errors": [{"timestamp": "old", "error": "boom"}],
        }
    }

    await store.record_scheduled_chat_result("sc1", "partial")

    assert patch_table.update_item.call_count == 2
    for call in patch_table.update_item.call_args_list:
        assert call.kwargs["ExpressionAttributeValues"][":errors"] == []


async def test_get_scheduled_chat_returns_item(patch_table, store):
    patch_table.get_item.return_value = {
        "Item": {
            **_scheduled_chat_item(),
            "PK": "SCHEDULED_CHAT#sc1",
            "SK": "#METADATA",
        }
    }

    item = await store.get_scheduled_chat("sc1")

    assert item is not None
    assert item.scheduled_chat_id == "sc1"
    assert item.name == "Chat sc1"


async def test_get_scheduled_chat_returns_none_when_missing(patch_table, store):
    patch_table.get_item.return_value = {}

    item = await store.get_scheduled_chat("sc1")

    assert item is None


async def test_create_scheduled_chat_writes_three_items(patch_table, store, mocker):
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="sc-new",
    )

    item = await store.create_scheduled_chat(
        name="My Chat",
        prompt="Summarize",
        schedule={"type": "hourly", "interval_hours": 2},
        watch_scans=[],
        enabled=True,
        created_by="user-1",
    )

    assert item.scheduled_chat_id == "sc-new"
    assert item.name == "My Chat"
    assert patch_table.put_item.call_count == 3


async def test_update_scheduled_chat_bumps_version(patch_table, store):
    patch_table.get_item.return_value = {
        "Item": {
            **_scheduled_chat_item(),
            "PK": "SCHEDULED_CHAT#sc1",
            "SK": "#METADATA",
        }
    }

    item = await store.update_scheduled_chat(
        "sc1",
        name="Updated",
        prompt="New prompt",
        schedule=None,
        watch_scans=[],
        enabled=False,
        updated_by="user-1",
    )

    assert item is not None
    assert item.name == "Updated"
    assert item.current_version == 2
    assert patch_table.put_item.call_count == 3


async def test_update_scheduled_chat_returns_none_when_missing(patch_table, store):
    patch_table.get_item.return_value = {}

    item = await store.update_scheduled_chat(
        "sc1",
        name="Updated",
        prompt="New prompt",
        schedule=None,
        watch_scans=[],
        enabled=True,
        updated_by="user-1",
    )

    assert item is None


_SQ_KWARGS = dict(
    name="My Query",
    cypher="MATCH (n) RETURN n",
    params=[],
    frequency=60,
    schedule=None,
    watch_scans=[],
    enabled=True,
    actions=[{"action_type": "log", "action_config": {}}],
    created_by="user@example.com",
)


async def test_list_scheduled_queries_empty(patch_table, store):
    patch_table.query.return_value = {"Items": []}
    result = await store.list_scheduled_queries()
    assert result == []


async def test_list_scheduled_queries_returns_items(patch_table, store):
    patch_table.query.return_value = {"Items": [_sq_metadata_item()]}
    result = await store.list_scheduled_queries()
    assert len(result) == 1
    assert result[0].scheduled_query_id == "sq1"
    assert result[0].name == "My Query"
    assert result[0].current_version == 1


async def test_get_scheduled_query_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _sq_metadata_item()}
    result = await store.get_scheduled_query("sq1")
    assert result is not None
    assert result.scheduled_query_id == "sq1"
    assert result.name == "My Query"


async def test_get_scheduled_query_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.get_scheduled_query("nonexistent")
    assert result is None


async def test_create_scheduled_query(patch_table, store, mocker):
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="sq1",
    )
    patch_table.meta.client.transact_write_items = MagicMock()
    result = await store.create_scheduled_query(**_SQ_KWARGS)
    assert result.scheduled_query_id == "sq1"
    assert result.current_version == 1
    assert result.created_by == "user@example.com"
    assert result.updated_by == "user@example.com"
    assert patch_table.meta.client.transact_write_items.call_count == 1


async def test_update_scheduled_query_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _sq_metadata_item(current_version=1)}
    patch_table.meta.client.transact_write_items = MagicMock()
    result = await store.update_scheduled_query(
        sq_id="sq1",
        name="Updated",
        cypher="MATCH (n) RETURN n LIMIT 1",
        params=[],
        frequency=120,
        schedule=None,
        watch_scans=[],
        enabled=False,
        actions=[],
        updated_by="editor@example.com",
        comment="v2",
    )
    assert result is not None
    assert result.current_version == 2
    assert result.updated_by == "editor@example.com"


async def test_update_scheduled_query_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.update_scheduled_query(
        sq_id="nonexistent",
        name="X",
        cypher="MATCH (n) RETURN n",
        params=[],
        frequency=60,
        schedule=None,
        watch_scans=[],
        enabled=True,
        actions=[],
        updated_by="u@x.com",
    )
    assert result is None


async def test_list_scheduled_query_versions_empty(patch_table, store):
    patch_table.query.return_value = {"Items": []}
    result = await store.list_scheduled_query_versions("sq1")
    assert result == []


async def test_list_scheduled_query_versions_returns_items(patch_table, store):
    patch_table.query.return_value = {
        "Items": [
            _sq_version_dynamo_item(version=2),
            _sq_version_dynamo_item(version=1),
        ]
    }
    result = await store.list_scheduled_query_versions("sq1")
    assert len(result) == 2
    assert result[0].version == 2
    assert result[1].version == 1


async def test_get_scheduled_query_version_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _sq_version_dynamo_item(version=1)}
    result = await store.get_scheduled_query_version("sq1", 1)
    assert result is not None
    assert result.version == 1
    assert result.scheduled_query_id == "sq1"


async def test_get_scheduled_query_version_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.get_scheduled_query_version("sq1", 99)
    assert result is None


async def test_delete_scheduled_query_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _sq_metadata_item()}
    patch_table.query.return_value = {
        "Items": [
            {"PK": "SQ#sq1", "SK": "#METADATA"},
            {"PK": "SQ#sq1", "SK": "VERSION#0000000001"},
        ]
    }
    batch_mock = MagicMock()
    patch_table.batch_writer.return_value.__enter__ = MagicMock(return_value=batch_mock)
    patch_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)
    result = await store.delete_scheduled_query("sq1")
    assert result is True
    assert batch_mock.delete_item.call_count == 3  # 2 items + 1 list item


async def test_delete_scheduled_query_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.delete_scheduled_query("nonexistent")
    assert result is False


async def test_acquire_scheduled_query_lock_no_previous(patch_table, store):
    """Lock acquired when no previous last_scheduled_at exists."""
    patch_table.update_item.return_value = {}
    result = await store.acquire_scheduled_query_lock("sq1", None)
    assert result is True
    assert patch_table.update_item.call_count == 2


async def test_acquire_scheduled_query_lock_with_expected(patch_table, store):
    """Lock acquired when last_scheduled_at matches expected."""
    patch_table.update_item.return_value = {}
    result = await store.acquire_scheduled_query_lock("sq1", "2024-01-01T00:00:00+00:00")
    assert result is True
    assert patch_table.update_item.call_count == 2


async def test_acquire_scheduled_query_lock_race(patch_table, store):
    """Lock not acquired when condition check fails (another worker won)."""
    import botocore.exceptions

    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}},
        "UpdateItem",
    )
    patch_table.update_item.side_effect = err
    result = await store.acquire_scheduled_query_lock("sq1", None)
    assert result is False


async def test_record_scheduled_query_result_success(patch_table, store):
    """Success result clears last_errors."""
    patch_table.get_item.return_value = {
        "Item": {
            **_sq_metadata_item(),
            "last_errors": [{"timestamp": "t", "error": "e"}],
        }
    }
    await store.record_scheduled_query_result("sq1", "success")
    assert patch_table.update_item.call_count == 2
    call_kwargs = patch_table.update_item.call_args_list[0][1]
    assert call_kwargs["ExpressionAttributeValues"][":errors"] == []


async def test_record_scheduled_query_result_failure(patch_table, store):
    """Failure result prepends error to last_errors, capped at 5."""
    existing = [{"timestamp": f"t{i}", "error": f"e{i}"} for i in range(5)]
    patch_table.get_item.return_value = {"Item": {**_sq_metadata_item(), "last_errors": existing}}
    await store.record_scheduled_query_result("sq1", "failure", error="new error")
    call_kwargs = patch_table.update_item.call_args_list[0][1]
    errors = call_kwargs["ExpressionAttributeValues"][":errors"]
    assert len(errors) == 5
    assert errors[0]["error"] == "new error"


async def test_record_scheduled_query_result_not_found(patch_table, store):
    """Missing item is handled gracefully without update calls."""
    patch_table.get_item.return_value = {}
    await store.record_scheduled_query_result("nonexistent", "success")
    assert patch_table.update_item.call_count == 0


async def test_set_workflow_schedule_sync_status_updates_metadata_and_list(patch_table, store):
    await store.set_workflow_schedule_sync_status(
        "sq1",
        "error",
        error="Temporal unavailable",
        synced_at="2026-01-01T00:00:00+00:00",
    )

    assert patch_table.update_item.call_count == 2
    keys = [call.kwargs["Key"] for call in patch_table.update_item.call_args_list]
    assert {key["PK"] for key in keys} == {"SQ#sq1", "SCHEDULED_QUERY_LIST"}
    values = patch_table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert values[":status"] == "error"
    assert values[":error"] == "Temporal unavailable"


async def test_request_scheduled_query_run_success_and_missing(patch_table, store):
    requested_at = await store.request_scheduled_query_run("sq1")
    assert requested_at is not None
    assert patch_table.update_item.call_count == 2

    import botocore.exceptions

    patch_table.update_item.reset_mock()
    patch_table.update_item.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "missing"}},
        "UpdateItem",
    )
    assert await store.request_scheduled_query_run("missing") is None


# ---------------------------------------------------------------------------
# Toolsets
# ---------------------------------------------------------------------------


def _ts_metadata_item(ts_id="ts1", current_version=1):
    return {
        "PK": f"TOOLSET#{ts_id}",
        "SK": "#METADATA",
        "toolset_id": ts_id,
        "name": "My Toolset",
        "description": "A toolset",
        "enabled": True,
        "current_version": current_version,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "updated_by": "user@example.com",
    }


def _ts_version_item(ts_id="ts1", version=1):
    return {
        "PK": f"TOOLSET#{ts_id}",
        "SK": f"VERSION#{version:010d}",  # noqa: E231
        "toolset_id": ts_id,
        "name": "My Toolset",
        "description": "A toolset",
        "enabled": True,
        "version": version,
        "created_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "comment": None,
    }


async def test_list_toolsets_empty(patch_table, store):
    patch_table.query.return_value = {"Items": []}
    result = await store.list_toolsets()
    assert result == []


async def test_list_toolsets_returns_items(patch_table, store):
    patch_table.query.return_value = {"Items": [_ts_metadata_item()]}
    result = await store.list_toolsets()
    assert len(result) == 1
    assert result[0].toolset_id == "ts1"
    assert result[0].name == "My Toolset"


async def test_get_toolset_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _ts_metadata_item()}
    result = await store.get_toolset("ts1")
    assert result is not None
    assert result.toolset_id == "ts1"


async def test_get_toolset_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.get_toolset("nonexistent")
    assert result is None


async def test_create_toolset(patch_table, store, mocker):
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="ts1",
    )
    patch_table.meta.client.transact_write_items = MagicMock()
    result = await store.create_toolset(
        toolset_id="ts1",
        name="My Toolset",
        description="desc",
        enabled=True,
        created_by="user@example.com",
    )
    assert result.toolset_id == "ts1"
    assert result.current_version == 1
    assert result.created_by == "user@example.com"
    assert patch_table.meta.client.transact_write_items.call_count == 1


async def test_update_toolset_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _ts_metadata_item(current_version=1)}
    patch_table.meta.client.transact_write_items = MagicMock()
    result = await store.update_toolset(
        toolset_id="ts1",
        name="Updated",
        description="new desc",
        enabled=False,
        updated_by="editor@example.com",
        comment="v2",
    )
    assert result is not None
    assert result.current_version == 2
    assert result.updated_by == "editor@example.com"


async def test_update_toolset_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.update_toolset(
        toolset_id="nonexistent",
        name="X",
        description="",
        enabled=True,
        updated_by="u@x.com",
    )
    assert result is None


async def test_delete_toolset_success(patch_table, store):
    def _query_side_effect(**kwargs):
        pk = kwargs["ExpressionAttributeValues"][":pk"]
        if pk == "TOOLSET#ts1":
            return {
                "Items": [
                    {"PK": "TOOLSET#ts1", "SK": "#METADATA"},
                    {"PK": "TOOLSET#ts1", "SK": "VERSION#0000000001"},
                ]
            }
        if pk == "TOOL_LIST#ts1":
            return {"Items": []}
        return {"Items": []}

    patch_table.get_item.return_value = {"Item": _ts_metadata_item()}
    patch_table.query.side_effect = _query_side_effect
    batch_mock = MagicMock()
    patch_table.batch_writer.return_value.__enter__ = MagicMock(return_value=batch_mock)
    patch_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)
    result = await store.delete_toolset("ts1")
    assert result is True


async def test_delete_toolset_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.delete_toolset("nonexistent")
    assert result is False


async def test_list_toolset_versions_empty(patch_table, store):
    patch_table.query.return_value = {"Items": []}
    result = await store.list_toolset_versions("ts1")
    assert result == []


async def test_list_toolset_versions_returns_items(patch_table, store):
    patch_table.query.return_value = {"Items": [_ts_version_item(version=2), _ts_version_item(version=1)]}
    result = await store.list_toolset_versions("ts1")
    assert len(result) == 2
    assert result[0].version == 2


async def test_get_toolset_version_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _ts_version_item(version=1)}
    result = await store.get_toolset_version("ts1", 1)
    assert result is not None
    assert result.version == 1


async def test_get_toolset_version_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.get_toolset_version("ts1", 99)
    assert result is None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _tool_metadata_item(tool_id="t1", toolset_id="ts1", current_version=1):
    return {
        "PK": f"TOOL#{tool_id}",
        "SK": "#METADATA",
        "tool_id": tool_id,
        "toolset_id": toolset_id,
        "name": "My Tool",
        "description": "A tool",
        "cypher": "MATCH (n) RETURN n LIMIT 1",
        "parameters": [],
        "enabled": True,
        "current_version": current_version,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "updated_by": "user@example.com",
    }


def _tool_version_dynamo_item(tool_id="t1", toolset_id="ts1", version=1):
    return {
        "PK": f"TOOL#{tool_id}",
        "SK": f"VERSION#{version:010d}",  # noqa: E231
        "tool_id": tool_id,
        "toolset_id": toolset_id,
        "name": "My Tool",
        "description": "A tool",
        "cypher": "MATCH (n) RETURN n LIMIT 1",
        "parameters": [],
        "enabled": True,
        "version": version,
        "created_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "comment": None,
    }


async def test_list_tools_empty(patch_table, store):
    patch_table.query.return_value = {"Items": []}
    result = await store.list_tools("ts1")
    assert result == []


async def test_list_tools_returns_items(patch_table, store):
    def _query_side_effect(**kwargs):
        pk = kwargs["ExpressionAttributeValues"][":pk"]
        if pk == "TOOL_LIST#ts1":
            return {"Items": [{"SK": "TOOL#t1"}]}
        return {"Items": []}

    patch_table.query.side_effect = _query_side_effect
    patch_table.get_item.return_value = {"Item": _tool_metadata_item()}
    result = await store.list_tools("ts1")
    assert len(result) == 1
    assert result[0].tool_id == "t1"


async def test_get_tool_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _tool_metadata_item()}
    result = await store.get_tool("t1")
    assert result is not None
    assert result.tool_id == "t1"


async def test_get_tool_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.get_tool("nonexistent")
    assert result is None


async def test_create_tool_success(patch_table, store, mocker):
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="t1",
    )
    patch_table.get_item.return_value = {"Item": _ts_metadata_item()}
    patch_table.meta.client.transact_write_items = MagicMock()
    result = await store.create_tool(
        toolset_id="ts1",
        tool_id="t1",
        name="My Tool",
        description="desc",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=True,
        created_by="user@example.com",
    )
    assert result is not None
    assert result.tool_id == "t1"
    assert result.current_version == 1
    assert patch_table.meta.client.transact_write_items.call_count == 1


async def test_create_tool_toolset_not_found(patch_table, store, mocker):
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="t1",
    )
    patch_table.get_item.return_value = {}
    result = await store.create_tool(
        toolset_id="nonexistent",
        tool_id="t1",
        name="My Tool",
        description="",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=True,
        created_by="user@example.com",
    )
    assert result is None


async def test_update_tool_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _tool_metadata_item(current_version=1)}
    patch_table.meta.client.transact_write_items = MagicMock()
    result = await store.update_tool(
        tool_id="t1",
        name="Updated",
        description="new desc",
        cypher="MATCH (n) RETURN n LIMIT 5",
        parameters=[],
        enabled=False,
        updated_by="editor@example.com",
        comment="v2",
    )
    assert result is not None
    assert result.current_version == 2
    assert result.updated_by == "editor@example.com"


async def test_update_tool_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.update_tool(
        tool_id="nonexistent",
        name="X",
        description="",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=True,
        updated_by="u@x.com",
    )
    assert result is None


async def test_delete_tool_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _tool_metadata_item()}
    patch_table.query.return_value = {
        "Items": [
            {"PK": "TOOL#t1", "SK": "#METADATA"},
            {"PK": "TOOL#t1", "SK": "VERSION#0000000001"},
        ]
    }
    batch_mock = MagicMock()
    patch_table.batch_writer.return_value.__enter__ = MagicMock(return_value=batch_mock)
    patch_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)
    result = await store.delete_tool("t1")
    assert result is True
    # 2 tool items + 1 list item
    assert batch_mock.delete_item.call_count == 3


async def test_delete_tool_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.delete_tool("nonexistent")
    assert result is False


async def test_list_tool_versions_empty(patch_table, store):
    patch_table.query.return_value = {"Items": []}
    result = await store.list_tool_versions("t1")
    assert result == []


async def test_list_tool_versions_returns_items(patch_table, store):
    patch_table.query.return_value = {
        "Items": [
            _tool_version_dynamo_item(version=2),
            _tool_version_dynamo_item(version=1),
        ]
    }
    result = await store.list_tool_versions("t1")
    assert len(result) == 2
    assert result[0].version == 2


async def test_get_tool_version_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _tool_version_dynamo_item(version=1)}
    result = await store.get_tool_version("t1", 1)
    assert result is not None
    assert result.version == 1
    assert result.tool_id == "t1"


async def test_get_tool_version_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.get_tool_version("t1", 99)
    assert result is None


async def test_list_enabled_tools_empty(patch_table, store):
    patch_table.query.return_value = {"Items": []}
    result = await store.list_enabled_tools()
    assert result == []


async def test_list_enabled_tools_skips_disabled_toolset(patch_table, store):
    def _query_side_effect(**kwargs):
        pk = kwargs["ExpressionAttributeValues"][":pk"]
        if pk == "TOOLSET_LIST":
            return {
                "Items": [
                    {**_ts_metadata_item(), "enabled": False},
                ]
            }
        return {"Items": []}

    patch_table.query.side_effect = _query_side_effect
    result = await store.list_enabled_tools()
    assert result == []


async def test_list_enabled_tools_returns_enabled_tools(patch_table, store):
    tool_list_item = {
        **_tool_metadata_item(),
        "PK": "TOOL_LIST#ts1",
        "SK": "TOOL#t1",
        "enabled": True,
    }
    call_count = 0

    def _query_side_effect(**kwargs):
        nonlocal call_count
        pk = kwargs["ExpressionAttributeValues"][":pk"]
        call_count += 1
        if pk == "TOOLSET_LIST":
            return {"Items": [_ts_metadata_item()]}
        if pk == "TOOL_LIST#ts1":
            return {"Items": [tool_list_item]}
        return {"Items": []}

    patch_table.query.side_effect = _query_side_effect
    patch_table.get_item.return_value = {"Item": _tool_metadata_item()}
    result = await store.list_enabled_tools()
    assert len(result) == 1
    assert result[0].tool_id == "t1"


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

_NOW = "2024-01-01T00:00:00+00:00"


def _role_metadata_item(role_id="r1", current_version=1):
    return {
        "PK": f"ROLE#{role_id}",
        "SK": "#METADATA",
        "role_id": role_id,
        "name": "Custom Role",
        "description": "A test role",
        "permissions": ["reports:read", "query:execute"],
        "current_version": current_version,
        "created_at": _NOW,
        "updated_at": _NOW,
        "created_by": "uid1",
        "updated_by": "uid1",
    }


def _role_version_item(role_id="r1", version=1):
    return {
        "PK": f"ROLE#{role_id}",
        "SK": f"VERSION#{version:010d}",  # noqa: E231
        "role_id": role_id,
        "name": "Custom Role",
        "description": "A test role",
        "permissions": ["reports:read"],
        "version": version,
        "created_at": _NOW,
        "created_by": "uid1",
    }


async def test_list_roles_empty(patch_table, store):
    patch_table.query.return_value = {"Items": []}
    result = await store.list_roles()
    assert result == []


async def test_list_roles_returns_items(patch_table, store):
    patch_table.query.return_value = {"Items": [_role_metadata_item()]}
    result = await store.list_roles()
    assert len(result) == 1
    assert result[0].role_id == "r1"
    assert result[0].name == "Custom Role"


async def test_get_role_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _role_metadata_item()}
    result = await store.get_role("r1")
    assert result is not None
    assert result.role_id == "r1"


async def test_get_role_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.get_role("nonexistent")
    assert result is None


async def test_get_role_by_name_found(patch_table, store):
    patch_table.query.return_value = {"Items": [_role_metadata_item()]}
    result = await store.get_role_by_name("Custom Role")
    assert result is not None
    assert result.name == "Custom Role"


async def test_get_role_by_name_not_found(patch_table, store):
    patch_table.query.return_value = {"Items": []}
    result = await store.get_role_by_name("nonexistent")
    assert result is None


async def test_create_role(patch_table, store, mocker):
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="r1",
    )
    patch_table.meta.client.transact_write_items = MagicMock()
    result = await store.create_role(
        name="Custom Role",
        description="A test role",
        permissions=["reports:read"],
        created_by="uid1",
    )
    assert result.role_id == "r1"
    assert result.name == "Custom Role"
    assert result.current_version == 1
    assert result.created_by == "uid1"
    assert patch_table.meta.client.transact_write_items.call_count == 1


async def test_update_role_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _role_metadata_item(current_version=1)}
    patch_table.meta.client.transact_write_items = MagicMock()
    result = await store.update_role(
        role_id="r1",
        name="Updated Role",
        description="new desc",
        permissions=["reports:read", "reports:write"],
        updated_by="uid2",
        comment="v2",
    )
    assert result is not None
    assert result.name == "Updated Role"
    assert result.current_version == 2
    assert result.updated_by == "uid2"


async def test_update_role_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.update_role(
        role_id="nonexistent",
        name="X",
        description="",
        permissions=[],
        updated_by="u",
    )
    assert result is None


async def test_delete_role_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _role_metadata_item()}
    patch_table.query.return_value = {
        "Items": [
            {"PK": "ROLE#r1", "SK": "#METADATA"},
            {"PK": "ROLE#r1", "SK": "VERSION#0000000001"},
        ]
    }
    batch_mock = MagicMock()
    patch_table.batch_writer.return_value.__enter__ = MagicMock(return_value=batch_mock)
    patch_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)
    result = await store.delete_role("r1")
    assert result is True


async def test_delete_role_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.delete_role("nonexistent")
    assert result is False


async def test_list_role_versions_empty(patch_table, store):
    patch_table.query.return_value = {"Items": []}
    result = await store.list_role_versions("r1")
    assert result == []


async def test_list_role_versions_returns_items(patch_table, store):
    patch_table.query.return_value = {"Items": [_role_version_item(version=2), _role_version_item(version=1)]}
    result = await store.list_role_versions("r1")
    assert len(result) == 2
    assert result[0].version == 2


async def test_get_role_version_success(patch_table, store):
    patch_table.get_item.return_value = {"Item": _role_version_item(version=1)}
    result = await store.get_role_version("r1", 1)
    assert result is not None
    assert result.version == 1


# ---------------------------------------------------------------------------
# Skillsets and skills
# ---------------------------------------------------------------------------


async def test_skillset_crud_and_versions(patch_table, store):
    patch_table.query.return_value = {"Items": [_skillset_metadata_item()]}
    skillsets = await store.list_skillsets()
    assert isinstance(skillsets[0], SkillsetListItem)
    assert skillsets[0].skillset_id == "ss1"

    patch_table.get_item.return_value = {"Item": _skillset_metadata_item()}
    skillset = await store.get_skillset("ss1")
    assert skillset is not None
    assert skillset.name == "Skillset"

    patch_table.meta.client.transact_write_items = MagicMock()
    created = await store.create_skillset(
        skillset_id="ss1",
        name="Skillset",
        description="desc",
        enabled=True,
        created_by="u1",
    )
    assert created.skillset_id == "ss1"
    assert patch_table.meta.client.transact_write_items.call_count == 1

    patch_table.get_item.return_value = {"Item": _skillset_metadata_item(current_version=1)}
    updated = await store.update_skillset(
        skillset_id="ss1",
        name="Updated",
        description="new",
        enabled=False,
        updated_by="u2",
        comment="v2",
    )
    assert updated is not None
    assert updated.current_version == 2
    assert updated.enabled is False

    patch_table.get_item.return_value = {}
    assert await store.update_skillset("missing", "n", "", True, "u2") is None

    patch_table.query.return_value = {"Items": [_skillset_version_item(version=2), _skillset_version_item(version=1)]}
    versions = await store.list_skillset_versions("ss1")
    assert isinstance(versions[0], SkillsetVersion)
    assert versions[0].version == 2

    patch_table.get_item.return_value = {"Item": _skillset_version_item(version=1)}
    assert (await store.get_skillset_version("ss1", 1)).version == 1
    patch_table.get_item.return_value = {}
    assert await store.get_skillset_version("ss1", 99) is None


async def test_delete_skillset_cascades_skills(patch_table, store):
    patch_table.get_item.return_value = {"Item": _skillset_metadata_item()}
    patch_table.query.side_effect = [
        {"Items": [{"SK": "SKILL#sk1"}]},
        {"Items": [{"PK": "SKILL#sk1", "SK": "#METADATA"}, {"PK": "SKILL#sk1", "SK": "VERSION#0000000001"}]},
        {"Items": [{"PK": "SKILLSET#ss1", "SK": "#METADATA"}, {"PK": "SKILLSET#ss1", "SK": "VERSION#0000000001"}]},
    ]
    batch_mock = MagicMock()
    patch_table.batch_writer.return_value.__enter__ = MagicMock(return_value=batch_mock)
    patch_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)
    assert await store.delete_skillset("ss1") is True
    assert batch_mock.delete_item.call_count >= 4

    patch_table.get_item.return_value = {}
    assert await store.delete_skillset("missing") is False


async def test_skill_crud_versions_and_enabled_filters(patch_table, store):
    patch_table.query.return_value = {"Items": [{"SK": "SKILL#sk1"}]}
    patch_table.get_item.return_value = {"Item": _skill_metadata_item()}
    skills = await store.list_skills("ss1")
    assert isinstance(skills[0], SkillItem)
    assert skills[0].skill_id == "sk1"

    patch_table.get_item.return_value = {"Item": _skill_metadata_item()}
    assert (await store.get_skill("sk1")).parameters[0].name == "topic"
    patch_table.get_item.return_value = {}
    assert await store.get_skill("missing") is None

    patch_table.get_item.return_value = {"Item": _skillset_metadata_item()}
    patch_table.meta.client.transact_write_items = MagicMock()
    created = await store.create_skill(
        skillset_id="ss1",
        skill_id="sk1",
        name="Skill",
        description="desc",
        template="Hello {{topic}}",
        parameters=[{"name": "topic", "type": "string", "required": True}],
        triggers=["say hello"],
        tools_required=["toolset__tool"],
        enabled=True,
        created_by="u1",
    )
    assert created is not None
    assert created.tools_required == ["toolset__tool"]

    patch_table.get_item.return_value = {}
    assert await store.create_skill("missing", "sk1", "n", "", "x", [], [], [], True, "u1") is None

    patch_table.get_item.return_value = {"Item": _skill_metadata_item(current_version=1)}
    updated = await store.update_skill(
        skill_id="sk1",
        name="Skill v2",
        description="new",
        template="Hello {{topic}} again",
        parameters=[{"name": "topic", "type": "string", "required": True}],
        triggers=[],
        tools_required=[],
        enabled=False,
        updated_by="u2",
        comment="v2",
    )
    assert updated is not None
    assert updated.current_version == 2
    assert updated.enabled is False
    patch_table.get_item.return_value = {}
    assert await store.update_skill("missing", "n", "", "x", [], [], [], True, "u2") is None

    patch_table.query.return_value = {"Items": [_skill_version_item(version=2), _skill_version_item(version=1)]}
    versions = await store.list_skill_versions("sk1")
    assert isinstance(versions[0], SkillVersion)
    assert versions[0].version == 2
    patch_table.get_item.return_value = {"Item": _skill_version_item(version=1)}
    assert (await store.get_skill_version("sk1", 1)).version == 1
    patch_table.get_item.return_value = {}
    assert await store.get_skill_version("sk1", 99) is None

    patch_table.query.side_effect = [
        {"Items": [_skillset_metadata_item(enabled=True), _skillset_metadata_item("disabled", enabled=False)]},
        {"Items": [{"skill_id": "sk1", "enabled": True}]},
    ]
    patch_table.get_item.return_value = {"Item": _skill_metadata_item(enabled=True)}
    enabled = await store.list_enabled_skills()
    assert enabled[0].skill_id == "sk1"

    patch_table.get_item.side_effect = [
        {"Item": _skill_metadata_item(enabled=True)},
        {"Item": _skillset_metadata_item(enabled=True)},
    ]
    assert (await store.get_enabled_skill("ss1", "sk1")).skill_id == "sk1"
    patch_table.get_item.side_effect = None
    patch_table.get_item.return_value = {"Item": _skill_metadata_item(enabled=False)}
    assert await store.get_enabled_skill("ss1", "sk1") is None


async def test_delete_skill(patch_table, store):
    patch_table.get_item.return_value = {"Item": _skill_metadata_item()}
    patch_table.query.return_value = {
        "Items": [{"PK": "SKILL#sk1", "SK": "#METADATA"}, {"PK": "SKILL#sk1", "SK": "VERSION#0000000001"}]
    }
    batch_mock = MagicMock()
    patch_table.batch_writer.return_value.__enter__ = MagicMock(return_value=batch_mock)
    patch_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)
    assert await store.delete_skill("sk1") is True
    assert batch_mock.delete_item.call_count == 3

    patch_table.get_item.return_value = {}
    assert await store.delete_skill("missing") is False


async def test_get_role_version_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    result = await store.get_role_version("r1", 99)
    assert result is None


async def test_create_action_confirmation_writes_session_confirmation_indexes(patch_table, store):
    item = _action_confirmation_item()
    confirmation = ActionConfirmation.model_validate(item)

    await store.create_action_confirmation(confirmation)

    transact_items = patch_table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
    assert len(transact_items) == 3
    put_keys = {(item["Put"]["Item"]["PK"], item["Put"]["Item"]["SK"]) for item in transact_items if "Put" in item}
    assert ("ACTION_CONFIRMATION#confirm-1", "#METADATA") in put_keys
    assert (
        "ACTION_CONFIRMATION_SESSION_LIST#user-1#SOURCE#mcp#SESSION#session-1",
        "STATUS#pending#CREATED#2024-01-01T00:00:00+00:00#CONFIRMATION#confirm-1",
    ) in put_keys
    # Dedup sentinel prevents concurrent creates with identical arguments.
    dedup_sk = _action_confirmation_dedup_sk(confirmation.model_dump())
    assert ("ACTION_CONFIRMATION_SESSION_LIST#user-1#SOURCE#mcp#SESSION#session-1", dedup_sk) in put_keys


async def test_create_action_confirmation_expires_stale_pending_dedup_before_retry(patch_table, store, mocker):
    stale_item = _action_confirmation_item(
        confirmation_id="stale-confirm",
        created_at="2020-01-01T00:00:00+00:00",
        expires_at="2020-01-01T00:30:00+00:00",
    )
    replacement = ActionConfirmation.model_validate(_action_confirmation_item(confirmation_id="replacement-confirm"))
    cancelled = botocore.exceptions.ClientError(
        {"Error": {"Code": "TransactionCanceledException", "Message": "cancelled"}},
        "TransactWriteItems",
    )
    patch_table.meta.client.transact_write_items.side_effect = [
        cancelled,  # initial create rejected by stale dedup sentinel
        None,  # stale pending confirmation is marked expired and dedup is removed
        None,  # replacement create succeeds
    ]
    patch_table.query.return_value = {
        "Items": [
            {
                "PK": "ACTION_CONFIRMATION_SESSION_LIST#user-1#SOURCE#mcp#SESSION#session-1",
                "SK": "STATUS#pending#CREATED#2020-01-01T00:00:00+00:00#CONFIRMATION#stale-confirm",
                "confirmation_id": "stale-confirm",
            }
        ]
    }
    patch_table.get_item.return_value = {"Item": stale_item}
    mocker.patch.object(store, "find_action_confirmation_grant", return_value=None)

    result = await store.create_action_confirmation(replacement)

    assert result.confirmation_id == "replacement-confirm"
    assert patch_table.meta.client.transact_write_items.call_count == 3
    expire_transact_items = patch_table.meta.client.transact_write_items.call_args_list[1].kwargs["TransactItems"]
    assert len(expire_transact_items) == 4
    delete_keys = {
        (item["Delete"]["Key"]["PK"], item["Delete"]["Key"]["SK"]) for item in expire_transact_items if "Delete" in item
    }
    stale_dedup_sk = _action_confirmation_dedup_sk(stale_item)
    assert ("ACTION_CONFIRMATION_SESSION_LIST#user-1#SOURCE#mcp#SESSION#session-1", stale_dedup_sk) in delete_keys


async def test_decide_action_confirmation_moves_session_status_pointer(patch_table, store):
    item = _action_confirmation_item()
    item["batch_id"] = "batch-1"
    patch_table.get_item.return_value = {"Item": item}
    confirmation = ActionConfirmation.model_validate(item)

    await store.decide_action_confirmation("confirm-1", "user-1", "approved")

    transact_items = patch_table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
    assert len(transact_items) == 4
    delete_keys = {
        (item["Delete"]["Key"]["PK"], item["Delete"]["Key"]["SK"]) for item in transact_items if "Delete" in item
    }
    put_keys = {(item["Put"]["Item"]["PK"], item["Put"]["Item"]["SK"]) for item in transact_items if "Put" in item}
    # Dedup sentinel is deleted when a confirmation is decided.
    dedup_sk = _action_confirmation_dedup_sk(confirmation.model_dump())
    assert (
        "ACTION_CONFIRMATION_SESSION_LIST#user-1#SOURCE#mcp#SESSION#session-1",
        "STATUS#pending#CREATED#2024-01-01T00:00:00+00:00#CONFIRMATION#confirm-1",
    ) in delete_keys
    assert (
        "ACTION_CONFIRMATION_SESSION_LIST#user-1#SOURCE#mcp#SESSION#session-1",
        "STATUS#approved#CREATED#2024-01-01T00:00:00+00:00#CONFIRMATION#confirm-1",
    ) in put_keys
    assert ("ACTION_CONFIRMATION_SESSION_LIST#user-1#SOURCE#mcp#SESSION#session-1", dedup_sk) in delete_keys
    assert not any(pk == "ACTION_CONFIRMATION_LIST#user-1" for pk, _sk in put_keys)
    assert not any(pk == "ACTION_CONFIRMATION_BATCH#user-1#BATCH#batch-1" for pk, _sk in put_keys)


async def test_claim_action_confirmation_moves_session_status_pointer_without_rewriting_static_pointers(
    patch_table, store
):
    item = _action_confirmation_item(status="approved")
    item["batch_id"] = "batch-1"
    patch_table.get_item.return_value = {"Item": item}

    await store.claim_action_confirmation_for_execution("confirm-1", "user-1")

    transact_items = patch_table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
    assert len(transact_items) == 4
    delete_keys = {
        (item["Delete"]["Key"]["PK"], item["Delete"]["Key"]["SK"]) for item in transact_items if "Delete" in item
    }
    put_keys = {(item["Put"]["Item"]["PK"], item["Put"]["Item"]["SK"]) for item in transact_items if "Put" in item}
    assert (
        "ACTION_CONFIRMATION_SESSION_LIST#user-1#SOURCE#mcp#SESSION#session-1",
        "STATUS#approved#CREATED#2024-01-01T00:00:00+00:00#CONFIRMATION#confirm-1",
    ) in delete_keys
    assert (
        "ACTION_CONFIRMATION_SESSION_LIST#user-1#SOURCE#mcp#SESSION#session-1",
        "STATUS#executed#CREATED#2024-01-01T00:00:00+00:00#CONFIRMATION#confirm-1",
    ) in put_keys
    assert not any(pk == "ACTION_CONFIRMATION_LIST#user-1" for pk, _sk in put_keys)
    assert not any(pk == "ACTION_CONFIRMATION_BATCH#user-1#BATCH#batch-1" for pk, _sk in put_keys)


async def test_find_action_confirmation_grant_hydrates_from_metadata(patch_table, store):
    """find_action_confirmation_grant must fetch the full metadata item for each
    session-list pointer record rather than parsing the pointer directly, which
    only contains {PK, SK, confirmation_id}."""
    pointer_item = {
        "PK": "ACTION_CONFIRMATION_SESSION_LIST#user-1#SOURCE#mcp#SESSION#session-1",
        "SK": "STATUS#approved#CREATED#2024-01-01T00:00:00+00:00#CONFIRMATION#confirm-1",
        "confirmation_id": "confirm-1",
    }
    metadata_item = _action_confirmation_item(status="approved")

    patch_table.query.return_value = {"Items": [pointer_item]}
    patch_table.get_item.return_value = {"Item": metadata_item}

    result = await store.find_action_confirmation_grant(
        user_id="user-1",
        source="mcp",
        session_key="session-1",
        tool_name="reports__delete",
        action="delete",
        resource_type="report",
        resource_id="report-1",
        arguments_hash="hash-1",
        statuses=("approved",),
    )

    assert result is not None
    assert result.confirmation_id == "confirm-1"
    assert result.status == "approved"
    # get_item must have been called to fetch the full metadata record.
    patch_table.get_item.assert_called_once_with(Key={"PK": "ACTION_CONFIRMATION#confirm-1", "SK": "#METADATA"})


async def test_find_action_confirmation_grant_skips_missing_metadata(patch_table, store):
    """If the metadata item is gone (e.g. TTL-deleted), the pointer is skipped."""
    pointer_item = {
        "PK": "ACTION_CONFIRMATION_SESSION_LIST#user-1#SOURCE#mcp#SESSION#session-1",
        "SK": "STATUS#approved#CREATED#2024-01-01T00:00:00+00:00#CONFIRMATION#confirm-1",
        "confirmation_id": "confirm-1",
    }
    patch_table.query.return_value = {"Items": [pointer_item]}
    patch_table.get_item.return_value = {}  # no Item key — metadata gone

    result = await store.find_action_confirmation_grant(
        user_id="user-1",
        source="mcp",
        session_key="session-1",
        tool_name="reports__delete",
        action="delete",
        resource_type="report",
        resource_id="report-1",
        arguments_hash="hash-1",
        statuses=("approved",),
    )

    assert result is None


async def test_update_scheduled_query_preserves_operational_fields(patch_table, store):
    """Updates must not drop run-state fields written outside the update path
    (e.g. a pending run-now request or the last-run bookkeeping)."""
    existing = _sq_metadata_item(current_version=1)
    existing["last_scheduled_at"] = "2026-01-01T00:00:00+00:00"
    existing["run_requested_at"] = "2026-01-01T00:05:00+00:00"
    existing["last_run_status"] = "success"
    patch_table.get_item.return_value = {"Item": existing}
    patch_table.meta.client.transact_write_items = MagicMock()
    result = await store.update_scheduled_query(
        sq_id="sq1",
        name="Updated",
        cypher="MATCH (n) RETURN n LIMIT 1",
        params=[],
        frequency=None,
        schedule={"type": "interval", "interval_minutes": 5},
        watch_scans=[],
        enabled=True,
        actions=[],
        updated_by="editor@example.com",
    )
    assert result is not None
    assert result.run_requested_at == "2026-01-01T00:05:00+00:00"
    assert result.last_scheduled_at == "2026-01-01T00:00:00+00:00"
    assert result.last_run_status == "success"
    # The old frequency trigger is cleared in favor of the new schedule.
    assert result.frequency is None
    transact_items = patch_table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
    metadata_put = transact_items[0]["Put"]["Item"]
    list_put = transact_items[1]["Put"]["Item"]
    assert metadata_put["run_requested_at"] == "2026-01-01T00:05:00+00:00"
    assert list_put["run_requested_at"] == "2026-01-01T00:05:00+00:00"
    assert "frequency" not in metadata_put
    assert "frequency" not in list_put


# ---------------------------------------------------------------------------
# Spaces
# ---------------------------------------------------------------------------


def _put_items(transact_mock) -> list[dict]:
    """Return the items written by the most recent transact_write_items call."""
    call = transact_mock.call_args
    return [entry["Put"]["Item"] for entry in call.kwargs["TransactItems"]]


def _by_key(items: list[dict], pk: str, sk: str) -> dict:
    matches = [item for item in items if item["PK"] == pk and item["SK"] == sk]
    assert len(matches) == 1, f"expected exactly one {pk}/{sk} item, got {len(matches)}"
    return matches[0]


def _space_metadata_item(space_id: str = "sp1", overview_report_id: str | None = None) -> dict:
    return {
        "PK": f"SPACE#{space_id}",
        "SK": "#METADATA",
        "space_id": space_id,
        "name": "Cloud",
        "description": "desc",
        **({"overview_report_id": overview_report_id} if overview_report_id else {}),
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "updated_by": "user@example.com",
    }


def _subspace_metadata_item(subspace_id: str = "ss1", space_id: str = "sp1") -> dict:
    return {
        "PK": f"SUBSPACE#{subspace_id}",
        "SK": "#METADATA",
        "subspace_id": subspace_id,
        "space_id": space_id,
        "name": "Network",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "updated_by": "user@example.com",
    }


def _report_list_entry(report_id: str, space_id: str | None = None, **extra) -> dict:
    item = {
        "PK": "REPORT_LIST",
        "SK": f"REPORT#{report_id}",
        "report_id": report_id,
        "name": f"Report {report_id}",
        "current_version": 1,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": "user@example.com",
        "updated_by": "user@example.com",
        "access": {"scope": "public"},
    }
    if space_id is not None:
        item["space_id"] = space_id
    item.update(extra)
    return item


async def test_list_spaces_queries_space_list_partition(patch_table, store):
    patch_table.query.return_value = {"Items": [_space_metadata_item()]}
    result = await store.list_spaces()
    assert [s.space_id for s in result] == ["sp1"]
    assert patch_table.query.call_args.kwargs["ExpressionAttributeValues"] == {":pk": "SPACE_LIST"}


async def test_get_space_uses_metadata_key(patch_table, store):
    patch_table.get_item.return_value = {"Item": _space_metadata_item()}
    result = await store.get_space("sp1")
    assert result is not None
    assert result.overview_report_id is None
    patch_table.get_item.assert_called_once_with(Key={"PK": "SPACE#sp1", "SK": "#METADATA"})


async def test_get_space_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.get_space("sp1") is None


async def test_create_space_writes_only_the_space(patch_table, store, mocker):
    """No overview report is created; the overview is a pointer set later."""
    mocker.patch(
        "reporting.services.report_store.dynamodb.generate_report_id",
        return_value="sp1",
    )
    patch_table.meta.client.transact_write_items = MagicMock()

    result = await store.create_space(name="Cloud", description="desc", created_by="user@example.com")

    assert result.space_id == "sp1"
    assert result.overview_report_id is None

    items = _put_items(patch_table.meta.client.transact_write_items)
    assert len(items) == 2
    _by_key(items, "SPACE#sp1", "#METADATA")
    _by_key(items, "SPACE_LIST", "SPACE#sp1")
    # _strip_none drops the unset pointer rather than writing NULL.
    assert "overview_report_id" not in items[0]


async def test_update_space_rewrites_metadata_and_list_items(patch_table, store):
    patch_table.get_item.return_value = {"Item": _space_metadata_item()}
    patch_table.meta.client.transact_write_items = MagicMock()

    result = await store.update_space(
        space_id="sp1", name="Cloud Security", description="new", updated_by="editor@example.com"
    )

    assert result is not None
    assert result.name == "Cloud Security"
    items = _put_items(patch_table.meta.client.transact_write_items)
    assert len(items) == 2
    assert _by_key(items, "SPACE#sp1", "#METADATA")["name"] == "Cloud Security"
    assert _by_key(items, "SPACE_LIST", "SPACE#sp1")["name"] == "Cloud Security"


async def test_update_space_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.update_space(space_id="sp1", name="x", description="", updated_by="u") is None


async def test_delete_space_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.delete_space("sp1") == SpaceDeleteResult.NOT_FOUND


async def test_delete_space_removes_its_subspaces(patch_table, store):
    """Sub-spaces go with the space rather than blocking the delete."""
    _force_index_fallback()  # exercise the no-GSI path
    patch_table.get_item.return_value = {"Item": _space_metadata_item()}
    patch_table.query.side_effect = [
        {"Items": []},  # no member reports
        {  # sub-spaces of the space
            "Items": [
                {"PK": "SUBSPACE_LIST#sp1", "SK": "SUBSPACE#ss1", "subspace_id": "ss1"},
                {"PK": "SUBSPACE_LIST#sp1", "SK": "SUBSPACE#ss2", "subspace_id": "ss2"},
            ]
        },
    ]
    batch = MagicMock()
    patch_table.batch_writer.return_value.__enter__.return_value = batch

    assert await store.delete_space("sp1") == SpaceDeleteResult.DELETED

    deleted = [call.kwargs["Key"] for call in batch.delete_item.call_args_list]
    assert {"PK": "SUBSPACE#ss1", "SK": "#METADATA"} in deleted
    assert {"PK": "SUBSPACE_LIST#sp1", "SK": "SUBSPACE#ss1"} in deleted
    assert {"PK": "SUBSPACE#ss2", "SK": "#METADATA"} in deleted
    assert {"PK": "SUBSPACE_LIST#sp1", "SK": "SUBSPACE#ss2"} in deleted
    assert {"PK": "SPACE#sp1", "SK": "#METADATA"} in deleted
    # No report is ever deleted with a space.
    assert not any(str(key["PK"]).startswith("REPORT") for key in deleted)


async def test_delete_space_blocked_by_member_report(patch_table, store):
    _force_index_fallback()  # exercise the no-GSI path
    patch_table.get_item.return_value = {"Item": _space_metadata_item()}
    patch_table.query.side_effect = [
        {"Items": [_report_list_entry("r1", "sp1"), _report_list_entry("r2", "sp1")]},
    ]
    assert await store.delete_space("sp1") == SpaceDeleteResult.NOT_EMPTY
    patch_table.batch_writer.assert_not_called()


async def test_delete_space_emptiness_ignores_report_visibility(patch_table, store):
    """A private report owned by someone else still blocks the delete."""
    _force_index_fallback()  # exercise the no-GSI path
    patch_table.get_item.return_value = {"Item": _space_metadata_item()}
    patch_table.query.side_effect = [
        {
            "Items": [
                _report_list_entry("r1", "sp1"),
                _report_list_entry("r2", "sp1", access={"scope": "private"}, created_by="other@example.com"),
            ]
        },
    ]
    assert await store.delete_space("sp1") == SpaceDeleteResult.NOT_EMPTY


async def test_list_space_reports_filters_by_space_and_visibility(patch_table, store):
    _force_index_fallback()  # exercise the no-GSI path
    patch_table.query.return_value = {
        "Items": [
            _report_list_entry("r1", "sp1"),
            _report_list_entry("r2", "sp2"),
            _report_list_entry("r3"),
            _report_list_entry("r4", "sp1", access={"scope": "private"}, created_by="other@example.com"),
        ]
    }
    result = await store.list_space_reports("sp1", user_id="user@example.com")
    assert [item.report_id for item in result] == ["r1"]

    patch_table.query.return_value = {
        "Items": [_report_list_entry("r4", "sp1", access={"scope": "private"}, created_by="other@example.com")]
    }
    unfiltered = await store.list_space_reports("sp1")
    assert [item.report_id for item in unfiltered] == ["r4"]


# ---------------------------------------------------------------------------
# Sub-spaces
# ---------------------------------------------------------------------------


async def test_list_subspaces_queries_per_space_partition(patch_table, store):
    patch_table.query.return_value = {"Items": [_subspace_metadata_item()]}
    result = await store.list_subspaces("sp1")
    assert [s.subspace_id for s in result] == ["ss1"]
    assert patch_table.query.call_args.kwargs["ExpressionAttributeValues"] == {":pk": "SUBSPACE_LIST#sp1"}


async def test_get_subspace_uses_metadata_key(patch_table, store):
    patch_table.get_item.return_value = {"Item": _subspace_metadata_item()}
    result = await store.get_subspace("ss1")
    assert result is not None
    assert result.space_id == "sp1"
    patch_table.get_item.assert_called_once_with(Key={"PK": "SUBSPACE#ss1", "SK": "#METADATA"})


async def test_create_subspace_requires_existing_space(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.create_subspace(space_id="sp1", name="Network", created_by="u") is None


async def test_create_subspace_writes_metadata_and_list_items(patch_table, store, mocker):
    mocker.patch("reporting.services.report_store.dynamodb.generate_report_id", return_value="ss1")
    patch_table.get_item.return_value = {"Item": _space_metadata_item()}
    patch_table.meta.client.transact_write_items = MagicMock()

    result = await store.create_subspace(space_id="sp1", name="Network", created_by="user@example.com")

    assert result is not None
    assert result.subspace_id == "ss1"
    items = _put_items(patch_table.meta.client.transact_write_items)
    assert len(items) == 2
    _by_key(items, "SUBSPACE#ss1", "#METADATA")
    _by_key(items, "SUBSPACE_LIST#sp1", "SUBSPACE#ss1")


async def test_update_subspace_rewrites_both_copies(patch_table, store):
    patch_table.get_item.return_value = {"Item": _subspace_metadata_item()}
    patch_table.meta.client.transact_write_items = MagicMock()

    result = await store.update_subspace(subspace_id="ss1", name="Networking", updated_by="editor@example.com")

    assert result is not None
    assert result.name == "Networking"
    items = _put_items(patch_table.meta.client.transact_write_items)
    assert _by_key(items, "SUBSPACE#ss1", "#METADATA")["name"] == "Networking"
    assert _by_key(items, "SUBSPACE_LIST#sp1", "SUBSPACE#ss1")["name"] == "Networking"


async def test_update_subspace_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.update_subspace(subspace_id="ss1", name="x", updated_by="u") is None


async def test_delete_subspace_removes_only_its_own_items(patch_table, store):
    """Member reports keep a dangling subspace_id; no fan-out write."""
    patch_table.get_item.return_value = {"Item": _subspace_metadata_item()}
    batch = MagicMock()
    patch_table.batch_writer.return_value.__enter__.return_value = batch

    assert await store.delete_subspace("ss1") is True

    deleted = [call.kwargs["Key"] for call in batch.delete_item.call_args_list]
    assert deleted == [
        {"PK": "SUBSPACE#ss1", "SK": "#METADATA"},
        {"PK": "SUBSPACE_LIST#sp1", "SK": "SUBSPACE#ss1"},
    ]


async def test_delete_subspace_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.delete_subspace("ss1") is False


# ---------------------------------------------------------------------------
# Report space membership
# ---------------------------------------------------------------------------


async def test_create_report_writes_space_membership_to_both_copies(patch_table, store, mocker):
    mocker.patch("reporting.services.report_store.dynamodb.generate_report_id", return_value="r1")
    patch_table.meta.client.transact_write_items = MagicMock()

    result = await store.create_report(
        name="Member",
        created_by="user@example.com",
        # Space members must be public; a bare create into a space is refused.
        access=ReportAccess(scope="public"),
        space_id="sp1",
        subspace_id="ss1",
    )

    assert result.space_id == "sp1"
    assert result.subspace_id == "ss1"
    items = _put_items(patch_table.meta.client.transact_write_items)
    for item in (_by_key(items, "REPORT#r1", "#METADATA"), _by_key(items, "REPORT_LIST", "REPORT#r1")):
        assert item["space_id"] == "sp1"
        assert item["subspace_id"] == "ss1"


async def test_create_report_without_space_omits_the_attributes(patch_table, store, mocker):
    mocker.patch("reporting.services.report_store.dynamodb.generate_report_id", return_value="r1")
    patch_table.meta.client.transact_write_items = MagicMock()

    await store.create_report(name="Loose", created_by="user@example.com")

    items = _put_items(patch_table.meta.client.transact_write_items)
    metadata = _by_key(items, "REPORT#r1", "#METADATA")
    # _strip_none: absent, not NULL — DynamoDB Local rejects NULL in a transaction.
    assert "space_id" not in metadata
    assert "subspace_id" not in metadata


async def test_save_report_version_preserves_space_membership(patch_table, store):
    """The highest-risk regression: a version save must not unfile a report.

    Both the #METADATA and REPORT_LIST copies are rebuilt on every save, and
    listing reads only the REPORT_LIST copy.
    """
    patch_table.get_item.return_value = {
        "Item": {
            "PK": "REPORT#r1",
            "SK": "#METADATA",
            "report_id": "r1",
            "name": "Member",
            "current_version": 1,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "created_by": "user@example.com",
            "updated_by": "user@example.com",
            "access": {"scope": "public"},
            "pinned": True,
            "space_id": "sp1",
            "subspace_id": "ss1",
        }
    }
    patch_table.meta.client.transact_write_items = MagicMock()

    result = await store.save_report_version(
        report_id="r1",
        config={"name": "Member", "rows": [], "schema_version": 1},
        created_by="editor@example.com",
    )

    assert result is not None
    assert result.space_id == "sp1"
    assert result.subspace_id == "ss1"

    items = _put_items(patch_table.meta.client.transact_write_items)
    for item in (_by_key(items, "REPORT#r1", "#METADATA"), _by_key(items, "REPORT_LIST", "REPORT#r1")):
        assert item["space_id"] == "sp1"
        assert item["subspace_id"] == "ss1"
        assert item["pinned"] is True
    # Membership is never written into the version items.
    version_item = _by_key(items, "REPORT#r1", "VERSION#0000000002")
    assert "space_id" not in version_item


async def test_update_report_visibility_preserves_space_membership(patch_table, store):
    patch_table.get_item.return_value = {
        "Item": {
            "PK": "REPORT#r1",
            "SK": "#METADATA",
            "report_id": "r1",
            "name": "Member",
            "current_version": 1,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "created_by": "user@example.com",
            "updated_by": "user@example.com",
            "access": {"scope": "private"},
            "space_id": "sp1",
            "subspace_id": "ss1",
        }
    }
    patch_table.meta.client.transact_write_items = MagicMock()

    result = await store.update_report_visibility(
        report_id="r1", updated_by="editor@example.com", access=ReportAccess(scope="public")
    )

    assert result is not None
    assert result.space_id == "sp1"
    items = _put_items(patch_table.meta.client.transact_write_items)
    for item in (_by_key(items, "REPORT#r1", "#METADATA"), _by_key(items, "REPORT_LIST", "REPORT#r1")):
        assert item["space_id"] == "sp1"
        assert item["subspace_id"] == "ss1"
        assert item["access"] == {"scope": "public"}


async def test_pin_report_leaves_space_attributes_untouched(patch_table, store):
    """pin_report uses targeted SET expressions, so membership survives."""
    patch_table.get_item.return_value = {
        "Item": {
            "PK": "REPORT#r1",
            "SK": "#METADATA",
            "report_id": "r1",
            "created_by": "user@example.com",
            "access": {"scope": "public"},
            "space_id": "sp1",
        }
    }
    assert await store.pin_report("r1", True, updated_by="user@example.com") is True
    for call in patch_table.update_item.call_args_list:
        assert "space_id" not in call.kwargs["UpdateExpression"]


async def test_update_report_space_writes_both_copies_without_a_version(patch_table, store):
    patch_table.get_item.return_value = {
        "Item": {
            "PK": "REPORT#r1",
            "SK": "#METADATA",
            "report_id": "r1",
            "name": "Member",
            "current_version": 3,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "created_by": "user@example.com",
            "updated_by": "user@example.com",
            "access": {"scope": "public"},
            "space_id": "spA",
            "subspace_id": "ssA",
        }
    }
    patch_table.meta.client.transact_write_items = MagicMock()

    # Replace semantics: moving to another space with no sub-space clears it.
    result = await store.update_report_space(
        report_id="r1",
        space_id="spB",
        subspace_id=None,
        updated_by="editor@example.com",
    )

    assert result is not None
    assert result.space_id == "spB"
    assert result.subspace_id is None

    items = _put_items(patch_table.meta.client.transact_write_items)
    assert len(items) == 2
    for item in (_by_key(items, "REPORT#r1", "#METADATA"), _by_key(items, "REPORT_LIST", "REPORT#r1")):
        assert item["space_id"] == "spB"
        assert "subspace_id" not in item
        assert item["current_version"] == 3


async def test_update_report_space_conditions_the_write_on_the_report_being_public(patch_table, store):
    """The up-front check is not enough: a concurrent unpublish could win.

    The metadata Put carries the condition, and a cancelled transaction covers
    the REPORT_LIST copy with it.
    """
    patch_table.get_item.return_value = {
        "Item": {
            "PK": "REPORT#r1",
            "SK": "#METADATA",
            "report_id": "r1",
            "name": "Member",
            "current_version": 1,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "created_by": "user@example.com",
            "access": {"scope": "public"},
        }
    }
    patch_table.meta.client.transact_write_items = MagicMock()

    await store.update_report_space(
        report_id="r1",
        space_id="spB",
        subspace_id=None,
        updated_by="editor@example.com",
    )

    items = patch_table.meta.client.transact_write_items.call_args[1]["TransactItems"]
    put = items[0]["Put"]
    assert put["ConditionExpression"] == "#access.#scope = :public"
    assert put["ExpressionAttributeValues"] == {":public": "public"}
    assert items[0]["Put"]["Item"]["SK"] == "#METADATA"
    # Unfiling is unconditional.
    patch_table.meta.client.transact_write_items = MagicMock()
    await store.update_report_space(report_id="r1", space_id=None, subspace_id=None, updated_by="editor@example.com")
    unfiled = patch_table.meta.client.transact_write_items.call_args[1]["TransactItems"]
    assert "ConditionExpression" not in unfiled[0]["Put"]


async def test_update_report_space_raises_when_the_condition_fails(patch_table, store):
    patch_table.get_item.return_value = {
        "Item": {
            "PK": "REPORT#r1",
            "SK": "#METADATA",
            "report_id": "r1",
            "name": "Member",
            "current_version": 1,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "created_by": "user@example.com",
            "access": {"scope": "public"},
        }
    }
    patch_table.meta.client.transact_write_items = MagicMock(
        side_effect=botocore.exceptions.ClientError(
            {
                "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
                "CancellationReasons": [{"Code": "ConditionalCheckFailed"}, {"Code": "None"}],
            },
            "TransactWriteItems",
        )
    )

    with pytest.raises(SpaceConflictError):
        await store.update_report_space(
            report_id="r1",
            space_id="spB",
            subspace_id=None,
            updated_by="editor@example.com",
        )


async def test_update_report_space_propagates_other_cancellation_reasons(patch_table, store):
    """Throttling and contention share the exception type but are not 409s.

    Turning them into SpaceConflictError would tell the user to publish a report
    that is already public, and hide a capacity problem.
    """
    patch_table.get_item.return_value = {
        "Item": {
            "PK": "REPORT#r1",
            "SK": "#METADATA",
            "report_id": "r1",
            "name": "Member",
            "current_version": 1,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "created_by": "user@example.com",
            "access": {"scope": "public"},
        }
    }
    patch_table.meta.client.transact_write_items = MagicMock(
        side_effect=botocore.exceptions.ClientError(
            {
                "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
                "CancellationReasons": [{"Code": "ThrottlingError"}, {"Code": "None"}],
            },
            "TransactWriteItems",
        )
    )

    with pytest.raises(botocore.exceptions.ClientError):
        await store.update_report_space(
            report_id="r1",
            space_id="spB",
            subspace_id=None,
            updated_by="editor@example.com",
        )


async def test_create_report_refuses_a_private_report_in_a_space(patch_table, store, mocker):
    mocker.patch("reporting.services.report_store.dynamodb.generate_report_id", return_value="r1")
    patch_table.meta.client.transact_write_items = MagicMock()

    with pytest.raises(SpaceConflictError):
        await store.create_report(
            name="Draft",
            created_by="user@example.com",
            access=ReportAccess(scope="private"),
            space_id="sp1",
        )

    patch_table.meta.client.transact_write_items.assert_not_called()


async def test_update_report_visibility_conditions_a_privatise_on_having_no_space(patch_table, store):
    patch_table.get_item.return_value = {
        "Item": {
            "PK": "REPORT#r1",
            "SK": "#METADATA",
            "report_id": "r1",
            "name": "Loose",
            "current_version": 1,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "created_by": "user@example.com",
            "access": {"scope": "public"},
        }
    }
    patch_table.meta.client.transact_write_items = MagicMock()

    await store.update_report_visibility(
        report_id="r1",
        updated_by="user@example.com",
        access=ReportAccess(scope="private"),
    )

    items = patch_table.meta.client.transact_write_items.call_args[1]["TransactItems"]
    assert items[0]["Put"]["ConditionExpression"] == "attribute_not_exists(space_id)"

    # Publishing needs no condition.
    patch_table.meta.client.transact_write_items = MagicMock()
    await store.update_report_visibility(
        report_id="r1",
        updated_by="user@example.com",
        access=ReportAccess(scope="public"),
    )
    published = patch_table.meta.client.transact_write_items.call_args[1]["TransactItems"]
    assert "ConditionExpression" not in published[0]["Put"]


async def test_update_report_space_respects_visibility(patch_table, store):
    patch_table.get_item.return_value = {
        "Item": {
            "PK": "REPORT#r1",
            "SK": "#METADATA",
            "report_id": "r1",
            "created_by": "other@example.com",
            "access": {"scope": "private"},
        }
    }
    assert (
        await store.update_report_space(
            report_id="r1",
            space_id="sp1",
            subspace_id=None,
            updated_by="user@example.com",
            user_id="user@example.com",
        )
        is None
    )


async def test_update_report_space_not_found(patch_table, store):
    patch_table.get_item.return_value = {}
    assert await store.update_report_space(report_id="r1", space_id=None, subspace_id=None, updated_by="u") is None


# ---------------------------------------------------------------------------
# Space reports GSI
# ---------------------------------------------------------------------------


async def test_table_creation_declares_the_space_reports_index(patch_table, store, mocker):
    resource = MagicMock()
    resource.tables.all.return_value = []
    mocker.patch(
        "reporting.services.report_store.dynamodb.get_boto_resource",
        return_value=resource,
    )
    mocker.patch("reporting.settings.DYNAMODB_TABLE_NAME", "tbl")

    await store.initialize()

    kwargs = resource.create_table.call_args.kwargs
    index = kwargs["GlobalSecondaryIndexes"][0]
    assert index["IndexName"] == "space_reports_index"
    assert index["KeySchema"] == [
        {"AttributeName": "space_id", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ]
    assert index["Projection"] == {"ProjectionType": "ALL"}
    # space_id has to be declared for the index to key on it.
    assert {"AttributeName": "space_id", "AttributeType": "S"} in kwargs["AttributeDefinitions"]


async def test_initialize_adds_the_index_to_an_existing_table(store, mocker):
    """Upgrade path: the table predates the index."""
    resource = MagicMock()
    existing = MagicMock()
    existing.name = "tbl"
    resource.tables.all.return_value = [existing]
    resource.Table.return_value.global_secondary_indexes = None
    mocker.patch(
        "reporting.services.report_store.dynamodb.get_boto_resource",
        return_value=resource,
    )
    mocker.patch("reporting.settings.DYNAMODB_TABLE_NAME", "tbl")

    await store.initialize()

    resource.create_table.assert_not_called()
    kwargs = resource.meta.client.update_table.call_args.kwargs
    assert kwargs["GlobalSecondaryIndexUpdates"][0]["Create"]["IndexName"] == "space_reports_index"


async def test_initialize_leaves_an_existing_index_alone(store, mocker):
    resource = MagicMock()
    existing = MagicMock()
    existing.name = "tbl"
    resource.tables.all.return_value = [existing]
    resource.Table.return_value.global_secondary_indexes = [{"IndexName": "space_reports_index"}]
    mocker.patch(
        "reporting.services.report_store.dynamodb.get_boto_resource",
        return_value=resource,
    )
    mocker.patch("reporting.settings.DYNAMODB_TABLE_NAME", "tbl")

    await store.initialize()

    resource.meta.client.update_table.assert_not_called()


async def test_list_space_reports_queries_the_index(patch_table, store):
    patch_table.query.return_value = {"Items": [_report_list_entry("r1", "sp1"), _report_list_entry("r2", "sp1")]}

    result = await store.list_space_reports("sp1", user_id="user@example.com")

    assert [item.report_id for item in result] == ["r1", "r2"]
    kwargs = patch_table.query.call_args.kwargs
    assert kwargs["IndexName"] == "space_reports_index"
    # The SK condition keeps the report's #METADATA copy and sub-space items —
    # which also carry space_id — out of the result.
    assert kwargs["KeyConditionExpression"] == "space_id = :space_id AND begins_with(SK, :prefix)"
    assert kwargs["ExpressionAttributeValues"] == {":space_id": "sp1", ":prefix": "REPORT#"}


async def test_list_space_reports_still_applies_visibility_on_the_index_path(patch_table, store):
    patch_table.query.return_value = {
        "Items": [
            _report_list_entry("r1", "sp1"),
            _report_list_entry("r2", "sp1", access={"scope": "private"}, created_by="other@example.com"),
        ]
    }

    result = await store.list_space_reports("sp1", user_id="user@example.com")

    assert [item.report_id for item in result] == ["r1"]


async def test_list_space_reports_falls_back_when_the_index_is_missing(patch_table, store):
    """A table managed outside the app may not have the index yet."""
    missing_index = botocore.exceptions.ClientError(
        {"Error": {"Code": "ValidationException", "Message": "no such index"}}, "Query"
    )
    patch_table.query.side_effect = [
        missing_index,
        {"Items": [_report_list_entry("r1", "sp1"), _report_list_entry("r2", "sp2")]},
    ]

    result = await store.list_space_reports("sp1")

    assert [item.report_id for item in result] == ["r1"]
    assert dynamodb_module._space_reports_index_available is False
    # Second call skips the doomed index query entirely.
    patch_table.query.side_effect = None
    patch_table.query.return_value = {"Items": [_report_list_entry("r3", "sp1")]}
    again = await store.list_space_reports("sp1")
    assert [item.report_id for item in again] == ["r3"]
    assert "IndexName" not in patch_table.query.call_args.kwargs


async def test_list_space_reports_propagates_unexpected_errors(patch_table, store):
    throttled = botocore.exceptions.ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow down"}},
        "Query",
    )
    patch_table.query.side_effect = throttled

    with pytest.raises(botocore.exceptions.ClientError):
        await store.list_space_reports("sp1")
    # A transient failure must not permanently disable the index.
    assert dynamodb_module._space_reports_index_available is None


async def test_delete_space_uses_the_index_for_the_emptiness_check(patch_table, store):
    patch_table.get_item.return_value = {"Item": _space_metadata_item()}
    patch_table.query.side_effect = [
        {"Items": [_report_list_entry("r1", "sp1"), _report_list_entry("r2", "sp1")]},
    ]

    assert await store.delete_space("sp1") == SpaceDeleteResult.NOT_EMPTY
    assert patch_table.query.call_args_list[0].kwargs["IndexName"] == "space_reports_index"


# ---------------------------------------------------------------------------
# GSI availability probe
# ---------------------------------------------------------------------------


async def test_missing_index_is_retried_after_the_backoff(patch_table, store, mocker):
    """An index created while the process runs has to be picked up eventually."""
    missing = botocore.exceptions.ClientError(
        {"Error": {"Code": "ValidationException", "Message": "no such index"}}, "Query"
    )
    clock = mocker.patch("reporting.services.report_store.dynamodb.time.monotonic")

    clock.return_value = 1000.0
    patch_table.query.side_effect = [missing, {"Items": []}]
    await store.list_space_reports("sp1")
    assert dynamodb_module._space_reports_index_available is False

    # Within the window: no probe, straight to the fallback.
    clock.return_value = 1000.0 + dynamodb_module._SPACE_INDEX_RETRY_SECONDS - 1
    patch_table.query.reset_mock()
    patch_table.query.side_effect = None
    patch_table.query.return_value = {"Items": [_report_list_entry("r1", "sp1")]}
    await store.list_space_reports("sp1")
    assert "IndexName" not in patch_table.query.call_args.kwargs

    # Past the window: probe again, and adopt the index now that it exists.
    clock.return_value = 1000.0 + dynamodb_module._SPACE_INDEX_RETRY_SECONDS + 1
    patch_table.query.reset_mock()
    result = await store.list_space_reports("sp1")
    assert patch_table.query.call_args_list[0].kwargs["IndexName"] == "space_reports_index"
    assert dynamodb_module._space_reports_index_available is True
    assert [item.report_id for item in result] == ["r1"]


def _user(user_id: str) -> dict[str, str]:
    """A USER_LOOKUP row as the sweep projects it: the id, and the key it resumes from."""
    return {"SK": f"sk-{user_id}", "user_id": user_id}


@pytest.fixture()
def no_reap_cursor(mocker):
    """Start the sweep from the first user and ignore where it saves its place.

    Tests here assert on which sessions a pass finds; the cursor's own behaviour
    has its own tests below.
    """
    mocker.patch.object(dynamodb_module, "_read_reap_cursor", return_value=None)
    mocker.patch.object(dynamodb_module, "_write_reap_cursor")


# ---------------------------------------------------------------------------
# Idle chat sessions (the session reaper's one cross-user read, SBX-011)
# ---------------------------------------------------------------------------


async def test_list_idle_chat_sessions_walks_users_then_asks_for_the_old_end(patch_table, store, no_reap_cursor):
    """Sessions are partitioned per user with no index over updated_at, so the
    sweep walks the user lookup partition. "Idle" is a key-range condition on
    the list SK -- a user with nothing old costs one query that returns nothing,
    rather than a scan of their sessions."""
    patch_table.query.side_effect = [
        {"Items": [_user("u1"), _user("u2")]},
        {"Items": [{"thread_id": "t1", "updated_at": "2020-01-01T00:00:00+00:00"}]},
        {"Items": []},
    ]

    idle = await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=10)

    assert [(i.user_id, i.thread_id) for i in idle] == [("u1", "t1")]
    sessions_call = patch_table.query.call_args_list[1].kwargs
    assert sessions_call["KeyConditionExpression"] == "PK = :pk AND SK < :cutoff"
    assert sessions_call["ExpressionAttributeValues"][":pk"] == "CHAT_SESSION_LIST#u1"
    assert sessions_call["ExpressionAttributeValues"][":cutoff"] == "UPDATED#2021-01-01T00:00:00+00:00"


async def test_list_idle_chat_sessions_stops_at_the_limit(patch_table, store, no_reap_cursor):
    """One pass must not delete an unbounded number of sessions; the next sweep
    picks up where this one stopped."""
    patch_table.query.side_effect = [
        {"Items": [_user("u1")]},
        {
            "Items": [
                {"thread_id": "t1", "updated_at": "2020-01-01T00:00:00+00:00"},
                {"thread_id": "t2", "updated_at": "2020-01-02T00:00:00+00:00"},
            ]
        },
    ]

    idle = await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=1)

    assert [i.thread_id for i in idle] == ["t1"]


async def test_list_idle_chat_sessions_excludes_headless_runs(patch_table, store, no_reap_cursor):
    """Scheduled run sessions belong to a schedule's history and never leave a
    suspended sandbox behind."""
    patch_table.query.side_effect = [{"Items": [_user("u1")]}, {"Items": []}]

    await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=10)

    filter_expression = patch_table.query.call_args_list[1].kwargs["FilterExpression"]
    assert filter_expression == "attribute_not_exists(origin) OR origin = :interactive"


# ---------------------------------------------------------------------------
# Retirement claims (SBX-011)
# ---------------------------------------------------------------------------


async def test_claiming_a_session_is_conditional_on_its_timestamp(patch_table, store):
    """One conditional write, not the read-then-retry the other mutators use --
    that helper re-reads and retries, which for a claim would mean "the session
    was just used, so try harder to delete it"."""
    assert await store.claim_chat_session_for_retirement("u1", "t1", "2020-01-01T00:00:00+00:00") is True

    kwargs = patch_table.update_item.call_args.kwargs
    assert kwargs["ConditionExpression"] == "attribute_exists(PK) AND updated_at = :expected"
    assert kwargs["ExpressionAttributeValues"][":expected"] == "2020-01-01T00:00:00+00:00"
    assert kwargs["Key"] == {"PK": dynamodb_module._chat_session_metadata_pk("u1"), "SK": "t1"}


async def test_claiming_a_session_that_moved_reports_failure(patch_table, store):
    """A conflict means keep, never retry: the user came back."""
    patch_table.update_item.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
    )

    assert await store.claim_chat_session_for_retirement("u1", "t1", "2020-01-01T00:00:00+00:00") is False


async def test_touching_a_claimed_session_returns_none(patch_table, store):
    """A claimed session is losing its checkpoint and sandbox, so a turn must not
    start against it -- and the retry loop must stop rather than spin."""
    patch_table.get_item.return_value = {
        "Item": {
            "PK": "CHAT_SESSION_METADATA#u1",
            "SK": "t1",
            "thread_id": "t1",
            "created_at": "2020-01-01T00:00:00+00:00",
            "updated_at": "2020-01-01T00:00:00+00:00",
            "retiring_at": "2026-08-11T00:00:00+00:00",
        }
    }

    assert await store.touch_chat_session("u1", "t1") is None
    patch_table.meta.client.transact_write_items.assert_not_called()


async def test_a_session_update_cannot_commit_after_a_claim_lands(patch_table, store):
    """The read above the write is not enough: a claim can land between the two
    and does not move updated_at, so the guard has to be in the condition."""
    patch_table.get_item.return_value = {
        "Item": {
            "PK": "CHAT_SESSION_METADATA#u1",
            "SK": "t1",
            "thread_id": "t1",
            "created_at": "2020-01-01T00:00:00+00:00",
            "updated_at": "2020-01-01T00:00:00+00:00",
        }
    }

    await store.touch_chat_session("u1", "t1")

    items = patch_table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
    condition = items[0]["Update"]["ConditionExpression"]
    assert condition == "updated_at = :old_updated_at AND attribute_not_exists(retiring_at)"


async def test_the_idle_sweep_stops_after_its_user_budget_and_saves_its_place(patch_table, store, mocker):
    """Walking every user in one pass is what makes an hourly sweep unaffordable
    at scale -- and a pass that cannot finish inside the activity timeout retries
    from the first page forever, never reaching the users at the end."""
    mocker.patch.object(dynamodb_module, "CHAT_SESSION_REAP_USERS_PER_PASS", 2)
    mocker.patch.object(dynamodb_module, "_read_reap_cursor", return_value=None)
    saved = mocker.patch.object(dynamodb_module, "_write_reap_cursor")
    patch_table.query.side_effect = [
        {"Items": [_user("u1"), _user("u2")], "LastEvaluatedKey": {"PK": "USER_LOOKUP", "SK": "sk-u2"}},
        {"Items": []},
        {"Items": []},
    ]

    await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=10)

    queried = [
        call.kwargs["ExpressionAttributeValues"][":pk"]
        for call in patch_table.query.call_args_list
        if call.kwargs["ExpressionAttributeValues"][":pk"].startswith("CHAT_SESSION_LIST#")
    ]
    assert queried == ["CHAT_SESSION_LIST#u1", "CHAT_SESSION_LIST#u2"]
    assert saved.call_args.args[1].user_key == {"PK": "USER_LOOKUP", "SK": "sk-u2"}


async def test_the_next_sweep_resumes_where_the_last_one_stopped(patch_table, store, mocker):
    """Otherwise the users at the end of the partition are never reached."""
    mocker.patch.object(
        dynamodb_module,
        "_read_reap_cursor",
        return_value=dynamodb_module._ReapCursor(user_key={"PK": "USER_LOOKUP", "SK": "sk-u2"}),
    )
    mocker.patch.object(dynamodb_module, "_write_reap_cursor")
    patch_table.query.side_effect = [{"Items": [_user("u3")]}, {"Items": []}]

    await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=10)

    assert patch_table.query.call_args_list[0].kwargs["ExclusiveStartKey"] == {"PK": "USER_LOOKUP", "SK": "sk-u2"}


async def test_reaching_the_last_user_clears_the_cursor(patch_table, store, mocker):
    """The next pass starts from the top rather than resuming past the end."""
    mocker.patch.object(
        dynamodb_module,
        "_read_reap_cursor",
        return_value=dynamodb_module._ReapCursor(user_key={"PK": "USER_LOOKUP", "SK": "sk-u2"}),
    )
    saved = mocker.patch.object(dynamodb_module, "_write_reap_cursor")
    patch_table.query.side_effect = [{"Items": [_user("u3")]}, {"Items": []}]

    await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=10)

    assert saved.call_args.args[1] is None


def test_an_unreadable_cursor_starts_from_the_first_user(patch_table):
    """Repeating work is the harmless direction; skipping users is not."""
    patch_table.get_item.side_effect = RuntimeError("boom")

    assert dynamodb_module._read_reap_cursor(patch_table) is None


def test_clearing_the_cursor_deletes_its_item(patch_table):
    dynamodb_module._write_reap_cursor(patch_table, None)

    patch_table.delete_item.assert_called_once()


async def test_stopping_mid_page_resumes_at_the_user_it_stopped_on(patch_table, store, mocker):
    """Saving the page's LastEvaluatedKey instead would resume *after* users this
    pass never looked at, and nothing would ever come back for them."""
    mocker.patch.object(dynamodb_module, "CHAT_SESSION_REAP_USERS_PER_PASS", 1)
    mocker.patch.object(dynamodb_module, "_read_reap_cursor", return_value=None)
    saved = mocker.patch.object(dynamodb_module, "_write_reap_cursor")
    patch_table.query.side_effect = [
        {
            "Items": [_user("u1"), _user("u2"), _user("u3")],
            "LastEvaluatedKey": {"PK": "USER_LOOKUP", "SK": "sk-u3"},
        },
        {"Items": []},
    ]

    await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=10)

    assert saved.call_args.args[1].user_key == {"PK": "USER_LOOKUP", "SK": "sk-u1"}


async def test_one_user_cannot_spend_the_whole_pass_on_headless_sessions(patch_table, store, mocker):
    """origin is a post-read filter, so a user with many old scheduled sessions
    returns empty pages forever; without a cap they exhaust the activity."""
    mocker.patch.object(dynamodb_module, "CHAT_SESSION_REAP_PAGES_PER_USER", 3)
    mocker.patch.object(dynamodb_module, "_read_reap_cursor", return_value=None)
    mocker.patch.object(dynamodb_module, "_write_reap_cursor")
    patch_table.query.side_effect = [
        {"Items": [_user("u1")]},
        *[{"Items": [], "LastEvaluatedKey": {"PK": "CHAT_SESSION_LIST#u1", "SK": f"page-{n}"}} for n in range(10)],
    ]

    await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=10)

    session_queries = [
        call
        for call in patch_table.query.call_args_list
        if call.kwargs["ExpressionAttributeValues"][":pk"].startswith("CHAT_SESSION_LIST#")
    ]
    assert len(session_queries) == 3


async def test_hitting_the_session_limit_resumes_after_that_session(patch_table, store, mocker):
    """The limit is a hard bound on what one pass retires, so resuming after the
    *page* would hand the next pass a page's worth it already collected -- or,
    worse, let this one overshoot by a page."""
    mocker.patch.object(dynamodb_module, "_read_reap_cursor", return_value=None)
    saved = mocker.patch.object(dynamodb_module, "_write_reap_cursor")
    patch_table.query.side_effect = [
        {"Items": [_user("u1")]},
        {
            "Items": [
                {"thread_id": "t1", "updated_at": "2020-01-01T00:00:00+00:00"},
                {"thread_id": "t2", "updated_at": "2020-01-02T00:00:00+00:00"},
            ],
            "LastEvaluatedKey": {"PK": "CHAT_SESSION_LIST#u1", "SK": "end-of-page"},
        },
    ]

    idle = await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=1)

    assert [entry.thread_id for entry in idle] == ["t1"]
    cursor = saved.call_args.args[1]
    assert cursor.session_key == {
        "PK": "CHAT_SESSION_LIST#u1",
        "SK": dynamodb_module._chat_session_list_sk("2020-01-01T00:00:00+00:00", "t1"),
    }


async def test_a_capped_user_is_resumed_from_where_their_pages_ran_out(patch_table, store, mocker):
    """The cap alone is a wall: without the inner key every pass rereads the same
    oldest pages, so an interactive session behind enough headless ones is never
    returned at all."""
    mocker.patch.object(dynamodb_module, "CHAT_SESSION_REAP_PAGES_PER_USER", 2)
    mocker.patch.object(dynamodb_module, "_read_reap_cursor", return_value=None)
    saved = mocker.patch.object(dynamodb_module, "_write_reap_cursor")
    patch_table.query.side_effect = [
        {"Items": [_user("u1")]},
        {"Items": [], "LastEvaluatedKey": {"PK": "CHAT_SESSION_LIST#u1", "SK": "page-1"}},
        {"Items": [], "LastEvaluatedKey": {"PK": "CHAT_SESSION_LIST#u1", "SK": "page-2"}},
    ]

    await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=10)

    cursor = saved.call_args.args[1]
    assert cursor.user_id == "u1"
    assert cursor.session_key == {"PK": "CHAT_SESSION_LIST#u1", "SK": "page-2"}
    assert cursor.user_key == {"PK": "USER_LOOKUP", "SK": "sk-u1"}


async def test_the_next_pass_continues_inside_that_user_before_moving_on(patch_table, store, mocker):
    """Resuming at the user *after* them would skip whatever is left of their
    list -- the sessions the cap hid in the first place."""
    mocker.patch.object(
        dynamodb_module,
        "_read_reap_cursor",
        return_value=dynamodb_module._ReapCursor(
            user_key={"PK": "USER_LOOKUP", "SK": "sk-u1"},
            user_id="u1",
            session_key={"PK": "CHAT_SESSION_LIST#u1", "SK": "page-2"},
        ),
    )
    saved = mocker.patch.object(dynamodb_module, "_write_reap_cursor")
    patch_table.query.side_effect = [
        # Their remaining pages, then the walk continues past them.
        {"Items": [{"thread_id": "t9", "updated_at": "2020-01-01T00:00:00+00:00"}]},
        {"Items": []},
    ]

    idle = await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=10)

    first = patch_table.query.call_args_list[0].kwargs
    assert first["ExpressionAttributeValues"][":pk"] == "CHAT_SESSION_LIST#u1"
    assert first["ExclusiveStartKey"] == {"PK": "CHAT_SESSION_LIST#u1", "SK": "page-2"}
    assert [(entry.user_id, entry.thread_id) for entry in idle] == [("u1", "t9")]
    # Their list is finished, so the next pass goes back to walking users.
    assert saved.call_args.args[1] is None or not saved.call_args.args[1].resumes_within_a_user


def test_a_cursor_item_round_trips():
    """Both halves survive storage, and an unrecognized shape reads as "start
    from the top" rather than resuming somewhere arbitrary."""
    cursor = dynamodb_module._ReapCursor(
        user_key={"PK": "USER_LOOKUP", "SK": "sk-u1"},
        user_id="u1",
        session_key={"PK": "CHAT_SESSION_LIST#u1", "SK": "page-2"},
    )

    assert dynamodb_module._ReapCursor.from_item(cursor.to_item()) == cursor
    assert dynamodb_module._ReapCursor.from_item({}) is None
    assert dynamodb_module._ReapCursor.from_item("nonsense") is None


def test_a_cursor_without_a_session_key_does_not_claim_to_resume_within_a_user():
    cursor = dynamodb_module._ReapCursor(user_key={"PK": "USER_LOOKUP", "SK": "sk-u1"})

    assert cursor.resumes_within_a_user is False
    assert "session_key" not in cursor.to_item()


# ---------------------------------------------------------------------------
# Chat turn event log
# ---------------------------------------------------------------------------


def _session_item(thread_id: str = "1001", **overrides):
    """The session a turn is admitted against; see AGT-008."""
    return {
        "PK": "CHAT_SESSION#u1",
        "SK": thread_id,
        "thread_id": thread_id,
        "user_id": "u1",
        "title": "",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        **overrides,
    }


def _turn_item(turn_id: str = "turn-1", **overrides):
    return {
        "PK": f"CHAT_TURN#{turn_id}",
        "SK": "#METADATA",
        "turn_id": turn_id,
        "thread_id": "1001",
        "user_id": "u1",
        "message_id": "msg_1",
        "text_id": "text_1",
        "status": "running",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        **overrides,
    }


async def test_creating_a_turn_writes_the_record_pointer_and_sweep_entry(patch_table, store):
    """One transaction, because a record without its pointer is a turn no
    reconnect can find and a pointer without its record is a thread that can
    never start one."""
    patch_table.get_item.return_value = {"Item": _session_item()}
    await store.create_chat_turn("u1", "1001", "msg_1", "text_1")

    items = patch_table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
    keys = [(item["Put"]["Item"]["PK"], item["Put"]["Item"]["SK"]) for item in items if "Put" in item]
    assert keys[0] == ("CHAT_TURN_THREAD#u1#THREAD#1001", "#ACTIVE")
    assert keys[1][1] == "#METADATA" and keys[1][0].startswith("CHAT_TURN#")
    assert keys[2][0] == "CHAT_TURN_LIST"
    # ...and the session's own timestamp moves in the very same transaction.
    assert any("Update" in item for item in items)


async def test_the_active_pointer_expires_so_a_dead_producer_cannot_wedge_a_thread(patch_table, store):
    """Without the expiry clause a producer that died mid-turn would leave the
    conversation permanently unable to start another."""
    patch_table.get_item.return_value = {"Item": _session_item()}
    await store.create_chat_turn("u1", "1001", "msg_1", "text_1")

    pointer = patch_table.meta.client.transact_write_items.call_args.kwargs["TransactItems"][0]["Put"]
    assert pointer["ConditionExpression"] == ("attribute_not_exists(PK) OR #s <> :running OR expires_at <= :now")


async def test_a_second_running_turn_is_refused_as_a_conflict(patch_table, store):
    """Only the pointer's own condition failing means "already running"; every
    other transaction cancellation is a real error and must propagate."""
    patch_table.get_item.return_value = {"Item": _session_item()}
    patch_table.meta.client.transact_write_items.side_effect = botocore.exceptions.ClientError(
        {
            "Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [{"Code": "ConditionalCheckFailed"}, {"Code": "None"}, {"Code": "None"}],
        },
        "TransactWriteItems",
    )

    with pytest.raises(ChatTurnConflictError):
        await store.create_chat_turn("u1", "1001", "msg_1", "text_1")


async def test_a_throttled_create_is_not_reported_as_a_conflict(patch_table, store):
    patch_table.get_item.return_value = {"Item": _session_item()}
    patch_table.meta.client.transact_write_items.side_effect = botocore.exceptions.ClientError(
        {
            "Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [{"Code": "ThrottlingError"}],
        },
        "TransactWriteItems",
    )

    with pytest.raises(botocore.exceptions.ClientError):
        await store.create_chat_turn("u1", "1001", "msg_1", "text_1")


def test_event_sort_keys_are_zero_padded():
    """Lexicographic order is the only order a Query gives, so without padding
    EVENT#10 comes back before EVENT#2 and the replay is scrambled."""
    keys = [dynamodb_module._chat_turn_event_sk(n) for n in (2, 10)]

    assert keys == sorted(keys)


def test_the_metadata_item_sorts_before_every_event():
    """The turn record shares its partition with the batches, so a "give me
    everything after seq N" range must never be able to return it."""
    assert dynamodb_module._SK_METADATA < dynamodb_module._chat_turn_event_sk(1)


async def test_reading_events_is_a_strongly_consistent_range_query(patch_table, store):
    """Eventually-consistent propagation is per item, so a poll could see seq 5
    and 7 but not 6 -- and a reader that took the gap would skip 6 forever."""
    patch_table.query.return_value = {"Items": [{"seq": 1, "parts_json": '["one"]'}]}
    patch_table.get_item.return_value = {"Item": _turn_item()}

    await store.read_chat_turn_events("turn-1", 0, limit=10)

    kwargs = patch_table.query.call_args.kwargs
    assert kwargs["ConsistentRead"] is True
    assert kwargs["KeyConditionExpression"] == "PK = :pk AND SK > :after"
    assert kwargs["ExpressionAttributeValues"][":after"] == "EVENT#0000000000"
    assert patch_table.get_item.call_args.kwargs["ConsistentRead"] is True


async def test_reading_events_truncates_at_the_first_gap(patch_table, store):
    patch_table.query.return_value = {
        "Items": [
            {"seq": 1, "parts_json": '["one"]'},
            {"seq": 3, "parts_json": '["three"]'},
        ]
    }
    patch_table.get_item.return_value = {"Item": _turn_item()}

    page = await store.read_chat_turn_events("turn-1", 0, limit=10)

    assert page is not None
    assert [batch.seq for batch in page.batches] == [1]


async def test_appending_a_duplicate_batch_is_a_no_op_success(patch_table, store):
    """A retried producer must not rewrite bytes a reader has already replayed,
    and that is not an error worth failing the turn over."""
    patch_table.put_item.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
    )

    assert await store.append_chat_turn_events("turn-1", 1, '["one"]') is False


async def test_appending_rejects_a_batch_no_item_could_hold(patch_table, store):
    with pytest.raises(ValueError):
        await store.append_chat_turn_events("turn-1", 1, "x" * (CHAT_TURN_MAX_BATCH_BYTES + 1))
    patch_table.put_item.assert_not_called()


async def test_finishing_a_turn_releases_the_thread_only_if_it_still_owns_it(patch_table, store):
    """A successor turn owns the pointer by then; clearing it blindly would let
    two producers run on one thread."""
    patch_table.get_item.return_value = {"Item": _turn_item()}
    patch_table.update_item.return_value = {"Attributes": _turn_item(status="completed", last_seq=4)}

    await store.finish_chat_turn("turn-1", "completed", 4)

    pointer_update = next(
        call.kwargs
        for call in patch_table.update_item.call_args_list
        if call.kwargs["Key"]["PK"] == "CHAT_TURN_THREAD#u1#THREAD#1001"
    )
    assert pointer_update["ConditionExpression"] == "turn_id = :turn_id"
    # The sweep entry's copy of the lease is refreshed here, once per turn.
    assert any(call.kwargs["Key"]["PK"] == "CHAT_TURN_LIST" for call in patch_table.update_item.call_args_list)


async def test_an_expired_running_turn_is_not_offered_for_reconnect(patch_table, store):
    """Its producer is gone, so there is nothing left to reattach to."""
    patch_table.get_item.side_effect = [
        {"Item": {"turn_id": "turn-1", "status": "running", "expires_at": "2020-01-01T00:00:00+00:00"}},
        {"Item": _turn_item(expires_at="2020-01-01T00:00:00+00:00")},
    ]

    assert await store.get_active_chat_turn("u1", "1001") is None


async def test_deleting_a_turn_batches_rather_than_transacts(patch_table, store):
    """A turn has far more batches than a transaction's 100-item cap."""
    patch_table.get_item.return_value = {"Item": _turn_item()}
    patch_table.query.return_value = {
        "Items": [
            {"PK": "CHAT_TURN#turn-1", "SK": "#METADATA"},
            {"PK": "CHAT_TURN#turn-1", "SK": "EVENT#0000000001"},
        ]
    }

    assert await store.delete_chat_turn("turn-1") is True

    patch_table.batch_writer.assert_called_once()
    patch_table.meta.client.transact_write_items.assert_not_called()


async def test_a_finished_turn_has_no_lease_to_renew(patch_table, store):
    patch_table.get_item.return_value = {"Item": _turn_item(status="completed")}

    assert await store.renew_chat_turn_lease("turn-1") is None
    patch_table.meta.client.transact_write_items.assert_not_called()


async def test_cancel_flags_the_running_turn_for_its_owner(patch_table, store):
    patch_table.get_item.return_value = {"Item": {"turn_id": "turn-1", "status": "running"}}
    patch_table.update_item.return_value = {"Attributes": _turn_item(cancel_requested=True)}

    flagged = await store.request_chat_turn_cancel("u1", "1001")

    assert flagged is not None and flagged.cancel_requested is True
    kwargs = patch_table.update_item.call_args.kwargs
    # Owner re-checked on the write, so a stale pointer cannot redirect it.
    assert ":user_id" in kwargs["ExpressionAttributeValues"]
    assert "user_id = :user_id" in kwargs["ConditionExpression"]


async def test_cancel_reports_nothing_when_no_turn_is_running(patch_table, store):
    patch_table.get_item.return_value = {"Item": {"turn_id": "turn-1", "status": "completed"}}

    assert await store.request_chat_turn_cancel("u1", "1001") is None
    patch_table.update_item.assert_not_called()


async def test_a_turn_is_indexed_under_its_own_thread(patch_table, store):
    """Deleting a session must find that thread's turns without filtering the
    global turn partition, whose size depends on every other user's traffic."""
    patch_table.get_item.return_value = {"Item": _session_item()}
    await store.create_chat_turn("u1", "1001", "msg_1", "text_1")

    items = patch_table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
    index_item = items[3]["Put"]["Item"]
    assert index_item["PK"] == "CHAT_TURN_THREAD#u1#THREAD#1001"
    assert index_item["SK"].startswith("TURN#")


async def test_deleting_a_threads_turns_queries_only_that_thread(patch_table, store):
    patch_table.query.return_value = {"Items": []}

    dynamodb_module._delete_thread_chat_turns_sync(patch_table, "u1", "1001")

    kwargs = patch_table.query.call_args.kwargs
    assert kwargs["KeyConditionExpression"] == "PK = :pk AND begins_with(SK, :prefix)"
    assert kwargs["ExpressionAttributeValues"][":pk"] == "CHAT_TURN_THREAD#u1#THREAD#1001"
    # Not a filtered scan of the shared partition.
    assert "FilterExpression" not in kwargs


async def test_lease_renewal_moves_both_halves_in_one_transaction(patch_table, store):
    """Two separate writes leave a window where the record is renewed but the
    pointer has already been taken by a successor -- the old producer would then
    believe it still holds a thread it has lost."""
    patch_table.get_item.return_value = {"Item": _turn_item()}

    renewed = await store.renew_chat_turn_lease("turn-1")

    assert renewed is not None
    items = patch_table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
    keys = [item["Update"]["Key"] for item in items]
    assert keys == [
        {"PK": "CHAT_TURN#turn-1", "SK": "#METADATA"},
        {"PK": "CHAT_TURN_THREAD#u1#THREAD#1001", "SK": "#ACTIVE"},
    ]
    patch_table.update_item.assert_not_called()


async def test_losing_the_pointer_reports_renewal_failure(patch_table, store):
    """The producer has to be told, or it carries on writing a conversation a
    second producer now owns."""
    patch_table.get_item.return_value = {"Item": _turn_item()}
    patch_table.meta.client.transact_write_items.side_effect = botocore.exceptions.ClientError(
        {
            "Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [{"Code": "None"}, {"Code": "ConditionalCheckFailed"}],
        },
        "TransactWriteItems",
    )

    assert await store.renew_chat_turn_lease("turn-1") is None


async def test_deleting_a_turn_collects_batches_whose_header_is_gone(patch_table, store):
    """A producer that kept writing after its conversation was deleted leaves
    headerless batches; refusing to look for them left the only rows worth
    collecting behind."""
    patch_table.get_item.return_value = {}
    patch_table.query.return_value = {"Items": [{"PK": "CHAT_TURN#turn-1", "SK": "EVENT#0000000001"}]}

    assert await store.delete_chat_turn("turn-1") is True

    patch_table.batch_writer.assert_called_once()


async def test_deleting_an_unknown_turn_reports_nothing_to_do(patch_table, store):
    patch_table.get_item.return_value = {}
    patch_table.query.return_value = {"Items": []}

    assert await store.delete_chat_turn("turn-1") is False


async def test_the_sweep_reads_past_turns_that_are_still_live(patch_table, store):
    """Renewal broke the assumption that creation order is expiry order: a few
    long-running turns at the head of the partition would otherwise be re-read
    and skipped by every pass while everything behind them accumulated."""
    patch_table.query.return_value = {
        "Items": [
            {"PK": "CHAT_TURN_LIST", "SK": "CREATED#a#TURN#live", "turn_id": "live"},
            {"PK": "CHAT_TURN_LIST", "SK": "CREATED#b#TURN#dead", "turn_id": "dead"},
        ]
    }

    def _get_item(Key, **kwargs):
        if Key["PK"] == "CHAT_TURN#live":
            # Still running, lease renewed well into the future.
            return {"Item": _turn_item("live", expires_at="2099-01-01T00:00:00+00:00")}
        return {"Item": _turn_item("dead", status="completed", expires_at="2020-01-01T00:00:00+00:00")}

    patch_table.get_item.side_effect = _get_item

    expired = await store.list_expired_chat_turns("2021-01-01T00:00:00+00:00", limit=25)

    assert [entry.turn_id for entry in expired] == ["dead"]


async def test_the_sweep_resumes_where_the_last_pass_stopped(patch_table, store):
    """The index is in creation order and a running turn renews its lease, so
    live entries sit at the head indefinitely. Without a durable cursor every
    pass re-reads them and anything behind them is never reached."""
    patch_table.get_item.side_effect = lambda Key, **kw: (
        {"Item": {"start_key": {"PK": "CHAT_TURN_LIST", "SK": "CREATED#z"}}}
        if Key["PK"] == "CHAT_TURN_SWEEP_CURSOR"
        else {"Item": _turn_item(expires_at="2099-01-01T00:00:00+00:00")}
    )
    patch_table.query.return_value = {"Items": []}

    await store.list_expired_chat_turns("2021-01-01T00:00:00+00:00", limit=25)

    kwargs = patch_table.query.call_args.kwargs
    assert kwargs["ExclusiveStartKey"] == {"PK": "CHAT_TURN_LIST", "SK": "CREATED#z"}
    # And bounded, so one turn's completion cannot read a megabyte of index.
    assert kwargs["Limit"] == dynamodb_module._CHAT_TURN_SWEEP_PAGE_SIZE


async def test_reaching_the_end_of_the_index_restarts_the_next_pass(patch_table, store):
    """Entries that were live during this pass have to be revisited, so
    exhausting the partition clears the cursor rather than pinning it."""
    patch_table.get_item.return_value = {}
    patch_table.query.return_value = {"Items": []}

    await store.list_expired_chat_turns("2021-01-01T00:00:00+00:00", limit=25)

    patch_table.delete_item.assert_called_once_with(Key={"PK": "CHAT_TURN_SWEEP_CURSOR", "SK": "#METADATA"})


async def test_a_pass_that_runs_out_of_pages_records_where_it_got_to(patch_table, store):
    """This is the starvation case: pages of live entries ahead of expired ones."""
    patch_table.get_item.side_effect = lambda Key, **kw: (
        {} if Key["PK"] == "CHAT_TURN_SWEEP_CURSOR" else {"Item": _turn_item(expires_at="2099-01-01T00:00:00+00:00")}
    )
    patch_table.query.return_value = {
        "Items": [{"PK": "CHAT_TURN_LIST", "SK": "CREATED#a", "turn_id": "live"}],
        "LastEvaluatedKey": {"PK": "CHAT_TURN_LIST", "SK": "CREATED#a"},
    }

    expired = await store.list_expired_chat_turns("2021-01-01T00:00:00+00:00", limit=25)

    assert expired == []
    saved = patch_table.put_item.call_args.kwargs["Item"]
    assert saved["PK"] == "CHAT_TURN_SWEEP_CURSOR"
    assert saved["start_key"] == {"PK": "CHAT_TURN_LIST", "SK": "CREATED#a"}


async def test_cancel_named_at_a_finished_turn_leaves_its_successor_alone(patch_table, store):
    """The pointer names whichever turn holds the thread now, which is not
    necessarily the one a delayed stop was aimed at."""
    patch_table.get_item.side_effect = [
        {"Item": _session_item()},
        {"Item": {"turn_id": "successor", "status": "running"}},
        {"Item": _turn_item("successor")},
    ]

    assert await store.request_chat_turn_cancel("u1", "1001", "the-one-that-finished") is None
    patch_table.update_item.assert_not_called()


def test_a_turn_item_round_trips_its_client_token():
    """Stored but not returned makes token-addressed Stop silently useless: the
    comparison is against a value that is always None."""
    turn = dynamodb_module._chat_turn_from_item(_turn_item(client_token="ct_roundtrip"))

    assert turn.client_token == "ct_roundtrip"


async def test_the_create_transaction_refuses_a_stopped_send(patch_table, store):
    """Checked inside the transaction, not read above it: a stop for this send
    can land while the create is in flight."""
    patch_table.get_item.return_value = {"Item": _session_item()}

    await store.create_chat_turn("u1", "1001", "msg_1", "text_1", "ct_racingsend")

    items = patch_table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
    guard = next(item["ConditionCheck"] for item in items if "ConditionCheck" in item)
    assert guard["Key"]["PK"] == "CHAT_TURN_CANCEL#u1#THREAD#1001#TOKEN#ct_racingsend"
    assert guard["ConditionExpression"].startswith("attribute_not_exists(PK)")


async def test_a_stopped_send_is_reported_as_canceled_not_a_conflict(patch_table, store):
    patch_table.get_item.return_value = {"Item": _session_item()}
    patch_table.meta.client.transact_write_items.side_effect = botocore.exceptions.ClientError(
        {
            "Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [
                *({"Code": "None"} for _ in range(7)),
                {"Code": "ConditionalCheckFailed"},
            ],
        },
        "TransactWriteItems",
    )

    with pytest.raises(ChatTurnCanceledError):
        await store.create_chat_turn("u1", "1001", "msg_1", "text_1", "ct_racingsend")


async def test_a_failed_sweep_refresh_does_not_hold_the_thread(patch_table, store):
    """The sweep entry is an optimisation. Failing to refresh it must not leave
    the conversation unable to start another turn until the lease expires."""
    patch_table.get_item.return_value = {"Item": _turn_item()}
    released: list[str] = []

    def _update(**kwargs):
        if kwargs["Key"]["PK"] == "CHAT_TURN_LIST":
            raise botocore.exceptions.ClientError({"Error": {"Code": "ThrottlingException"}}, "UpdateItem")
        released.append(kwargs["Key"]["PK"])
        return {"Attributes": _turn_item(status="completed", last_seq=4)}

    patch_table.update_item.side_effect = _update

    finished = await store.finish_chat_turn("turn-1", "completed", 4)

    assert finished is not None
    assert "CHAT_TURN_THREAD#u1#THREAD#1001" in released


async def test_the_tombstone_is_written_before_the_search(patch_table, store):
    """A create can commit between looking and writing. One that commits after
    the tombstone is refused by its own transaction; one that commits before is
    found by the search. Looking first leaves a window where neither happens."""
    calls: list[str] = []
    patch_table.put_item.side_effect = lambda **kw: calls.append("tombstone")

    def _get_item(Key, **kwargs):
        if Key["PK"].startswith("CHAT_SESSION#"):
            # The session check that gates the call; not the turn search.
            return {"Item": _session_item()}
        calls.append("search")
        return {}

    patch_table.get_item.side_effect = _get_item

    await store.request_chat_turn_cancel("u1", "1001", None, "ct_racingsend")

    assert calls == ["tombstone", "search"]


async def test_an_expired_tombstone_does_not_brick_a_token(patch_table, store):
    """Kept forever it would make the token permanently unusable."""
    patch_table.get_item.return_value = {"Item": _session_item()}

    await store.create_chat_turn("u1", "1001", "msg_1", "text_1", "ct_racingsend")

    items = patch_table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
    guard = next(item["ConditionCheck"] for item in items if "ConditionCheck" in item)
    assert guard["ConditionExpression"] == "attribute_not_exists(PK) OR expires_at <= :cancel_now"


async def test_a_stop_for_a_thread_with_no_session_records_nothing(patch_table, store):
    """A tombstone only means anything for a session that can hold a turn;
    without this any caller could write one per token against any thread."""
    patch_table.get_item.return_value = {}

    assert await store.request_chat_turn_cancel("u1", "9999", None, "ct_nosession") is None

    patch_table.put_item.assert_not_called()


def test_table_creation_enables_ttl_for_tombstones(mocker):
    """They have no shared partition to sweep, so without TTL an app-managed
    table keeps them forever."""
    dynamodb = mocker.MagicMock()
    dynamodb.meta.client.describe_time_to_live.return_value = {
        "TimeToLiveDescription": {"TimeToLiveStatus": "DISABLED"}
    }

    dynamodb_module._enable_ttl(dynamodb)

    dynamodb.meta.client.update_time_to_live.assert_called_once()
    spec = dynamodb.meta.client.update_time_to_live.call_args.kwargs["TimeToLiveSpecification"]
    assert spec == {"Enabled": True, "AttributeName": "ttl"}


def test_enabling_ttl_is_skipped_when_already_on(mocker):
    dynamodb = mocker.MagicMock()
    dynamodb.meta.client.describe_time_to_live.return_value = {"TimeToLiveDescription": {"TimeToLiveStatus": "ENABLED"}}

    dynamodb_module._enable_ttl(dynamodb)

    dynamodb.meta.client.update_time_to_live.assert_not_called()
