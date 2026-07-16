import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import temporalio.api.common.v1 as common_pb
import temporalio.api.enums.v1 as enums_pb
import temporalio.api.failure.v1 as failure_pb
import temporalio.api.history.v1 as history_pb
import temporalio.client
import temporalio.service

from reporting.services import temporal_runs

_SQ_ID = "sq-abc123"
_WORKFLOW_ID = f"seizu:cve_repo_report:{_SQ_ID}:2024-01-01T00:00:00+00:00"


def _payloads(value):
    return common_pb.Payloads(
        payloads=[
            common_pb.Payload(
                metadata={"encoding": b"json/plain"},
                data=json.dumps(value).encode(),
            )
        ]
    )


def _event(event_id, **attrs):
    event = history_pb.HistoryEvent(event_id=event_id, **attrs)
    event.event_time.FromDatetime(datetime(2024, 1, 1, 0, event_id, 0))
    return event


async def _aiter(items):
    for item in items:
        yield item


def _mock_client(mocker, *, executions=None, handle=None):
    client = MagicMock()
    captured = {}

    def _list_workflows(query, limit=None):
        captured["query"] = query
        captured["limit"] = limit
        return _aiter(executions or [])

    client.list_workflows = _list_workflows
    if handle is not None:
        client.get_workflow_handle = MagicMock(return_value=handle)
    mocker.patch.object(temporal_runs, "_get_client", new=AsyncMock(return_value=client))
    return client, captured


def _mock_handle(events, *, status=temporalio.client.WorkflowExecutionStatus.COMPLETED, pending=()):
    handle = MagicMock()
    handle.describe = AsyncMock(
        return_value=SimpleNamespace(
            run_id="run-1",
            status=status,
            start_time=datetime(2024, 1, 1, tzinfo=UTC),
            close_time=datetime(2024, 1, 1, 1, tzinfo=UTC),
            raw_description=SimpleNamespace(pending_activities=list(pending)),
        )
    )
    handle.fetch_history_events = lambda **kwargs: _aiter(events)
    return handle


# ---------------------------------------------------------------------------
# workflow_id_matches
# ---------------------------------------------------------------------------


def test_workflow_id_matches():
    assert temporal_runs.workflow_id_matches(_SQ_ID, _WORKFLOW_ID)
    # Timestamp segment may itself contain colons.
    assert temporal_runs.workflow_id_matches("sq1", "seizu:cve_repo_report:sq1:2024-01-01T00:00:00+00:00")
    assert temporal_runs.workflow_id_matches(
        _SQ_ID,
        f"seizu-workflow:{_SQ_ID}-2026-07-16T12:00:00Z",
    )


def test_workflow_id_matches_rejects_foreign_ids():
    assert not temporal_runs.workflow_id_matches("other-sq", _WORKFLOW_ID)
    assert not temporal_runs.workflow_id_matches(_SQ_ID, f"other:{_SQ_ID}:x:y")
    assert not temporal_runs.workflow_id_matches(_SQ_ID, f"seizu::{_SQ_ID}:x")
    # Workflow-name segment must be a registered workflow.
    assert not temporal_runs.workflow_id_matches(_SQ_ID, f"seizu:not_a_registered_workflow:{_SQ_ID}:x")
    assert not temporal_runs.workflow_id_matches(_SQ_ID, "seizu:cve_repo_report")
    assert not temporal_runs.workflow_id_matches(
        _SQ_ID,
        f"seizu-workflow:{_SQ_ID}-2026-07-16T12:00:00Z:activity:1:cartography_sync",
    )
    assert not temporal_runs.workflow_id_matches(
        _SQ_ID,
        f"seizu-workflow:{_SQ_ID}-not-a-schedule-time",
    )


async def test_get_client_caches_and_wraps_connection_errors(mocker):
    temporal_runs._client = None
    client = object()
    connect = mocker.patch("temporalio.client.Client.connect", new=AsyncMock(return_value=client))
    assert await temporal_runs._get_client() is client
    assert await temporal_runs._get_client() is client
    connect.assert_awaited_once()
    temporal_runs._client = None
    connect.side_effect = RuntimeError("offline")
    try:
        await temporal_runs._get_client()
        raise AssertionError("expected unavailable")
    except temporal_runs.TemporalUnavailableError:
        pass
    finally:
        temporal_runs._client = None


