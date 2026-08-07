"""Multi-sample A/B harness for chat agent settings.

Runs the same two-turn conversation N times per arm against a live backend and
reports medians and ranges, so a comparison rests on more than one sample.

    make chat_harness ARMS="baseline CHAT_EPISODIC_RECALL_MAX_CHARS=0" SAMPLES=4

Each arm is ``KEY=VALUE`` for any setting in ``reporting.settings``, or the
literal ``baseline`` for no override. An arm is applied by recreating the
``seizu`` service with a compose overlay, and the value is then read back out of
the running container before any sample is taken -- an override that silently
fails to reach the service is otherwise indistinguishable from one that had no
effect.

**Why this exists.** Answer quality on the same configuration has been observed
to vary several-fold between runs. Single-run comparisons of this system have
repeatedly produced "clean separations" that did not survive more samples, and
several conclusions were drawn from them before that was understood. Four
settings have since been swept without a distinguishable difference between any
of them, which is itself the useful result: it says the tuning surface is not
where the remaining problems are.

**Counting.** Delegations and inner tool calls are de-duplicated on
``detail_id``. A subagent detail is re-emitted after *every* inner tool call,
each time carrying the whole children list so the UI can reconcile one growing
section, so counting stream events instead inflates a delegation once per
emission and its Nth child N times over -- quadratic in the size of a
delegation, and therefore distorting comparisons between configurations
non-linearly. A run measured that way as 121 delegations and 1,726 queries was
3 and 54.

Token totals, budget mode and step outcomes are read from the persisted ledger
rather than inferred from the stream, so they are unaffected by any of that.

Requires the dev stack running (``make up``) with ``CHAT_ENABLED=true`` and a
real ``CHAT_LLM_PROVIDER``. Sessions are left in place for inspection.

**Do not edit backend code while a run is in progress.** The dev ``seizu``
service runs gunicorn with ``--reload``, so a save part-way through an arm means
its samples were not all taken against the same build -- and the arm labels then
describe a configuration rather than a build, which is exactly the kind of
silently-invented result the read-back check above exists to prevent.

Runs on the host rather than in a container -- it recreates the ``seizu``
service between arms, which it could not do from inside that service -- and uses
only the standard library so the host needs no project environment.
"""

import argparse
import ast
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OVERLAY = REPO / "chat-harness-arm.yml"
API = "http://localhost:8080"

# A conversation, not a list of unrelated questions: each turn refers back, so
# the history actually has to be carried. Cycled when more turns are asked for
# than there are prompts, which is how a run long enough to trigger history
# compaction is built without inventing forty distinct security questions.
CONVERSATION = [
    "Give me a security overview of the most critical vulnerabilities in the graph",
    "Now cross-check that against the actual CVE data in the graph and tell me "
    "which of those findings are actually reachable from the internet",
    "Which repositories are most exposed by those, and why?",
    "Summarise what we have established so far in five bullet points",
    "Of everything discussed, what would you fix first and what would you defer?",
    "Which of the findings so far depend on data that might be stale?",
    "Group the findings by the team that would own the fix",
    "What have we not looked at yet that you would expect to matter?",
]


def conversation_turns(count: int) -> list[str]:
    """The first *count* prompts, cycling once the written ones run out."""
    return [CONVERSATION[index % len(CONVERSATION)] for index in range(max(1, count))]


_ARM_RE = re.compile(r"^[A-Z][A-Z0-9_]*=[^\s]*$")


def _post(path: str, payload: dict[str, Any], *, stream: bool = False) -> str:
    request = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Seizu-Csrf": "1"},
    )
    # No timeout: a turn legitimately runs for many minutes.
    with urllib.request.urlopen(request) as response:  # noqa: S310 - localhost dev stack
        return response.read().decode(errors="replace") if stream else response.read().decode()


class HarnessError(RuntimeError):
    """A run could not be trusted, so it must not be recorded as a sample."""


