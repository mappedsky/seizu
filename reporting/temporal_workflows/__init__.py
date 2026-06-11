"""Registry of Temporal workflows startable from scheduled query actions.

Workflow AI sessions run headlessly as the scheduled query's creator. When
the creator holds ``chat:bypass_permissions``, the session runs with action
confirmations bypassed (audit-logged in mcp_runtime); otherwise confirmation-
gated tools fail closed for the run.

This module is imported by the web process for the action config schema, so
it must stay free of ``temporalio`` (and other heavy) imports.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowSpec:
    """A workflow startable from the temporal scheduled query action."""

    name: str
    description: str


WORKFLOW_REGISTRY: dict[str, WorkflowSpec] = {
    "cve_repo_report": WorkflowSpec(
        name="cve_repo_report",
        description=(
            "Per affected repository, runs an AI chat session as the scheduled"
            " query's creator that evaluates newly discovered CVEs and"
            " creates/updates a versioned 'CVE Findings' report."
        ),
    ),
}


def get_workflow_spec(name: str) -> WorkflowSpec | None:
    return WORKFLOW_REGISTRY.get(name)
