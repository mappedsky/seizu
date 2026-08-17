"""Workflow/activity payload dataclasses and deterministic helpers.

Imported by workflow code inside the Temporal sandbox — keep this module free
of I/O and heavy imports (dataclasses and pure functions only).
"""

from dataclasses import dataclass, field
from typing import Any

# Emitted by WorkflowSpec.summary_status() (and surfaced in a code-defined
# workflow activity's ConfiguredActivityOutput.metadata["status"]) when the
# workflow returned normally but its own result indicates a partial failure
# it deliberately chose not to raise for (e.g. a per-dependency remediation
# error, a cartography module failure) — as opposed to "completed", meaning
# every unit of work the workflow reports on actually succeeded.
STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"


@dataclass
class ConfiguredWorkflowInvocation:
    workflow_id: str
    manual: bool = False
    # Set only by the watch-poll parent after it has already observed a new
    # SyncMetadata value. Keeping the default preserves old Temporal payloads.
    watch_checked: bool = False
    # Workflow IDs already visited by a post-completion trigger chain. This
    # prevents a configured A -> B -> A cycle from starting forever.
    trigger_lineage: list[str] = field(default_factory=list)


@dataclass
class ConfiguredQueryInput:
    output_id: str
    cypher: str
    parameters: dict[str, Any] = field(default_factory=dict)
    max_rows: int = 200
    max_bytes: int = 1_000_000
    has_input: bool = False
    input_value: Any = None


@dataclass
class ConfiguredActivity:
    type: str
    input_id: str | None
    output_id: str
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_rows: bool = True
    maximum_attempts: int = 1
    # The code-defined child workflow this activity dispatches, resolved
    # activity-side at load time. Persisting the classification in durable
    # history keeps replay deterministic even if the workflow registry changes
    # between the run and its replay. None for query/module activities.
    code_workflow_name: str | None = None


@dataclass
class ConfiguredStage:
    activities: list[ConfiguredActivity] = field(default_factory=list)


@dataclass
class ConfiguredWorkflowDefinition:
    workflow_id: str
    creator_user_id: str
    version: int
    stages: list[ConfiguredStage] = field(default_factory=list)
    trigger_workflows: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


@dataclass
class TriggerConfiguredWorkflowsRequest:
    source_workflow_id: str
    source_run_id: str
    workflow_ids: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)


@dataclass
class ConfiguredActivityOutput:
    output_id: str
    # ``Any`` is required for converter-safe, module-defined JSON values.
    value: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfiguredWorkflowResult:
    status: str
    version: int = 0
    skipped_reason: str | None = None
    activity_results: list[Any] = field(default_factory=list)


@dataclass
class ConfiguredActivityInput:
    workflow_id: str
    activity_type: str
    output_id: str
    parameters: dict[str, Any]
    input_value: Any = None


@dataclass
class CodeWorkflowInputRequest:
    workflow_id: str
    creator_user_id: str
    workflow_name: str
    parameters: dict[str, Any]
    input_value: Any = None


@dataclass
class CodeWorkflowOutputRequest:
    workflow_name: str
    output_id: str
    value: Any = None


def normalize_configured_rows(
    rows: list[Any],
    return_attribute: str = "details",
) -> list[dict[str, Any]]:
    """Normalize query rows, including pre-fix Temporal history payloads.

    Neo4j ``Record`` objects were previously serialized by Temporal as lists
    of positional values. Most scheduled queries return one named map (usually
    ``details``), so a single value can be reconstructed losslessly using the
    activity's configured return attribute. Multiple positional values cannot
    recover their original column names; retain them under ``_values`` while
    exposing the first value under the configured attribute.
    """
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(row)
        elif isinstance(row, list):
            if len(row) == 1:
                normalized.append({return_attribute: row[0]})
            else:
                normalized.append(
                    {
                        return_attribute: row[0] if row else None,
                        "_values": row,
                    }
                )
        else:
            normalized.append({return_attribute: row})
    return normalized


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


@dataclass
class ScheduledChatInvocation:
    """Identifies the scheduled chat a run belongs to.

    ``manual`` marks a run-now (it bypasses the enabled/trigger checks);
    ``watch_checked`` marks a run the watch-poll workflow already qualified.
    """

    scheduled_chat_id: str = ""
    manual: bool = False
    watch_checked: bool = False


@dataclass
class ScheduledChatDefinition:
    """The resolved schedule a run executes, or the reason it was skipped."""

    scheduled_chat_id: str = ""
    creator_user_id: str = ""
    name: str = ""
    prompt: str = ""
    timeout_seconds: int = 600
    version: int = 0
    skipped_reason: str | None = None


@dataclass
class ScheduledChatRunResult:
    # success | partial | budget_exhausted | blocked | failure | skipped
    status: str = "success"
    thread_id: str = ""
    summary: str = ""
    error: str | None = None
    budget: dict[str, Any] | None = None