def _compose(*args: str, capture: bool = False, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )
    if result.returncode != 0:
        # Never soft-fail. A compose failure leaves the previous arm's service
        # running and healthy, so every sample that follows is recorded under a
        # label describing a configuration that was never applied.
        raise HarnessError(f"docker compose {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout if capture else ""


def _compose_file_chain() -> str:
    """The checkout's own compose files, with the arm overlay appended.

    Naming only ``docker-compose.yml`` here replaces whatever file chain the
    checkout uses rather than adding to it. A ``.env`` that selects an overlay
    -- ``COMPOSE_FILE=docker-compose.yml:docker-compose.neo4j-latest.yml``, for
    instance -- would be dropped, so every arm silently recreated *those*
    services too, under a different configuration, and the run failed on a
    dependency that was still starting.
    """
    existing = os.environ.get("COMPOSE_FILE") or _dotenv_value("COMPOSE_FILE") or "docker-compose.yml"
    return f"{existing}:{OVERLAY.name}"


def _dotenv_value(key: str) -> str:
    """Read one key out of the checkout's .env, which compose reads but we don't."""
    env_file = REPO / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == key:
            return value.strip().strip("'\"")
    return ""


def apply_arm(arm: str) -> str:
    """Recreate the backend with this arm applied, and read the value back."""
    if arm == "baseline":
        OVERLAY.write_text("services:\n  seizu:\n    environment: []\n")
    else:
        OVERLAY.write_text(f"services:\n  seizu:\n    environment:\n      - {arm}\n")
    _compose("up", "-d", "--force-recreate", "seizu", env={"COMPOSE_FILE": _compose_file_chain()})
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{API}/healthcheck", timeout=5)  # noqa: S310
            break
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    else:
        raise HarnessError("backend did not become healthy after recreating it")
    if arm == "baseline":
        return "baseline"
    key, _, expected = arm.partition("=")
    out = _compose(
        "exec",
        "-T",
        "seizu",
        "uv",
        "run",
        "--frozen",
        "--no-sync",
        "python",
        "-c",
        f"from reporting import settings; print(settings.{key})",
        capture=True,
    )
    applied = out.strip().splitlines()[-1] if out.strip() else ""
    # Compare, do not merely print. An override that never reaches the service
    # leaves it running the default, which measures perfectly well and is then
    # recorded under the experimental arm's label -- a silently invented result.
    # Settings are typed, so compare loosely enough that "2000" matches 2000.
    if not _same_value(applied, expected):
        raise HarnessError(f"arm {arm} did not apply: {key} is {applied!r}, expected {expected!r}")
    return applied


def _same_value(applied: str, expected: str) -> bool:
    """Whether a read-back setting matches what the arm asked for.

    Settings are typed, so the string that comes back is a repr, not the input:
    "2000" arrives as 2000, and a lowercase "true" arrives as "True". Comparing
    strings alone rejects perfectly ordinary arms.
    """
    if applied == expected:
        return True
    # Booleans only. Comparing every string case-insensitively would accept a
    # model identifier that differs in case from the one asked for, which is a
    # different configuration wearing the right label.
    booleans = {"true": True, "false": False}
    left, right = booleans.get(applied.strip().lower()), booleans.get(expected.strip().lower())
    if left is not None and right is not None:
        return left is right
    # List settings (SANDBOX_CORE_TOOLS, MCP_ENABLED_BUILTINS, ...) come back as
    # a repr, so a comma-separated arm never matched and every one of them was
    # rejected as "did not apply" -- including the empty value, which reads back
    # as "[]" and is the whole point of arming a list at all.
    if applied.startswith("[") and applied.endswith("]"):
        try:
            actual = [str(item) for item in ast.literal_eval(applied)]
        except (ValueError, SyntaxError):
            return False
        wanted = [part.strip() for part in expected.split(",") if part.strip()]
        return actual == wanted
    try:
        return float(applied) == float(expected)
    except ValueError:
        return False


def stream_metrics(sse: str) -> dict[str, Any]:
    """Metrics from one turn's SSE stream, counting distinct calls."""
    delegations: set[str] = set()
    children: dict[str, str] = {}
    answer: list[str] = []
    steps: dict[str, str] = {}
    for line in sse.splitlines():
        if not line.startswith("data: ") or line.strip().endswith("[DONE]"):
            continue
        try:
            event = json.loads(line[6:])
        except ValueError:
            continue
        kind = event.get("type", "")
        if kind == "text-delta":
            answer.append(event.get("delta", ""))
        elif kind.startswith("data-seizu-detail"):
            data = event.get("data", {})
            if data.get("kind") == "subagent":
                delegations.add(str(data.get("detail_id")))
                for child in data.get("children") or []:
                    if child.get("detail_id"):
                        children[child["detail_id"]] = child.get("title", "?")
            elif data.get("kind") in ("step", "verify"):
                steps[f"{data['kind']}:{data.get('step_id')}"] = str(data.get("status"))
    titles = list(children.values())
    verifies = {k: v for k, v in steps.items() if k.startswith("verify:")}
    return {
        "delegations": len(delegations),
        "inner_calls": len(children),
        "queries": titles.count("Sandbox: graph__query"),
        "run_python": titles.count("Sandbox: run_python"),
        "answer_chars": len("".join(answer)),
        "steps_failed": sum(1 for v in verifies.values() if v != "completed"),
        "steps_total": len(verifies),
    }


def ledger(thread_id: str, user_id: str) -> dict[str, Any]:
    """Every turn's persisted budget -- the only trustworthy token source.

    Summed across the conversation rather than read off the last turn: what a
    long session costs is the whole thing, and a per-turn figure hides the fact
    that early turns are cheap and later ones carry the accumulated history.
    ``history_summary`` comes back too, because its presence is the direct
    evidence that compaction engaged at all.
    """
    script = (
        "import asyncio, json\n"
        "from reporting.services import chat_graph\n"
        "async def main():\n"
        "    await chat_graph.initialize_chat_checkpoints()\n"
        "    graph = chat_graph.get_chat_graph()\n"
        f"    key = 'user:{user_id}:thread:{thread_id}'\n"
        "    state = await graph.aget_state({'configurable': {'thread_id': key}})\n"
        "    values = getattr(state, 'values', {}) or {}\n"
        "    budgets = [\n"
        "        b for m in values.get('messages', [])\n"
        "        if (b := (getattr(m, 'response_metadata', {}) or {}).get('seizu_budget'))\n"
        "    ]\n"
        "    summary = (values.get('history_summary') or {}).get('text') or ''\n"
        "    print('LEDGER' + json.dumps({'budgets': budgets, 'summary_chars': len(summary)}))\n"
        "asyncio.run(main())"
    )
    out = _compose("exec", "-T", "seizu", "uv", "run", "--frozen", "--no-sync", "python", "-c", script, capture=True)
    for line in out.splitlines():
        if not line.startswith("LEDGER"):
            continue
        payload = json.loads(line[len("LEDGER") :])
        budgets = payload.get("budgets") or []
        if not budgets:
            break

        def total(key: str) -> int:
            return sum(int(b.get(key) or 0) for b in budgets)

        input_tokens = total("input_tokens")
        cache_read = total("cache_read_tokens")
        return {
            "total_tokens": total("total_tokens"),
            "llm_calls": total("llm_calls"),
            # The last turn's mode: whether the conversation ended able to spend.
            "mode": budgets[-1].get("mode", ""),
            "sandbox_tokens": sum(
                v.get("total_tokens", 0)
                for b in budgets
                for k, v in (b.get("phases") or {}).items()
                if k.endswith("sandbox_subagent")
            ),
            # Prompt caching is most of what a conversation's *cost* depends on,
            # and it is invisible in a token total: cached input is billed at a
            # fraction of fresh input, so two runs with identical token counts
            # can differ severalfold in spend.
            "cache_read_tokens": cache_read,
            "cache_hit_pct": round(cache_read / input_tokens * 100) if input_tokens else 0,
            "cost_usd": round(sum(float(b.get("cost_usd") or 0.0) for b in budgets), 4),
            # Non-zero means history compaction engaged.
            "summary_chars": int(payload.get("summary_chars") or 0),
        }
    return {
        "total_tokens": 0,
        "llm_calls": 0,
        "mode": "?",
        "sandbox_tokens": 0,
        "cache_read_tokens": 0,
        "cache_hit_pct": 0,
        "cost_usd": 0.0,
        "summary_chars": 0,
    }


def run_sample(arm: str, index: int, user_id: str, out_dir: Path, turns: int = 2) -> dict[str, Any]:
    session = json.loads(_post("/api/v1/chat/sessions", {"title": f"harness {arm} #{index}"}))
    thread_id = session["thread_id"]
    started = time.time()
    prompts = conversation_turns(turns)
    sse = ""
    for turn, prompt in enumerate(prompts, start=1):
        sse = _post("/api/v1/chat/stream", {"thread_id": thread_id, "message": prompt}, stream=True)
        print(f"    turn {turn}/{len(prompts)} done ({int(time.time() - started)}s)", flush=True)
    # Hash the arm rather than embedding it: an ordinary value such as
    # "openai/gpt-4" carries a path separator and would write outside out_dir.
    slug = "baseline" if arm == "baseline" else hashlib.sha256(arm.encode()).hexdigest()[:10]
    # The last turn's stream: the deepest context the conversation reached, and
    # the one a context change is most likely to show up in.
    (out_dir / f"{slug}_{index}.sse").write_text(sse)
    row = {
        "arm": arm,
        "slug": slug,
        "sample": index,
        "thread": thread_id,
        "turns": len(prompts),
        "seconds": int(time.time() - started),
    }
    row.update(stream_metrics(sse))
    row.update(ledger(thread_id, user_id))
    return row


def summarize(rows: list[dict[str, Any]]) -> None:
    arms: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        arms.setdefault(row["arm"], []).append(row)
    keys = ("answer_chars", "total_tokens", "llm_calls", "cache_hit_pct", "cost_usd", "summary_chars")
    print(f"\n{'arm':38} {'n':>2}  " + "  ".join(f"{k:>22}" for k in keys))
    for arm, samples in arms.items():
        cells = []
        for key in keys:
            if key == "cost_usd":
                costs = sorted(float(s[key]) for s in samples)
                cells.append(f"${statistics.median(costs):.3f} [{costs[0]:.3f}-{costs[-1]:.3f}]".rjust(22))
                continue
            values = sorted(int(s[key]) for s in samples)
            cells.append(f"{int(statistics.median(values))} [{values[0]}-{values[-1]}]".rjust(22))
        print(f"{arm[:38]:38} {len(samples):>2}  " + "  ".join(cells))
    print()
    for arm, samples in arms.items():
        failed = sum(s["steps_failed"] for s in samples)
        total = sum(s["steps_total"] for s in samples)
        clean = sum(1 for s in samples if s["mode"] == "normal")
        print(f"{arm[:38]:38} steps failed {failed}/{total}   budget not exhausted {clean}/{len(samples)}")
    print("\nRanges overlapping between arms mean the setting is not distinguishable at this sample size.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument(
        "--turns",
        type=int,
        default=2,
        help="Turns per conversation. Long runs are how history compaction is reached at all.",
    )
    parser.add_argument("--arms", nargs="+", required=True, help='"baseline" or KEY=VALUE')
    parser.add_argument("--user-id", required=True, help="Seizu user id whose threads to read budgets from")
    parser.add_argument("--out", default="chat-harness-results")
    args = parser.parse_args()

    for arm in args.arms:
        if arm != "baseline" and not _ARM_RE.match(arm):
            parser.error(f"arm {arm!r} must be 'baseline' or KEY=VALUE")

    out_dir = REPO / args.out
    out_dir.mkdir(exist_ok=True)
    results = out_dir / "results.jsonl"
    results.write_text("")

    rows: list[dict[str, Any]] = []
    try:
        for arm in args.arms:
            applied = apply_arm(arm)
            print(f"ARM {arm} applied={applied}", flush=True)
            for index in range(1, args.samples + 1):
                row = run_sample(arm, index, args.user_id, out_dir, turns=args.turns)
                rows.append(row)
                with results.open("a") as handle:
                    handle.write(json.dumps(row) + "\n")
                print(json.dumps(row), flush=True)
    finally:
        OVERLAY.unlink(missing_ok=True)
        # Restore the stack to its own configuration, and say so loudly if that
        # fails: leaving the service on the last experimental arm would make
        # every later run -- by anyone, for any purpose -- quietly wrong.
        try:
            _compose("up", "-d", "--force-recreate", "seizu")
        except HarnessError as exc:
            print(f"WARNING: could not restore the baseline service: {exc}", file=sys.stderr)
            raise
    summarize(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
