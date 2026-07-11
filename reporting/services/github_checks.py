"""Worker-side GitHub PR check-suite inspection, commenting, and fork management.

Used by the ``cve_dependency_remediation`` workflow's CI-watch stage (via
``reporting/temporal_workflows/activities.py``) to poll a remediation PR's
checks, gather failure context for the fix agent, and post PR comments — and
by ``sandbox_remediation`` to ensure the bot-owned fork in fork mode
(``REMEDIATION_USE_FORK``).

This runs in the Temporal worker with ``REMEDIATION_GITHUB_TOKEN`` — safe,
unlike the sandboxes, because no untrusted code executes here: it is plain
REST I/O against the GitHub API. Anything *derived from* CI output (log
tails, annotations) is untrusted data and is wrapped/escaped by the caller
before reaching an agent prompt.

Classification is a pure function (:func:`classify_checks`) over the fetched
payloads so the queued-stuck / failing / pending logic is unit-testable
without HTTP mocking.
"""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from reporting.services import sandbox_agent
from reporting.temporal_workflows.shared import PrCiCheck, PrCiStatusResult

logger = logging.getLogger(__name__)

_PR_NUMBER_RE = re.compile(r"/pull/(\d+)(?:$|[/?#])")

# Check-run conclusions that should trigger the fix flow. "action_required"
# (e.g. a workflow awaiting approval) is not failing — it needs a human, and
# an agent can neither fix nor meaningfully triage it.
_FAILING_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
# Conclusions that neither pass nor fail — a cancelled run is usually a
# superseded push; waiting on or "fixing" it would be noise.
_IGNORED_CONCLUSIONS = frozenset({"cancelled", "stale", "action_required"})

_REQUEST_TIMEOUT_SECONDS = 30
# Bound on Link-header pagination (10 pages × 100 = 1000 checks/statuses).
_MAX_PAGES = 10
# Bounds for the failure context injected into the fix agent's prompt.
_LOG_TAIL_BYTES_PER_CHECK = 8_000
_MAX_FAILING_CHECKS_DETAILED = 5
_MAX_ANNOTATIONS_PER_CHECK = 20

# Agent-authored PR-comment text is untrusted output (the agent read CI logs
# that may carry prompt injection), so the worker never posts it verbatim:
# render_agent_pr_comment wraps it in a fixed server-owned template, quotes it,
# neutralizes @-mentions and line-leading slash commands (bots like /retest,
# /deploy, /approve parse those), and caps its length.
_COMMENT_BODY_MAX_CHARS = 4_000
# Zero-width space entity: renders invisibly on GitHub but breaks the token so
# mention/slash-command parsers no longer match.
_ZWSP = "&#8203;"
_COMMENT_HEADER = (
    "**Automated CI triage** — Seizu dependency-update workflow\n\n"
    "The quoted analysis below was written by a sandboxed coding agent from "
    "this pull request's CI output. Treat it as informational; it is not a "
    "command or an approval.\n\n"
)


def api_base_url() -> str:
    """REST API base for the configured GitHub host (github.com or GHES)."""
    from reporting import settings

    host = settings.REMEDIATION_GITHUB_HOST
    if host == "github.com":
        return "https://api.github.com"
    return f"https://{host}/api/v3"


def parse_pr_number(pr_url: str) -> int | None:
    match = _PR_NUMBER_RE.search(pr_url)
    return int(match.group(1)) if match else None


