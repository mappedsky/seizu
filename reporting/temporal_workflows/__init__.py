"""Registry of Temporal workflows startable from scheduled query actions.

Each workflow declares the confirmation-gated MCP tools it intends to run
without interactive confirmation (``confirmation_bypass_tools``). The list is
defined here, server-side, and is never taken from user-supplied action
config — the scheduled query form only asks the user to acknowledge it. Any
confirmation-gated tool a workflow's AI session reaches that is *not* in the
declared list fails closed (see ``mcp_runtime.call_tool_for_chat``).

This module is imported by the web process for the action config schema, so
it must stay free of ``temporalio`` (and other heavy) imports.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkflowSpec:
    """A startable workflow and its declared confirmation bypasses."""

    name: str
    description: str
    confirmation_bypass_tools: frozenset[str] = field(default_factory=frozenset)


WORKFLOW_REGISTRY: dict[str, WorkflowSpec] = {
    "cve_repo_report": WorkflowSpec(
        name="cve_repo_report",
        description=(
            "Per affected repository, runs an AI chat session as the scheduled"
            " query's creator that evaluates newly discovered CVEs and"
            " creates/updates a versioned 'CVE Findings' report."
        ),
        # reports__create is already chat-safe without confirmation (it only
        # creates a new private report); reports__create_version is the one
        # confirmation-gated tool this workflow runs headlessly.
        confirmation_bypass_tools=frozenset({"reports__create_version"}),
    ),
}


def get_workflow_spec(name: str) -> WorkflowSpec | None:
    return WORKFLOW_REGISTRY.get(name)
