import temporalio.client
import temporalio.exceptions

from reporting import settings
from reporting.scheduled_query_modules import temporal
from reporting.schema.report_config import ScheduledQueryItem
from reporting.schema.reporting_config import ScheduledQueryAction
from reporting.temporal_workflows.shared import CveRepoReportInput

_NOW = "2024-01-01T00:00:00+00:00"


def _action(**overrides):
    config = {
        "workflow": "cve_repo_report",
        **overrides,
    }
    return ScheduledQueryAction(action_type="temporal", action_config=config)


def _item() -> ScheduledQueryItem:
    return ScheduledQueryItem(
        scheduled_query_id="sq-1",
        name="New CVEs affecting repositories",
        cypher="MATCH (n) RETURN n",
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user-1",
        last_scheduled_at=_NOW,
    )


def _patch_client(mocker):
    client = mocker.AsyncMock()
    mocker.patch.object(temporalio.client.Client, "connect", mocker.AsyncMock(return_value=client))
    return client


def test_action_name():
    assert temporal.action_name() == "temporal"


def test_action_config_schema():
    schema = {field.name: field for field in temporal.action_config_schema()}
    assert schema["workflow"].required is True
    assert "cve_repo_report" in (schema["workflow"].options or [])
    assert "cve_dependency_remediation" in (schema["workflow"].options or [])
    assert "accept_confirmation_bypass" not in schema


def test_action_config_schema_honors_enabled_allowlist(mocker):
    mocker.patch("reporting.settings.TEMPORAL_ENABLED_WORKFLOWS", ["cve_repo_report"])
    schema = {field.name: field for field in temporal.action_config_schema()}
    assert schema["workflow"].options == ["cve_repo_report"]
    # Only the enabled workflow's description is surfaced in the picker help.
    assert "cve_dependency_remediation" not in schema["workflow"].description


async def test_handle_results_refuses_disabled_workflow(mocker):
    client = _patch_client(mocker)
    mocker.patch("reporting.settings.TEMPORAL_ENABLED_WORKFLOWS", ["cve_repo_report"])
    mocker.patch(
        "reporting.services.report_store.get_scheduled_query",
        mocker.AsyncMock(return_value=_item()),
    )
    results = [{"details": {"repo": "org/app", "package": "requests"}}]

    await temporal.handle_results("sq-1", _action(workflow="cve_dependency_remediation"), results)

    # Registered but not in the allowlist → not dispatched.
    client.start_workflow.assert_not_called()


async def test_setup():
    assert await temporal.setup() is None


async def test_handle_results_no_results(mocker):
    connect = mocker.patch.object(temporalio.client.Client, "connect")
    await temporal.handle_results("sq-1", _action(), [])
    connect.assert_not_called()


async def test_handle_results_starts_workflow(mocker):
    client = _patch_client(mocker)
    mocker.patch(
        "reporting.services.report_store.get_scheduled_query",
        mocker.AsyncMock(return_value=_item()),
    )
    results = [{"details": {"repo": "org/app", "cve_id": "CVE-2026-0001"}}]

    await temporal.handle_results("sq-1", _action(), results)

    client.start_workflow.assert_awaited_once()
    args, kwargs = client.start_workflow.await_args
    assert args[0] == "cve_repo_report"
    workflow_input = args[1]
    assert isinstance(workflow_input, CveRepoReportInput)
    assert workflow_input.creator_user_id == "user-1"
    assert workflow_input.rows == [{"repo": "org/app", "cve_id": "CVE-2026-0001"}]
    assert kwargs["id"] == f"seizu:cve_repo_report:sq-1:{_NOW}"
    assert kwargs["task_queue"] == settings.TEMPORAL_TASK_QUEUE


async def test_handle_results_refuses_unknown_workflow(mocker):
    client = _patch_client(mocker)
    mocker.patch("reporting.services.report_store.get_scheduled_query")
    results = [{"details": {"repo": "org/app"}}]

    await temporal.handle_results("sq-1", _action(workflow="does_not_exist"), results)

    client.start_workflow.assert_not_called()


async def test_handle_results_swallows_already_started(mocker):
    client = _patch_client(mocker)
    client.start_workflow.side_effect = temporalio.exceptions.WorkflowAlreadyStartedError("wf-1", "cve_repo_report")
    mocker.patch(
        "reporting.services.report_store.get_scheduled_query",
        mocker.AsyncMock(return_value=_item()),
    )
    results = [{"details": {"repo": "org/app"}}]

    await temporal.handle_results("sq-1", _action(), results)


async def test_handle_results_truncates_rows(mocker):
    client = _patch_client(mocker)
    mocker.patch(
        "reporting.services.report_store.get_scheduled_query",
        mocker.AsyncMock(return_value=_item()),
    )
    results = [{"details": {"repo": f"org/app-{i}"}} for i in range(5)]

    await temporal.handle_results("sq-1", _action(max_rows=2), results)

    args, _kwargs = client.start_workflow.await_args
    assert len(args[1].rows) == 2


async def test_handle_results_dispatches_rowless_workflow_without_results(mocker):
    client = _patch_client(mocker)
    mocker.patch(
        "reporting.services.report_store.get_scheduled_query",
        mocker.AsyncMock(return_value=_item()),
    )

    # cartography_sync has requires_rows=False: the query (e.g. RETURN 1) is
    # only the trigger, so even an empty result set dispatches.
    await temporal.handle_results("sq-1", _action(workflow="cartography_sync", modules=["cve"]), [])

    client.start_workflow.assert_awaited_once()
    args, kwargs = client.start_workflow.await_args
    assert args[0] == "cartography_sync"
    workflow_input = args[1]
    # The action_config reached the input factory: one stage per module,
    # wrapped by the implicit create-indexes/analysis stages.
    assert [run.module for stage in workflow_input.stages for run in stage.runs] == [
        "create-indexes",
        "cve",
        "analysis",
    ]
    assert workflow_input.scheduled_query_id == "sq-1"
    assert kwargs["id"] == f"seizu:cartography_sync:sq-1:{_NOW}"


async def test_handle_results_refuses_invalid_stored_config(mocker):
    client = _patch_client(mocker)
    mocker.patch(
        "reporting.services.report_store.get_scheduled_query",
        mocker.AsyncMock(return_value=_item()),
    )

    # A stored config that no longer validates (module since disabled or
    # unknown) must not dispatch.
    await temporal.handle_results("sq-1", _action(workflow="cartography_sync", modules=["gcp"]), [])

    client.start_workflow.assert_not_called()


def test_validate_action_config_delegates_to_workflow():
    assert temporal.validate_action_config({"workflow": "cve_repo_report"}) is None
    assert "exactly one" in temporal.validate_action_config({"workflow": "cartography_sync"})
    assert temporal.validate_action_config({"workflow": "cartography_sync", "modules": ["cve"]}) is None
