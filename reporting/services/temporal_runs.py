"""Read-only Temporal visibility/history lookups for scheduled query runs.

Workflow IDs started by the removed temporal action were
``seizu:{workflow}:{sq_id}:{ts}``, so historical runs for a scheduled query
are found with ``WorkflowId STARTS_WITH`` visibility queries —
no extra bookkeeping in the report store. Run details come from the workflow's
event history: each activity execution is folded into a single
``WorkflowRunActivity`` carrying status, final attempt count (Temporal writes
one ActivityTaskStarted event with the final attempt number), retry/failure
detail, and truncated input/result previews. Child workflow executions (e.g.
cartography_module runs, whose fixed workflow IDs serve as per-module
mutexes) fold into the same record shape, with repeated blocked start
attempts aggregated as a "waiting" state.

``temporalio`` is imported lazily inside functions so the web process only
pays for it when the endpoints are actually used.
"""

import asyncio
import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from reporting import settings
from reporting.schema.report_config import (
    WorkflowRunActivity,
    WorkflowRunDetail,
    WorkflowRunSummary,
)
from reporting.temporal_workflows import WORKFLOW_REGISTRY

if TYPE_CHECKING:
    import temporalio.api.common.v1
    import temporalio.api.failure.v1
    import temporalio.client

logger = logging.getLogger(__name__)

# Truncation cap for activity input/result previews (post-JSON-encoding).
PAYLOAD_PREVIEW_MAX_CHARS = 2048
# Failure cause chains are summarized to at most this many levels.
_FAILURE_CAUSE_DEPTH = 4

_ENUM_PREFIXES = (
    "WORKFLOW_EXECUTION_STATUS_",
    "PENDING_ACTIVITY_STATE_",
    "RETRY_STATE_",
)
_SCHEDULE_TIME_RE = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"


class TemporalUnavailableError(Exception):
    """Temporal could not be reached (connection or availability failure)."""


_client: "temporalio.client.Client | None" = None
_client_lock = asyncio.Lock()


async def _get_client() -> "temporalio.client.Client":
    import temporalio.client

    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                try:
                    _client = await temporalio.client.Client.connect(
                        settings.TEMPORAL_ADDRESS,
                        namespace=settings.TEMPORAL_NAMESPACE,
                    )
                except Exception as exc:
                    raise TemporalUnavailableError(str(exc)) from exc
    return _client


def workflow_id_matches(sq_id: str, workflow_id: str) -> bool:
    """True when the workflow id was minted for this scheduled query.

    The workflow-name segment must be a registered workflow — the same name
    set the run listing queries — so detail reads can never reach beyond the
    surface the list exposes.
    """
    configured_id = f"seizu-workflow:{sq_id}"
    if workflow_id == configured_id:
        return True
    if workflow_id.startswith(f"{configured_id}:manual:"):
        suffix = workflow_id.removeprefix(f"{configured_id}:manual:")
        return bool(suffix) and ":stage:" not in suffix and ":activity:" not in suffix
    if re.fullmatch(rf"{re.escape(configured_id)}:run:{_SCHEDULE_TIME_RE}", workflow_id):
        return True
    # Temporal Schedule actions append the nominal schedule time to the
    # configured workflow ID. Keep this strict so child workflow IDs (which
    # append ``:activity:...``) and similarly prefixed definitions cannot be
    # used to escape the run-detail authorization boundary.
    if re.fullmatch(
        rf"{re.escape(configured_id)}-{_SCHEDULE_TIME_RE}",
        workflow_id,
    ):
        return True
    parts = workflow_id.split(":", 3)
    return len(parts) == 4 and parts[0] == "seizu" and parts[1] in WORKFLOW_REGISTRY and parts[2] == sq_id


def _workflow_name(workflow_id: str, configured_name: str | None = None) -> str:
    if workflow_id.startswith("seizu-workflow:"):
        return configured_name or "configured"
    return workflow_id.split(":", 3)[1]


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _enum_label(name: str) -> str:
    for prefix in _ENUM_PREFIXES:
        name = name.removeprefix(prefix)
    return name.lower()


