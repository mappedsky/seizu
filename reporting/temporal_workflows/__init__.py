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

from reporting.temporal_workflows.shared import CveRepoReportInput


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
}


def get_workflow_spec(name: str) -> WorkflowSpec | None:
    return WORKFLOW_REGISTRY.get(name)
