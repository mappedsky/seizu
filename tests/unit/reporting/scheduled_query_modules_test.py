from reporting import scheduled_query_modules
from reporting.scheduled_query_modules import get_action_schemas


async def test_load_modules(mocker):
    # agent_chat.setup() initializes the chat checkpointer; keep the test hermetic.
    mocker.patch("reporting.services.chat_graph.initialize_chat_checkpoints", mocker.AsyncMock())
    await scheduled_query_modules.load_modules()
    assert list(scheduled_query_modules._MODULES.keys()) == ["sqs", "slack", "statsd", "temporal", "agent_chat"]
    assert scheduled_query_modules.get_module_names() == ["sqs", "slack", "statsd", "temporal", "agent_chat"]
    assert scheduled_query_modules.get_module("sqs") is not None


def test_get_action_permissions():
    permissions = scheduled_query_modules.get_action_permissions()
    assert permissions.get("agent_chat") == "chat:bypass_permissions"
    assert "temporal" not in permissions
    assert "log" not in permissions


def test_get_action_schemas_returns_builtin_schemas():
    schemas = get_action_schemas()
    assert "log" in schemas


def test_get_action_schemas_skips_bad_module(mocker):
    mocker.patch(
        "reporting.scheduled_query_modules.settings.SCHEDULED_QUERY_MODULES",
        ["not.a.real.module.xyz"],
    )
    schemas = get_action_schemas()
    assert "not.a.real.module.xyz" not in schemas
    assert "log" in schemas
