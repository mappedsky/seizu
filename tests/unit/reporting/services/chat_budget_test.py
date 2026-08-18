import asyncio
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage

from reporting.services.chat_budget import (
    BudgetController,
    BudgetExceeded,
    estimate_tokens,
    usage_cost_usd,
    usage_from_message,
)


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


async def test_parallel_reservations_cannot_oversubscribe_budget(mocker):
    # Nothing will settle here, so the second call waits out its window and then
    # fails. The invariant under test is that it never gets through.
    mocker.patch("reporting.settings.CHAT_BUDGET_CONTENTION_WAIT_SECONDS", 0.05)
    controller = BudgetController(_ledger(reserve_tokens=0))

    results = await asyncio.gather(
        controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20),
        controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, BudgetExceeded) for result in results) == 1


# --- Contention is backpressure, not exhaustion (AGT-021) ----------------------


async def test_a_call_waits_for_an_in_flight_reservation_instead_of_failing():
    """Contention waits for capacity; it never ends the run (AGT-021).

    The budget has room here -- it is held by a call that has not returned.
    """
    controller = BudgetController(_ledger(reserve_tokens=0))
    held = await controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20)

    waiter = asyncio.ensure_future(controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20))
    await asyncio.sleep(0)
    assert not waiter.done()  # blocked on capacity, not failed

    # The call returns, and its 60-token estimate settles as 10 actual tokens.
    await controller.commit(held, input_tokens=8, output_tokens=2, cost_usd=0.0, usage_estimated=False)

    assert await asyncio.wait_for(waiter, timeout=1)
    assert controller.mode == "normal"  # contention never marks the run finished


async def test_contention_does_not_finalize_the_run(mocker):
    mocker.patch("reporting.settings.CHAT_BUDGET_CONTENTION_WAIT_SECONDS", 0.05)
    controller = BudgetController(_ledger(reserve_tokens=0))
    await controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20)

    with pytest.raises(BudgetExceeded, match="in flight"):
        await controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20)

    # The step that lost the race reports what it has; the run carries on with
    # the budget it still demonstrably owns.
    assert controller.mode == "normal"
    assert controller.snapshot()["exhaustion_reason"] is None


async def test_committed_spend_still_finalizes_immediately_without_waiting():
    """The genuine case must stay fast: waiting cannot un-spend what is spent."""
    controller = BudgetController(_ledger(reserve_tokens=0))
    reservation = await controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20)
    await controller.commit(reservation, input_tokens=90, output_tokens=5, cost_usd=0.0, usage_estimated=False)

    with pytest.raises(BudgetExceeded, match="reserved for final synthesis"):
        await asyncio.wait_for(controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20), timeout=1)

    assert controller.mode in ("finalizing", "exhausted")


async def test_cost_contention_waits_like_token_contention(mocker):
    """The cost dimension is reachable by default, so it needs the same split."""
    mocker.patch("reporting.settings.CHAT_BUDGET_CONTENTION_WAIT_SECONDS", 0.05)
    ledger = _ledger(token_limit=0)
    ledger.update({"cost_limit_usd": 1.0, "reserve_cost_usd": 0.0})
    controller = BudgetController(ledger)
    held = await controller.reserve(estimated_input_tokens=1, estimated_output_tokens=1, estimated_cost_usd=0.6)

    with pytest.raises(BudgetExceeded, match="cost budget is reserved by calls still in flight"):
        await controller.reserve(estimated_input_tokens=1, estimated_output_tokens=1, estimated_cost_usd=0.6)
    assert controller.mode == "normal"

    # The reservation settles for a tenth of its estimate, and the waiter fits.
    await controller.commit(held, input_tokens=1, output_tokens=1, cost_usd=0.06, usage_estimated=False)
    assert await controller.reserve(estimated_input_tokens=1, estimated_output_tokens=1, estimated_cost_usd=0.6)


