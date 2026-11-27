"""Settings parsing that is not a plain env read."""

import os
from unittest.mock import patch

import pytest

from reporting.settings import (
    _DEFAULT_SANDBOX_CORE_TOOLS,
    ExternalPluginURLMatchMode,
    _core_tools_from_env,
    _external_plugin_url_match_mode_from_env,
    validate_persistence_settings,
)


async def test_unset_core_tools_uses_the_default() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SANDBOX_CORE_TOOLS", None)
        assert _core_tools_from_env() == _DEFAULT_SANDBOX_CORE_TOOLS


async def test_an_explicitly_empty_core_tools_means_no_tools() -> None:
    """`SANDBOX_CORE_TOOLS=` is the documented way to bind nothing up front.

    Regression test: this went through `list_env`, which treats an empty value
    as absent and returns the default, so the documented disable path silently
    kept all four graph tools. The harness's read-back check caught it; nothing
    in the test suite would have.
    """
    with patch.dict(os.environ, {"SANDBOX_CORE_TOOLS": ""}):
        assert _core_tools_from_env() == []


async def test_core_tools_are_split_and_stripped() -> None:
    with patch.dict(os.environ, {"SANDBOX_CORE_TOOLS": "graph__schema, graph__query ,"}):
        assert _core_tools_from_env() == ["graph__schema", "graph__query"]


def test_plugin_url_match_mode_defaults_to_none() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE", None)
        assert _external_plugin_url_match_mode_from_env() == ExternalPluginURLMatchMode.NONE


@pytest.mark.parametrize("mode", list(ExternalPluginURLMatchMode))
def test_plugin_url_match_mode_accepts_declared_values(mode: ExternalPluginURLMatchMode) -> None:
    with patch.dict(os.environ, {"MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE": mode.value}):
        assert _external_plugin_url_match_mode_from_env() == mode


def test_plugin_url_match_mode_rejects_unknown_values() -> None:
    with patch.dict(os.environ, {"MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE": "sometimes"}):
        with pytest.raises(RuntimeError, match="must be one of: strict, lax, none"):
            _external_plugin_url_match_mode_from_env()


def test_persistence_settings_accept_postgres_configuration() -> None:
    with patch.dict(os.environ, {"SQL_DATABASE_URL": "postgresql://db/seizu"}, clear=True):
        validate_persistence_settings()


@pytest.mark.parametrize("name", ["REPORT_STORE_BACKEND", "CHAT_CHECKPOINT_BACKEND", "DYNAMODB_TABLE_NAME"])
def test_persistence_settings_reject_removed_configuration(name: str) -> None:
    with patch.dict(os.environ, {name: "legacy"}, clear=True):
        with pytest.raises(RuntimeError, match=name):
            validate_persistence_settings()


def _bedrock(**overrides: object) -> object:
    values: dict[str, object] = {
        "SANDBOX_ENABLED": True,
        "SANDBOX_PROVIDER": "bedrock",
        "SANDBOX_BEDROCK_MANAGE_RUNTIME": True,
        "SANDBOX_BEDROCK_IMAGE": "img",
        "SANDBOX_BEDROCK_ROLE_ARN": "arn:aws:iam::1:role/r",
        "SANDBOX_BEDROCK_SUBNETS": ["subnet-a"],
        "SANDBOX_BEDROCK_SECURITY_GROUPS": ["sg-1"],
        "SANDBOX_BEDROCK_STORAGE": "session",
        "SANDBOX_BEDROCK_S3_BUCKET": "",
        "SANDBOX_BEDROCK_MAX_LIFETIME_SECONDS": 28_800,
        "SANDBOX_SESSION_TIMEOUT_SECONDS": 1_800,
        "SANDBOX_ALLOW_INTERNET": False,
        "SANDBOX_BEDROCK_RUNTIME_ARN": "",
        "SANDBOX_BEDROCK_REGION": "",
    }
    values.update(overrides)
    return patch.multiple("reporting.settings", **values)