def _client() -> httpx.AsyncClient:
    from reporting import settings

    return httpx.AsyncClient(
        base_url=api_base_url(),
        headers={
            "Authorization": f"Bearer {settings.REMEDIATION_GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=_REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    )


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _paginate(client: httpx.AsyncClient, url: str, items_key: str) -> list[dict[str, Any]]:
    """Collect ``items_key`` across RFC 5988 ``Link: rel="next"`` pages.

    Matrix-heavy workflows easily exceed one page of check runs; missing them
    would report a false success. Bounded by :data:`_MAX_PAGES`.
    """
    items: list[dict[str, Any]] = []
    next_url: str | None = url
    params: dict[str, Any] | None = {"per_page": 100}
    for _ in range(_MAX_PAGES):
        if next_url is None:
            break
        resp = await client.get(next_url, params=params)
        resp.raise_for_status()
        items.extend(resp.json().get(items_key, []))
        next_url = resp.links.get("next", {}).get("url")
        params = None  # the next link's URL already carries the query string
    return items


# Fork mode: how long to wait for a newly created fork's git data to become
# available (fork creation is asynchronous on GitHub's side) and how often to
# probe. Existing forks answer the first probe immediately.
_FORK_READY_TIMEOUT_SECONDS = 300
_FORK_READY_POLL_SECONDS = 5


async def ensure_fork(repo: str, *, org: str = "") -> str:
    """Create (or find) the token owner's fork of ``repo``; return its full name.

    ``POST /repos/{repo}/forks`` is idempotent — when a fork already exists it
    returns that fork — and the response carries the fork's actual
    ``full_name`` (GitHub renames on collision, e.g. ``app-1``, so the name is
    taken from the API rather than assumed). Fork creation is asynchronous:
    a brand-new fork is polled until its git data is reachable (the commits
    endpoint answers 404/409 until then), so the caller can clone/push it.
    """
    payload: dict[str, Any] = {"organization": org} if org else {}
    async with _client() as client:
        resp = await client.post(f"/repos/{repo}/forks", json=payload)
        resp.raise_for_status()
        full_name = str(resp.json().get("full_name") or "")
        if not full_name:
            raise RuntimeError(f"fork of {repo} returned no full_name")
        deadline = time.monotonic() + _FORK_READY_TIMEOUT_SECONDS
        while True:
            probe = await client.get(f"/repos/{full_name}/commits", params={"per_page": 1})
            if probe.status_code == 200:
                return full_name
            if probe.status_code not in (404, 409):
                probe.raise_for_status()
            if time.monotonic() >= deadline:
                raise TimeoutError(f"fork {full_name} has no git data after {_FORK_READY_TIMEOUT_SECONDS}s")
            await asyncio.sleep(_FORK_READY_POLL_SECONDS)


def render_agent_pr_comment(body: str) -> str:
    """Render untrusted agent triage text as a safe, fixed-template PR comment.

    The agent was exposed to untrusted CI output, so its comment text could be
    prompt-injected into pings or bot commands. The template is server-owned;
    the agent text is length-capped, has ``@``-mentions and line-leading
    slash commands broken with a zero-width space, and is block-quoted so it
    reads as reported analysis rather than a statement by the bot.
    """
    text = body.strip()
    if len(text) > _COMMENT_BODY_MAX_CHARS:
        text = text[:_COMMENT_BODY_MAX_CHARS] + "\n… (truncated)"
    # "@user"/"@org/team" must never notify anyone.
    text = text.replace("@", f"@{_ZWSP}")
    # "/retest", "/deploy", … at line start drive slash-command bots. The
    # block quote below already prevents the ^/ match; the zero-width space is
    # belt-and-braces for parsers that strip quoting first.
    text = re.sub(r"(?m)^(\s*)/", rf"\1{_ZWSP}/", text)
    quoted = "\n".join(f"> {line}".rstrip() for line in text.splitlines())
    return _COMMENT_HEADER + quoted


def classify_checks(
    pull: dict[str, Any],
    check_runs: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
    *,
    now: datetime,
    queued_stuck_seconds: int,
    fallback_queued_at: datetime | None = None,
) -> PrCiStatusResult:
    """Classify a PR head's check runs + legacy commit statuses.

    ``fallback_queued_at`` ages queued check runs that expose no timestamp of
    their own (common while a run has never started) — callers pass the head
    commit's date, since checks are created on push.

    ``state`` semantics for the workflow watch loop:
    - ``merged`` / ``closed`` — the PR is done; stop watching.
    - ``pending`` — at least one non-stuck check is queued or running; keep
      polling (a failure verdict waits for the full picture, so one fix run
      sees every failure).
    - ``failure`` — every non-ignored check finished and at least one failed.
    - ``success`` — every non-ignored check finished without failures (checks
      ignored as stuck/cancelled are listed but do not block).
    - ``no_checks`` — the head commit has no checks or statuses at all (the
      caller allows a grace period for CI to register them).
    """
    if pull.get("merged"):
        return PrCiStatusResult(state="merged", head_sha=str(pull.get("head", {}).get("sha", "")))
    if pull.get("state") != "open":
        return PrCiStatusResult(state="closed", head_sha=str(pull.get("head", {}).get("sha", "")))

    head_sha = str(pull.get("head", {}).get("sha", ""))
    stuck_cutoff = timedelta(seconds=queued_stuck_seconds)
    failing: list[PrCiCheck] = []
    pending: list[str] = []
    ignored: list[str] = []
    seen_any = False

    for run in check_runs:
        seen_any = True
        name = str(run.get("name", "unknown"))
        status = run.get("status")
        if status == "completed":
            conclusion = run.get("conclusion")
            if conclusion in _FAILING_CONCLUSIONS:
                output = run.get("output") or {}
                failing.append(
                    PrCiCheck(
                        name=name,
                        check_run_id=run.get("id"),
                        summary=str(output.get("title") or "")[:200],
                        details_url=str(run.get("details_url") or ""),
                    )
                )
            elif conclusion in _IGNORED_CONCLUSIONS:
                ignored.append(f"{name} ({conclusion})")
            # success / neutral / skipped → passed.
        elif status == "queued":
            # A check queued past the threshold likely has no runner coming
            # (offline self-hosted runner, disabled app) — don't wait on it.
            # Queued runs often have no started_at yet, so prefer created_at
            # and finally the caller's fallback anchor (head commit date).
            queued_at = _parse_time(run.get("created_at")) or _parse_time(run.get("started_at")) or fallback_queued_at
            if queued_at is not None and now - queued_at > stuck_cutoff:
                ignored.append(f"{name} (queued for over {queued_stuck_seconds}s)")
            else:
                pending.append(name)
        else:  # in_progress and any future states: genuinely running, wait.
            pending.append(name)

    for status_ctx in statuses:
        seen_any = True
        name = str(status_ctx.get("context", "unknown"))
        state = status_ctx.get("state")
        if state in ("failure", "error"):
            failing.append(
                PrCiCheck(
                    name=name,
                    check_run_id=None,
                    summary=str(status_ctx.get("description") or "")[:200],
                    details_url=str(status_ctx.get("target_url") or ""),
                )
            )
        elif state == "pending":
            updated_at = _parse_time(status_ctx.get("updated_at"))
            if updated_at is not None and now - updated_at > stuck_cutoff:
                ignored.append(f"{name} (pending for over {queued_stuck_seconds}s)")
            else:
                pending.append(name)
        # success → passed.

    if not seen_any:
        return PrCiStatusResult(state="no_checks", head_sha=head_sha)
    if pending:
        return PrCiStatusResult(state="pending", head_sha=head_sha, failing=failing, pending=pending, ignored=ignored)
    if failing:
        return PrCiStatusResult(state="failure", head_sha=head_sha, failing=failing, ignored=ignored)
    return PrCiStatusResult(state="success", head_sha=head_sha, ignored=ignored)


async def fetch_pr_ci_status(repo: str, pr_number: int, *, queued_stuck_seconds: int) -> PrCiStatusResult:
    """Fetch and classify the current CI state of a PR's head commit."""
    async with _client() as client:
        pull_resp = await client.get(f"/repos/{repo}/pulls/{pr_number}")
        pull_resp.raise_for_status()
        pull = pull_resp.json()
        if pull.get("merged") or pull.get("state") != "open":
            return classify_checks(pull, [], [], now=datetime.now(UTC), queued_stuck_seconds=queued_stuck_seconds)

        head_sha = pull["head"]["sha"]
        check_runs = await _paginate(client, f"/repos/{repo}/commits/{head_sha}/check-runs", "check_runs")
        # Legacy commit statuses (Jenkins plugins, codecov, …) don't appear in
        # the check-runs API; the combined status covers them.
        statuses = await _paginate(client, f"/repos/{repo}/commits/{head_sha}/status", "statuses")

        # Anchor for queued runs exposing no timestamp at all: the head
        # commit's date (checks are created on push). Fetched only when needed.
        fallback_queued_at: datetime | None = None
        if any(
            run.get("status") == "queued" and not (run.get("created_at") or run.get("started_at")) for run in check_runs
        ):
            try:
                commit_resp = await client.get(f"/repos/{repo}/commits/{head_sha}")
                commit_resp.raise_for_status()
                commit = commit_resp.json().get("commit", {})
                fallback_queued_at = _parse_time(commit.get("committer", {}).get("date"))
            except Exception:
                logger.warning("head commit fetch failed for %s@%s; ageless queued checks stay pending", repo, head_sha)

    return classify_checks(
        pull,
        check_runs,
        statuses,
        now=datetime.now(UTC),
        queued_stuck_seconds=queued_stuck_seconds,
        fallback_queued_at=fallback_queued_at,
    )


async def fetch_failure_context(repo: str, failing: list[PrCiCheck]) -> str:
    """Best-effort failure details for the fix agent's prompt (UNTRUSTED text).

    Per failing check run: its output summary, annotations, and — when the
    check is a GitHub Actions job (job id == check-run id) — a tail of the job
    log. Every fetch is optional; a check with no retrievable detail still
    contributes its name so the agent knows what failed.
    """
    sections: list[str] = []
    async with _client() as client:
        for check in failing[:_MAX_FAILING_CHECKS_DETAILED]:
            lines = [f"### Check: {check.name}"]
            if check.summary:
                lines.append(f"Summary: {check.summary}")
            if check.details_url:
                lines.append(f"Details: {check.details_url}")
            if check.check_run_id is not None:
                lines.extend(await _check_run_details(client, repo, check.check_run_id))
            sections.append("\n".join(lines))
    for check in failing[_MAX_FAILING_CHECKS_DETAILED:]:
        sections.append(f"### Check: {check.name}\n(details omitted)")
    return "\n\n".join(sections)


async def _check_run_details(client: httpx.AsyncClient, repo: str, check_run_id: int) -> list[str]:
    lines: list[str] = []
    try:
        run_resp = await client.get(f"/repos/{repo}/check-runs/{check_run_id}")
        run_resp.raise_for_status()
        output = run_resp.json().get("output") or {}
        if output.get("summary"):
            lines.append("Output summary:\n" + sandbox_agent.tail_bytes(str(output["summary"]), 2_000))
        if output.get("text"):
            lines.append("Output text:\n" + sandbox_agent.tail_bytes(str(output["text"]), 2_000))
    except Exception:
        logger.debug("check-run output fetch failed for %s#%s", repo, check_run_id, exc_info=True)
    try:
        ann_resp = await client.get(f"/repos/{repo}/check-runs/{check_run_id}/annotations")
        ann_resp.raise_for_status()
        annotations = ann_resp.json()[:_MAX_ANNOTATIONS_PER_CHECK]
        if annotations:
            annotation_lines = (
                f"- {a.get('path')}:{a.get('start_line')} [{a.get('annotation_level')}] {a.get('message', '')[:500]}"
                for a in annotations
            )
            lines.append("Annotations:\n" + "\n".join(annotation_lines))
    except Exception:
        logger.debug("annotations fetch failed for %s#%s", repo, check_run_id, exc_info=True)
    try:
        # For GitHub Actions the job id equals the check-run id; other check
        # providers 404 here, which the except swallows.
        log_resp = await client.get(f"/repos/{repo}/actions/jobs/{check_run_id}/logs")
        log_resp.raise_for_status()
        lines.append("Log tail:\n" + sandbox_agent.tail_bytes(log_resp.text, _LOG_TAIL_BYTES_PER_CHECK))
    except Exception:
        logger.debug("job log fetch failed for %s#%s", repo, check_run_id, exc_info=True)
    return lines


async def post_pr_comment(repo: str, pr_number: int, body: str) -> str:
    """Post a PR comment; returns the comment's html_url."""
    async with _client() as client:
        resp = await client.post(f"/repos/{repo}/issues/{pr_number}/comments", json={"body": body})
        resp.raise_for_status()
        return str(resp.json().get("html_url", ""))