def test_small_visibility_helpers():
    assert temporal_runs.workflow_id_matches(_SQ_ID, f"seizu-workflow:{_SQ_ID}:manual:request")
    assert temporal_runs._event_time(history_pb.HistoryEvent()) is None
    assert temporal_runs._failure_summary(None) is None
    assert temporal_runs._retry_state_label(0) is None


# ---------------------------------------------------------------------------
# list_workflow_runs
# ---------------------------------------------------------------------------


async def test_list_workflow_runs(mocker):
    executions = [
        SimpleNamespace(
            id=_WORKFLOW_ID,
            run_id="run-1",
            status=temporalio.client.WorkflowExecutionStatus.FAILED,
            start_time=datetime(2024, 1, 1, tzinfo=UTC),
            close_time=datetime(2024, 1, 1, 1, tzinfo=UTC),
            history_length=42,
        ),
        SimpleNamespace(
            id=_WORKFLOW_ID,
            run_id="run-2",
            status=None,
            start_time=None,
            close_time=None,
            history_length=None,
        ),
        SimpleNamespace(
            id=f"seizu-workflow:{_SQ_ID}-2026-07-16T12:00:00Z",
            run_id="run-3",
            status=temporalio.client.WorkflowExecutionStatus.COMPLETED,
            start_time=datetime(2026, 7, 16, 12, tzinfo=UTC),
            close_time=datetime(2026, 7, 16, 13, tzinfo=UTC),
            history_length=24,
        ),
    ]
    _, captured = _mock_client(mocker, executions=executions)

    runs = await temporal_runs.list_workflow_runs(_SQ_ID, limit=10)

    assert captured["limit"] == 10
    # One STARTS_WITH clause per registered workflow.
    assert f'WorkflowId STARTS_WITH "seizu:cve_repo_report:{_SQ_ID}:"' in captured["query"]
    assert f'WorkflowId STARTS_WITH "seizu:cve_dependency_remediation:{_SQ_ID}:"' in captured["query"]
    assert [r.status for r in runs] == ["failed", "unknown", "completed"]
    assert runs[0].workflow_name == "cve_repo_report"
    assert runs[0].start_time == "2024-01-01T00:00:00+00:00"
    assert runs[0].history_length == 42
    assert runs[1].start_time is None
    assert runs[2].workflow_name == "configured"


async def test_list_workflow_runs_rejects_unquotable_sq_id(mocker):
    client, _ = _mock_client(mocker)
    assert await temporal_runs.list_workflow_runs('sq" OR WorkflowId="x', limit=10) == []


async def test_list_workflow_runs_unavailable(mocker):
    client = MagicMock()

    def _list_workflows(query, limit=None):
        raise temporalio.service.RPCError("unavailable", temporalio.service.RPCStatusCode.UNAVAILABLE, b"")

    client.list_workflows = _list_workflows
    mocker.patch.object(temporal_runs, "_get_client", new=AsyncMock(return_value=client))
    try:
        await temporal_runs.list_workflow_runs(_SQ_ID, limit=10)
        raise AssertionError("expected TemporalUnavailableError")
    except temporal_runs.TemporalUnavailableError:
        pass


# ---------------------------------------------------------------------------
# get_workflow_run_detail
# ---------------------------------------------------------------------------


async def test_get_workflow_run_detail_rejects_foreign_workflow_id(mocker):
    get_client = mocker.patch.object(temporal_runs, "_get_client", new=AsyncMock())
    detail = await temporal_runs.get_workflow_run_detail("other-sq", _WORKFLOW_ID, "run-1")
    assert detail is None
    get_client.assert_not_awaited()


async def test_get_workflow_run_detail_not_found(mocker):
    handle = MagicMock()
    handle.describe = AsyncMock(
        side_effect=temporalio.service.RPCError("not found", temporalio.service.RPCStatusCode.NOT_FOUND, b"")
    )
    _mock_client(mocker, handle=handle)
    assert await temporal_runs.get_workflow_run_detail(_SQ_ID, _WORKFLOW_ID, "run-x") is None


async def test_get_workflow_run_detail_invalid_run_id(mocker):
    # Temporal rejects a malformed run_id with INVALID_ARGUMENT, not NOT_FOUND.
    handle = MagicMock()
    handle.describe = AsyncMock(
        side_effect=temporalio.service.RPCError(
            "Invalid RunId.", temporalio.service.RPCStatusCode.INVALID_ARGUMENT, b""
        )
    )
    _mock_client(mocker, handle=handle)
    assert await temporal_runs.get_workflow_run_detail(_SQ_ID, _WORKFLOW_ID, "not-a-uuid") is None


