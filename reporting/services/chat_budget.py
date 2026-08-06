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


def initial_budget_ledger() -> dict[str, Any]:
    token_limit = max(0, settings.CHAT_RUN_TOKEN_BUDGET)
    cost_limit = max(0.0, settings.CHAT_RUN_COST_BUDGET_USD)
    reserve_ratio = min(max(settings.CHAT_RUN_RESERVE_PERCENT / 100.0, 0.0), 0.9)
    return {
        "enabled": token_limit > 0 or cost_limit > 0 or settings.CHAT_RUN_MAX_LLM_CALLS > 0,
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
        "max_llm_calls": max(0, settings.CHAT_RUN_MAX_LLM_CALLS),
        "reserve_llm_calls": min(2, max(0, settings.CHAT_RUN_MAX_LLM_CALLS - 1)),
        "usage_estimated": False,
        "mode": "normal",
        "exhaustion_reason": None,
        "estimated_remaining_tokens": 0,
        "phases": {},
    }


class BudgetController:
    """Atomic run-level budget ledger shared by parallel orchestrator workers."""

    def __init__(self, ledger: dict[str, Any] | None = None) -> None:
        self._ledger = dict(ledger or initial_budget_ledger())
        self._reservations: dict[str, BudgetReservation] = {}
        self._lock = asyncio.Lock()
        # scope -> ceiling, and scope -> tokens committed. Held here rather than
        # counted by the caller because steps run concurrently: a caller reading
        # a snapshot before and after its own work would attribute a sibling's
        # spend to itself, and miss its own sub-agents entirely.
        self._scope_ceilings: dict[str, int] = {}
        self._scope_soft: dict[str, int] = {}
        self._scope_spend: dict[str, int] = {}

    def open_scope(self, scope: str, ceiling_tokens: int, soft_tokens: int = 0) -> None:
        """Bound one unit of work, including anything it delegates to.

        Two thresholds, because they answer different questions. ``soft_tokens``
        is the step's fair share of what the run has left -- crossing it means
        siblings are now being competed with, which is a reason to converge, not
        a reason to die. ``ceiling_tokens`` is the point where continuing would
        eat the run's finalization reserve, which is a reason to stop.

        Measured with the share as a hard cut, every configuration where it bound
        produced a degraded answer, because a step killed mid-work hands the
        verifier a truncated summary to reject.
        """
        if scope and ceiling_tokens > 0:
            self._scope_ceilings[scope] = ceiling_tokens
            self._scope_soft[scope] = soft_tokens if soft_tokens > 0 else ceiling_tokens
            self._scope_spend.setdefault(scope, 0)

    def close_scope(self, scope: str) -> None:
        self._scope_ceilings.pop(scope, None)
        self._scope_soft.pop(scope, None)
        self._scope_spend.pop(scope, None)
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

    def scope_exhausted(self, scope: str) -> bool:
        ceiling = self._scope_ceilings.get(scope)
        return ceiling is not None and self.scope_spend(scope) >= ceiling

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
        return soft is not None and self.scope_spend(scope) >= soft

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
        async with self._lock:
            self._authorize_locked(reservation)
            self._reservations[reservation.reservation_id] = reservation
        return reservation

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
            if usage_estimated:
                self._ledger["usage_estimated"] = True
            self._refresh_mode_locked()

    async def release(self, reservation: BudgetReservation) -> None:
        async with self._lock:
            self._reservations.pop(reservation.reservation_id, None)

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

    def _authorize_locked(self, reservation: BudgetReservation) -> None:
        # Checked before `enabled`, and before the finalization gate, because a
        # step ceiling bounds one step against its siblings rather than the run
        # against itself: it must hold even where the run-level dimensions are
        # all disabled, and a finalizing run's reserve is for the *run* to
        # summarize, not for an over-budget step to keep working.
        ceiling = self._scope_ceilings.get(reservation.scope)
        if ceiling is not None:
            # Count what the scope has in flight as well as what it has spent,
            # matching the run-wide check below. On committed spend alone a
            # parallel batch authorizes every call at once -- each sees the same
            # unchanged total -- so a scope can overshoot its ceiling by however
            # many delegations happen to start together, and starve the siblings
            # the ceiling exists to protect.
            in_flight = sum(
                item.estimated_input_tokens + item.estimated_output_tokens
                for item in self._reservations.values()
                if item.scope == reservation.scope
            )
            requested = reservation.estimated_input_tokens + reservation.estimated_output_tokens
            if self._scope_spend.get(reservation.scope, 0) + in_flight + requested > ceiling:
                raise BudgetExceeded(
                    f"Step {reservation.scope} reached its share of the run budget ({ceiling} tokens)."
                )
        if not self.enabled:
            return
        if self.finalizing and not reservation.allow_reserve:
            raise BudgetExceeded(self._ledger.get("exhaustion_reason") or "Run budget is reserved for finalization.")

        max_calls = int(self._ledger.get("max_llm_calls") or 0)
        reserve_calls = 0 if reservation.allow_reserve else int(self._ledger.get("reserve_llm_calls") or 0)
        projected_calls = int(self._ledger["llm_calls"]) + len(self._reservations) + 1
        if max_calls and projected_calls > max_calls - reserve_calls:
            self.begin_finalization("The run reached its LLM-call safety limit.")
            raise BudgetExceeded(str(self._ledger["exhaustion_reason"]))

        reserved_tokens = sum(
            item.estimated_input_tokens + item.estimated_output_tokens for item in self._reservations.values()
        )
        requested_tokens = reservation.estimated_input_tokens + reservation.estimated_output_tokens
        token_limit = int(self._ledger.get("token_limit") or 0)
        reserve_tokens = 0 if reservation.allow_reserve else int(self._ledger.get("reserve_tokens") or 0)
        projected_tokens = int(self._ledger["total_tokens"]) + reserved_tokens + requested_tokens
        if token_limit and projected_tokens > token_limit - reserve_tokens:
            self.begin_finalization("The run token budget is reserved for final synthesis.")
            raise BudgetExceeded(str(self._ledger["exhaustion_reason"]))

        cost_limit = float(self._ledger.get("cost_limit_usd") or 0.0)
        if cost_limit:
            reserved_cost = sum(item.estimated_cost_usd for item in self._reservations.values())
            reserve_cost = 0.0 if reservation.allow_reserve else float(self._ledger.get("reserve_cost_usd") or 0.0)
            if (
                float(self._ledger["cost_usd"]) + reserved_cost + reservation.estimated_cost_usd
                > cost_limit - reserve_cost
            ):
                self.begin_finalization("The run cost budget is reserved for final synthesis.")
                raise BudgetExceeded(str(self._ledger["exhaustion_reason"]))

    def _refresh_mode_locked(self) -> None:
        token_limit = int(self._ledger.get("token_limit") or 0)
        cost_limit = float(self._ledger.get("cost_limit_usd") or 0.0)
        max_calls = int(self._ledger.get("max_llm_calls") or 0)
        ratios = [
            int(self._ledger["total_tokens"]) / token_limit if token_limit else 0.0,
            float(self._ledger["cost_usd"]) / cost_limit if cost_limit else 0.0,
            int(self._ledger["llm_calls"]) / max_calls if max_calls else 0.0,
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
    return LlmUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(details.get("cache_read") or 0),
        cache_creation_tokens=int(details.get("cache_creation") or 0),
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
