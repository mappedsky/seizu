import asyncio

import pytest

from reporting.services.chat_budget import BudgetController, BudgetExceeded


def _ledger(
    *,
    token_limit: int = 100,
    reserve_tokens: int = 20,
    max_llm_calls: int = 10,
    reserve_llm_calls: int = 2,
) -> dict[str, object]:
    return {
        "enabled": True,
        "token_limit": token_limit,
        "cost_limit_usd": 0.0,
        "reserve_tokens": reserve_tokens,
        "reserve_cost_usd": 0.0,
        "soft_limit_ratio": 0.75,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "llm_calls": 0,
        "max_llm_calls": max_llm_calls,
        "reserve_llm_calls": reserve_llm_calls,
        "usage_estimated": False,
        "mode": "normal",
        "exhaustion_reason": None,
        "estimated_remaining_tokens": 0,
    }


async def test_token_reserve_is_only_available_for_finalization():
    controller = BudgetController(_ledger())
    reservation = await controller.reserve(
        estimated_input_tokens=60,
        estimated_output_tokens=20,
        phase="worker:s1",
    )
    await controller.commit(
        reservation,
        input_tokens=60,
        output_tokens=20,
        cost_usd=0.0,
        usage_estimated=False,
    )

    with pytest.raises(BudgetExceeded, match="reserved for final synthesis"):
        await controller.reserve(estimated_input_tokens=1, estimated_output_tokens=1)

    final_reservation = await controller.reserve(
        estimated_input_tokens=5,
        estimated_output_tokens=10,
        allow_reserve=True,
    )
    await controller.commit(
        final_reservation,
        input_tokens=5,
        output_tokens=10,
        cost_usd=0.0,
        usage_estimated=False,
    )

    assert controller.snapshot()["total_tokens"] == 95
    assert controller.snapshot()["phases"]["worker:s1"]["total_tokens"] == 80
    assert controller.mode == "finalizing"


async def test_parallel_reservations_cannot_oversubscribe_budget():
    controller = BudgetController(_ledger(reserve_tokens=0))

    results = await asyncio.gather(
        controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20),
        controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, BudgetExceeded) for result in results) == 1


async def test_llm_call_reserve_is_protected_for_finalization():
    controller = BudgetController(_ledger(token_limit=0, reserve_tokens=0, max_llm_calls=5, reserve_llm_calls=2))
    for _ in range(3):
        reservation = await controller.reserve(estimated_input_tokens=0, estimated_output_tokens=0)
        await controller.commit(
            reservation,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            usage_estimated=False,
        )

    with pytest.raises(BudgetExceeded, match="LLM-call safety limit"):
        await controller.reserve(estimated_input_tokens=0, estimated_output_tokens=0)

    for _ in range(2):
        reservation = await controller.reserve(
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            allow_reserve=True,
        )
        await controller.commit(
            reservation,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            usage_estimated=False,
        )

    assert controller.snapshot()["llm_calls"] == 5
    assert controller.mode == "exhausted"


async def test_estimated_cost_reservations_protect_cost_reserve():
    ledger = _ledger(token_limit=0, reserve_tokens=0)
    ledger.update({"cost_limit_usd": 1.0, "reserve_cost_usd": 0.2})
    controller = BudgetController(ledger)

    reservation = await controller.reserve(
        estimated_input_tokens=0,
        estimated_output_tokens=0,
        estimated_cost_usd=0.8,
    )
    await controller.commit(
        reservation,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.8,
        usage_estimated=False,
    )

    with pytest.raises(BudgetExceeded, match="cost budget"):
        await controller.reserve(
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            estimated_cost_usd=0.01,
        )

    final_reservation = await controller.reserve(
        estimated_input_tokens=0,
        estimated_output_tokens=0,
        estimated_cost_usd=0.1,
        allow_reserve=True,
    )
    await controller.release(final_reservation)


def test_remaining_plan_estimate_triggers_early_degradation():
    controller = BudgetController(_ledger())

    controller.set_estimated_remaining_tokens(81)

    assert controller.mode == "degraded"
    assert controller.snapshot()["estimated_remaining_tokens"] == 81