def _status_name(status: Any) -> str:
    return _enum_label(status.name) if status is not None else "unknown"


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _event_time(event: Any) -> str | None:
    if not event.HasField("event_time"):
        return None
    return event.event_time.ToDatetime(tzinfo=UTC).isoformat()


def _failure_summary(failure: "temporalio.api.failure.v1.Failure | None") -> str | None:
    if failure is None:
        return None
    parts: list[str] = []
    current: Any = failure
    for _ in range(_FAILURE_CAUSE_DEPTH):
        message = current.message or "unknown failure"
        error_type = (
            current.application_failure_info.type
            if current.HasField("application_failure_info") and current.application_failure_info.type
            else None
        )
        parts.append(f"{error_type}: {message}" if error_type else message)
        if not current.HasField("cause"):
            break
        current = current.cause
    return "; caused by: ".join(parts) or None


def _payload_preview(payloads: "Iterable[temporalio.api.common.v1.Payload]") -> str | None:
    import temporalio.converter

    items = list(payloads)
    if not items:
        return None
    try:
        values = temporalio.converter.DataConverter.default.payload_converter.from_payloads(items)
        rendered = json.dumps(values[0] if len(values) == 1 else values, default=str)
    except Exception:
        return "<undecodable payload>"
    if len(rendered) > PAYLOAD_PREVIEW_MAX_CHARS:
        rendered = rendered[:PAYLOAD_PREVIEW_MAX_CHARS] + "…"
    return rendered


async def list_workflow_runs(
    sq_id: str,
    limit: int,
    *,
    configured_name: str | None = None,
    watch_polling: bool = False,
) -> list[WorkflowRunSummary]:
    """Return the most recent workflow runs started for a scheduled query.

    Visibility results come back StartTime-descending, so the first ``limit``
    executions are the latest runs across every registered workflow name.
    """
    import temporalio.service

    if '"' in sq_id or "\\" in sq_id:
        return []
    prefixes = [f"seizu:{name}:{sq_id}:" for name in sorted(WORKFLOW_REGISTRY)]
    if watch_polling:
        # Watch schedules now use a separate poll workflow. Only its triggered
        # child has the :run: prefix, so poll executions never enter this list.
        prefixes.extend(
            (
                f"seizu-workflow:{sq_id}:run:",
                f"seizu-workflow:{sq_id}:manual:",
            )
        )
    else:
        prefixes.append(f"seizu-workflow:{sq_id}")
    query = " OR ".join(f"WorkflowId STARTS_WITH {_quote(prefix)}" for prefix in prefixes)
    client = await _get_client()
    runs: list[WorkflowRunSummary] = []
    # Triggered configured runs may own child workflow IDs beneath the same
    # prefix. Read extra visibility candidates so those filtered children do
    # not crowd top-level runs out of the requested page.
    visibility_limit = min(limit * 10, 500) if watch_polling else limit
    try:
        async for execution in client.list_workflows(query, limit=visibility_limit):
            if not workflow_id_matches(sq_id, execution.id):
                continue
            runs.append(
                WorkflowRunSummary(
                    workflow_id=execution.id,
                    run_id=execution.run_id,
                    workflow_name=_workflow_name(execution.id, configured_name),
                    status=_status_name(execution.status),
                    start_time=_isoformat(execution.start_time),
                    close_time=_isoformat(execution.close_time),
                    history_length=execution.history_length,
                )
            )
            if len(runs) >= limit:
                break
    except temporalio.service.RPCError as exc:
        _raise_if_unavailable(exc)
        raise
    return runs


