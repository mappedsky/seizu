"""Token and cost budgeting for headless chat orchestration."""

import asyncio
import math
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, cast

from langchain_core.messages import BaseMessage

from reporting import settings
from reporting.services.chat_messages import message_text

BudgetMode = Literal["normal", "degraded", "finalizing", "exhausted"]


class BudgetExceeded(RuntimeError):
    """Raised when a new LLM call would exceed the available run budget."""


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    phase: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float
    allow_reserve: bool
    # Which bounded unit of work this belongs to (a plan step). Spend by any
    # descendant -- notably a sandbox sub-agent, which reserves against the
    # controller directly -- counts toward the same scope.
    scope: str = ""


#: Calls a run makes whatever its plan costs: routing, planning, synthesis, and
#: the retries those three are allowed.
_CALL_CEILING_BASE = 8


def derived_call_ceiling(steps: int, *, models_priced: bool | None = None) -> int:
    """The call ceiling a plan of *steps* steps implies, or 0 when disabled.

    ``CHAT_RUN_MAX_LLM_CALLS`` overrides it when set (AGT-024).

    Two per-step figures, for the same reason :func:`derived_token_ceiling` has
    two: what a run may spend is bounded by cost, so where cost can bind, this
    only has to sit above what legitimate work does -- reaching it should itself
    be evidence of pathology. Where the model is unpriced, cost never accrues and
    this is the last guard, so it stays tight (AGT-030).
    """
    override = max(0, settings.CHAT_RUN_MAX_LLM_CALLS)
    if override:
        return override
    per_step = max(0, settings.CHAT_RUN_LLM_CALLS_PER_STEP)
    if not per_step:
        # The explicit off switch, whatever the pricing: the unpriced figure
        # tightens this dimension, it never resurrects one that was turned off.
        return 0
    from reporting.services.chat_models import capability

    priced = (
        models_priced
        if models_priced is not None
        else max(0.0, settings.CHAT_RUN_COST_BUDGET_USD) > 0 and capability(settings.CHAT_LLM_MODEL).priced
    )
    if not priced:
        tight = max(0, settings.CHAT_RUN_UNPRICED_LLM_CALLS_PER_STEP)
        per_step = min(per_step, tight) if tight else per_step
    return _CALL_CEILING_BASE + per_step * max(1, steps)


def derived_token_ceiling(*, cost_limit_usd: float | None = None, models_priced: bool | None = None) -> int:
    """The run's token ceiling, or 0 when cost is bound to do the bounding.

    ``CHAT_RUN_TOKEN_BUDGET`` pins it when set. Otherwise: a run budgeted in
    cost on a model LiteLLM can price needs no token ceiling, because cost
    already bounds it and a token figure that also bounded it would have to be
    re-derived for every model's price. When the model is *not* priced, cost can
    never accrue, and the backstop is the only guard left. Rationale: AGT-022.
    """
    configured = max(0, settings.CHAT_RUN_TOKEN_BUDGET)
    if configured:
        return configured
    cost_limit = max(0.0, settings.CHAT_RUN_COST_BUDGET_USD if cost_limit_usd is None else cost_limit_usd)
    if cost_limit <= 0:
        # Not budgeting on cost either: this is an explicit "no token limit".
        return 0
    from reporting.services.chat_models import capability

    priced = models_priced if models_priced is not None else capability(settings.CHAT_LLM_MODEL).priced
    if priced:
        return 0
    return max(0, settings.CHAT_RUN_UNPRICED_TOKEN_BUDGET)


