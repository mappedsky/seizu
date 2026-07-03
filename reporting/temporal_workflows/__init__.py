"""Registry of Temporal workflows startable from scheduled query actions.

Workflow AI sessions run headlessly as the scheduled query's creator. When
the creator holds ``chat:bypass_permissions``, the session runs with action
confirmations bypassed (audit-logged in mcp_runtime); otherwise confirmation-
gated tools fail closed for the run.

This module is imported by the web process for the action config schema, so
it must stay free of ``temporalio`` (and other heavy) imports.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from reporting.temporal_workflows.shared import CveDependencyRemediationInput, CveRepoReportInput


@dataclass(frozen=True)
class WorkflowInputContext:
    """Common values available when constructing a workflow input."""

    scheduled_query_id: str
    creator_user_id: str
    rows: list[dict[str, Any]]
    chat_timeout_seconds: int


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
    )


@dataclass(frozen=True)
class WorkflowSpec:
    """A workflow startable from the temporal scheduled query action."""

    name: str
    description: str
    input_factory: Callable[[WorkflowInputContext], object]

    def build_input(self, context: WorkflowInputContext) -> object:
        return self.input_factory(context)


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
            " compatibility), runs the tests, and opens a pull request."
            " Credentials are phase-isolated — the coding agent never sees the"
            " GitHub token. Runs only when configured"
            " (REMEDIATION_GITHUB_TOKEN plus an agent API key)."
        ),
        input_factory=_cve_dependency_remediation_input,
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