async def get_workflow_run_detail(
    sq_id: str,
    workflow_id: str,
    run_id: str,
    *,
    include_payload_previews: bool = False,
    configured_name: str | None = None,
) -> WorkflowRunDetail | None:
    """Return one run's activity breakdown, or None when it doesn't exist.

    Refuses (as not-found) any workflow id that wasn't minted for this
    scheduled query by a registered workflow, so the endpoint can't be used
    to read arbitrary workflows in the namespace.

    Payload previews are omitted by default because they carry query-result
    rows and activity outputs. Callers must opt in only after applying the
    stronger permission check appropriate to their API surface.
    """
    import temporalio.service

    if not workflow_id_matches(sq_id, workflow_id):
        return None
    client = await _get_client()
    handle = client.get_workflow_handle(workflow_id, run_id=run_id)
    try:
        description = await handle.describe()
        activities, workflow_failure = await _collect_activities(
            handle, include_payload_previews=include_payload_previews
        )
    except temporalio.service.RPCError as exc:
        # INVALID_ARGUMENT covers malformed client-supplied ids (e.g. a run_id
        # that isn't a UUID) — same not-found semantics for this endpoint.
        if exc.status in (
            temporalio.service.RPCStatusCode.NOT_FOUND,
            temporalio.service.RPCStatusCode.INVALID_ARGUMENT,
        ):
            return None
        _raise_if_unavailable(exc)
        raise
    _merge_pending_activities(activities, description.raw_description.pending_activities)
    return WorkflowRunDetail(
        workflow_id=workflow_id,
        run_id=description.run_id,
        workflow_name=_workflow_name(workflow_id, configured_name),
        status=_status_name(description.status),
        start_time=_isoformat(description.start_time),
        close_time=_isoformat(description.close_time),
        failure=workflow_failure,
        activities=list(activities.values()),
    )


def _raise_if_unavailable(exc: Any) -> None:
    import temporalio.service

    if exc.status in (
        temporalio.service.RPCStatusCode.UNAVAILABLE,
        temporalio.service.RPCStatusCode.DEADLINE_EXCEEDED,
    ):
        raise TemporalUnavailableError(str(exc)) from exc


