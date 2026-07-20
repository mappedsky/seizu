from reporting import scheduled_query_modules
from reporting.scheduled_query_modules import get_action_schemas


async def test_load_modules(mocker):
    await scheduled_query_modules.load_modules()
    assert list(scheduled_query_modules._MODULES.keys()) == [
        "log",
        "sqs",
        "slack",
        "statsd",
    ]
    assert scheduled_query_modules.get_module_names() == [
        "log",
        "sqs",
        "slack",
        "statsd",
    ]
    assert scheduled_query_modules.get_module("sqs") is not None


async def test_load_modules_skips_removed_workflow_module(mocker):
    # A stale WORKFLOW_ACTIVITY_MODULES env value naming the removed
    # dispatcher must not crash the worker at startup.
    mocker.patch(
        "reporting.settings.WORKFLOW_ACTIVITY_MODULES",
        [
            "reporting.scheduled_query_modules.sqs",
            "reporting.scheduled_query_modules.workflow",
            "reporting.scheduled_query_modules.temporal",
        ],
    )
    assert scheduled_query_modules._configured_module_names() == [
        "reporting.scheduled_query_modules.log",
        "reporting.scheduled_query_modules.sqs",
    ]
    assert scheduled_query_modules.get_configured_action_names() == ["log", "sqs"]


def test_get_action_schemas_returns_builtin_schemas():
    schemas = get_action_schemas()
    assert "log" in schemas


def test_get_action_schemas_skips_bad_module(mocker):
    mocker.patch(
        "reporting.scheduled_query_modules.settings.WORKFLOW_ACTIVITY_MODULES",
        ["not.a.real.module.xyz"],
    )
    schemas = get_action_schemas()
    assert "not.a.real.module.xyz" not in schemas
    assert "log" in schemas
