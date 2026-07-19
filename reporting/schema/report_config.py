from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from reporting.schema.reporting_config import (
    ScheduleSpec,
    Workflow,
    WorkflowStage,
    validate_exclusive_triggers,
)


def _coerce_decimal(value: Any) -> Any:
    """Recursively convert Decimal to int/float.

    DynamoDB's boto3 resource returns all numbers as Decimal; this normalises
    them back to native Python int/float so Pydantic models can validate them
    without needing to know about Decimal.
    """
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {k: _coerce_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_decimal(v) for v in value]
    return value


class ReportAccess(BaseModel):
    """Report-level visibility metadata."""

    scope: Literal["private", "public"]


class ReportListItem(BaseModel):
    """Lightweight summary of a report for list views."""

    report_id: str
    name: str
    current_version: int
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str
    access: ReportAccess
    pinned: bool = False

    @field_validator("current_version", mode="before")
    @classmethod
    def coerce_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return v


class ReportVersion(BaseModel):
    """A single versioned report config."""

    report_id: str
    name: str
    version: int
    config: dict[str, Any]
    created_at: str
    created_by: str
    comment: str | None = None
    report_created_by: str
    report_updated_by: str
    access: ReportAccess
    query_capabilities: dict[str, str] | None = None

    @field_validator("version", mode="before")
    @classmethod
    def coerce_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return v

    @field_validator("config", mode="before")
    @classmethod
    def coerce_config(cls, v: Any) -> dict[str, Any]:
        return _coerce_decimal(v)


class ReportListResponse(BaseModel):
    reports: list["ReportListItem"]
    total: int
    page: int
    per_page: int


class ReportVersionListResponse(BaseModel):
    versions: list["ReportVersion"]


class ReportIdResponse(BaseModel):
    report_id: str


class ScheduledQueryListResponse(BaseModel):
    scheduled_queries: list["ScheduledQueryItem"]


class ScheduledQueryVersionListResponse(BaseModel):
    versions: list["ScheduledQueryVersion"]


class ScheduledQueryIdResponse(BaseModel):
    scheduled_query_id: str


class CreateReportRequest(BaseModel):
    """Request body for POST /api/v1/reports."""

    name: str


class PinReportRequest(BaseModel):
    """Request body for PUT /api/v1/reports/<id>/pin."""

    pinned: bool


class UpdateReportVisibilityRequest(BaseModel):
    """Request body for PUT /api/v1/reports/<id>/visibility."""

    access: ReportAccess | None = None


