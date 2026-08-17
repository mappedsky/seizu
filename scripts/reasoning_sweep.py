"""Sweep a stage's reasoning effort and report whether lowering it costs anything.

Reasoning is not free and it is not uniformly useful. On these providers the
thinking and the answer come out of one allowance (AGT-019), so effort trades
directly against both latency and the room left to answer -- and the stages of
the chat loop want opposite things from it. Decomposition and judgment are what
reasoning is *for*; a binary classifier and a report-writer only lose allowance
to it.

That is an empirical claim per stage and per model, so this measures it rather
than asserting it. Two stages are cheap enough to sweep directly, and they are
the two whose failure is worst:

* ``router`` -- one structured call per turn, on **every** turn, and a failure
  degrades silently to the single-agent path, bypassing plan and verify
  entirely. Scored on whether it still routes correctly.
* ``planner`` -- decides the plan's shape, and therefore whether any parallelism
  exists at all (AGT-018). Scored on the widest independent batch, and on
  whether the plan was real or ``planner_node``'s single-step fallback.

The worker, verifier and synthesizer need real step results to judge, so they
belong in ``chat_harness`` with per-role arms rather than here.

    docker compose exec -T seizu uv run --frozen --no-sync \\
        python -m scripts.reasoning_sweep --stage router --efforts "" minimal low

Every arm runs the same cases the same number of times. Report medians and
ranges, never a single sample: answer quality on an unchanged configuration here
has been observed to vary several-fold.
"""

import argparse
import asyncio
import statistics
import sys
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from scripts.plan_probe import _dev_user, _independent_batches

#: Requests whose correct route is not in question, so a disagreement is the
#: router getting it wrong rather than the case being ambiguous.
ROUTER_CASES: list[tuple[str, str]] = [
    ("hello", "simple"),
    ("what does CVE-2015-9251 affect?", "simple"),
    ("how many repositories are in the graph?", "simple"),
    ("what do you mean by 'reachable'?", "simple"),
    (
        "Find the highest-severity CVEs in the mappedsky org, work out which are actually "
        "installed, then trace which repositories they expose and rank them by fix priority.",
        "orchestrate",
    ),
    (
        "Audit GitHub security across the whole organization and produce a prioritized remediation plan with owners.",
        "orchestrate",
    ),
    (
        "Review the org's security posture, choose the highest-risk remotely exploitable "
        "CVE, then trace its attack paths.",
        "orchestrate",
    ),
]

PLANNER_CASE = (
    "Investigate these four vulnerabilities in the mappedsky organization, treating each one as "
    "a separate and independent piece of work: CVE-2011-4969, CVE-2015-9251, CVE-2016-9639, "
    "CVE-2017-12791. For each, tell me its severity and CVSS, which repositories and packages "
    "carry it, and whether the vulnerable version is actually installed."
)


async def _route_once(message: str, effort: str) -> tuple[str, float, int]:
    from reporting.services import chat_graph, chat_models, chat_orchestrator
    from reporting.services.chat_budget import BudgetController, initial_budget_ledger

    user = _dev_user()
    controller = BudgetController(initial_budget_ledger())
    config = chat_graph.build_turn_config(user, "reasoning-sweep", budget_controller=controller)
    spec = chat_models.resolve("router", reasoning_effort=effort)
    model = chat_graph.build_chat_model(spec)
    started = time.monotonic()
    decision = await chat_graph._invoke_structured_output(
        model,
        chat_orchestrator._RouteDecision,
        [SystemMessage(content=chat_orchestrator._ROUTER_PROMPT), HumanMessage(content=message)],
        config,
        phase="router",
        max_output_tokens=spec.max_output_tokens,
    )
    elapsed = time.monotonic() - started
    # Output tokens, not wall-clock: reasoning is billed and emitted, while
    # latency here is dominated by network variance wide enough to swamp it.
    return str(getattr(decision, "route", "")), elapsed, int(controller.usage_report().get("output_tokens") or 0)


