"""Pydantic models for the Seizu reporting YAML config format.

This module is the single authoritative source for the reporting dashboard
configuration schema.  It is used by both the ``reporting`` backend and the
``seizu_cli`` CLI; neither package defines these models independently.
"""

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LOWER_SNAKE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def validate_lower_snake_id(value: str) -> str:
    if not LOWER_SNAKE_ID_RE.fullmatch(value):
        raise ValueError("must be lower_snake_case matching ^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    return value


class InputDefault(BaseModel):
    label: str = Field(
        description="The label for the default.",
    )

    value: str = Field(
        description="The value for the default.",
    )


class Input(BaseModel):
    input_id: str = Field(
        description="Reference to the query in the inputs section.",
        examples=["cve_base_severity"],
    )

    label: str = Field(
        description="The label to use for the select element.",
        examples=["CVE base severity"],
    )

    type: Literal["autocomplete", "text"] = Field(
        description="The type of input to use.",
        examples=["autocomplete"],
    )

    cypher: str | None = Field(
        default=None,
        description="The Cypher query to execute. Must return ``value``.",
        examples=[
            """
            .. code-block:: cypher

              MATCH (c:CVE)
              RETURN DISTINCT c.base_severity AS value
            """
        ],
    )

    default: InputDefault | None = Field(
        default=None,
        description="The default value to set if no value is selected.",
        examples=[
            """
            .. code-block:: yaml

              label: (all)
              value: .*
            """
        ],
    )

    size: int | None = Field(
        default=2,
        description="The size of the input element.",
        examples=["2"],
    )


class BarPanelSettings(BaseModel):
    legend: str | None = Field(
        default=None,
        description=("The type of legend to use; ``row`` or ``column``. If unset, then no legend will be used."),
    )


class PiePanelSettings(BaseModel):
    legend: str | None = Field(
        default=None,
        description=(
            "The type of legend to use; ``row`` or ``column``. If unset,"
            " then no legend will be used, and arc labels will be used"
            " instead."
        ),
    )


class GraphPanelSettings(BaseModel):
    node_label: str | None = Field(
        default=None,
        description=("The node property to display as the node label. Defaults to ``label`` if unset."),
    )

    node_color_by: str | None = Field(
        default=None,
        description=("The node property to use for color grouping. Defaults to ``group`` if unset."),
    )


class ProgressPanelSettings(BaseModel):
    show_label: bool | None = Field(
        default=None,
        description=(
            "Whether to render the ``numerator / denominator`` label above"
            " the progress wheel. Defaults to ``True`` when unset; pass"
            " ``False`` to hide the label and show only the wheel and"
            " percentage."
        ),
    )


class PanelThreshold(BaseModel):
    value: float = Field(
        description=(
            "The threshold trigger value. For ``count`` panels, the metric"
            " is the raw total. For ``progress`` panels, the metric is the"
            " completion percentage (0-100)."
        ),
        examples=[70],
    )

    color: str = Field(
        description=(
            "The color to apply when the metric matches this threshold. A CSS color string (e.g. ``#F44336``, ``red``)."
        ),
        examples=["#F44336"],
    )


class PanelParam(BaseModel):
    name: str = Field(
        description="The parameter name to use when passing this input into the query.",
        examples=["severity"],
    )

    input_id: str | None = Field(
        default=None,
        description="Reference to the query in the inputs section.",
        examples=["cve_base_severity"],
    )

    value: Any | None = Field(
        default=None,
        description="The parameter value to pass into the query.",
        examples=[
            """
            .. code-block:: yaml

              params:
                - name: integrityImpact
                  value: HIGH
            """
        ],
    )


class Panel(BaseModel):
    type: Literal[
        "table",
        "vertical-table",
        "count",
        "bar",
        "pie",
        "graph",
        "progress",
        "markdown",
    ] = Field(
        description="The type of panel to use.",
        examples=["table"],
    )

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_size(cls, data: Any) -> Any:
        if isinstance(data, dict) and "size" in data:
            raise ValueError("Panel field 'size' is no longer supported; use 'w' for width.")
        return data

    cypher: str | None = Field(
        default=None,
        description="A reference to a cypher from the cypher section of the configuration.",
        examples=["cves"],
    )

    details_cypher: str | None = Field(
        default=None,
        description=(
            "A reference to a cypher from the cypher section of the configuration."
            " Used in the details section of the panel, as a table."
            " The query can return rows in any of the formats supported by the"
            " ``table`` panel type."
        ),
        examples=["cves-details"],
    )

    params: list[PanelParam] = Field(
        default_factory=list,
        description=(
            "A list of parameters to send into the query. The parameters can"
            " directly have values, or can be a reference to an input."
        ),
        examples=[
            """
            .. code-block:: yaml

              params:
                - name: severity
                  input_id: cve_base_severity
                - name: integrityImpact
                  value: HIGH
            """
        ],
    )

    caption: str | None = Field(
        default=None,
        description="The caption to use for the panel.",
        examples=["Critical CVEs"],
    )

    hide_caption: bool | None = Field(
        default=None,
        description="When True, the panel caption is not rendered in view mode.",
        examples=["true"],
    )

    table_id: str | None = Field(
        default=None,
        description=(
            "The cypher attribute to use for the table's unique ID, if using a"
            " type of table or vertical-table. If not set, a random ID will be"
            " generated. A table_id should be set for ``vertical-table``, or"
            " the panel will have a random ID used as the caption."
        ),
        examples=["cve_id"],
    )

    markdown: str | None = Field(
        default=None,
        description=("The markdown to use for the panel. Only used for type ``markdown``."),
        examples=[
            """
            .. code-block:: markdown

                ## Affects

                Versions x.x.x - x.x.x

                ## Recommended action

                Upgrade to the latest version of the software.
            """
        ],
    )

    @model_validator(mode="after")
    def require_markdown_content(self) -> "Panel":
        # A markdown panel without its content field renders as an empty box;
        # this is always a mistake (e.g. content placed under a different key).
        if self.type == "markdown" and not self.markdown:
            raise ValueError("Panel type 'markdown' requires the 'markdown' field to contain the panel content.")
        return self

    w: int | None = Field(
        default=None,
        description="The width of the panel in grid columns (1-12). Used by react-grid-layout.",
        examples=["3"],
    )

    h: int | None = Field(
        default=None,
        description=(
            "The height of the panel in grid row units (each row is 48px)."
            " When unset, the frontend derives a sensible default per panel"
            " type."
        ),
        examples=["8"],
    )

    x: int | None = Field(
        default=None,
        description=(
            "The column index (0-based) within the row's grid. When unset,"
            " the frontend packs panels left-to-right automatically."
        ),
        examples=["0"],
    )

    y: int | None = Field(
        default=None,
        description=(
            "The row index within the row's grid. Always 0 for single-row layouts; reserved for future multi-row use."
        ),
        examples=["0"],
    )

    min_h: int | None = Field(
        default=None,
        description=(
            "Optional minimum height in grid row units. Enforced by react-grid-layout when the user resizes the panel."
        ),
        examples=["4"],
    )

    auto_height: bool | None = Field(
        default=None,
        description=(
            "When ``True``, the panel renders at its natural content height"
            " instead of filling its grid cell. Only meaningful for"
            " ``markdown`` and ``vertical-table`` panel types; ignored"
            " elsewhere."
        ),
        examples=["true"],
    )

    threshold: float | None = Field(
        default=None,
        description=(
            "(Legacy) Single threshold value for ``count`` / ``progress``"
            " panels. New configs should use ``thresholds``; this field is"
            " retained as a fallback when ``thresholds`` is unset."
        ),
        examples=["70"],
    )

    thresholds: list[PanelThreshold] | None = Field(
        default=None,
        description=(
            "Ordered list of ``{value, color}`` pairs for ``count`` and"
            " ``progress`` panels. The applicable color is the color of the"
            " threshold with the highest ``value`` that is less than or"
            " equal to the metric (raw total for ``count``, completion"
            " percentage for ``progress``). When unset, falls back to the"
            " legacy ``threshold`` field."
        ),
        examples=[
            """
            .. code-block:: yaml

              thresholds:
                - value: 0
                  color: "#F44336"
                - value: 70
                  color: "#4CAF50"
            """
        ],
    )

    bar_settings: BarPanelSettings | None = Field(
        default=None,
        description="Settings specific to bar panels.",
        examples=[
            """
            .. code-block:: yaml

              bar_settings:
                legend: column
            """
        ],
    )

    pie_settings: PiePanelSettings | None = Field(
        default=None,
        description="Settings specific to pie panels.",
        examples=[
            """
            .. code-block:: yaml

              pie_settings:
                legend: column
            """
        ],
    )

    graph_settings: GraphPanelSettings | None = Field(
        default=None,
        description="Settings specific to graph panels.",
        examples=[
            """
            .. code-block:: yaml

              graph_settings:
                node_label: label
                node_color_by: group
            """
        ],
    )

    progress_settings: ProgressPanelSettings | None = Field(
        default=None,
        description="Settings specific to progress panels.",
        examples=[
            """
            .. code-block:: yaml

              progress_settings:
                show_label: false
            """
        ],
    )


class Row(BaseModel):
    name: str = Field(
        description="The name of the row; shown as title above the row.",
        examples=["CVEs"],
    )

    hide_header: bool | None = Field(
        default=None,
        description="When True, the row name header is not rendered in view mode.",
        examples=["true"],
    )

    collapsible: bool | None = Field(
        default=None,
        description="When True, the row can be collapsed by clicking the header in view mode.",
        examples=["true"],
    )

    panels: list[Panel] = Field(
        description="The panels to show in the row.",
        examples=[
            """
            .. code-block:: yaml

              panels:
                - cypher: cves-by-severity
                  details_cypher: cves-by-severity-details
                  type: count
                  params:
                    base_severity: CRITICAL
                  caption: Critical CVEs
                  w: 2
            """
        ],
    )


class Report(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_top_level_panels(cls, data: Any) -> Any:
        if isinstance(data, dict) and "panels" in data:
            raise ValueError("Report field 'panels' is invalid; panels must be nested under 'rows[].panels'.")
        return data

    schema_version: int = Field(
        default=1,
        description=(
            "The schema version of the report config. Increment when making breaking changes to the report schema."
        ),
        examples=[1],
    )

    pinned: bool | None = Field(
        default=None,
        description=(
            "Optional seed metadata for dashboard navigation. When set, the"
            " seeder pins or unpins the report after creating or updating it."
            " This field is not stored inside report version configs."
        ),
        examples=[True],
    )

    name: str = Field(
        description="The name of the report.",
        examples=["CVEs"],
    )

    queries: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Named Cypher queries for this report."
            " Panel ``cypher`` fields may reference a key from this dict,"
            " or provide a Cypher query string directly."
        ),
        examples=[
            """
            .. code-block:: yaml

              queries:
                cves-total: |-
                  MATCH (c:CVE)
                  RETURN count(c.id) AS total
            """
        ],
    )

    inputs: list[Input] = Field(
        default_factory=list,
        description="The inputs to use for the report.",
        examples=[
            """
            .. code-block:: yaml

              inputs:
                - input_id: cve_base_severity
                  cypher: |-
                    MATCH (c:CVE)
                    RETURN c.base_severity AS base_severity
                  default:
                    label: (all)
                    value: .*
                  label: Base Severity
                  type: autocomplete
                  size: 2
            """
        ],
    )

    rows: list[Row] = Field(
        default_factory=list,
        description="The rows of the report.",
        examples=[
            """
            .. code-block:: yaml

              rows:
                - name: "CVEs"
                  panels:
                    - cypher: cves
                      type: table
                      params:
                        - name: severity
                          input_id: cve_base_severity
                      w: 12
            """
        ],
    )