async def test_committed_cost_over_the_limit_finalizes_rather_than_waits():
    ledger = _ledger(token_limit=0)
    ledger.update({"cost_limit_usd": 1.0, "reserve_cost_usd": 0.0})
    controller = BudgetController(ledger)
    reservation = await controller.reserve(estimated_input_tokens=1, estimated_output_tokens=1, estimated_cost_usd=0.5)
    await controller.commit(reservation, input_tokens=1, output_tokens=1, cost_usd=0.99, usage_estimated=False)

    with pytest.raises(BudgetExceeded, match="cost budget is reserved for final synthesis"):
        await asyncio.wait_for(
            controller.reserve(estimated_input_tokens=1, estimated_output_tokens=1, estimated_cost_usd=0.5), timeout=1
        )
    assert controller.mode in ("finalizing", "exhausted")


async def test_zero_wait_restores_fail_fast(mocker):
    mocker.patch("reporting.settings.CHAT_BUDGET_CONTENTION_WAIT_SECONDS", 0.0)
    controller = BudgetController(_ledger(reserve_tokens=0))
    await controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20)

    with pytest.raises(BudgetExceeded):
        await asyncio.wait_for(controller.reserve(estimated_input_tokens=40, estimated_output_tokens=20), timeout=1)


# --- Reservations are sized from what calls emit, not from the ceiling --------


async def test_output_reservation_starts_at_the_seed_and_then_tracks_observation(mocker):
    mocker.patch("reporting.settings.CHAT_BUDGET_OUTPUT_ESTIMATE_TOKENS", 4_096)
    mocker.patch("reporting.settings.CHAT_BUDGET_OUTPUT_ESTIMATE_SAFETY", 1.5)
    controller = BudgetController(_ledger(token_limit=0))

    # Cold: the seed, never the model's 32,768 ceiling.
    assert controller.projected_output_tokens("worker:s1", 32_768) == 4_096

    for _ in range(6):
        reservation = await controller.reserve(
            estimated_input_tokens=10, estimated_output_tokens=100, phase="worker:s2"
        )
        await controller.commit(reservation, input_tokens=10, output_tokens=1_000, cost_usd=0.0, usage_estimated=False)

    # Converged on what workers actually emit, plus the safety multiple, and a
    # different step of the same role inherits it rather than starting cold.
    assert controller.projected_output_tokens("worker:s9", 32_768) == pytest.approx(1_500, rel=0.05)


def test_output_reservation_never_exceeds_the_models_ceiling():
    controller = BudgetController(_ledger(token_limit=0))
    assert controller.projected_output_tokens("planner", 512) == 512


async def test_output_reservation_keeps_a_floor_for_a_terse_phase():
    controller = BudgetController(_ledger(token_limit=0))
    for _ in range(8):
        reservation = await controller.reserve(estimated_input_tokens=1, estimated_output_tokens=1, phase="router")
        await controller.commit(reservation, input_tokens=1, output_tokens=3, cost_usd=0.0, usage_estimated=False)

    # An authorization that rounds to nothing authorizes nothing.
    assert controller.projected_output_tokens("router", 32_768) == 256


def test_observation_key_groups_by_role_and_sub_role_not_by_step():
    from reporting.services.chat_budget import _observation_key

    assert _observation_key("planner") == "planner"
    # Keying on the step id would give every new step a cold start, which is the
    # case this exists to fix.
    assert _observation_key("worker:s1") == _observation_key("worker:s2") == "worker"
    # A sandbox sub-agent's calls look nothing like its worker's, so they stay apart.
    assert _observation_key("worker:s1:sandbox_subagent") == "worker:sandbox_subagent"
    assert _observation_key("") == "unspecified"


async def test_absorbed_grant_totals_seed_the_estimate():
    """A distributed step reports totals; the coordinator still learns from them."""
    controller = BudgetController(_ledger(token_limit=0))
    await controller.absorb(
        {
            "input_tokens": 9_000,
            "output_tokens": 4_000,
            "llm_calls": 4,
            "phases": {"worker:s1": {"output_tokens": 4_000, "llm_calls": 4}},
        }
    )

    assert controller.projected_output_tokens("worker:s7", 32_768) == 1_500  # 1,000 mean x 1.5


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


