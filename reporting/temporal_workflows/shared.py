"""Workflow/activity payload dataclasses and deterministic helpers.

Imported by workflow code inside the Temporal sandbox — keep this module free
of I/O and heavy imports (dataclasses and pure functions only).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConfiguredWorkflowInvocation:
    workflow_id: str
    manual: bool = False


@dataclass
class ConfiguredQueryInput:
    input_id: str
    cypher: str
    parameters: dict[str, Any] = field(default_factory=dict)
    max_rows: int = 200


@dataclass
class ConfiguredActivity:
    type: str
    input_id: str | None
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_rows: bool = True


@dataclass
class ConfiguredWorkflowDefinition:
    workflow_id: str
    creator_user_id: str
    version: int
    inputs: list[ConfiguredQueryInput] = field(default_factory=list)
    activities: list[ConfiguredActivity] = field(default_factory=list)
    skipped_reason: str | None = None


@dataclass
class ConfiguredQueryResult:
    input_id: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False


@dataclass
class ConfiguredWorkflowResult:
    status: str
    version: int = 0
    skipped_reason: str | None = None
    input_rows: dict[str, int] = field(default_factory=dict)
    activity_results: list[Any] = field(default_factory=list)


@dataclass
class ConfiguredActivityInput:
    workflow_id: str
    activity_type: str
    parameters: dict[str, Any]
    rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CodeWorkflowInputRequest:
    workflow_id: str
    creator_user_id: str
    workflow_name: str
    parameters: dict[str, Any]
    rows: list[dict[str, Any]] = field(default_factory=list)


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
    # Bound for one remediation run (all sandbox phases).
    timeout_seconds: int = 1800
    # Post-push CI watch: total wait for a PR's checks to settle (0 disables
    # the watch), poll interval, queued-stuck threshold (see PrCiStatusInput),
    # and how many coding-agent fix runs one PR may get.
    ci_watch_max_seconds: int = 3600
    ci_poll_seconds: int = 120
    ci_queued_stuck_seconds: int = 1800
    ci_fix_max_attempts: int = 1


@dataclass
class DependencyRemediationInput:
    repo: str
    package: str
    cves: list[dict[str, Any]]
    creator_user_id: str
    scheduled_query_id: str


@dataclass
class DependencyRemediationResult:
    repo: str
    package: str
    pr_url: str | None = None
    error: str | None = None
    status: str = "completed"
    # Short masked tail of the sandbox run output, for debugging failed runs.
    output_tail: str = ""
    # Branch targets echoed back by the activity so the workflow can drive
    # follow-up activities (CI watch / fix) against the same PR branch.
    base_branch: str = ""
    branch_name: str = ""
    # Outcome of the post-push CI watch ("" when the PR was not watched):
    # passed | fixed | failures_commented | ci_failed | fix_failed | timed_out |
    # no_checks | merged | pr_closed | error.
    ci_status: str = ""
    ci_detail: str = ""


@dataclass
class PrCiStatusInput:
    repo: str
    pr_url: str
    # Checks still queued (never started) after this long are ignored — CI that
    # never schedules a runner would otherwise stall the watch until max wait.
    queued_stuck_seconds: int = 1800


@dataclass
class PrCiCheck:
    """One failing check, with enough identity to fetch its logs later."""

    name: str
    # Check-run id when the check is a GitHub check run (log/annotation lookup);
    # None for legacy commit statuses, which carry only a description/URL.
    check_run_id: int | None = None
    summary: str = ""
    details_url: str = ""


@dataclass
class PrCiStatusResult:
    # pending | success | failure | no_checks | merged | closed
    state: str
    head_sha: str = ""
    failing: list[PrCiCheck] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    # Checks the watch will not wait on (queued past the stuck threshold, or
    # cancelled/stale runs), with a "name (reason)" label each.
    ignored: list[str] = field(default_factory=list)


@dataclass
class CiFixInput:
    repo: str
    package: str
    pr_url: str
    base_branch: str
    branch_name: str
    failing: list[PrCiCheck]
    creator_user_id: str
    scheduled_query_id: str
    ignored: list[str] = field(default_factory=list)


@dataclass
class CiFixResult:
    repo: str
    package: str
    # pushed | commented | pushed_and_commented | none
    action: str
    comment_url: str | None = None
    error: str | None = None
    output_tail: str = ""


@dataclass
class CveDependencyRemediationResult:
    per_dependency: list[DependencyRemediationResult] = field(default_factory=list)


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