def initial_budget_ledger(
    *,
    cost_limit_usd: float | None = None,
    model_specs: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    models_priced: bool | None = None
    if model_specs:
        from reporting.services.chat_models import ModelSpec, capability

        resolved = [ModelSpec.from_payload(payload) for payload in model_specs]
        models_priced = all(capability(spec.model_id).priced for spec in resolved)
    cost_limit = max(0.0, settings.CHAT_RUN_COST_BUDGET_USD if cost_limit_usd is None else cost_limit_usd)
    token_limit = derived_token_ceiling(cost_limit_usd=cost_limit, models_priced=models_priced)
    reserve_ratio = min(max(settings.CHAT_RUN_RESERVE_PERCENT / 100.0, 0.0), 0.9)
    # One step's worth until a plan exists: routing and planning happen before
    # there is anything to derive from.
    max_calls = derived_call_ceiling(1, models_priced=models_priced)
    return {
        "enabled": token_limit > 0 or cost_limit > 0 or max_calls > 0,
        "token_limit": token_limit,
        "cost_limit_usd": cost_limit,
        "reserve_tokens": math.ceil(token_limit * reserve_ratio) if token_limit else 0,
        "reserve_cost_usd": cost_limit * reserve_ratio if cost_limit else 0.0,
        "soft_limit_ratio": min(max(settings.CHAT_RUN_SOFT_LIMIT_PERCENT / 100.0, 0.0), 1.0),
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        # Subsets of input_tokens the provider served from (or wrote to) its
        # prompt cache. Recorded so the saving is visible in the run ledger and
        # so cost projections can be based on what this run actually got rather
        # than on an assumption.
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": 0.0,
        "llm_calls": 0,
        "max_llm_calls": max_calls,
        "models_priced": models_priced,
        "reserve_llm_calls": min(2, max(0, max_calls - 1)),
        "usage_estimated": False,
        "mode": "normal",
        "exhaustion_reason": None,
        "estimated_remaining_tokens": 0,
        "phases": {},
    }


def grant_ledger(
    *,
    token_grant: int,
    soft_token_grant: int,
    cost_grant_usd: float,
    llm_call_grant: int,
    soft_cost_grant_usd: float = 0.0,
) -> dict[str, Any]:
    """A self-contained ledger for one unit of work executing somewhere else.

    A distributed plan step cannot share the run's controller -- it runs in a
    different process -- so it is handed a **grant** instead: a slice of what the
    run had left, allocated before the fan-out and returned as actuals when the
    step finishes (AGT-018). Slices are non-overlapping and sum to no more than
    the run's remaining normal tokens, which is what makes concurrent workers
    unable to collectively overspend without a distributed transaction.

    The price is that a distributed step's share is a hard cut where an
    in-process one may overrun into a sibling's idle budget: the run's controller
    is not there to be asked. ``soft_token_grant`` keeps the converge-then-stop
    shape within the slice, so a step still gets to summarize what it found
    rather than being killed at the wall (AGT-012).
    """
    grant = max(0, token_grant)
    return {
        **initial_budget_ledger(),
        # Marks this ledger as one unit of work's slice rather than the run's
        # own. A step that exhausts it has used its share; the run is elsewhere
        # and knows nothing about it (AGT-025).
        "is_grant": True,
        "enabled": grant > 0 or cost_grant_usd > 0 or llm_call_grant > 0,
        "token_limit": grant,
        "cost_limit_usd": max(0.0, cost_grant_usd),
        # The reserve is what pays for the step's summary pass, which is the only
        # thing standing between a capped step and reporting nothing.
        "reserve_tokens": max(0, grant - max(0, soft_token_grant)) if soft_token_grant else 0,
        # The cost dimension needs the same reserve as the token one, or a step
        # whose grant binds on cost is cut with nothing to show (AGT-025).
        "reserve_cost_usd": (max(0.0, cost_grant_usd - max(0.0, soft_cost_grant_usd)) if soft_cost_grant_usd else 0.0),
        "max_llm_calls": max(0, llm_call_grant),
        "reserve_llm_calls": min(2, max(0, llm_call_grant - 1)),
    }


#: Smallest output reservation, however little a phase is observed to emit.
_MIN_OUTPUT_RESERVATION = 256

#: How long a waiter sleeps before re-checking capacity of its own accord.
_CAPACITY_POLL_SECONDS = 1.0

#: Weight of the newest observation in the running output estimate: a phase
#: whose calls grow is tracked within a few calls, and one outlier decays out.
_OUTPUT_OBSERVATION_ALPHA = 0.4


def _observation_key(phase: str) -> str:
    """The family of calls a phase belongs to, for estimating what it emits.

    Phases are ``role``, ``role:step_id``, or ``role:step_id:sub_role``. The step
    id is dropped, so every step of a role shares one estimate; the sub-role is
    kept, so a sandbox sub-agent is estimated apart from its worker.
    """
    parts = [part for part in phase.split(":") if part]
    if not parts:
        return "unspecified"
    return parts[0] if len(parts) < 3 else f"{parts[0]}:{parts[-1]}"


class BudgetController:
    """Atomic run-level budget ledger shared by parallel orchestrator workers."""

    def __init__(self, ledger: dict[str, Any] | None = None) -> None:
        self._ledger = dict(ledger or initial_budget_ledger())
        self._reservations: dict[str, BudgetReservation] = {}
        self._lock = asyncio.Lock()
        # Signalled whenever a reservation settles, so a call refused for
        # contention can wait for room. Wraps the ledger lock, so code already
        # holding that lock may notify.
        self._capacity = asyncio.Condition(self._lock)
        # phase family -> exponentially weighted mean of output tokens emitted,
        # which is what a reservation is sized from.
        self._observed_output: dict[str, float] = {}
        # scope -> ceiling, and scope -> tokens committed. Held here rather than
        # counted by the caller because steps run concurrently: a caller reading
        # a snapshot before and after its own work would attribute a sibling's
        # spend to itself, and miss its own sub-agents entirely.
        self._scope_ceilings: dict[str, int] = {}
        self._scope_soft: dict[str, int] = {}
        self._scope_spend: dict[str, int] = {}
        # The same three in cost, so a scope is bounded in whichever dimension
        # the run is actually budgeted on (AGT-022).
        self._scope_cost_ceilings: dict[str, float] = {}
        self._scope_cost_soft: dict[str, float] = {}
        self._scope_cost_spend: dict[str, float] = {}

    def open_scope(
        self,
        scope: str,
        ceiling_tokens: int,
        soft_tokens: int = 0,
        *,
        ceiling_cost_usd: float = 0.0,
        soft_cost_usd: float = 0.0,
    ) -> None:
        """Bound one unit of work, including anything it delegates to.

        Two thresholds per dimension, because they answer different questions.
        The soft one is the scope's fair share of what the run has left, and
        crossing it means siblings are being competed with -- a reason to
        converge. The ceiling is where continuing would eat the run's
        finalization reserve -- a reason to stop.

        A dimension left at ``0`` does not bound the scope, so a run budgeted
        only on cost is still bounded per step, and one budgeted only on tokens
        behaves exactly as before (AGT-022).
        """
        if not scope:
            return
        if ceiling_tokens > 0:
            self._scope_ceilings[scope] = ceiling_tokens
            self._scope_soft[scope] = soft_tokens if soft_tokens > 0 else ceiling_tokens
            self._scope_spend.setdefault(scope, 0)
        if ceiling_cost_usd > 0:
            self._scope_cost_ceilings[scope] = ceiling_cost_usd
            self._scope_cost_soft[scope] = soft_cost_usd if soft_cost_usd > 0 else ceiling_cost_usd
            self._scope_cost_spend.setdefault(scope, 0.0)

    def close_scope(self, scope: str) -> None:
        self._scope_ceilings.pop(scope, None)
        self._scope_soft.pop(scope, None)
        self._scope_spend.pop(scope, None)
        self._scope_cost_ceilings.pop(scope, None)
        self._scope_cost_soft.pop(scope, None)
        self._scope_cost_spend.pop(scope, None)
        # Drop anything still reserved against it. A reservation that outlives
        # its scope keeps inflating the run's projected spend and call count for
        # the rest of the turn, on work that has already finished or been
        # cancelled.
        for key, item in list(self._reservations.items()):
            if item.scope == scope:
                self._reservations.pop(key, None)

    def scope_spend(self, scope: str) -> int:
        """Tokens a scope has actually spent.

        Committed actuals, where run-level authorization works on *estimates*
        (including a full CHAT_LLM_MAX_TOKENS of assumed output). The two are
        deliberately different: a scope bounds work that has happened, while
        authorization has to refuse a call before it happens. The consequence is
        that the run's own token check can refuse a call while a scope still
        looks well inside its bound, so a scope ceiling is never the only thing
        standing between a step and the reserve.
        """
        return int(self._scope_spend.get(scope, 0))

    def scope_cost_spend(self, scope: str) -> float:
        """Estimated USD a scope has actually spent."""
        return float(self._scope_cost_spend.get(scope, 0.0))

    def scope_exhausted(self, scope: str) -> bool:
        """Whether either bound on the scope has been reached."""
        ceiling = self._scope_ceilings.get(scope)
        if ceiling is not None and self.scope_spend(scope) >= ceiling:
            return True
        cost_ceiling = self._scope_cost_ceilings.get(scope)
        return cost_ceiling is not None and self.scope_cost_spend(scope) >= cost_ceiling

    def scope_remaining(self, scope: str) -> int | None:
        """Tokens the scope may still spend, or ``None`` when it is unbounded."""
        ceiling = self._scope_ceilings.get(scope)
        if ceiling is None:
            return None
        return max(0, ceiling - self.scope_spend(scope))

    def scope_soft_limit_reached(self, scope: str) -> bool:
        """Whether a scope has spent its fair share and should be converging.

        The run has always had this as a mode change (a cheaper model, optional
        steps dropped). A scope needs it as a *signal it can act on*, because
        the thing spending a step's budget is often a sub-agent that will
        otherwise work until it is cut mid-task and lose what it had.
        """
        soft = self._scope_soft.get(scope)
        if soft is not None and self.scope_spend(scope) >= soft:
            return True
        cost_soft = self._scope_cost_soft.get(scope)
        return cost_soft is not None and self.scope_cost_spend(scope) >= cost_soft

    def snapshot(self) -> dict[str, Any]:
        return dict(self._ledger)

    @property
    def observed_cache_read_ratio(self) -> float:
        """Share of this run's input tokens the provider has served from cache.

        Reporting only. It is deliberately *not* used to discount reservations:
        see ``project_cost_usd``.
        """
        input_tokens = int(self._ledger.get("input_tokens") or 0)
        if input_tokens <= 0:
            return 0.0
        cached = int(self._ledger.get("cache_read_tokens") or 0)
        return min(1.0, max(0.0, cached / input_tokens))

    def projected_output_tokens(self, phase: str, ceiling: int) -> int:
        """Output tokens to reserve for a call of this kind.

        An exponentially weighted mean of what the phase family has emitted,
        times ``CHAT_BUDGET_OUTPUT_ESTIMATE_SAFETY``, floored at
        :data:`_MIN_OUTPUT_RESERVATION` and never above *ceiling* -- the call
        cannot return more than that. Falls back to
        ``CHAT_BUDGET_OUTPUT_ESTIMATE_TOKENS`` until the first call of the
        family commits.

        This is an authorization, not a prediction: a call that emits more than
        its reservation is corrected exactly on commit and overshoots into the
        finalization reserve. Pass the model's ceiling, not this, when sizing
        the context window. Rationale and measurements: AGT-021.
        """
        ceiling = max(1, ceiling)
        seed = max(1, settings.CHAT_BUDGET_OUTPUT_ESTIMATE_TOKENS)
        observed = self._observed_output.get(_observation_key(phase))
        if observed is None:
            return min(ceiling, seed)
        safety = max(1.0, settings.CHAT_BUDGET_OUTPUT_ESTIMATE_SAFETY)
        return max(1, min(ceiling, max(_MIN_OUTPUT_RESERVATION, int(observed * safety))))

    def _record_output_locked(self, phase: str, output_tokens: int) -> None:
        key = _observation_key(phase)
        prior = self._observed_output.get(key)
        observed = float(max(0, output_tokens))
        self._observed_output[key] = (
            observed
            if prior is None
            else (1 - _OUTPUT_OBSERVATION_ALPHA) * prior + _OUTPUT_OBSERVATION_ALPHA * observed
        )

    def project_cost_usd(self, model: Any, input_tokens: int, output_tokens: int) -> float:
        """What to *reserve* for a call of this size: the uncached price.

        This deliberately does not discount by the run's observed cache rate,
        which is what it used to do. Two things were wrong with that. The ratio
        is a property of the whole run, so a cache-heavy sandbox phase discounted
        a cold planner call on a different model -- reproduced at 6.6x under-
        reserved. And more fundamentally a cache hit is never guaranteed: a
        ceiling that assumes one is not a ceiling. Reserving the price the call
        would cost if nothing hit is the only figure that cannot be overrun.

        Committed cost stays exact -- it is billed from the provider's own cache
        accounting -- so the ledger self-corrects the moment a call returns, and
        the over-reservation only ever applies to calls still in flight.
        """
        return usage_cost_usd(model, input_tokens, output_tokens)

    @property
    def enabled(self) -> bool:
        return bool(self._ledger.get("enabled"))

    @property
    def mode(self) -> BudgetMode:
        return cast(BudgetMode, str(self._ledger.get("mode", "normal")))

    @property
    def is_grant(self) -> bool:
        """Whether this ledger is one step's slice rather than the run's own."""
        return bool(self._ledger.get("is_grant"))

    @property
    def degraded(self) -> bool:
        return self.mode in ("degraded", "finalizing", "exhausted")

    @property
    def finalizing(self) -> bool:
        return self.mode in ("finalizing", "exhausted")

    @property
    def remaining_normal_tokens(self) -> int | None:
        """Tokens still spendable outside the finalization reserve.

        ``None`` when no token limit is configured, so callers can tell "no
        constraint" from "nothing left".
        """
        token_limit = int(self._ledger.get("token_limit") or 0)
        if not token_limit:
            return None
        spent = int(self._ledger.get("total_tokens") or 0)
        reserve = int(self._ledger.get("reserve_tokens") or 0)
        return max(0, token_limit - reserve - spent)

    @property
    def remaining_normal_cost_usd(self) -> float | None:
        """USD still spendable outside the finalization reserve.

        ``None`` when no cost limit is configured, matching
        :attr:`remaining_normal_tokens`, so a caller can tell "no constraint"
        from "nothing left".
        """
        cost_limit = float(self._ledger.get("cost_limit_usd") or 0.0)
        if not cost_limit:
            return None
        spent = float(self._ledger.get("cost_usd") or 0.0)
        reserve = float(self._ledger.get("reserve_cost_usd") or 0.0)
        return max(0.0, cost_limit - reserve - spent)

    def set_planned_steps(self, steps: int) -> None:
        """Re-derive the call ceiling for a plan of *steps* steps.

        Called wherever the plan changes, including when a step expands into
        several (AGT-023), so the ceiling tracks the work rather than the shape
        the plan happened to have when the turn started. Only ever raises: a
        ceiling that fell below what a run had already spent would finalize it
        retroactively.
        """
        derived = derived_call_ceiling(steps, models_priced=self._ledger.get("models_priced"))
        if not derived or derived <= int(self._ledger.get("max_llm_calls") or 0):
            return
        self._ledger["max_llm_calls"] = derived
        self._ledger["reserve_llm_calls"] = min(2, max(0, derived - 1))

    def set_estimated_remaining_tokens(self, tokens: int) -> None:
        self._ledger["estimated_remaining_tokens"] = max(0, tokens)
        token_limit = int(self._ledger.get("token_limit") or 0)
        if not token_limit or self.finalizing:
            return
        normal_remaining = (
            token_limit - int(self._ledger.get("reserve_tokens") or 0) - int(self._ledger.get("total_tokens") or 0)
        )
        if tokens > normal_remaining and self.mode == "normal":
            self._ledger["mode"] = "degraded"

    async def reserve(
        self,
        *,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
        estimated_cost_usd: float = 0.0,
        allow_reserve: bool = False,
        phase: str = "unspecified",
        scope: str = "",
    ) -> BudgetReservation:
        reservation = BudgetReservation(
            reservation_id=uuid.uuid4().hex,
            phase=phase,
            estimated_input_tokens=max(0, estimated_input_tokens),
            estimated_output_tokens=max(0, estimated_output_tokens),
            estimated_cost_usd=max(0.0, estimated_cost_usd),
            allow_reserve=allow_reserve,
            scope=scope or current_budget_scope(),
        )
        wait_seconds = max(0.0, settings.CHAT_BUDGET_CONTENTION_WAIT_SECONDS)
        deadline: float | None = None
        async with self._capacity:
            while True:
                contention = self._authorize_locked(reservation)
                if not contention:
                    self._reservations[reservation.reservation_id] = reservation
                    return reservation
                # The budget has room, held by calls that have not returned:
                # wait for one to settle and re-authorize against its actuals.
                if wait_seconds <= 0:
                    raise BudgetExceeded(contention)
                loop = asyncio.get_running_loop()
                if deadline is None:
                    deadline = loop.time() + wait_seconds
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise BudgetExceeded(f"{contention} (waited {wait_seconds:g}s for capacity)")
                try:
                    async with asyncio.timeout(min(remaining, _CAPACITY_POLL_SECONDS)):
                        await self._capacity.wait()
                except TimeoutError:
                    # Re-check rather than trust the notification: ``close_scope``
                    # and ``discard`` free capacity from synchronous paths that
                    # cannot take the lock to announce it.
                    continue

    def ambient_scope(self) -> str:
        """The scope a caller belongs to when it does not name one itself.

        Every reservation made while a step is running should count toward that
        step, whether it comes from the step's own loop or from something it
        delegated to several layers down. Reading it here rather than threading
        a parameter through every call site is what makes that hold by default.
        """
        return current_budget_scope()

    async def commit(
        self,
        reservation: BudgetReservation,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        usage_estimated: bool,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        async with self._lock:
            self._reservations.pop(reservation.reservation_id, None)
            self._ledger["input_tokens"] += max(0, input_tokens)
            self._ledger["output_tokens"] += max(0, output_tokens)
            self._ledger["total_tokens"] = self._ledger["input_tokens"] + self._ledger["output_tokens"]
            self._ledger["cache_read_tokens"] = int(self._ledger.get("cache_read_tokens") or 0) + max(
                0, cache_read_tokens
            )
            self._ledger["cache_creation_tokens"] = int(self._ledger.get("cache_creation_tokens") or 0) + max(
                0, cache_creation_tokens
            )
            self._ledger["cost_usd"] += max(0.0, cost_usd)
            self._ledger["llm_calls"] += 1
            phases = dict(self._ledger.get("phases") or {})
            phase_usage = dict(phases.get(reservation.phase) or {})
            phase_usage["input_tokens"] = int(phase_usage.get("input_tokens") or 0) + max(0, input_tokens)
            phase_usage["output_tokens"] = int(phase_usage.get("output_tokens") or 0) + max(0, output_tokens)
            phase_usage["total_tokens"] = int(phase_usage["input_tokens"]) + int(phase_usage["output_tokens"])
            phase_usage["cache_read_tokens"] = int(phase_usage.get("cache_read_tokens") or 0) + max(
                0, cache_read_tokens
            )
            phase_usage["cost_usd"] = float(phase_usage.get("cost_usd") or 0.0) + max(0.0, cost_usd)
            phase_usage["llm_calls"] = int(phase_usage.get("llm_calls") or 0) + 1
            phases[reservation.phase] = phase_usage
            self._ledger["phases"] = phases
            if reservation.scope in self._scope_spend:
                self._scope_spend[reservation.scope] += max(0, input_tokens) + max(0, output_tokens)
            if reservation.scope in self._scope_cost_spend:
                self._scope_cost_spend[reservation.scope] += max(0.0, cost_usd)
            if usage_estimated:
                self._ledger["usage_estimated"] = True
            self._record_output_locked(reservation.phase, output_tokens)
            self._refresh_mode_locked()
            # This call's estimate is now an actual, usually smaller: wake
            # anything waiting for the difference.
            self._capacity.notify_all()

    def usage_report(self) -> dict[str, Any]:
        """What this ledger actually spent.

        A plain accessor, but the only honest way to compare two configurations:
        wall-clock here is dominated by network variance wide enough to swamp any
        difference between them, while tokens are what reasoning actually moves
        and what the provider bills.
        """
        return {
            "input_tokens": int(self._ledger.get("input_tokens") or 0),
            "output_tokens": int(self._ledger.get("output_tokens") or 0),
            "cache_read_tokens": int(self._ledger.get("cache_read_tokens") or 0),
            "cache_creation_tokens": int(self._ledger.get("cache_creation_tokens") or 0),
            "cost_usd": float(self._ledger.get("cost_usd") or 0.0),
            "llm_calls": int(self._ledger.get("llm_calls") or 0),
            "usage_estimated": bool(self._ledger.get("usage_estimated")),
            "phases": dict(self._ledger.get("phases") or {}),
        }

    async def absorb(self, usage: dict[str, Any], *, scope: str = "") -> None:
        """Fold work done under a separate ledger back into this one.

        The other half of a grant (:func:`grant_ledger`): a distributed step
        spends against its own slice and reports actuals here, so the run's view
        of what it has spent stays complete even though the spending happened in
        another process. Committed after the fact rather than reserved, because
        the grant was the reservation -- authorizing it again would double-count
        it against the run.
        """
        input_tokens = max(0, int(usage.get("input_tokens") or 0))
        output_tokens = max(0, int(usage.get("output_tokens") or 0))
        async with self._lock:
            self._ledger["input_tokens"] += input_tokens
            self._ledger["output_tokens"] += output_tokens
            self._ledger["total_tokens"] = self._ledger["input_tokens"] + self._ledger["output_tokens"]
            self._ledger["cache_read_tokens"] = int(self._ledger.get("cache_read_tokens") or 0) + max(
                0, int(usage.get("cache_read_tokens") or 0)
            )
            self._ledger["cache_creation_tokens"] = int(self._ledger.get("cache_creation_tokens") or 0) + max(
                0, int(usage.get("cache_creation_tokens") or 0)
            )
            self._ledger["cost_usd"] += max(0.0, float(usage.get("cost_usd") or 0.0))
            self._ledger["llm_calls"] += max(0, int(usage.get("llm_calls") or 0))
            phases = dict(self._ledger.get("phases") or {})
            for name, reported in (usage.get("phases") or {}).items():
                if not isinstance(reported, dict):
                    continue
                merged = dict(phases.get(name) or {})
                for key in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "llm_calls"):
                    merged[key] = int(merged.get(key) or 0) + max(0, int(reported.get(key) or 0))
                merged["cost_usd"] = float(merged.get("cost_usd") or 0.0) + max(
                    0.0, float(reported.get("cost_usd") or 0.0)
                )
                phases[name] = merged
                # A grant reports totals rather than individual calls, so the
                # per-call mean is the sample this can contribute.
                calls = max(0, int(reported.get("llm_calls") or 0))
                if calls:
                    self._record_output_locked(name, max(0, int(reported.get("output_tokens") or 0)) // calls)
            self._ledger["phases"] = phases
            if scope and scope in self._scope_spend:
                self._scope_spend[scope] += input_tokens + output_tokens
            if scope and scope in self._scope_cost_spend:
                self._scope_cost_spend[scope] += max(0.0, float(usage.get("cost_usd") or 0.0))
            if usage.get("usage_estimated"):
                self._ledger["usage_estimated"] = True
            self._refresh_mode_locked()
            self._capacity.notify_all()

    async def release(self, reservation: BudgetReservation) -> None:
        async with self._lock:
            self._reservations.pop(reservation.reservation_id, None)
            # A call that never happened frees exactly what it booked.
            self._capacity.notify_all()

    def discard(self, reservation: BudgetReservation) -> None:
        """Drop a reservation without awaiting.

        For cleanup that must run while a task is being cancelled, where
        awaiting the lock can itself be interrupted and leave the reservation
        held forever. A single dict pop needs no lock to be safe.
        """
        self._reservations.pop(reservation.reservation_id, None)

    def begin_finalization(self, reason: str) -> None:
        if self.mode != "exhausted":
            self._ledger["mode"] = "finalizing"
        if not self._ledger.get("exhaustion_reason"):
            self._ledger["exhaustion_reason"] = reason

    def mark_exhausted(self, reason: str) -> None:
        self._ledger["mode"] = "exhausted"
        self._ledger["exhaustion_reason"] = reason

    def _authorize_locked(self, reservation: BudgetReservation) -> str:
        """Authorize a call, or say why it must wait.

        Returns ``""`` when the call may proceed, and a reason when it may not
        *yet* -- the budget has room, but the room is held by calls still in
        flight, so :meth:`reserve` waits and re-authorizes against the actuals
        they commit. Raises :class:`BudgetExceeded`, and finalizes the run, only
        when **committed** spend leaves no room; waiting cannot change that.

        Contention must never take the finalizing path: finalization is
        permanent and the dispatcher skips every remaining step on it (AGT-021).
        """
        # Checked before `enabled`, and before the finalization gate, because a
        # step ceiling bounds one step against its siblings rather than the run
        # against itself: it must hold even where the run-level dimensions are
        # all disabled, and a finalizing run's reserve is for the *run* to
        # summarize, not for an over-budget step to keep working.
        ceiling = self._scope_ceilings.get(reservation.scope)
        requested_tokens = reservation.estimated_input_tokens + reservation.estimated_output_tokens
        if ceiling is not None:
            spent = self._scope_spend.get(reservation.scope, 0)
            if spent + requested_tokens > ceiling:
                raise BudgetExceeded(
                    f"Step {reservation.scope} reached its share of the run budget ({ceiling} tokens)."
                )
            # Sibling delegations of the same step, still running. Waiting for
            # them holds the ceiling exactly as counting them did, without
            # ending the step to do it.
            in_flight = sum(
                item.estimated_input_tokens + item.estimated_output_tokens
                for item in self._reservations.values()
                if item.scope == reservation.scope
            )
            if spent + in_flight + requested_tokens > ceiling:
                return f"Step {reservation.scope} has its share of the run budget in flight."
        cost_ceiling = self._scope_cost_ceilings.get(reservation.scope)
        if cost_ceiling is not None:
            spent_cost = self.scope_cost_spend(reservation.scope)
            if spent_cost + reservation.estimated_cost_usd > cost_ceiling:
                raise BudgetExceeded(
                    f"Step {reservation.scope} reached its share of the run cost budget (${cost_ceiling:.4f})."
                )
            in_flight_cost = sum(
                item.estimated_cost_usd for item in self._reservations.values() if item.scope == reservation.scope
            )
            if spent_cost + in_flight_cost + reservation.estimated_cost_usd > cost_ceiling:
                return f"Step {reservation.scope} has its share of the run cost budget in flight."
        if not self.enabled:
            return ""
        if self.finalizing and not reservation.allow_reserve:
            raise BudgetExceeded(self._ledger.get("exhaustion_reason") or "Run budget is reserved for finalization.")

        # Waiting cannot help the call count: a commit raises `llm_calls` by
        # exactly what it removes from the in-flight count. It stays hard.
        max_calls = int(self._ledger.get("max_llm_calls") or 0)
        reserve_calls = 0 if reservation.allow_reserve else int(self._ledger.get("reserve_llm_calls") or 0)
        projected_calls = int(self._ledger["llm_calls"]) + len(self._reservations) + 1
        if max_calls and projected_calls > max_calls - reserve_calls:
            self.begin_finalization("The run reached its LLM-call safety limit.")
            raise BudgetExceeded(str(self._ledger["exhaustion_reason"]))

        token_limit = int(self._ledger.get("token_limit") or 0)
        reserve_tokens = 0 if reservation.allow_reserve else int(self._ledger.get("reserve_tokens") or 0)
        if token_limit:
            committed_tokens = int(self._ledger["total_tokens"]) + requested_tokens
            if committed_tokens > token_limit - reserve_tokens:
                self.begin_finalization("The run token budget is reserved for final synthesis.")
                raise BudgetExceeded(str(self._ledger["exhaustion_reason"]))
            reserved_tokens = sum(
                item.estimated_input_tokens + item.estimated_output_tokens for item in self._reservations.values()
            )
            if committed_tokens + reserved_tokens > token_limit - reserve_tokens:
                return "The run's remaining tokens are reserved by calls still in flight."

        cost_limit = float(self._ledger.get("cost_limit_usd") or 0.0)
        if cost_limit:
            reserve_cost = 0.0 if reservation.allow_reserve else float(self._ledger.get("reserve_cost_usd") or 0.0)
            committed_cost = float(self._ledger["cost_usd"]) + reservation.estimated_cost_usd
            if committed_cost > cost_limit - reserve_cost:
                self.begin_finalization("The run cost budget is reserved for final synthesis.")
                raise BudgetExceeded(str(self._ledger["exhaustion_reason"]))
            reserved_cost = sum(item.estimated_cost_usd for item in self._reservations.values())
            if committed_cost + reserved_cost > cost_limit - reserve_cost:
                return "The run's remaining cost budget is reserved by calls still in flight."
        return ""

    def _refresh_mode_locked(self) -> None:
        token_limit = int(self._ledger.get("token_limit") or 0)
        cost_limit = float(self._ledger.get("cost_limit_usd") or 0.0)
        max_calls = int(self._ledger.get("max_llm_calls") or 0)
        # Deliberately not the call count. Degrading is a real cost -- optional
        # steps are dropped and the worker and synthesizer fall to the economy
        # model -- and the call ceiling is a loop guard, not a spend limit
        # (AGT-024). A run was measured crossing the soft limit on calls alone
        # with 17.6% of its cost budget spent, and was quietly downgraded for
        # having done a lot of productive work. A guard against pathology does
        # nothing until it fires; the hard stop below is where it fires (AGT-030).
        ratios = [
            int(self._ledger["total_tokens"]) / token_limit if token_limit else 0.0,
            float(self._ledger["cost_usd"]) / cost_limit if cost_limit else 0.0,
        ]
        if max(ratios) >= float(self._ledger.get("soft_limit_ratio") or 1.0) and self.mode == "normal":
            self._ledger["mode"] = "degraded"
        if token_limit and int(self._ledger["total_tokens"]) >= token_limit:
            self.mark_exhausted("The run exhausted its token budget.")
        elif cost_limit and float(self._ledger["cost_usd"]) >= cost_limit:
            self.mark_exhausted("The run exhausted its cost budget.")
        elif max_calls and int(self._ledger["llm_calls"]) >= max_calls:
            self.mark_exhausted("The run exhausted its LLM-call budget.")


_current_budget_controller: ContextVar[BudgetController | None] = ContextVar("_current_budget_controller", default=None)


def set_current_budget_controller(controller: BudgetController | None) -> None:
    """Publish the run's ledger to code that cannot be handed the graph config.

    Sub-agents reached through the MCP built-in interface get ``(args,
    current_user)`` and nothing else, so there is no parameter by which the
    controller could travel. Without this their model calls bill nobody: the
    sandbox subagent builds its own model and runs outside
    ``_run_llm_tool_turn``, and one measured turn made 2,001 inner tool calls
    while the ledger reported 37,535 tokens in "normal" mode.
    """
    _current_budget_controller.set(controller)


def current_budget_controller() -> BudgetController | None:
    return _current_budget_controller.get()


_current_budget_scope: ContextVar[str] = ContextVar("_current_budget_scope", default="")


def set_current_budget_scope(scope: str) -> None:
    """Name the bounded unit of work that descendants' spend belongs to."""
    _current_budget_scope.set(scope)


def current_budget_scope() -> str:
    return _current_budget_scope.get()


def budget_controller_from_config(config: dict[str, Any]) -> BudgetController | None:
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    controller = configurable.get("budget_controller")
    return controller if isinstance(controller, BudgetController) else None


def estimate_tokens(model: Any, system_prompt: str, messages: list[BaseMessage], tools: list[dict[str, Any]]) -> int:
    model_name = str(getattr(model, "model_name", None) or getattr(model, "model", "") or "")
    text = "\n".join([system_prompt, *(message_text(message.content) for message in messages), str(tools)])
    if not model_name:
        return max(1, math.ceil(len(text) / 4))
    try:
        from litellm import token_counter

        return max(1, int(token_counter(model=model_name, text=text)))
    except Exception:
        return max(1, math.ceil(len(text) / 4))


@dataclass(frozen=True)
class LlmUsage:
    """One call's token usage, including the parts the provider served from cache."""

    input_tokens: int = 0
    output_tokens: int = 0
    # Subsets of input_tokens, not additions to it -- LangChain reports
    # input_tokens as the sum of every input token type, and litellm prices
    # `prompt_tokens` the same way, subtracting the cached portions and charging
    # each at its own rate.
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    # A subset of output_tokens, not an addition to it: what the model spent
    # thinking rather than answering. Diagnosis only -- it is already paid for
    # and already counted (AGT-033).
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def reported(self) -> bool:
        return bool(self.input_tokens or self.output_tokens)


def usage_from_message(message: Any) -> LlmUsage:
    """Read a LangChain response's usage, cache details included.

    ``input_token_details`` is where a provider's cache accounting surfaces
    (``cache_read`` on a hit, ``cache_creation`` on a write). Reading only
    ``input_tokens`` and pricing all of it at the full rate overstates the cost
    of an agent loop by most of its input: a measured DeepSeek call re-sending
    a 4,016-token prefix reported 3,968 of them as ``cache_read``, billed at a
    tenth of the rate we were charging ourselves for.
    """
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict):
        return LlmUsage()
    details = usage.get("input_token_details")
    details = details if isinstance(details, dict) else {}
    # The output side of the same accounting. A reasoning model spends most of
    # an answer here -- 37 of 47 output tokens on a "name three primes" call --
    # and without reading it a slow stage looks like a slow model rather than a
    # thinking one (AGT-033).
    out_details = usage.get("output_token_details")
    out_details = out_details if isinstance(out_details, dict) else {}
    return LlmUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(details.get("cache_read") or 0),
        cache_creation_tokens=int(details.get("cache_creation") or 0),
        reasoning_tokens=int(out_details.get("reasoning") or 0),
    )


def usage_cost_usd(
    model: Any,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Price one call. ``input_tokens`` is the total, cached portions included.

    litellm subtracts ``cache_read_input_tokens`` / ``cache_creation_input_tokens``
    from ``prompt_tokens`` and charges each at its own rate, so passing the
    details alongside the total is all that is needed -- and passing no details
    prices every input token at the full rate, which is what this used to do.
    """
    model_name = str(getattr(model, "model_name", None) or getattr(model, "model", "") or "")
    if not model_name:
        return 0.0
    try:
        from litellm import cost_per_token

        input_cost, output_cost = cost_per_token(
            model=model_name,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            cache_read_input_tokens=min(max(0, cache_read_tokens), max(0, input_tokens)),
            cache_creation_input_tokens=min(max(0, cache_creation_tokens), max(0, input_tokens)),
        )
        return float(input_cost + output_cost)
    except Exception:
        return 0.0