async def test_get_workflow_run_detail_activities(mocker):
    events = [
        _event(
            1,
            activity_task_scheduled_event_attributes=history_pb.ActivityTaskScheduledEventAttributes(
                activity_id="1",
                activity_type=common_pb.ActivityType(name="run_repo_report_chat"),
                input=_payloads({"repo": "org/app"}),
                retry_policy=common_pb.RetryPolicy(maximum_attempts=3),
            ),
        ),
        _event(
            2,
            activity_task_started_event_attributes=history_pb.ActivityTaskStartedEventAttributes(
                scheduled_event_id=1,
                attempt=3,
                last_failure=failure_pb.Failure(message="attempt 2 crashed"),
            ),
        ),
        _event(
            3,
            activity_task_completed_event_attributes=history_pb.ActivityTaskCompletedEventAttributes(
                scheduled_event_id=1,
                result=_payloads({"status": "ok"}),
            ),
        ),
        _event(
            4,
            activity_task_scheduled_event_attributes=history_pb.ActivityTaskScheduledEventAttributes(
                activity_id="2",
                activity_type=common_pb.ActivityType(name="run_repo_report_chat"),
                input=_payloads({"repo": "org/other"}),
            ),
        ),
        _event(
            5,
            activity_task_started_event_attributes=history_pb.ActivityTaskStartedEventAttributes(
                scheduled_event_id=4,
                attempt=1,
            ),
        ),
        _event(
            6,
            activity_task_failed_event_attributes=history_pb.ActivityTaskFailedEventAttributes(
                scheduled_event_id=4,
                failure=failure_pb.Failure(
                    message="boom",
                    application_failure_info=failure_pb.ApplicationFailureInfo(type="RuntimeError"),
                    cause=failure_pb.Failure(message="root cause"),
                ),
                retry_state=enums_pb.RetryState.RETRY_STATE_MAXIMUM_ATTEMPTS_REACHED,
            ),
        ),
        _event(
            7,
            workflow_execution_failed_event_attributes=history_pb.WorkflowExecutionFailedEventAttributes(
                failure=failure_pb.Failure(message="workflow failed"),
            ),
        ),
    ]
    handle = _mock_handle(events, status=temporalio.client.WorkflowExecutionStatus.FAILED)
    client, _ = _mock_client(mocker, handle=handle)

    detail = await temporal_runs.get_workflow_run_detail(_SQ_ID, _WORKFLOW_ID, "run-1")

    assert detail is not None
    client.get_workflow_handle.assert_called_once_with(_WORKFLOW_ID, run_id="run-1")
    assert detail.status == "failed"
    assert detail.workflow_name == "cve_repo_report"
    assert detail.failure == "workflow failed"
    assert len(detail.activities) == 2

    completed, failed = detail.activities
    assert completed.status == "completed"
    assert completed.attempts == 3
    assert completed.maximum_attempts == 3
    assert completed.last_attempt_failure == "attempt 2 crashed"
    assert completed.input_preview == '{"repo": "org/app"}'
    assert completed.result_preview == '{"status": "ok"}'
    assert completed.scheduled_at is not None
    assert completed.closed_at is not None

    assert failed.status == "failed"
    assert failed.attempts == 1
    assert failed.retry_state == "maximum_attempts_reached"
    assert failed.failure == "RuntimeError: boom; caused by: root cause"