# --- Prompt-cache accounting ---------------------------------------------------


def _model(name: str = "deepseek/deepseek-chat") -> MagicMock:
    model = MagicMock()
    model.model_name = name
    return model


def _message(usage: dict | None) -> MagicMock:
    message = MagicMock()
    message.usage_metadata = usage
    return message


def test_usage_from_message_reads_the_providers_cache_accounting():
    usage = usage_from_message(
        _message(
            {
                "input_tokens": 4016,
                "output_tokens": 50,
                "total_tokens": 4066,
                "input_token_details": {"cache_read": 3968},
            }
        )
    )

    assert (usage.input_tokens, usage.output_tokens) == (4016, 50)
    # A subset of input_tokens, not an addition to it.
    assert usage.cache_read_tokens == 3968
    assert usage.total_tokens == 4066
    assert usage.reported is True


def test_usage_from_message_tolerates_providers_that_report_nothing():
    for value in (None, {}, {"input_tokens": 0, "output_tokens": 0}, {"input_token_details": "nonsense"}):
        usage = usage_from_message(_message(value))
        assert usage.cache_read_tokens == 0
        assert usage.reported is False


def test_cached_input_is_priced_as_cached():
    """The bug: every input token was billed at the uncached rate.

    A measured DeepSeek call re-sending a 4,016-token prefix reported 3,968 of
    them as cache reads, charged at a tenth of the rate we charged ourselves.
    """
    model = _model()
    uncached = usage_cost_usd(model, 4016, 50)
    cached = usage_cost_usd(model, 4016, 50, cache_read_tokens=3968)

    assert cached < uncached
    # Input tokens stay the *total* and litellm subtracts the cached portion, so
    # the cached call must price out as 48 fresh input tokens plus 3,968 charged
    # at the cache rate -- not as either of them counted twice.
    assert cached == pytest.approx(
        usage_cost_usd(model, 48, 50) + usage_cost_usd(model, 3968, 0, cache_read_tokens=3968)
    )


def test_cache_details_are_clamped_to_the_input_they_describe():
    """A provider reporting more cached tokens than input must not negative-price
    a call: litellm subtracts the cached portion from prompt_tokens, so an
    unclamped count would credit tokens that were never sent."""
    model = _model()
    assert usage_cost_usd(model, 100, 10, cache_read_tokens=10_000) >= 0.0
    # Nothing to subtract from means nothing to discount.
    assert usage_cost_usd(model, 0, 0, cache_read_tokens=3968) == 0.0


async def test_the_ledger_records_what_was_served_from_cache():
    controller = BudgetController(_ledger(token_limit=0))
    reservation = await controller.reserve(estimated_input_tokens=10, estimated_output_tokens=10)

    await controller.commit(
        reservation,
        input_tokens=4016,
        output_tokens=50,
        cost_usd=0.001,
        usage_estimated=False,
        cache_read_tokens=3968,
    )

    snapshot = controller.snapshot()
    assert snapshot["cache_read_tokens"] == 3968
    # Tokens are still counted whole: a cached token occupies the context window
    # and still costs something. Only the *price* differs.
    assert snapshot["total_tokens"] == 4066
    assert controller.observed_cache_read_ratio == pytest.approx(3968 / 4016)


async def test_a_reservation_reserves_the_uncached_price(_unused=None):
    """A reservation decides whether a call is *allowed*, so it must assume the
    worst it could cost. A cache hit is never guaranteed, and a ceiling that
    assumes one is not a ceiling."""
    controller = BudgetController(_ledger(token_limit=0))
    model = _model()

    reservation = await controller.reserve(estimated_input_tokens=10, estimated_output_tokens=10)
    await controller.commit(
        reservation, input_tokens=4000, output_tokens=50, cost_usd=0.0, usage_estimated=False, cache_read_tokens=3600
    )

    # The run has been 90% cached, and the reservation still prices the full rate.
    assert controller.observed_cache_read_ratio == pytest.approx(0.9)
    assert controller.project_cost_usd(model, 4000, 100) == pytest.approx(usage_cost_usd(model, 4000, 100))


