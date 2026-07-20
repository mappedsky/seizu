"""Registry of code-defined Temporal workflows exposed as top-level activity types.

Each registered workflow is its own activity type in the configurable
workflow editor; ``ConfiguredWorkflow`` dispatches it as an awaited child
workflow. Workflow AI sessions run headlessly as the workflow's creator. When
the creator holds ``chat:bypass_permissions``, the session runs with action
confirmations bypassed (audit-logged in mcp_runtime); otherwise confirmation-
gated tools fail closed for the run.

This module is imported by the web process for the activity config schema, so
it must stay free of ``temporalio`` (and other heavy) imports.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cartography_sync.shared import CartographySyncResult
from reporting.temporal_workflows.shared import (
    CveDependencyRemediationInput,
    CveDependencyRemediationResult,
    CveRepoReportInput,
    CveRepoReportResult,
)

if TYPE_CHECKING:
    from reporting.schema.report_config import ActionConfigFieldDef


@dataclass(frozen=True)
class WorkflowInputContext:
    """Common values available when constructing a workflow input."""

    scheduled_query_id: str
    creator_user_id: str
    rows: list[dict[str, Any]]
    chat_timeout_seconds: int
    # The activity's full parameters, so workflows with their own
    # config_fields can read per-workflow settings in their input factory.
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
    """A code-defined workflow exposed as a top-level workflow activity type."""

    name: str
    description: str
    input_factory: Callable[[WorkflowInputContext], object]
    # Whether the workflow needs a referenced-output row list. When False the
    # activity dispatches even without an input reference (e.g. cartography's
    # trigger-only schedules).
    requires_rows: bool = True
    output_type: Any = dict[str, Any]
    # The activity type's ActionConfigFieldDef fields rendered in the workflow
    # editor (a callable so defaults can read settings lazily), and an
    # optional validator for the submitted activity parameters.
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
        output_type=CveRepoReportResult,
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
        output_type=CveDependencyRemediationResult,
    ),
    "cartography_sync": WorkflowSpec(
        name="cartography_sync",
        description=(
            "Runs the configured cartography intel-module runs sequentially on"
            " the dedicated cartography sync worker. Consumes no input rows;"
            " for parallel syncs place multiple cartography activities in one"
            " workflow stage."
        ),
        input_factory=_cartography_sync_input,
        requires_rows=False,
        config_fields=_cartography_config_fields,
        config_validator=_cartography_config_validator,
        output_type=CartographySyncResult,
    ),
}


def get_workflow_spec(name: str) -> WorkflowSpec | None:
    return WORKFLOW_REGISTRY.get(name)


def enabled_workflow_names() -> list[str]:
    """Registered workflow names an operator has enabled for dispatch (sorted).

    ``TEMPORAL_ENABLED_WORKFLOWS`` empty → every registered workflow. Otherwise
    only the configured names that actually exist in the registry (unknown
    names are ignored). Lets an operator expose only a subset of workflows as
    activity types — e.g. enable ``cve_repo_report`` but not
    ``cve_dependency_remediation``. Lazy settings import keeps this module
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