class ScheduledQueryWatchScan(BaseModel):
    grouptype: str | None = Field(
        default=".*",
        description=(
            "Match against the grouptype attribute of the SyncMetadata"
            " node, as a regex. If not set, the query will match against"
            " ``.*``."
        ),
        examples=["CVE"],
    )

    syncedtype: str | None = Field(
        default=".*",
        description=(
            "Match against the syncedtype attribute of the SyncMetadata"
            " node, as a regex. If not set, the query will match against"
            " ``.*``."
        ),
        examples=["year"],
    )

    groupid: str | None = Field(
        default=".*",
        description=(
            "Match against the groupid attribute of the SyncMetadata"
            " node, as a regex. If not set, the query will match against"
            " ``.*``."
        ),
        examples=["2019"],
    )


class ScheduledQueryAction(BaseModel):
    action_type: str = Field(
        description="The type of action to perform.",
        examples=["slack", "sqs"],
    )

    action_config: dict[str, Any] = Field(
        description=(
            "The configuration for the action. See the documentation for the"
            " relevant scheduled query module for information about the"
            " configuration needed for each action type."
        ),
        examples=[
            """
            .. code-block:: yaml

              action_config:
                webhook_url: https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX
                channel: #cve
                username: CVE
                icon_emoji: :cve:
            """
        ],
    )