async def test_a_complete_bedrock_configuration_is_valid() -> None:
    from reporting.settings import sandbox_configuration_errors, validate_sandbox_settings

    with _bedrock():
        assert sandbox_configuration_errors() == []
        validate_sandbox_settings()


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"SANDBOX_PROVIDER": "docker"}, "SANDBOX_PROVIDER must be one of"),
        ({"SANDBOX_BEDROCK_IMAGE": ""}, "SANDBOX_BEDROCK_IMAGE"),
        ({"SANDBOX_BEDROCK_ROLE_ARN": ""}, "SANDBOX_BEDROCK_ROLE_ARN"),
        ({"SANDBOX_BEDROCK_STORAGE": "efs"}, "SANDBOX_BEDROCK_STORAGE must be one of"),
        ({"SANDBOX_BEDROCK_STORAGE": "s3"}, "SANDBOX_BEDROCK_S3_BUCKET"),
        ({"SANDBOX_BEDROCK_SECURITY_GROUPS": []}, "must be set together"),
        ({"SANDBOX_BEDROCK_SUBNETS": [], "SANDBOX_BEDROCK_SECURITY_GROUPS": []}, "PUBLIC network mode"),
        ({"SANDBOX_BEDROCK_MAX_LIFETIME_SECONDS": 30}, "MAX_LIFETIME"),
        ({"SANDBOX_SESSION_TIMEOUT_SECONDS": 30_000}, "idle timeout"),
    ],
)
async def test_an_unworkable_sandbox_configuration_refuses_to_start(
    overrides: dict[str, object], fragment: str
) -> None:
    from reporting.settings import validate_sandbox_settings

    with _bedrock(**overrides), pytest.raises(RuntimeError, match=fragment):
        validate_sandbox_settings()


async def test_public_mode_is_accepted_when_internet_access_is_intended() -> None:
    from reporting.settings import sandbox_configuration_errors

    with _bedrock(SANDBOX_BEDROCK_SUBNETS=[], SANDBOX_BEDROCK_SECURITY_GROUPS=[], SANDBOX_ALLOW_INTERNET=True):
        assert sandbox_configuration_errors() == []


async def test_an_operator_managed_runtime_needs_no_image_or_role() -> None:
    from reporting.settings import sandbox_configuration_errors

    with _bedrock(SANDBOX_BEDROCK_MANAGE_RUNTIME=False, SANDBOX_BEDROCK_IMAGE="", SANDBOX_BEDROCK_ROLE_ARN=""):
        assert sandbox_configuration_errors() == []


_OPERATOR_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/seizu_sandbox_prod-a1B2c3D4e5"


async def test_an_operator_runtime_can_be_named_by_arn() -> None:
    from reporting.settings import sandbox_configuration_errors

    with _bedrock(
        SANDBOX_BEDROCK_MANAGE_RUNTIME=False,
        SANDBOX_BEDROCK_RUNTIME_ARN=_OPERATOR_ARN,
        SANDBOX_BEDROCK_REGION="us-east-1",
    ):
        assert sandbox_configuration_errors() == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"SANDBOX_BEDROCK_MANAGE_RUNTIME": True}, "requires SANDBOX_BEDROCK_MANAGE_RUNTIME=false"),
        ({"SANDBOX_BEDROCK_RUNTIME_ARN": "seizu_sandbox_prod-a1B2c3D4e5"}, "must be an AgentCore runtime ARN"),
        (
            {"SANDBOX_BEDROCK_RUNTIME_ARN": _OPERATOR_ARN + "/runtime-endpoint/prod"},
            "must be an AgentCore runtime ARN",
        ),
        ({"SANDBOX_BEDROCK_REGION": "eu-west-1"}, "is in us-east-1, but SANDBOX_BEDROCK_REGION is eu-west-1"),
    ],
)
async def test_an_unusable_runtime_arn_refuses_to_start(overrides: dict[str, object], fragment: str) -> None:
    from reporting.settings import validate_sandbox_settings

    values: dict[str, object] = {"SANDBOX_BEDROCK_MANAGE_RUNTIME": False, "SANDBOX_BEDROCK_RUNTIME_ARN": _OPERATOR_ARN}
    values.update(overrides)
    with _bedrock(**values), pytest.raises(RuntimeError, match=fragment):
        validate_sandbox_settings()


async def test_a_disabled_sandbox_is_never_an_error() -> None:
    from reporting.settings import sandbox_configuration_errors

    with _bedrock(SANDBOX_ENABLED=False, SANDBOX_PROVIDER="docker"):
        assert sandbox_configuration_errors() == []
