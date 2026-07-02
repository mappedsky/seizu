"""Workflow/activity payload dataclasses and deterministic helpers.

Imported by workflow code inside the Temporal sandbox — keep this module free
of I/O and heavy imports (dataclasses and pure functions only).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CveRepoReportInput:
    scheduled_query_id: str
    creator_user_id: str
    # Projected query result rows (the per-row data map, e.g. the "details"
    # attribute), each expected to carry a "repo" key.
    rows: list[dict[str, Any]] = field(default_factory=list)
    chat_timeout_seconds: int = 600


@dataclass
class RepoChatInput:
    repo: str
    cves: list[dict[str, Any]]
    creator_user_id: str
    scheduled_query_id: str


@dataclass
class RepoChatResult:
    repo: str
    thread_id: str | None
    summary: str
    error: str | None = None
    status: str = "completed"
    budget: dict[str, Any] | None = None


@dataclass
class CveRepoReportResult:
    per_repo: list[RepoChatResult] = field(default_factory=list)


@dataclass
class CveDependencyRemediationInput:
    scheduled_query_id: str
    creator_user_id: str
    # Projected query result rows (the per-row data map, e.g. the "details"
    # attribute), each expected to carry "repo" and "package" keys.
    rows: list[dict[str, Any]] = field(default_factory=list)
    chat_timeout_seconds: int = 2400


@dataclass
class DependencyChatInput:
    repo: str
    package: str
    cves: list[dict[str, Any]]
    creator_user_id: str
    scheduled_query_id: str


@dataclass
class DependencyChatResult:
    repo: str
    package: str
    thread_id: str | None
    summary: str
    error: str | None = None
    status: str = "completed"
    budget: dict[str, Any] | None = None


@dataclass
class CveDependencyRemediationResult:
    per_dependency: list[DependencyChatResult] = field(default_factory=list)


def group_rows_by_repo(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group result rows by their "repo" key; rows without one are dropped."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        repo = row.get("repo")
        if not isinstance(repo, str) or not repo:
            continue
        grouped.setdefault(repo, []).append(row)
    return grouped


def group_rows_by_repo_package(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group result rows by ("repo", "package"); rows missing either are dropped.

    One package can be flagged by several CVEs and appear in several manifests —
    grouping keeps that one (repo, package) remediation to a single chat/PR.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        repo = row.get("repo")
        package = row.get("package")
        if not isinstance(repo, str) or not repo or not isinstance(package, str) or not package:
            continue
        grouped.setdefault((repo, package), []).append(row)
    return grouped