async def test_get_workflow_run_detail_folds_child_workflows(mocker):
    child_id = "seizu-cartography-module:aws"
    events = [
        _event(
            1,
            start_child_workflow_execution_initiated_event_attributes=(
                history_pb.StartChildWorkflowExecutionInitiatedEventAttributes(
                    workflow_id=child_id,
                    workflow_type=common_pb.WorkflowType(name="cartography_module"),
                    input=_payloads({"module": "aws"}),
                )
            ),
        ),
        # The child workflow ID (the per-module mutex) is held by another run:
        # the parent's start fails and it retries after a timer.
        _event(
            2,
            start_child_workflow_execution_failed_event_attributes=(
                history_pb.StartChildWorkflowExecutionFailedEventAttributes(initiated_event_id=1)
            ),
        ),
        _event(
            3,
            start_child_workflow_execution_initiated_event_attributes=(
                history_pb.StartChildWorkflowExecutionInitiatedEventAttributes(
                    workflow_id=child_id,
                    workflow_type=common_pb.WorkflowType(name="cartography_module"),
                    input=_payloads({"module": "aws"}),
                )
            ),
        ),
        _event(
            4,
            child_workflow_execution_started_event_attributes=(
                history_pb.ChildWorkflowExecutionStartedEventAttributes(initiated_event_id=3)
            ),
        ),
        _event(
            5,
            child_workflow_execution_completed_event_attributes=(
                history_pb.ChildWorkflowExecutionCompletedEventAttributes(
                    initiated_event_id=3,
                    result=_payloads({"module": "aws", "status": "completed"}),
                )
            ),
        ),
        # A second run of the same child id later in the pipeline gets its own
        # record instead of overwriting the closed one.
        _event(
            6,
            start_child_workflow_execution_initiated_event_attributes=(
                history_pb.StartChildWorkflowExecutionInitiatedEventAttributes(
                    workflow_id=child_id,
                    workflow_type=common_pb.WorkflowType(name="cartography_module"),
                )
            ),
        ),
        _event(
            7,
            child_workflow_execution_started_event_attributes=(
                history_pb.ChildWorkflowExecutionStartedEventAttributes(initiated_event_id=6)
            ),
        ),
        _event(
            8,
            child_workflow_execution_failed_event_attributes=(
                history_pb.ChildWorkflowExecutionFailedEventAttributes(
                    initiated_event_id=6,
                    failure=failure_pb.Failure(
                        message="child failed",
                        cause=failure_pb.Failure(message="cartography aws exited 1"),
                    ),
                )
            ),
        ),
    ]
    workflow_id = f"seizu:cartography_sync:{_SQ_ID}:2024-01-01T00:00:00+00:00"
    handle = _mock_handle(events)
    _mock_client(mocker, handle=handle)

    detail = await temporal_runs.get_workflow_run_detail(_SQ_ID, workflow_id, "run-1")

    assert detail is not None
    assert len(detail.activities) == 2
    archived, failed = detail.activities
    # Blocked start attempts aggregate into one record's attempt count.
    assert archived.activity_id == child_id
    assert archived.activity_type == "cartography_module"
    assert archived.attempts == 2
    assert archived.status == "completed"
    assert archived.last_attempt_failure == "another run of this child workflow is in progress"
    assert archived.input_preview == '{"module": "aws"}'
    assert archived.result_preview == '{"module": "aws", "status": "completed"}'
    assert failed.status == "failed"
    assert "child failed" in failed.failure
    assert "cartography aws exited 1" in failed.failure


async def test_get_workflow_run_detail_merges_pending(mocker):
    events = [
        _event(
            1,
            activity_task_scheduled_event_attributes=history_pb.ActivityTaskScheduledEventAttributes(
                activity_id="1",
                activity_type=common_pb.ActivityType(name="run_repo_report_chat"),
            ),
        ),
    ]
    import temporalio.api.workflow.v1 as workflow_pb

    info = workflow_pb.PendingActivityInfo(
        activity_id="1",
        state=enums_pb.PendingActivityState.PENDING_ACTIVITY_STATE_STARTED,
        attempt=2,
        maximum_attempts=5,
        last_failure=failure_pb.Failure(message="first attempt failed"),
    )
    info.last_started_time.FromDatetime(datetime(2024, 1, 1, 0, 30, 0))
    handle = _mock_handle(
        events,
        status=temporalio.client.WorkflowExecutionStatus.RUNNING,
        pending=[info],
    )
    _mock_client(mocker, handle=handle)

    detail = await temporal_runs.get_workflow_run_detail(_SQ_ID, _WORKFLOW_ID, "run-1")

    assert detail is not None
    assert detail.status == "running"
    activity = detail.activities[0]
    assert activity.status == "running"
    assert activity.attempts == 2
    assert activity.maximum_attempts == 5
    assert activity.last_attempt_failure == "first attempt failed"
    assert activity.started_at == "2024-01-01T00:30:00+00:00"