@dataclass
class SessionReapResult:
    """What one session-reap sweep did, carried back into workflow history.

    Temporal keeps it, so the last sweeps are readable in the UI without
    grepping worker logs -- the usual question after a surprise ("what did it
    delete, and when") has an answer that outlives a log retention window.
    """

    sessions_seen: int = 0
    sessions_reaped: int = 0
    sessions_kept: int = 0
    sandboxes_seen: int = 0
    orphans_reaped: int = 0
    failed: int = 0


@dataclass
class AgentChatInput:
    """Input for the ``agent_chat`` workflow module."""

    workflow_id: str = ""
    creator_user_id: str = ""
    prompt: str = ""
    session_title: str = "Workflow chat"
    timeout_seconds: int = 600
    skill: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentChatResult:
    """The ``agent_chat`` activity's named output, consumable by later stages."""

    status: str = "success"
    thread_id: str = ""
    summary: str = ""
    error: str | None = None
    budget: dict[str, Any] | None = None


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


@dataclass
class ChatTurnInvocation:
    """Stable input needed to run one admitted interactive turn.

    The work and its permission cap live on the turn record. Keeping them out
    of this payload leaves one durable source of truth while retaining the
    timeout here because the workflow must schedule deterministically.
    """

    turn_id: str = ""
    timeout_seconds: int = 600


@dataclass
class ChatTurnRunResult:
    # completed | failed | canceled
    status: str = "completed"
    last_seq: int = 0


@dataclass
class ChatWorkerStepInvocation:
    """Everything one plan step needs to run on a worker that has none of it.

    A step used to run inside the turn's own activity, where it read its sandbox,
    its budget ledger, its disclosed tool set and its session memory out of
    context variables the dispatcher had set. Distributed, none of those exist:
    the worker is a different process and possibly a different machine, so every
    one of them is an explicit field here (AGT-018).

    ``version`` is checked by the activity. Adding a field with a default is
    compatible; changing what an existing field *means* is not, and a worker
    running older code must refuse the payload rather than interpret it wrongly.

    The turn record stays the source of truth for identity: only the ``user_id``
    to resolve and the cap to intersect against travel here, never a token and
    never a resolved permission set.
    """

    version: int = 1
    turn_id: str = ""
    # Duplicated out of ``step_json`` so workflow code can name a step it could
    # not run without having to parse the plan.
    step_id: str = ""
    user_id: str = ""
    # Client-side thread id; the worker namespaces it exactly as the turn does.
    thread_id: str = ""
    permission_cap: list[str] = field(default_factory=list)
    bypass_confirmations: bool = False
    # JSON, not nested dataclasses: a plan step is an open dict the planner
    # shapes, and pinning its keys into a payload schema would make every
    # planner change a Temporal compatibility question.
    step_json: str = ""
    plan_json: str = ""
    dependency_results_json: str = ""
    conversation_context: str = ""
    session_memory_json: str = ""
    disclosed_tools: list[str] = field(default_factory=list)
    progressive: bool = True
    # The conversation's sandbox, when the coordinator has one open. The worker
    # attaches to it and never creates or tears one down (SBX-015).
    sandbox_id: str = ""
    sandbox_thread: str = ""
    # A slice of the run budget, allocated before the fan-out.
    token_grant: int = 0
    soft_token_grant: int = 0
    cost_grant_usd: float = 0.0
    llm_call_grant: int = 0
    # The model the *turn* resolved, not a hint for the worker to re-resolve.
    # This was a bare ``economy: bool`` because settings could only express a
    # binary; once a model is a per-turn choice, re-resolving worker-side would
    # run a different model than the turn was admitted with -- the same reason
    # ``permission_cap`` travels rather than being re-derived (AGT-006/019).
    model_spec: dict[str, Any] = field(default_factory=dict)
    #: The model the step's *summary* passes run on. Separate because a summary
    #: is transcription rather than decision-making and may carry a different
    #: reasoning budget; carried for the same reason as ``model_spec``.
    summary_model_spec: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatWorkerStepOutcome:
    """One distributed step's result, its spend, and what it learned.

    ``result_json`` is inline when it is small enough to be worth carrying;
    otherwise it is empty and ``result_ref`` names a payload in the report store,
    because a step's full call trace can exceed what Temporal will carry and
    would be copied into workflow history twice.

    ``error`` is set when the step could not run at all. That is not the same as
    a step that ran and failed -- which comes back as an ordinary result for the
    verifier to judge -- and the two are kept apart so a worker crash never looks
    like a finding.

    What the step disclosed is deliberately *not* a field here: it is already in
    the result the dispatcher reads, and the same fact carried in two places is
    the shape that drifts.
    """

    version: int = 1
    step_id: str = ""
    result_json: str = ""
    result_ref: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    episodes: list[dict[str, Any]] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass
class ChatStepFanoutInvocation:
    """One dispatch batch, scheduled step by step rather than gathered locally."""

    version: int = 1
    turn_id: str = ""
    steps: list[ChatWorkerStepInvocation] = field(default_factory=list)
    step_timeout_seconds: int = 600


@dataclass
class ChatStepFanoutResult:
    outcomes: list[ChatWorkerStepOutcome] = field(default_factory=list)