async def sweep_router(efforts: list[str], repeat: int) -> None:
    for effort in efforts:
        correct = 0
        total = 0
        failures: list[str] = []
        durations: list[float] = []
        outputs: list[int] = []
        for _ in range(repeat):
            for message, expected in ROUTER_CASES:
                total += 1
                try:
                    route, elapsed, output_tokens = await _route_once(message, effort)
                except Exception as exc:
                    # A router exception is not a neutral outcome: chat_orchestrator
                    # degrades to the single-agent path, so this *is* a wrong route.
                    failures.append(f"{type(exc).__name__}: {exc}"[:90])
                    continue
                durations.append(elapsed)
                outputs.append(output_tokens)
                if route == expected:
                    correct += 1
                else:
                    failures.append(f"{message[:44]!r} -> {route} (want {expected})")
        median_out = int(statistics.median(outputs)) if outputs else 0
        _report(
            f"router effort={effort or 'provider default'}",
            correct,
            total,
            durations,
            failures,
            extra=f"median_output_tokens={median_out}",
        )


async def sweep_planner(efforts: list[str], repeat: int) -> None:
    from reporting.services import chat_orchestrator, report_store

    await report_store.initialize()
    chat_orchestrator.get_stream_writer = lambda: lambda *_a, **_k: None  # type: ignore[assignment]
    for effort in efforts:
        widths: list[int] = []
        durations: list[float] = []
        outputs: list[int] = []
        failures: list[str] = []
        for _ in range(repeat):
            result = await _plan_once(effort)
            durations.append(result["seconds"])
            widths.append(result["width"])
            outputs.append(result["output_tokens"])
            if result["fallback"]:
                failures.append("FALLBACK: " + result["error"][:80])
        usable = sum(1 for width in widths if width >= 2)
        median_out = int(statistics.median(outputs)) if outputs else 0
        _report(
            f"planner effort={effort or 'provider default'}",
            usable,
            len(widths),
            durations,
            failures,
            extra=f"widths={widths}  median_output_tokens={median_out}  outputs={outputs}",
        )


async def _plan_once(effort: str) -> dict[str, Any]:
    from reporting.services import chat_graph, chat_models, chat_orchestrator
    from reporting.services.chat_budget import BudgetController, initial_budget_ledger

    spec = chat_models.resolve("planner", reasoning_effort=effort)
    # Patched rather than parameterized: planner_node resolves its own model, and
    # the point of the sweep is to vary exactly that one input.
    original = chat_orchestrator.get_chat_model
    chat_orchestrator.get_chat_model = lambda *_a, **_k: chat_graph.build_chat_model(spec)  # type: ignore[assignment]
    controller = BudgetController(initial_budget_ledger())
    try:
        config = chat_graph.build_turn_config(_dev_user(), "reasoning-sweep", budget_controller=controller)
        started = time.monotonic()
        update = await chat_orchestrator.planner_node({"messages": [HumanMessage(content=PLANNER_CASE)]}, config)  # type: ignore[arg-type]
        elapsed = time.monotonic() - started
    finally:
        chat_orchestrator.get_chat_model = original  # type: ignore[assignment]
    plan = update.get("plan") or []
    errors = update.get("run_errors") or []
    fallback = len(plan) == 1 and plan[0].get("success_criteria") == "Answers the user's request."
    usage = controller.usage_report()
    return {
        "width": _independent_batches(plan),
        "seconds": elapsed,
        # Output tokens are the metric reasoning actually moves, and unlike
        # wall-clock they are not dominated by network variance -- which at these
        # sample sizes swamped every latency difference between arms.
        "output_tokens": int(usage.get("output_tokens") or 0),
        "fallback": fallback,
        "error": "; ".join(str(e) for e in errors),
    }


def _report(label: str, good: int, total: int, durations: list[float], failures: list[str], extra: str = "") -> None:
    median = statistics.median(durations) if durations else 0.0
    spread = f"{min(durations):.1f}-{max(durations):.1f}s" if durations else "-"
    print(f"\n{label}")
    print(f"  ok {good}/{total}   median {median:.1f}s   range {spread}   {extra}")
    for failure in failures[:6]:
        print(f"    ! {failure}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("router", "planner"), required=True)
    parser.add_argument(
        "--efforts",
        nargs="+",
        default=["", "minimal", "low"],
        help='Effort levels to compare; "" is the provider default',
    )
    parser.add_argument("--repeat", type=int, default=2, help="Passes over the case set per arm")
    args = parser.parse_args()

    sweep = sweep_router if args.stage == "router" else sweep_planner
    asyncio.run(sweep(args.efforts, max(1, args.repeat)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