class ScheduledQueryParam(BaseModel):
    name: str = Field(
        description="The parameter name to use when passing this input into the query.",
        examples=["severity"],
    )

    value: Any = Field(
        description="The parameter value to pass into the query.",
        examples=[
            """
            .. code-block:: yaml

              params:
                - name: integrityImpact
                  value: HIGH
            """
        ],
    )


class WorkflowActivity(BaseModel):
    """One activity in a configurable workflow stage."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(description="Registered activity type.")
    input: str | None = Field(default=None, description="Output from an activity in an earlier stage.")
    output: str = Field(description="Globally unique name for this activity's output.")
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("output")
    @classmethod
    def validate_output(cls, value: str) -> str:
        return validate_lower_snake_id(value)


class WorkflowStage(BaseModel):
    """Activities that start in parallel after the preceding stage settles."""

    model_config = ConfigDict(extra="forbid")

    activities: list[WorkflowActivity] = Field(min_length=1)


class ScheduleSpec(BaseModel):
    """A structured time-based schedule. All times are UTC.

    - ``interval``: every ``interval_minutes`` minutes, anchored to the last
      run (a new schedule runs immediately).
    - ``hourly``: every ``interval_hours`` hours, anchored to the last run
      (a new schedule runs immediately).
    - ``daily``: on the selected ``days_of_week`` (0=Monday..6=Sunday) at
      ``hour``:``minute``.
    - ``monthly``: on the selected ``days_of_month`` (1-31) at
      ``hour``:``minute`` (default 00:00). A day a month doesn't have runs on
      that month's last day instead (e.g. 31 in April runs on the 30th).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["interval", "hourly", "daily", "monthly"]
    interval_minutes: int | None = Field(default=None, ge=1, le=43200)
    interval_hours: int | None = Field(default=None, ge=1, le=720)
    days_of_week: list[int] = Field(default_factory=list)
    hour: int = Field(default=0, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    days_of_month: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_type_fields(self) -> "ScheduleSpec":
        if self.type == "interval" and not self.interval_minutes:
            raise ValueError("interval_minutes is required for interval schedules")
        if self.type == "hourly" and not self.interval_hours:
            raise ValueError("interval_hours is required for hourly schedules")
        if self.type == "daily":
            if not self.days_of_week:
                raise ValueError("days_of_week is required for daily schedules")
            if any(day < 0 or day > 6 for day in self.days_of_week):
                raise ValueError("days_of_week values must be 0 (Monday) through 6 (Sunday)")
        if self.type == "monthly":
            if not self.days_of_month:
                raise ValueError("days_of_month is required for monthly schedules")
            if any(day < 1 or day > 31 for day in self.days_of_month):
                raise ValueError("days_of_month values must be 1 through 31")
        return self


def validate_exclusive_triggers(
    frequency: int | None,
    schedule: "ScheduleSpec | None",
    watch_scans: list[Any],
) -> None:
    """Raise if more than one scheduled-query trigger is configured.

    ``frequency``, ``schedule``, and ``watch_scans`` are mutually exclusive;
    shared by the YAML config model and the API request model.
    """
    configured = []
    if frequency is not None:
        configured.append("frequency")
    if schedule is not None:
        configured.append("schedule")
    if watch_scans:
        configured.append("watch_scans")
    if len(configured) > 1:
        raise ValueError(f"triggers are mutually exclusive; got {' and '.join(configured)}")


class ScheduledQuery(BaseModel):
    name: str = Field(
        description="The name of the scheduled query.",
        examples=["Recently published HIGH/CRITICAL CVEs"],
    )

    cypher: str = Field(
        description="The cypher to use for the scheduled query.",
        examples=["recent-cves"],
    )

    params: list[ScheduledQueryParam] = Field(
        default_factory=list,
        description=(
            "A dictionary of parameters to pass to the cypher query. The keys"
            " are the variable names, and the values are the values to pass."
        ),
        examples=[
            """
            .. code-block:: yaml

              params:
                - name: syncedtype
                  value:
                    - recent
                - name: base_severity
                  value:
                    - HIGH
                    - CRITICAL
            """
        ],
    )

    frequency: int | None = Field(
        default=None,
        description=(
            "The frequency of the scheduled query in minutes. Deprecated in"
            " favor of ``schedule``; mutually exclusive with ``schedule`` and"
            " ``watch_scans``."
        ),
        examples=["1440"],
    )

    schedule: ScheduleSpec | None = Field(
        default=None,
        description=(
            "A structured time-based schedule (interval/hourly/daily/monthly,"
            " UTC). Mutually exclusive with ``frequency`` and ``watch_scans``."
        ),
        examples=[
            """
            .. code-block:: yaml

              schedule:
                type: daily
                days_of_week: [0, 2, 4]
                hour: 9
                minute: 30
            """
        ],
    )

    watch_scans: list[ScheduledQueryWatchScan] = Field(
        default_factory=list,
        description=(
            "The scans to watch for the scheduled query. Based on"
            " SyncMetadata. Query will triger if any of the watched"
            " scans listed are updated. Mutually exclusive with"
            " ``frequency``."
        ),
        examples=[
            """
            .. code-block:: yaml

              watch_scans:
                - grouptype: CVE
                  syncedtype: recent
                - grouptype: CVE
                  syncedtype: modified
            """
        ],
    )

    enabled: bool | None = Field(
        default=True,
        description=("Whether the scheduled query should be enabled. If not set, the scheduled query will be enabled."),
        examples=["true"],
    )

    actions: list[ScheduledQueryAction] = Field(
        default_factory=list,
        description=("The actions to perform when the scheduled query is triggered."),
        examples=[
            """
            .. code-block:: yaml

              actions:
                - action_type: slack
                  title: Recently published HIGH/CRITICAL CVEs
                  initial_comment: |
                    The following HIGH/CRITICAL CVEs have been published in the last 24 hours.
                  channels:
                    - C0000000000
            """
        ],
    )

    @model_validator(mode="after")
    def exclusive_triggers(self) -> "ScheduledQuery":
        validate_exclusive_triggers(self.frequency, self.schedule, self.watch_scans)
        return self


class Workflow(BaseModel):
    """A versioned, configurable workflow executed by Temporal."""

    model_config = ConfigDict(extra="forbid")

    name: str
    schedule: ScheduleSpec | None = None
    watch_scans: list[ScheduledQueryWatchScan] = Field(default_factory=list)
    enabled: bool = True
    stages: list[WorkflowStage] = Field(min_length=1)
    trigger_workflows: list[str] = Field(
        default_factory=list,
        description="Workflow IDs to start after all stages complete successfully.",
    )

    @field_validator("trigger_workflows")
    @classmethod
    def validate_trigger_workflows(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for workflow_id in value:
            workflow_id = workflow_id.strip()
            if not workflow_id:
                raise ValueError("triggered workflow IDs must not be empty")
            if workflow_id not in seen:
                result.append(workflow_id)
                seen.add(workflow_id)
        return result

    @model_validator(mode="after")
    def validate_references(self) -> "Workflow":
        validate_exclusive_triggers(None, self.schedule, self.watch_scans)
        available: set[str] = set()
        all_outputs: set[str] = set()
        for stage_position, stage in enumerate(self.stages, start=1):
            stage_outputs: set[str] = set()
            for activity_position, activity in enumerate(stage.activities, start=1):
                if activity.output in all_outputs or activity.output in stage_outputs:
                    raise ValueError(f"duplicate activity output '{activity.output}'")
                if activity.input is not None and activity.input not in available:
                    raise ValueError(
                        f"stage {stage_position} activity {activity_position} references output"
                        f" '{activity.input}' that is not produced by an earlier stage"
                    )
                stage_outputs.add(activity.output)
            available.update(stage_outputs)
            all_outputs.update(stage_outputs)
        return self


class ToolParamDef(BaseModel):
    """Definition of a single parameter accepted by a tool."""

    name: str
    type: Literal["string", "integer", "float", "boolean"]
    description: str = ""
    required: bool = True
    default: Any | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_lower_snake_id(v)


class ToolDef(BaseModel):
    """A tool definition within a toolset."""

    name: str
    description: str = ""
    cypher: str
    parameters: list[ToolParamDef] = Field(default_factory=list)
    enabled: bool = True


class ToolsetDef(BaseModel):
    """A toolset definition for MCP tool exposure."""

    name: str
    description: str = ""
    enabled: bool = True
    tools: dict[str, ToolDef] = Field(default_factory=dict)

    @field_validator("tools")
    @classmethod
    def validate_tool_ids(cls, v: dict[str, ToolDef]) -> dict[str, ToolDef]:
        for key in v:
            validate_lower_snake_id(key)
        return v


class SkillDef(BaseModel):
    """A prompt template definition within a skillset."""

    name: str
    description: str = ""
    template: str
    parameters: list[ToolParamDef] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    tools_required: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("triggers")
    @classmethod
    def validate_triggers(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in v:
            if not value.strip():
                raise ValueError("triggers entries must not be empty")
            if value in seen:
                raise ValueError("triggers entries must be unique")
            seen.add(value)
        return v

    @field_validator("tools_required")
    @classmethod
    def validate_tools_required(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in v:
            if not re.fullmatch(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*__[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", value):
                raise ValueError("tools_required entries must use MCP tool names like toolset_id__tool_id")
            if value in seen:
                raise ValueError("tools_required entries must be unique")
            seen.add(value)
        return v


class SkillsetDef(BaseModel):
    """A skillset definition for MCP prompt exposure."""

    name: str
    description: str = ""
    enabled: bool = True
    skills: dict[str, SkillDef] = Field(default_factory=dict)

    @field_validator("skills")
    @classmethod
    def validate_skill_ids(cls, v: dict[str, SkillDef]) -> dict[str, SkillDef]:
        for key in v:
            validate_lower_snake_id(key)
        return v


class ReportingConfig(BaseModel):
    queries: dict[str, str] = Field(
        default_factory=dict,
        description="The queries to use for the report.",
        examples=[
            """
            .. code-block:: yaml

              queries:
                cves-severity-of-total: |-
                  MATCH (c:CVE)
                  WITH COUNT(DISTINCT c.id) AS denominator
                  MATCH (c:CVE)
                  WHERE c.base_severity = "CRITICAL"
                  RETURN count(DISTINCT c.id) AS numerator, denominator
            """
        ],
    )

    dashboard: str | None = Field(
        default=None,
        description=(
            "Key of the report in the ``reports`` section to use as the default"
            " dashboard. If unset, no report is displayed on the dashboard page."
        ),
        examples=["dashboard"],
    )

    reports: dict[str, Report] = Field(
        default_factory=dict,
        description="The reports to use for the report.",
        examples=[
            """
            .. code-block:: yaml

              reports:
                cves:
                  name: CVEs
                  rows:
                    - name: CVEs
                      panels:
                        - cypher: cves
                          type: table
                          w: 12
            """
        ],
    )

    scheduled_queries: list[ScheduledQuery] = Field(
        default_factory=list,
        description="The scheduled queries to run.",
        examples=[
            """
            .. code-block:: yaml

              scheduled_queries:
                - name: CVEs by severity
                  cypher: recent-cves
                  frequency: 1440
                  actions:
                    - action_type: slack
                      action_config:
                        title: Recently published HIGH/CRITICAL CVEs
            """
        ],
    )

    workflows: list[Workflow] = Field(
        default_factory=list,
        description="Temporal-backed configurable workflows.",
    )

    @model_validator(mode="after")
    def exclusive_workflow_sections(self) -> "ReportingConfig":
        if self.workflows and self.scheduled_queries:
            raise ValueError(
                "workflows and scheduled_queries cannot both be configured; scheduled_queries is deprecated"
            )
        return self

    toolsets: dict[str, ToolsetDef] = Field(
        default_factory=dict,
        description="Toolset definitions for MCP tool exposure.",
        examples=[
            """
            .. code-block:: yaml

              toolsets:
                my-toolset:
                  name: My Toolset
                  description: A collection of graph tools
                  enabled: true
                  tools:
                    my-tool:
                      name: My Tool
                      description: Counts nodes
                      cypher: MATCH (n) RETURN count(n) AS total
                      enabled: true
            """
        ],
    )

    skillsets: dict[str, SkillsetDef] = Field(
        default_factory=dict,
        description="Skillset definitions for MCP prompt exposure.",
        examples=[
            """
            .. code-block:: yaml

              skillsets:
                investigations:
                  name: Investigations
                  description: Prompt templates for graph investigations
                  enabled: true
                  skills:
                    summarize_node:
                      name: Summarize node
                      template: Summarize {{node_id}} for a security analyst.
                      parameters:
                        - name: node_id
                          type: string
            """
        ],
    )

    @field_validator("toolsets")
    @classmethod
    def validate_toolset_ids(cls, v: dict[str, ToolsetDef]) -> dict[str, ToolsetDef]:
        for key in v:
            validate_lower_snake_id(key)
        return v

    @field_validator("skillsets")
    @classmethod
    def validate_skillset_ids(cls, v: dict[str, SkillsetDef]) -> dict[str, SkillsetDef]:
        for key in v:
            validate_lower_snake_id(key)
        return v

    @field_validator("scheduled_queries", mode="before")
    @classmethod
    def coerce_scheduled_queries(cls, v: Any) -> Any:
        """Accept old dict format (key -> ScheduledQuery) as well as the new list format."""
        if isinstance(v, dict):
            return list(v.values())
        return v


def output_json_schema() -> dict[str, Any]:
    schema = ReportingConfig.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


def load_file(path: str) -> ReportingConfig:
    """Load a ``ReportingConfig`` from a YAML file at *path*."""
    with open(path) as f:
        return ReportingConfig.model_validate(yaml.safe_load(f))


def dump_yaml(config: ReportingConfig) -> str:
    """Serialise *config* to a YAML string."""
    return yaml.dump(
        config.model_dump(),
        default_flow_style=False,
        allow_unicode=True,
    )