async def _collect_activities(
    handle: "temporalio.client.WorkflowHandle[Any, Any]",
    *,
    include_payload_previews: bool,
) -> tuple[dict[str, WorkflowRunActivity], str | None]:
    """Fold history events into per-activity records keyed by activity id.

    Close events reference their ActivityTaskScheduled event id, so a scheduled
    event id → activity id map bridges the two.
    """
    activities: dict[str, WorkflowRunActivity] = {}
    scheduled_event_activity: dict[int, str] = {}
    workflow_failure: str | None = None
    # Child workflow executions (e.g. cartography_module runs) fold into the
    # same record shape. Repeated start attempts against a taken child
    # workflow ID (the per-module mutex wait) aggregate into one record, and a
    # fresh record starts once the previous execution for that ID is terminal.
    child_records: dict[str, WorkflowRunActivity] = {}  # child workflow id → open record
    initiated_event_child: dict[int, str] = {}
    _CHILD_TERMINAL = ("completed", "failed", "timed_out", "canceled", "terminated")

    def _for_scheduled_event(event_id: int) -> WorkflowRunActivity | None:
        activity_id = scheduled_event_activity.get(event_id)
        return activities.get(activity_id) if activity_id is not None else None

    def _for_initiated_event(event_id: int) -> WorkflowRunActivity | None:
        child_id = initiated_event_child.get(event_id)
        return child_records.get(child_id) if child_id is not None else None

    async for event in handle.fetch_history_events():
        if event.HasField("activity_task_scheduled_event_attributes"):
            attrs = event.activity_task_scheduled_event_attributes
            scheduled_event_activity[event.event_id] = attrs.activity_id
            activities[attrs.activity_id] = WorkflowRunActivity(
                activity_id=attrs.activity_id,
                activity_type=attrs.activity_type.name,
                status="scheduled",
                maximum_attempts=(attrs.retry_policy.maximum_attempts if attrs.HasField("retry_policy") else None),
                scheduled_at=_event_time(event),
                input_preview=(
                    _payload_preview(attrs.input.payloads if attrs.HasField("input") else [])
                    if include_payload_previews
                    else None
                ),
            )
        elif event.HasField("activity_task_started_event_attributes"):
            started = event.activity_task_started_event_attributes
            activity = _for_scheduled_event(started.scheduled_event_id)
            if activity is not None:
                activity.status = "running"
                activity.attempts = started.attempt
                activity.started_at = _event_time(event)
                if started.HasField("last_failure"):
                    activity.last_attempt_failure = _failure_summary(started.last_failure)
        elif event.HasField("activity_task_completed_event_attributes"):
            completed = event.activity_task_completed_event_attributes
            activity = _for_scheduled_event(completed.scheduled_event_id)
            if activity is not None:
                activity.status = "completed"
                activity.closed_at = _event_time(event)
                if include_payload_previews:
                    activity.result_preview = _payload_preview(
                        completed.result.payloads if completed.HasField("result") else []
                    )
        elif event.HasField("activity_task_failed_event_attributes"):
            failed = event.activity_task_failed_event_attributes
            activity = _for_scheduled_event(failed.scheduled_event_id)
            if activity is not None:
                activity.status = "failed"
                activity.closed_at = _event_time(event)
                activity.failure = _failure_summary(failed.failure if failed.HasField("failure") else None)
                activity.retry_state = _retry_state_label(failed.retry_state)
        elif event.HasField("activity_task_timed_out_event_attributes"):
            timed_out = event.activity_task_timed_out_event_attributes
            activity = _for_scheduled_event(timed_out.scheduled_event_id)
            if activity is not None:
                activity.status = "timed_out"
                activity.closed_at = _event_time(event)
                activity.failure = _failure_summary(timed_out.failure if timed_out.HasField("failure") else None)
                activity.retry_state = _retry_state_label(timed_out.retry_state)
        elif event.HasField("activity_task_canceled_event_attributes"):
            canceled = event.activity_task_canceled_event_attributes
            activity = _for_scheduled_event(canceled.scheduled_event_id)
            if activity is not None:
                activity.status = "canceled"
                activity.closed_at = _event_time(event)
        elif event.HasField("activity_task_cancel_requested_event_attributes"):
            cancel_requested = event.activity_task_cancel_requested_event_attributes
            activity = _for_scheduled_event(cancel_requested.scheduled_event_id)
            if activity is not None:
                activity.status = "cancel_requested"
        elif event.HasField("start_child_workflow_execution_initiated_event_attributes"):
            initiated = event.start_child_workflow_execution_initiated_event_attributes
            initiated_event_child[event.event_id] = initiated.workflow_id
            record = child_records.get(initiated.workflow_id)
            if record is not None and record.status not in _CHILD_TERMINAL:
                # A retry of the same start (mutex wait): fold into the record.
                record.attempts += 1
            else:
                if record is not None:
                    # Same child id run again later (e.g. one module in two
                    # sequential stages): archive the closed record.
                    activities[f"{initiated.workflow_id}#{event.event_id}"] = activities.pop(initiated.workflow_id)
                record = WorkflowRunActivity(
                    activity_id=initiated.workflow_id,
                    activity_type=initiated.workflow_type.name,
                    status="scheduled",
                    scheduled_at=_event_time(event),
                    input_preview=(
                        _payload_preview(initiated.input.payloads if initiated.HasField("input") else [])
                        if include_payload_previews
                        else None
                    ),
                )
                child_records[initiated.workflow_id] = record
                activities[initiated.workflow_id] = record
        elif event.HasField("start_child_workflow_execution_failed_event_attributes"):
            start_failed = event.start_child_workflow_execution_failed_event_attributes
            record = _for_initiated_event(start_failed.initiated_event_id)
            if record is not None:
                # WORKFLOW_ALREADY_EXISTS while waiting on the mutex; the
                # parent retries, so this is a wait state, not a failure.
                record.status = "waiting"
                record.last_attempt_failure = "another run of this child workflow is in progress"
        elif event.HasField("child_workflow_execution_started_event_attributes"):
            child_started = event.child_workflow_execution_started_event_attributes
            record = _for_initiated_event(child_started.initiated_event_id)
            if record is not None:
                record.status = "running"
                record.started_at = _event_time(event)
        elif event.HasField("child_workflow_execution_completed_event_attributes"):
            child_completed = event.child_workflow_execution_completed_event_attributes
            record = _for_initiated_event(child_completed.initiated_event_id)
            if record is not None:
                record.status = "completed"
                record.closed_at = _event_time(event)
                if include_payload_previews:
                    record.result_preview = _payload_preview(
                        child_completed.result.payloads if child_completed.HasField("result") else []
                    )
        elif event.HasField("child_workflow_execution_failed_event_attributes"):
            child_failed = event.child_workflow_execution_failed_event_attributes
            record = _for_initiated_event(child_failed.initiated_event_id)
            if record is not None:
                record.status = "failed"
                record.closed_at = _event_time(event)
                record.failure = _failure_summary(child_failed.failure if child_failed.HasField("failure") else None)
        elif event.HasField("child_workflow_execution_timed_out_event_attributes"):
            child_timed_out = event.child_workflow_execution_timed_out_event_attributes
            record = _for_initiated_event(child_timed_out.initiated_event_id)
            if record is not None:
                record.status = "timed_out"
                record.closed_at = _event_time(event)
        elif event.HasField("child_workflow_execution_canceled_event_attributes"):
            child_canceled = event.child_workflow_execution_canceled_event_attributes
            record = _for_initiated_event(child_canceled.initiated_event_id)
            if record is not None:
                record.status = "canceled"
                record.closed_at = _event_time(event)
        elif event.HasField("child_workflow_execution_terminated_event_attributes"):
            child_terminated = event.child_workflow_execution_terminated_event_attributes
            record = _for_initiated_event(child_terminated.initiated_event_id)
            if record is not None:
                record.status = "terminated"
                record.closed_at = _event_time(event)
        elif event.HasField("workflow_execution_failed_event_attributes"):
            failed_attrs = event.workflow_execution_failed_event_attributes
            workflow_failure = _failure_summary(failed_attrs.failure if failed_attrs.HasField("failure") else None)
        elif event.HasField("workflow_execution_terminated_event_attributes"):
            workflow_failure = event.workflow_execution_terminated_event_attributes.reason or "terminated"

    return activities, workflow_failure


