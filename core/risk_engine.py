from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.exceptions import RiskBlockedError
from core.schemas import PortfolioSummary, ReviewPayload, SignalPayload

if TYPE_CHECKING:
    from core.app_context import AppContext


@dataclass
class ExecutionGuard:
    size: int
    price_limit: float
    notional_usd: float


class RiskEngine:
    def __init__(self, context: AppContext):
        self.context = context
        self.config = context.risk_config

    async def refresh(self) -> None:
        await self.context.reload_configs()
        self.config = self.context.risk_config

    def kelly_size(self, edge: float, price: float, bankroll: float) -> int:
        if edge < self.config.min_edge or price <= 0 or price >= 1:
            return 0
        kelly_fraction = edge / max(1 - price, 1e-6)
        adjusted_fraction = min(
            kelly_fraction * self.config.max_kelly_fraction,
            self.config.max_single_exposure_fraction,
        )
        value_to_bet = min(bankroll * adjusted_fraction, self.config.max_single_position_usd)
        return max(0, int(value_to_bet / price))

    async def validate_signal(self, signal: SignalPayload) -> None:
        if signal.edge < self.config.min_edge:
            raise RiskBlockedError(f"edge below minimum ({signal.edge:.3f} < {self.config.min_edge:.3f})")
        if signal.confidence < self.config.min_confidence:
            raise RiskBlockedError(
                f"confidence below minimum ({signal.confidence:.3f} < {self.config.min_confidence:.3f})"
            )
        spread_bps = abs(signal.price_yes - (1 - signal.price_no)) * 10000
        if spread_bps > self.config.max_spread_bps:
            raise RiskBlockedError(f"synthetic spread too wide ({spread_bps:.0f} bps)")

    async def portfolio_state(self) -> PortfolioSummary:
        return await self.context.repository.get_portfolio_summary()

    async def build_execution_guard(self, review: ReviewPayload) -> ExecutionGuard:
        portfolio = await self.portfolio_state()
        signal = review.original_signal
        size = review.kelly_size or self.kelly_size(signal.edge, signal.price, portfolio.available_balance)
        if size <= 0:
            raise RiskBlockedError("kelly sizing returned zero")

        notional = size * signal.price
        if notional > self.config.max_single_position_usd:
            raise RiskBlockedError("single position notional exceeds max_single_position_usd")
        if portfolio.total_exposure + notional > self.config.max_total_exposure_usd:
            raise RiskBlockedError("portfolio exposure exceeds max_total_exposure_usd")
        if portfolio.open_positions >= self.config.max_open_positions:
            raise RiskBlockedError("max_open_positions reached")

        buffer = self.config.default_limit_buffer_bps / 10000
        price_limit = review.corrected_price_limit or min(signal.price + buffer, 0.99)
        slippage_bps = max(price_limit - signal.price, 0) * 10000
        if slippage_bps > self.config.max_slippage_bps:
            raise RiskBlockedError("slippage exceeds max_slippage_bps")

        return ExecutionGuard(size=size, price_limit=price_limit, notional_usd=notional)

    async def record_block(self, agent: str, reason: str, details: dict[str, Any]) -> None:
        payload = {"reason": reason, **details}
        await self.context.repository.record_risk_event(str(uuid4()), agent, reason, payload)
        await self.context.bus.publish_event(
            "events:risk",
            {"event_type": "risk.blocked", "version": "v1", "agent": agent, "reason": reason, "details": details},
        )
