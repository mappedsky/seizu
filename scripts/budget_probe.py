"""Show what a call is *authorized* for against what it actually spends.

The run budget authorizes a call against the sum of what every call in flight
has reserved, so a reservation that overstates its call is budget the run cannot
use for anything else. This reports that ratio, per phase, on real provider
calls (AGT-021).

    docker compose run --rm -T seizu uv run --frozen --no-sync \\
        python -m scripts.budget_probe

Each phase runs twice against one controller: the first call uses the cold-start
seed (``CHAT_BUDGET_OUTPUT_ESTIMATE_TOKENS``), the second is sized from what the
first emitted, so read the second row.

Measure the model you deploy. Reasoning is billed as output, so a planner on a
reasoning model emits thousands of tokens where a router verdict emits a few
dozen, against the same ceiling.
"""

import argparse
import asyncio
import sys
from typing import Any

from langchain_core.messages import HumanMessage

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import User

_DEFAULT_REQUEST = "Summarize our GitHub org's open security alerts and count the CVEs in the graph by severity."


def _dev_user() -> CurrentUser:
    """A caller with every permission, so the planner sees the real catalogue."""
    now = "1970-01-01T00:00:00+00:00"
    return CurrentUser(
        user=User(user_id="budget-probe", sub="budget-probe", iss="dev", created_at=now, last_login=now),
        jwt_claims={},
        permissions=ALL_PERMISSIONS,
    )


def _phase_output(ledger: dict[str, Any], phase: str) -> int:
    return int(((ledger.get("phases") or {}).get(phase) or {}).get("output_tokens") or 0)


async def _probe(node: Any, phase: str, *, controller: Any, config: Any, ceiling: int, request: str) -> None:
    print(f"\n{phase}: model output ceiling {ceiling}")
    for call in (1, 2):
        reserved = controller.projected_output_tokens(phase, ceiling)
        before = _phase_output(controller.snapshot(), phase)
        try:
            await node({"messages": [HumanMessage(content=request)]}, config)
        except Exception as exc:  # report the phase that could not run; do not abandon the rest
            print(f"  call {call}: failed ({exc.__class__.__name__}: {exc})")
            return
        actual = _phase_output(controller.snapshot(), phase) - before
        if actual <= 0:
            # The router degrades to the simple path without an LLM call
            # when structured output fails, and records nothing.
            print(f"  call {call}: no output recorded for this phase")
            continue
        print(
            f"  call {call}: reserved {reserved:>6}  actual {actual:>6}  "
            f"| ceiling {ceiling / actual:>5.1f}x actual, reservation {reserved / actual:>4.1f}x"
        )


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", default=_DEFAULT_REQUEST, help="The request to route and plan")
    args = parser.parse_args(argv)

    from reporting.services import chat_graph, chat_orchestrator, report_store
    from reporting.services.chat_budget import BudgetController, initial_budget_ledger
    from reporting.services.chat_context import max_output_tokens

    # No graph run, so there is no stream writer for a node to emit details to.
    chat_orchestrator.get_stream_writer = lambda: lambda *_a, **_k: None  # type: ignore[assignment]
    await report_store.initialize()

    controller = BudgetController(initial_budget_ledger())
    config = chat_graph.build_turn_config(_dev_user(), "budget-probe", budget_controller=controller)

    for node, phase in ((chat_orchestrator.router_node, "router"), (chat_orchestrator.planner_node, "planner")):
        await _probe(
            node,
            phase,
            controller=controller,
            config=config,
            ceiling=max_output_tokens(chat_graph.get_chat_model(phase)),
            request=args.request,
        )

    ledger = controller.snapshot()
    print(f"\nrun total: {ledger['total_tokens']} tokens, ${ledger['cost_usd']:.4f}, mode {controller.mode}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