class CreateVersionRequest(BaseModel):
    """Request body for POST /api/v1/reports/<id>/versions."""

    config: dict[str, Any]
    comment: str | None = None

    @field_validator("config")
    @classmethod
    def validate_report_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Validate against the Report schema so malformed configs (wrong field
        # names, queries as a list, markdown panels without content) are
        # rejected with an actionable error instead of stored and rendered
        # empty. The original dict is stored, so unknown-but-ignored extras
        # are preserved as before.
        from reporting.schema.reporting_config import Report

        Report.model_validate(value)
        return value


class CloneReportRequest(BaseModel):
    """Request body for POST /api/v1/reports/<id>/clone."""

    name: str


class User(BaseModel):
    """A user record, created on first login (JIT provisioning)."""

    user_id: str
    sub: str
    iss: str
    email: str | None = None
    display_name: str | None = None
    preferred_username: str | None = None
    created_at: str
    last_login: str
    archived_at: str | None = None
    # Last role claim observed on an authenticated request. Lets headless
    # callers (Temporal workflows) resolve the user's permissions without a
    # token; None means the user's tokens carry no role claim and headless
    # resolution falls back to RBAC_DEFAULT_ROLE, same as the request path.
    role: str | None = None


class ScheduledQueryItem(BaseModel):
    """A scheduled query record stored in the database."""

    scheduled_query_id: str
    name: str
    cypher: str
    params: list[dict[str, Any]] = Field(default_factory=list)
    # Interval in minutes. Deprecated in favor of schedule; still honored by
    # the worker for existing records.
    frequency: int | None = None
    schedule: ScheduleSpec | None = None
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    actions: list[dict[str, Any]] = Field(default_factory=list)
    # Canonical workflow field. Legacy scheduled-query records omit this and
    # are projected on read by reporting.services.workflows.
    stages: list[dict[str, Any]] | None = None
    # Branch-only fields retained so databases created by an earlier revision
    # remain readable. They are not accepted as workflow definitions.
    inputs: dict[str, Any] | None = None
    activities: list[dict[str, Any]] | None = None
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None
    last_run_status: str | None = None
    last_run_at: str | None = None
    last_errors: list[dict[str, str]] = Field(default_factory=list)
    last_scheduled_at: str | None = None
    # Set by "run now": the worker runs the query on its next poll when this
    # is newer than last_scheduled_at (even when the query is disabled, so
    # operators can test before enabling).
    run_requested_at: str | None = None
    schedule_sync_status: Literal["synced", "pending", "error"] = "pending"
    schedule_sync_error: str | None = None
    schedule_synced_at: str | None = None

    @field_validator("current_version", mode="before")
    @classmethod
    def coerce_current_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return int(v) if v is not None else 0

    @field_validator("params", "watch_scans", "actions", mode="before")
    @classmethod
    def coerce_json_fields(cls, v: Any) -> list[dict[str, Any]]:
        return _coerce_decimal(v) if v is not None else []

    @field_validator("schedule", mode="before")
    @classmethod
    def coerce_schedule(cls, v: Any) -> Any:
        return _coerce_decimal(v)

    @field_validator("last_errors", mode="before")
    @classmethod
    def coerce_last_errors(cls, v: Any) -> list[dict[str, str]]:
        return v if v is not None else []


class ScheduledQueryVersion(BaseModel):
    """A point-in-time snapshot of a scheduled query's configuration."""

    scheduled_query_id: str
    name: str
    version: int
    cypher: str
    params: list[dict[str, Any]] = Field(default_factory=list)
    frequency: int | None = None
    schedule: ScheduleSpec | None = None
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    actions: list[dict[str, Any]] = Field(default_factory=list)
    inputs: dict[str, Any] | None = None
    activities: list[dict[str, Any]] | None = None
    stages: list[dict[str, Any]] | None = None
    created_at: str
    created_by: str
    comment: str | None = None

    @field_validator("version", mode="before")
    @classmethod
    def coerce_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return int(v)

    @field_validator("params", "watch_scans", "actions", mode="before")
    @classmethod
    def coerce_json_fields(cls, v: Any) -> list[dict[str, Any]]:
        return _coerce_decimal(v) if v is not None else []

    @field_validator("schedule", mode="before")
    @classmethod
    def coerce_schedule(cls, v: Any) -> Any:
        return _coerce_decimal(v)


class CreateScheduledQueryRequest(BaseModel):
    """Request body for POST/PUT /api/v1/scheduled-queries."""

    name: str
    cypher: str
    params: list[dict[str, Any]] = Field(default_factory=list)
    # Deprecated: interval in minutes. Use schedule instead.
    frequency: int | None = None
    schedule: ScheduleSpec | None = None
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    actions: list[dict[str, Any]] = Field(default_factory=list)
    comment: str | None = None

    @model_validator(mode="after")
    def exclusive_triggers(self) -> "CreateScheduledQueryRequest":
        validate_exclusive_triggers(self.frequency, self.schedule, self.watch_scans)
        return self


class ScheduledQueryRunRequestedResponse(BaseModel):
    """Acknowledgement that a manual run was requested."""

    scheduled_query_id: str
    run_requested_at: str


class WorkflowItem(BaseModel):
    """Canonical stored workflow returned by the REST API."""

    workflow_id: str
    name: str
    stages: list[WorkflowStage]
    schedule: ScheduleSpec | None = None
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None
    last_run_status: str | None = None
    last_run_at: str | None = None
    last_errors: list[dict[str, str]] = Field(default_factory=list)
    schedule_sync_status: Literal["synced", "pending", "error"] = "pending"
    schedule_sync_error: str | None = None
    schedule_synced_at: str | None = None


class WorkflowVersion(BaseModel):
    """A point-in-time workflow definition."""

    workflow_id: str
    name: str
    version: int
    stages: list[WorkflowStage]
    schedule: ScheduleSpec | None = None
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    created_at: str
    created_by: str
    comment: str | None = None


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowItem]