async def test_get_workflow_run_detail_without_payload_previews(mocker):
    events = [
        _event(
            1,
            activity_task_scheduled_event_attributes=history_pb.ActivityTaskScheduledEventAttributes(
                activity_id="1",
                activity_type=common_pb.ActivityType(name="run_repo_report_chat"),
                input=_payloads({"repo": "org/app"}),
            ),
        ),
        _event(
            2,
            activity_task_started_event_attributes=history_pb.ActivityTaskStartedEventAttributes(
                scheduled_event_id=1,
                attempt=2,
                last_failure=failure_pb.Failure(message="attempt 1 crashed"),
            ),
        ),
        _event(
            3,
            activity_task_completed_event_attributes=history_pb.ActivityTaskCompletedEventAttributes(
                scheduled_event_id=1,
                result=_payloads({"status": "ok"}),
            ),
        ),
    ]
    handle = _mock_handle(events)
    _mock_client(mocker, handle=handle)

    detail = await temporal_runs.get_workflow_run_detail(_SQ_ID, _WORKFLOW_ID, "run-1", include_payload_previews=False)

    assert detail is not None
    activity = detail.activities[0]
    # Status/attempt/failure detail is kept; payload previews are omitted.
    assert activity.status == "completed"
    assert activity.attempts == 2
    assert activity.last_attempt_failure == "attempt 1 crashed"
    assert activity.input_preview is None
    assert activity.result_preview is None


async def test_get_workflow_run_detail_terminal_event_variants(mocker):
    child_ids = ["child-timeout", "child-canceled", "child-terminated"]
    events = [
        _event(
            1,
            activity_task_scheduled_event_attributes=history_pb.ActivityTaskScheduledEventAttributes(
                activity_id="timed", activity_type=common_pb.ActivityType(name="task")
            ),
        ),
        _event(
            2,
            activity_task_timed_out_event_attributes=history_pb.ActivityTaskTimedOutEventAttributes(
                scheduled_event_id=1,
                failure=failure_pb.Failure(message="late"),
                retry_state=enums_pb.RetryState.RETRY_STATE_TIMEOUT,
            ),
        ),
        _event(
            3,
            activity_task_scheduled_event_attributes=history_pb.ActivityTaskScheduledEventAttributes(
                activity_id="canceled", activity_type=common_pb.ActivityType(name="task")
            ),
        ),
        _event(
            4,
            activity_task_cancel_requested_event_attributes=history_pb.ActivityTaskCancelRequestedEventAttributes(
                scheduled_event_id=3
            ),
        ),
        _event(
            5,
            activity_task_canceled_event_attributes=history_pb.ActivityTaskCanceledEventAttributes(
                scheduled_event_id=3
            ),
        ),
    ]
    event_id = 6
    terminal_attrs = [
        (
            "child_workflow_execution_timed_out_event_attributes",
            history_pb.ChildWorkflowExecutionTimedOutEventAttributes,
        ),
        (
            "child_workflow_execution_canceled_event_attributes",
            history_pb.ChildWorkflowExecutionCanceledEventAttributes,
        ),
        (
            "child_workflow_execution_terminated_event_attributes",
            history_pb.ChildWorkflowExecutionTerminatedEventAttributes,
        ),
    ]
    for child_id, (field, attr_type) in zip(child_ids, terminal_attrs, strict=True):
        initiated_id = event_id
        events.append(
            _event(
                initiated_id,
                start_child_workflow_execution_initiated_event_attributes=(
                    history_pb.StartChildWorkflowExecutionInitiatedEventAttributes(
                        workflow_id=child_id,
                        workflow_type=common_pb.WorkflowType(name="child"),
                    )
                ),
            )
        )
        events.append(_event(event_id + 1, **{field: attr_type(initiated_event_id=initiated_id)}))
        event_id += 2
    events.append(
        _event(
            event_id,
            workflow_execution_terminated_event_attributes=history_pb.WorkflowExecutionTerminatedEventAttributes(
                reason="stopped"
            ),
        )
    )
    handle = _mock_handle(events, status=temporalio.client.WorkflowExecutionStatus.TERMINATED)
    _mock_client(mocker, handle=handle)
    detail = await temporal_runs.get_workflow_run_detail(_SQ_ID, _WORKFLOW_ID, "run-1")
    assert detail is not None
    assert detail.failure == "stopped"
    statuses = {activity.activity_id: activity.status for activity in detail.activities}
    assert statuses == {
        "timed": "timed_out",
        "canceled": "canceled",
        "child-timeout": "timed_out",
        "child-canceled": "canceled",
        "child-terminated": "terminated",
    }


async def test_payload_preview_truncates_and_survives_garbage():
    big = temporal_runs._payload_preview(_payloads({"x": "y" * 5000}).payloads)
    assert big is not None
    assert len(big) == temporal_runs.PAYLOAD_PREVIEW_MAX_CHARS + 1
    assert big.endswith("…")

    garbage = common_pb.Payload(metadata={"encoding": b"json/plain"}, data=b"\xff not json")
    assert temporal_runs._payload_preview([garbage]) == "<undecodable payload>"

    assert temporal_runs._payload_preview([]) is None
