"""Show the plan a request produces, without running it.

The planner is the only thing that decides a turn's *shape*, and shape is what
decides whether anything can run in parallel: the orchestrator's concurrency --
in-process or distributed (AGT-018) -- operates on independent plan steps, so a
request that plans one step has no parallelism available to it no matter how
much work that step then does. Measuring that through ``chat_harness`` costs a
full turn (measured: 553 seconds and ~$0.20) to learn one integer.

This runs the planner node alone. One LLM call, no tools, no sandbox, nothing
executed -- so plan shaping can be iterated on in seconds.

    docker compose exec -T seizu uv run --frozen --no-sync \\
        python -m scripts.plan_probe --repeat 3 "your request here"

``--repeat`` matters more than it looks: plan shape varies between identical
calls, and a single sample of a planner is exactly the kind of evidence that has
produced "clean separations" here before which did not survive more samples.

Reports whether each plan came from the model or from ``planner_node``'s
single-step fallback, which is otherwise invisible: the fallback's step carries
the user's whole request as its goal, so it looks like a deliberate one-step
plan in every artifact downstream of it.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import User

# The exact success_criteria planner_node's fallback stamps on its single step.
# Matching on it is what separates "the model planned one step" from "the model
# returned nothing usable", which no other artifact records.
_FALLBACK_CRITERIA = "Answers the user's request."


def _dev_user() -> CurrentUser:
    """A synthetic caller with every permission.

    The probe reads the skill and tool catalogue to build the planner's
    capability context, and a narrower caller would show the planner a smaller
    catalogue than a real turn does -- changing the thing being measured.
    """
    now = "1970-01-01T00:00:00+00:00"
    return CurrentUser(
        user=User(user_id="plan-probe", sub="plan-probe", iss="dev", created_at=now, last_login=now),
        jwt_claims={},
        permissions=ALL_PERMISSIONS,
    )


async def plan_for(message: str, history: list[str]) -> dict[str, Any]:
    from reporting.services import chat_graph, chat_orchestrator
    from reporting.services.chat_budget import BudgetController, initial_budget_ledger

    # The planner emits its plan as a stream detail. Outside a graph run there
    # is no writer to emit to, and the plan comes back in the node's return
    # value anyway.
    chat_orchestrator.get_stream_writer = lambda: lambda *_args, **_kwargs: None  # type: ignore[assignment]

    user = _dev_user()
    controller = BudgetController(initial_budget_ledger())
    config = chat_graph.build_turn_config(user, "plan-probe", budget_controller=controller)
    messages = [HumanMessage(content=text) for text in [*history, message]]
    update = await chat_orchestrator.planner_node({"messages": messages}, config)  # type: ignore[arg-type]
    return {"plan": update.get("plan") or [], "run_errors": update.get("run_errors") or []}


def render(index: int, result: dict[str, Any]) -> int:
    from reporting.services import chat_orchestrator

    plan = result["plan"]
    fallback = len(plan) == 1 and plan[0].get("success_criteria") == _FALLBACK_CRITERIA
    batches = _independent_batches(plan)
    print(f"\n--- sample {index}: {len(plan)} step(s){'  [FALLBACK, not a real plan]' if fallback else ''}")
    if result["run_errors"]:
        # Where an invalid graph shows up: the planner validates the DAG it is
        # given, replans once, and reports what it had to repair (AGT-020).
        print(f"    run_errors: {result['run_errors']}")
    # The plan reaching here is always a DAG; what varies is its depth, which is
    # what each step's budget slice is now divided by.
    print(f"    dispatch waves: {chat_orchestrator._remaining_waves(plan)}")
    for step in plan:
        print(
            f"    {step['id']}  deps={step.get('depends_on') or []}  "
            f"kind={step.get('action_kind')}  action={step.get('required_action') or '-'}"
        )
        print(f"        goal: {str(step.get('goal'))[:140]}")
        if step.get("required_arguments"):
            print(f"        args: {json.dumps(step['required_arguments'])[:140]}")
    print(f"    widest independent batch: {batches}")
    return batches


def _independent_batches(plan: list[dict[str, Any]]) -> int:
    """The largest number of steps that could ever run at once.

    This, not the step count, is what parallelism is available to: a plan of
    five steps in a dependency chain runs one at a time. Computed the way the
    dispatcher does it -- steps whose dependencies are all satisfied become
    runnable together (``_runnable_steps``).
    """
    done: set[str] = set()
    remaining = {step["id"]: set(step.get("depends_on") or []) for step in plan}
    widest = 0
    while remaining:
        ready = [step_id for step_id, deps in remaining.items() if deps <= done]
        if not ready:
            break  # an unsatisfiable dependency; the dispatcher stops here too
        widest = max(widest, len(ready))
        done.update(ready)
        for step_id in ready:
            remaining.pop(step_id)
    return widest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="?", help="The request to plan")
    parser.add_argument("--prompts", type=Path, help="File of prompts; the last is planned, earlier ones are history")
    parser.add_argument("--repeat", type=int, default=1, help="Plan the same request N times (shape varies)")
    args = parser.parse_args()

    if args.prompts:
        lines = [line.strip() for line in args.prompts.read_text().splitlines()]
        prompts = [line for line in lines if line and not line.startswith("#")]
        if not prompts:
            parser.error(f"{args.prompts} contains no prompts")
        history, message = prompts[:-1], prompts[-1]
    elif args.message:
        history, message = [], args.message
    else:
        parser.error("give a message or --prompts")

    async def run_all() -> list[int]:
        from reporting.services import report_store

        # Once, not per sample: the store's engine binds to the loop that
        # created it, so a fresh asyncio.run() per sample leaves every later
        # sample talking to a closed loop.
        await report_store.initialize()
        return [render(index, await plan_for(message, history)) for index in range(1, max(1, args.repeat) + 1)]

    widths = asyncio.run(run_all())
    print(f"\nwidest independent batch across {len(widths)} sample(s): {widths}")
    # Two or more is the whole question: below that the dispatcher has one step
    # to run and every concurrency setting in the system is inert.
    print("fan-out possible" if max(widths) >= 2 else "NO fan-out possible: every batch is a single step")
    return 0


if __name__ == "__main__":
    sys.exit(main())
