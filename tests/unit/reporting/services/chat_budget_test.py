import asyncio
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage

from reporting.services.chat_budget import BudgetController, BudgetExceeded, estimate_tokens, usage_cost_usd


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


async def test_parallel_reserve_calls_cannot_exceed_hard_call_limit():
    controller = BudgetController(_ledger(token_limit=0, reserve_tokens=0, max_llm_calls=2, reserve_llm_calls=0))

    results = await asyncio.gather(
        *[
            controller.reserve(
                estimated_input_tokens=0,
                estimated_output_tokens=0,
                allow_reserve=True,
            )
            for _ in range(3)
        ],
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 2
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


def test_set_estimated_remaining_tokens_early_return_when_no_limit():
    controller = BudgetController({**_ledger(), "token_limit": 0, "mode": "normal"})

    controller.set_estimated_remaining_tokens(500)

    # No limit → the early-return path; mode stays "normal".
    assert controller.mode == "normal"
    assert controller.snapshot()["estimated_remaining_tokens"] == 500


async def test_authorize_locked_skips_all_checks_when_disabled():
    ledger = {**_ledger(), "enabled": False}
    controller = BudgetController(ledger)

    reservation = await controller.reserve(estimated_input_tokens=9999, estimated_output_tokens=9999)

    assert reservation is not None


async def test_authorize_locked_raises_when_finalizing_and_no_allow_reserve():
    ledger = {**_ledger(token_limit=0), "mode": "finalizing", "exhaustion_reason": "exhausted"}
    controller = BudgetController(ledger)

    with pytest.raises(BudgetExceeded, match="exhausted"):
        await controller.reserve(estimated_input_tokens=0, estimated_output_tokens=0)


async def test_token_exhaustion_marks_exhausted():
    controller = BudgetController(_ledger(token_limit=50, reserve_tokens=0))
    reservation = await controller.reserve(estimated_input_tokens=50, estimated_output_tokens=0, allow_reserve=True)
    await controller.commit(reservation, input_tokens=50, output_tokens=0, cost_usd=0.0, usage_estimated=False)

    assert controller.mode == "exhausted"
    assert "token budget" in (controller.snapshot()["exhaustion_reason"] or "")


async def test_cost_exhaustion_marks_exhausted():
    ledger = {**_ledger(token_limit=0, reserve_tokens=0), "cost_limit_usd": 1.0, "reserve_cost_usd": 0.0}
    controller = BudgetController(ledger)
    reservation = await controller.reserve(
        estimated_input_tokens=0, estimated_output_tokens=0, estimated_cost_usd=1.0, allow_reserve=True
    )
    await controller.commit(reservation, input_tokens=0, output_tokens=0, cost_usd=1.0, usage_estimated=False)

    assert controller.mode == "exhausted"
    assert "cost budget" in (controller.snapshot()["exhaustion_reason"] or "")


def test_estimate_tokens_with_model_name(mocker):
    mock_model = MagicMock()
    mock_model.model_name = "anthropic/claude-sonnet-4-6"
    mocker.patch("litellm.token_counter", return_value=42)

    result = estimate_tokens(mock_model, "system prompt", [HumanMessage(content="hi")], [])

    assert result == 42


def test_estimate_tokens_falls_back_on_litellm_error(mocker):
    mock_model = MagicMock()
    mock_model.model_name = "unknown-model"
    mocker.patch("litellm.token_counter", side_effect=Exception("no pricing data"))

    result = estimate_tokens(mock_model, "system", [HumanMessage(content="hello world")], [])

    assert result >= 1


def test_usage_cost_usd_with_model_name(mocker):
    mock_model = MagicMock()
    mock_model.model_name = "anthropic/claude-sonnet-4-6"
    mocker.patch("litellm.cost_per_token", return_value=(0.001, 0.002))

    result = usage_cost_usd(mock_model, input_tokens=100, output_tokens=50)

    assert result == pytest.approx(0.003)


def test_usage_cost_usd_falls_back_on_litellm_error(mocker):
    mock_model = MagicMock()
    mock_model.model_name = "unknown-model"
    mocker.patch("litellm.cost_per_token", side_effect=Exception("no pricing data"))

    result = usage_cost_usd(mock_model, input_tokens=100, output_tokens=50)

    assert result == 0.0