def _retry_state_label(retry_state: int) -> str | None:
    import temporalio.api.enums.v1

    if not retry_state:
        return None
    return _enum_label(temporalio.api.enums.v1.RetryState.Name(retry_state))


def _merge_pending_activities(activities: dict[str, WorkflowRunActivity], pending: Iterable[Any]) -> None:
    """Overlay live attempt/failure state for activities still in flight.

    History only shows an ActivityTaskStarted event once an attempt settles,
    so for running activities the describe() pending info is the source of
    truth for attempts and the latest failure.
    """
    import temporalio.api.enums.v1

    for info in pending:
        activity = activities.get(info.activity_id)
        if activity is None:
            continue
        state = temporalio.api.enums.v1.PendingActivityState.Name(info.state)
        activity.status = {
            "scheduled": "scheduled",
            "started": "running",
            "cancel_requested": "cancel_requested",
            "paused": "paused",
            "pause_requested": "paused",
        }.get(_enum_label(state), "running")
        activity.attempts = max(activity.attempts, info.attempt)
        if info.maximum_attempts:
            activity.maximum_attempts = info.maximum_attempts
        if info.HasField("last_started_time"):
            activity.started_at = info.last_started_time.ToDatetime(tzinfo=UTC).isoformat()
        if info.HasField("last_failure"):
            activity.last_attempt_failure = _failure_summary(info.last_failure)