async def test_one_phases_cache_rate_cannot_discount_another_phases_call():
    """The ratio is a property of the whole run, so a cache-heavy sandbox phase
    used to discount a cold planner call on a different model -- reproduced at
    6.6x under-reserved."""
    controller = BudgetController(_ledger(token_limit=0))
    cheap, expensive = _model("deepseek/deepseek-chat"), _model("anthropic/claude-sonnet-4-5")

    reservation = await controller.reserve(estimated_input_tokens=10, estimated_output_tokens=10)
    await controller.commit(
        reservation,
        input_tokens=100_000,
        output_tokens=10,
        cost_usd=0.0,
        usage_estimated=False,
        cache_read_tokens=99_000,
    )

    cold = controller.project_cost_usd(expensive, 100_000, 1_000)
    assert cold == pytest.approx(usage_cost_usd(expensive, 100_000, 1_000))
    assert cold > usage_cost_usd(cheap, 100_000, 1_000)


# --- A step is bounded in whichever dimension the run is budgeted on (AGT-022) --


async def test_a_scope_is_bounded_by_cost_when_that_is_what_the_run_budgets():
    controller = BudgetController(_ledger(token_limit=0))
    controller.open_scope("worker:s1", 0, ceiling_cost_usd=0.10, soft_cost_usd=0.05)

    reservation = await controller.reserve(
        estimated_input_tokens=10, estimated_output_tokens=10, estimated_cost_usd=0.06, scope="worker:s1"
    )
    await controller.commit(reservation, input_tokens=10, output_tokens=10, cost_usd=0.06, usage_estimated=False)

    assert controller.scope_cost_spend("worker:s1") == pytest.approx(0.06)
    # Past its fair share, so it should converge, but not yet stopped.
    assert controller.scope_soft_limit_reached("worker:s1")
    assert not controller.scope_exhausted("worker:s1")

    with pytest.raises(BudgetExceeded, match="share of the run cost budget"):
        await controller.reserve(
            estimated_input_tokens=10, estimated_output_tokens=10, estimated_cost_usd=0.05, scope="worker:s1"
        )


async def test_scope_cost_contention_waits_rather_than_failing_the_step():
    controller = BudgetController(_ledger(token_limit=0))
    controller.open_scope("worker:s1", 0, ceiling_cost_usd=0.10)
    held = await controller.reserve(
        estimated_input_tokens=1, estimated_output_tokens=1, estimated_cost_usd=0.06, scope="worker:s1"
    )

    waiter = asyncio.ensure_future(
        controller.reserve(
            estimated_input_tokens=1, estimated_output_tokens=1, estimated_cost_usd=0.06, scope="worker:s1"
        )
    )
    await asyncio.sleep(0)
    assert not waiter.done()

    await controller.commit(held, input_tokens=1, output_tokens=1, cost_usd=0.01, usage_estimated=False)

    assert await asyncio.wait_for(waiter, timeout=1)


async def test_a_scope_exhausted_on_tokens_is_still_exhausted():
    """The token bound is unchanged where a run is budgeted on tokens."""
    controller = BudgetController(_ledger(token_limit=0))
    controller.open_scope("worker:s1", 100)
    reservation = await controller.reserve(estimated_input_tokens=90, estimated_output_tokens=10, scope="worker:s1")
    await controller.commit(reservation, input_tokens=90, output_tokens=10, cost_usd=0.0, usage_estimated=False)

    assert controller.scope_exhausted("worker:s1")


