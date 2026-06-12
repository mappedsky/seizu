"""Token and cost budgeting for headless chat orchestration."""

import asyncio
import math
import uuid
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

    def snapshot(self) -> dict[str, Any]:
        return dict(self._ledger)

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
    ) -> BudgetReservation:
        reservation = BudgetReservation(
            reservation_id=uuid.uuid4().hex,
            phase=phase,
            estimated_input_tokens=max(0, estimated_input_tokens),
            estimated_output_tokens=max(0, estimated_output_tokens),
            estimated_cost_usd=max(0.0, estimated_cost_usd),
            allow_reserve=allow_reserve,
        )
        async with self._lock:
            self._authorize_locked(reservation)
            self._reservations[reservation.reservation_id] = reservation
        return reservation

    async def commit(
        self,
        reservation: BudgetReservation,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        usage_estimated: bool,
    ) -> None:
        async with self._lock:
            self._reservations.pop(reservation.reservation_id, None)
            self._ledger["input_tokens"] += max(0, input_tokens)
            self._ledger["output_tokens"] += max(0, output_tokens)
            self._ledger["total_tokens"] = self._ledger["input_tokens"] + self._ledger["output_tokens"]
            self._ledger["cost_usd"] += max(0.0, cost_usd)
            self._ledger["llm_calls"] += 1
            phases = dict(self._ledger.get("phases") or {})
            phase_usage = dict(phases.get(reservation.phase) or {})
            phase_usage["input_tokens"] = int(phase_usage.get("input_tokens") or 0) + max(0, input_tokens)
            phase_usage["output_tokens"] = int(phase_usage.get("output_tokens") or 0) + max(0, output_tokens)
            phase_usage["total_tokens"] = int(phase_usage["input_tokens"]) + int(phase_usage["output_tokens"])
            phase_usage["cost_usd"] = float(phase_usage.get("cost_usd") or 0.0) + max(0.0, cost_usd)
            phase_usage["llm_calls"] = int(phase_usage.get("llm_calls") or 0) + 1
            phases[reservation.phase] = phase_usage
            self._ledger["phases"] = phases
            if usage_estimated:
                self._ledger["usage_estimated"] = True
            self._refresh_mode_locked()

    async def release(self, reservation: BudgetReservation) -> None:
        async with self._lock:
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
        if not self.enabled:
            return
        if self.finalizing and not reservation.allow_reserve:
            raise BudgetExceeded(self._ledger.get("exhaustion_reason") or "Run budget is reserved for finalization.")

        max_calls = int(self._ledger.get("max_llm_calls") or 0)
        reserve_calls = 0 if reservation.allow_reserve else int(self._ledger.get("reserve_llm_calls") or 0)
        reserved_calls = len(self._reservations)
        if max_calls and int(self._ledger["llm_calls"]) + reserved_calls >= max_calls - reserve_calls:
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


def usage_cost_usd(model: Any, input_tokens: int, output_tokens: int) -> float:
    model_name = str(getattr(model, "model_name", None) or getattr(model, "model", "") or "")
    if not model_name:
        return 0.0
    try:
        from litellm import cost_per_token

        input_cost, output_cost = cost_per_token(
            model=model_name,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        )
        return float(input_cost + output_cost)
    except Exception:
        return 0.0
