from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


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
    frequency: int | None = None
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    actions: list[dict[str, Any]] = Field(default_factory=list)
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None
    last_run_status: str | None = None
    last_run_at: str | None = None
    last_errors: list[dict[str, str]] = Field(default_factory=list)
    last_scheduled_at: str | None = None

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
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    actions: list[dict[str, Any]] = Field(default_factory=list)
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


class CreateScheduledQueryRequest(BaseModel):
    """Request body for POST/PUT /api/v1/scheduled-queries."""

    name: str
    cypher: str
    params: list[dict[str, Any]] = Field(default_factory=list)
    frequency: int | None = None
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    actions: list[dict[str, Any]] = Field(default_factory=list)
    comment: str | None = None


class ActionConfigFieldDef(BaseModel):
    """Describes a single field in an action module's config schema."""

    name: str
    label: str
    type: Literal["string", "text", "number", "boolean", "string_list", "select"]
    required: bool = False
    description: str | None = None
    default: Any | None = None
    options: list[str] | None = None
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