def test_remaining_normal_cost_reports_none_when_cost_is_not_budgeted():
    ledger = _ledger(token_limit=0)
    assert BudgetController(ledger).remaining_normal_cost_usd is None

    ledger.update({"cost_limit_usd": 2.0, "reserve_cost_usd": 0.4, "cost_usd": 0.5})
    assert BudgetController(ledger).remaining_normal_cost_usd == pytest.approx(1.1)


def test_closing_a_scope_clears_both_dimensions():
    controller = BudgetController(_ledger(token_limit=0))
    controller.open_scope("worker:s1", 100, ceiling_cost_usd=0.10)
    controller.close_scope("worker:s1")

    assert not controller.scope_exhausted("worker:s1")
    assert controller.scope_cost_spend("worker:s1") == 0.0


# --- The call ceiling is derived from the plan (AGT-024) ----------------------


def test_the_call_ceiling_is_derived_from_the_plan(mocker):
    from reporting.services.chat_budget import derived_call_ceiling

    mocker.patch("reporting.settings.CHAT_RUN_MAX_LLM_CALLS", 0)
    mocker.patch("reporting.settings.CHAT_RUN_LLM_CALLS_PER_STEP", 24)

    # A run has to route and plan before there is a plan to derive from.
    assert derived_call_ceiling(0) == derived_call_ceiling(1) == 32
    assert derived_call_ceiling(4) == 104
    # A plan that expands is a bigger plan, and gets a bigger ceiling.
    assert derived_call_ceiling(9) == 224


def test_an_explicit_ceiling_still_wins(mocker):
    from reporting.services.chat_budget import derived_call_ceiling

    mocker.patch("reporting.settings.CHAT_RUN_MAX_LLM_CALLS", 64)
    mocker.patch("reporting.settings.CHAT_RUN_LLM_CALLS_PER_STEP", 24)

    assert derived_call_ceiling(1) == derived_call_ceiling(40) == 64


def test_zeroing_both_disables_the_dimension(mocker):
    from reporting.services.chat_budget import derived_call_ceiling, initial_budget_ledger

    mocker.patch("reporting.settings.CHAT_RUN_MAX_LLM_CALLS", 0)
    mocker.patch("reporting.settings.CHAT_RUN_LLM_CALLS_PER_STEP", 0)

    assert derived_call_ceiling(9) == 0
    assert initial_budget_ledger()["max_llm_calls"] == 0


def test_a_growing_plan_raises_the_ceiling_but_nothing_lowers_it(mocker):
    mocker.patch("reporting.settings.CHAT_RUN_MAX_LLM_CALLS", 0)
    mocker.patch("reporting.settings.CHAT_RUN_LLM_CALLS_PER_STEP", 24)
    controller = BudgetController(_ledger(max_llm_calls=32, reserve_llm_calls=2))

    controller.set_planned_steps(4)
    assert controller.snapshot()["max_llm_calls"] == 104

    # A step expanding into four makes the plan bigger, not the run's allowance
    # smaller; and a plan that shrinks must not retroactively exhaust the run.
    controller.set_planned_steps(9)
    assert controller.snapshot()["max_llm_calls"] == 224
    controller.set_planned_steps(2)
    assert controller.snapshot()["max_llm_calls"] == 224


async def test_a_wide_plan_is_not_stopped_by_a_ceiling_meant_for_a_narrow_one(mocker):
    """The failure this replaces: an expanded plan died on a count, not on spend."""
    mocker.patch("reporting.settings.CHAT_RUN_MAX_LLM_CALLS", 0)
    mocker.patch("reporting.settings.CHAT_RUN_LLM_CALLS_PER_STEP", 24)
    controller = BudgetController(_ledger(token_limit=0, max_llm_calls=32, reserve_llm_calls=0))
    controller.set_planned_steps(9)

    for _ in range(60):
        reservation = await controller.reserve(estimated_input_tokens=1, estimated_output_tokens=1)
        await controller.commit(reservation, input_tokens=1, output_tokens=1, cost_usd=0.0, usage_estimated=False)

    assert controller.mode == "normal"
    assert controller.snapshot()["llm_calls"] == 60