class WorkflowVersionListResponse(BaseModel):
    versions: list[WorkflowVersion]


class WorkflowIdResponse(BaseModel):
    workflow_id: str


class CreateWorkflowRequest(Workflow):
    """Request body for workflow create/update."""

    comment: str | None = None


class WorkflowRunRequestedResponse(BaseModel):
    workflow_id: str
    temporal_workflow_id: str
    run_id: str | None = None


class WorkflowRunSummary(BaseModel):
    """A Temporal workflow execution started by a scheduled query's temporal action."""

    workflow_id: str
    run_id: str
    workflow_name: str
    # Lower-cased Temporal WorkflowExecutionStatus: running, completed, failed,
    # canceled, terminated, continued_as_new, timed_out, or unknown.
    status: str
    start_time: str | None = None
    close_time: str | None = None
    history_length: int | None = None


class WorkflowRunListResponse(BaseModel):
    runs: list[WorkflowRunSummary]


class WorkflowRunActivity(BaseModel):
    """One activity execution within a workflow run, derived from its history.

    Temporal records retries as a single activity execution whose final
    attempt count reflects how many activity tasks ran; ``attempts`` plus the
    failure fields therefore carry the per-task success/failure/retry story.
    """

    activity_id: str
    activity_type: str
    # scheduled, running, cancel_requested, paused, completed, failed,
    # timed_out, or canceled.
    status: str
    attempts: int = 1
    maximum_attempts: int | None = None
    scheduled_at: str | None = None
    started_at: str | None = None
    closed_at: str | None = None
    # Lower-cased Temporal RetryState on terminal failure/timeout, e.g.
    # maximum_attempts_reached or non_retryable_failure.
    retry_state: str | None = None
    # Terminal failure summary (message chain).
    failure: str | None = None
    # Failure that caused the most recent retry (previous attempt's failure).
    last_attempt_failure: str | None = None
    input_preview: str | None = None
    result_preview: str | None = None


class WorkflowRunDetail(BaseModel):
    """A workflow run with its activity breakdown."""

    workflow_id: str
    run_id: str
    workflow_name: str
    status: str
    start_time: str | None = None
    close_time: str | None = None
    # Workflow-level terminal failure summary, when the run failed.
    failure: str | None = None
    activities: list[WorkflowRunActivity] = Field(default_factory=list)


class ActionConfigFieldDef(BaseModel):
    """Describes a single field in an action module's config schema."""

    name: str
    label: str
    type: Literal["string", "text", "number", "boolean", "string_list", "select", "parameters"]
    required: bool = False
    description: str | None = None
    default: Any | None = None
    options: list[str] | None = None
    minimum: float | None = None
    maximum: float | None = None
    # Rendered as a warning alert above the field; pair with a required
    # boolean to force an explicit acknowledgement (a required boolean must be
    # checked for the action config to validate).
    warning: str | None = None


class QueryHistoryItem(BaseModel):
    """A single query console history entry for a user."""

    history_id: str
    user_id: str
    query: str
    executed_at: str


class QueryHistoryListResponse(BaseModel):
    """Paginated list of query history items."""

    items: list[QueryHistoryItem]
    total: int
    page: int
    per_page: int
