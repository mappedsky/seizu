"""Registry of Temporal workflows startable from scheduled query actions.

Workflow AI sessions run headlessly as the scheduled query's creator. When
the creator holds ``chat:bypass_permissions``, the session runs with action
confirmations bypassed (audit-logged in mcp_runtime); otherwise confirmation-
gated tools fail closed for the run.

This module is imported by the web process for the action config schema, so
it must stay free of ``temporalio`` (and other heavy) imports.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from reporting.temporal_workflows.shared import CveDependencyRemediationInput, CveRepoReportInput

if TYPE_CHECKING:
    from reporting.schema.report_config import ActionConfigFieldDef


@dataclass(frozen=True)
class WorkflowInputContext:
    """Common values available when constructing a workflow input."""

    scheduled_query_id: str
    creator_user_id: str
    rows: list[dict[str, Any]]
    chat_timeout_seconds: int
    # The temporal action's full action_config, so workflows with their own
    # config_fields can read per-schedule settings in their input factory.
    action_config: dict[str, Any] = field(default_factory=dict)


def _cve_repo_report_input(context: WorkflowInputContext) -> CveRepoReportInput:
    return CveRepoReportInput(
        scheduled_query_id=context.scheduled_query_id,
        creator_user_id=context.creator_user_id,
        rows=context.rows,
        chat_timeout_seconds=context.chat_timeout_seconds,
    )


def _cve_dependency_remediation_input(context: WorkflowInputContext) -> CveDependencyRemediationInput:
    # Remediation runs a full clone → upgrade → test → PR cycle, so it gets its
    # own (much larger) timeout instead of the context's generic chat activity
    # timeout. Lazy import keeps this module light for the web process.
    from reporting import settings

    return CveDependencyRemediationInput(
        scheduled_query_id=context.scheduled_query_id,
        creator_user_id=context.creator_user_id,
        rows=context.rows,
        timeout_seconds=settings.REMEDIATION_TIMEOUT_SECONDS,
        ci_watch_max_seconds=settings.REMEDIATION_CI_MAX_WAIT_SECONDS,
        ci_poll_seconds=settings.REMEDIATION_CI_POLL_SECONDS,
        ci_queued_stuck_seconds=settings.REMEDIATION_CI_QUEUED_STUCK_SECONDS,
        ci_fix_max_attempts=settings.REMEDIATION_CI_FIX_MAX_ATTEMPTS,
    )


@dataclass(frozen=True)
class WorkflowSpec:
    """A workflow startable from the temporal scheduled query action."""

    name: str
    description: str
    input_factory: Callable[[WorkflowInputContext], object]
    # Whether the workflow needs query result rows. When False the temporal
    # action dispatches even for schedules whose query returned nothing (the
    # schedule's cypher is just the trigger, e.g. RETURN 1 for cartography).
    requires_rows: bool = True
    # Extra ActionConfigFieldDef fields rendered in the scheduled-query UI
    # when this workflow is selected (a callable so defaults can read settings
    # lazily), and an optional validator for the submitted action_config.
    config_fields: Callable[[], list["ActionConfigFieldDef"]] | None = None
    config_validator: Callable[[dict[str, Any]], str | None] | None = None

    def build_input(self, context: WorkflowInputContext) -> object:
        return self.input_factory(context)


def _cartography_sync_input(context: WorkflowInputContext) -> object:
    from reporting.temporal_workflows import cartography_config

    return cartography_config.build_input(context)


def _cartography_config_fields() -> list["ActionConfigFieldDef"]:
    from reporting.temporal_workflows import cartography_config

    return cartography_config.config_fields()


def _cartography_config_validator(action_config: dict[str, Any]) -> str | None:
    from reporting.temporal_workflows import cartography_config

    return cartography_config.validate_config(action_config)


WORKFLOW_REGISTRY: dict[str, WorkflowSpec] = {
    "cve_repo_report": WorkflowSpec(
        name="cve_repo_report",
        description=(
            "Per affected repository, runs an AI chat session as the scheduled"
            " query's creator that evaluates newly discovered CVEs and"
            " creates/updates a versioned 'CVE Findings' report."
        ),
        input_factory=_cve_repo_report_input,
    ),
    "cve_dependency_remediation": WorkflowSpec(
        name="cve_dependency_remediation",
        description=(
            "Per (repository, vulnerable dependency), remediates newly"
            " discovered CVEs: a coding-agent CLI in an isolated sandbox"
            " updates the dependency (with any code changes needed for"
            " compatibility) and opens a pull request (CI runs the tests, not"
            " the agent). Credentials are phase-isolated — the coding agent never"
            " sees the GitHub token. Runs only when configured"
            " (REMEDIATION_GITHUB_TOKEN plus an agent API key)."
        ),
        input_factory=_cve_dependency_remediation_input,
    ),
    "cartography_sync": WorkflowSpec(
        name="cartography_sync",
        description=(
            "Runs cartography intel-module syncs as a staged pipeline (modules"
            " within a stage run in parallel, stages run sequentially) on the"
            " dedicated cartography sync worker. The schedule's query is only"
            " the trigger (e.g. RETURN 1) — no result rows are consumed."
        ),
        input_factory=_cartography_sync_input,
        requires_rows=False,
        config_fields=_cartography_config_fields,
        config_validator=_cartography_config_validator,
    ),
}


def get_workflow_spec(name: str) -> WorkflowSpec | None:
    return WORKFLOW_REGISTRY.get(name)


def enabled_workflow_names() -> list[str]:
    """Registered workflow names an operator has enabled for dispatch (sorted).

    ``TEMPORAL_ENABLED_WORKFLOWS`` empty → every registered workflow. Otherwise
    only the configured names that actually exist in the registry (unknown
    names are ignored). Lets an operator run the temporal action module while
    allowing only a subset of workflows — e.g. enable ``cve_repo_report`` but
    not ``cve_dependency_remediation``. Lazy settings import keeps this module
    importable by the web process without heavy deps.
    """
    from reporting import settings

    configured = settings.TEMPORAL_ENABLED_WORKFLOWS
    if not configured:
        return sorted(WORKFLOW_REGISTRY)
    return sorted(name for name in configured if name in WORKFLOW_REGISTRY)


def get_enabled_workflow_spec(name: str) -> WorkflowSpec | None:
    """Return the spec only when it is both registered and operator-enabled."""
    if name not in enabled_workflow_names():
        return None
    return WORKFLOW_REGISTRY.get(name)


def workflow_config_schemas() -> dict[str, list["ActionConfigFieldDef"]]:
    """Per-workflow extra config fields, for enabled workflows that have any.

    Served to the frontend so the scheduled-query dialog can render a
    dependent sub-form once a workflow is selected.
    """
    schemas: dict[str, list[ActionConfigFieldDef]] = {}
    for name in enabled_workflow_names():
        spec = WORKFLOW_REGISTRY[name]
        if spec.config_fields is not None:
            schemas[name] = spec.config_fields()
    return schemas


def validate_workflow_action_config(action_config: dict[str, Any]) -> str | None:
    """Validate a temporal action's config against the selected workflow.

    Checks the workflow's own required config_fields and runs its
    config_validator. The base fields (workflow/max_rows/...) are validated by
    the generic schema loop; an unknown/disabled workflow is reported here so
    a schedule can't be saved for a workflow that dispatch would refuse.
    """
    workflow_name = action_config.get("workflow")
    if not isinstance(workflow_name, str) or not workflow_name:
        return None  # the generic required-field check reports missing workflow
    spec = get_enabled_workflow_spec(workflow_name)
    if spec is None:
        return f"Unknown or disabled workflow '{workflow_name}'. Enabled workflows: {enabled_workflow_names()}."
    if spec.config_fields is not None:
        for field_def in spec.config_fields():
            if not field_def.required:
                continue
            value = action_config.get(field_def.name)
            if value is None or value == "" or value == []:
                return f"Workflow '{workflow_name}' is missing required field '{field_def.name}'."
            if field_def.type == "boolean" and value is not True:
                return f"Workflow '{workflow_name}' requires '{field_def.name}' to be accepted."
    if spec.config_validator is not None:
        return spec.config_validator(action_config)
    return None
