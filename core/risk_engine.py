from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.exceptions import RiskBlockedError
from core.schemas import PortfolioSummary, ReviewPayload, SignalPayload
from core.utils import clamp

if TYPE_CHECKING:
    from core.app_context import AppContext


@dataclass
class ExecutionGuard:
    size: int
    price_limit: float
    notional_usd: float
    risk_fraction: float


class RiskEngine:
    def __init__(self, context: AppContext):
        self.context = context
        self.config = context.risk_config
        self.crypto = context.crypto_config

    async def refresh(self) -> None:
        await self.context.reload_configs()
        self.config = self.context.risk_config
        self.crypto = self.context.crypto_config

    def tier_settings(self, tier_name: str):
        return self.crypto.tier(tier_name)

    def kelly_fraction(self, edge: float, price: float) -> float:
        if edge < self.config.min_edge or price <= 0 or price >= 1:
            return 0.0
        kelly_fraction = edge / max(1 - price, 1e-6)
        return clamp(
            min(kelly_fraction * self.config.max_kelly_fraction, self.config.max_single_exposure_fraction),
            0.0,
            self.config.max_single_exposure_fraction,
        )

    def kelly_size(self, edge: float, price: float, bankroll: float) -> int:
        risk_fraction = self.kelly_fraction(edge, price)
        value_to_bet = min(bankroll * risk_fraction, self.config.max_single_position_usd)
        return max(0, int(value_to_bet / max(price, 1e-6)))

    async def validate_signal(self, signal: SignalPayload) -> None:
        tier = self.tier_settings(signal.crypto_tier)
        min_edge = max(self.config.min_edge, tier.min_edge)
        min_confidence = max(self.config.min_confidence, tier.min_confidence)
        min_volume = max(self.config.min_market_volume_24h, tier.min_volume_24h)

        if signal.edge < min_edge:
            raise RiskBlockedError(f"edge below minimum ({signal.edge:.3f} < {min_edge:.3f})")
        if signal.confidence < min_confidence:
            raise RiskBlockedError(
                f"confidence below minimum ({signal.confidence:.3f} < {min_confidence:.3f})"
            )
        if signal.volume_24h < min_volume:
            raise RiskBlockedError(f"market volume below minimum ({signal.volume_24h:.2f} < {min_volume:.2f})")
        if signal.regime == "illiquid_choppy":
            raise RiskBlockedError("strategy engine marked market as illiquid/choppy")
        spread_bps = abs(signal.price_yes - (1 - signal.price_no)) * 10000
        if spread_bps > self.config.max_spread_bps:
            raise RiskBlockedError(f"synthetic spread too wide ({spread_bps:.0f} bps)")
        liquidity_spread = float(signal.liquidity_summary.get("spread_bps") or 0.0)
        if liquidity_spread and liquidity_spread > self.config.max_spread_bps:
            raise RiskBlockedError(f"orderbook spread too wide ({liquidity_spread:.0f} bps)")
        if signal.expected_slippage_bps > self.config.max_slippage_bps:
            raise RiskBlockedError(
                f"expected slippage too high ({signal.expected_slippage_bps:.0f} > {self.config.max_slippage_bps:.0f})"
            )

    async def portfolio_state(self) -> PortfolioSummary:
        return await self.context.repository.get_portfolio_summary()

    def build_exit_plan(self, signal: SignalPayload) -> dict[str, float | int]:
        edge_buffer = max(signal.edge, 0.03)
        take_profit = clamp(signal.price + max(edge_buffer * 0.75, 0.04), 0.02, 0.98)
        stop_loss = clamp(signal.price - max(edge_buffer * 0.55, 0.03), 0.01, 0.96)
        time_stop = max(int(signal.expected_holding_minutes or 90), 30)
        if signal.regime == "mean_revert":
            take_profit = clamp(signal.price + max(edge_buffer * 0.55, 0.03), 0.02, 0.98)
            time_stop = max(int(time_stop * 0.75), 30)
        return {
            "take_profit_price": round(take_profit, 4),
            "stop_loss_price": round(stop_loss, 4),
            "time_stop_minutes": time_stop,
        }

    async def build_execution_guard(self, review: ReviewPayload) -> ExecutionGuard:
        portfolio = await self.portfolio_state()
        signal = review.original_signal
        tier = self.tier_settings(signal.crypto_tier)
        positions = await self._open_positions()
        risk_state = await self._recent_execution_state()
        if risk_state["circuit_breaker_active"]:
            raise RiskBlockedError("circuit breaker active due to recent losses")

        bankroll = max(portfolio.total_equity or portfolio.available_balance, 0.0)
        base_fraction = review.risk_fraction or self.kelly_fraction(signal.edge, signal.price)
        drawdown = 0.0
        settings = getattr(self.context, "settings", None)
        initial_bankroll = float(getattr(settings, "paper_bankroll_usd", bankroll) or bankroll or 1.0)
        if initial_bankroll > 0:
            drawdown = max((initial_bankroll - bankroll) / initial_bankroll, 0.0)
        if drawdown >= self.config.daily_drawdown_limit_fraction:
            raise RiskBlockedError("drawdown exceeds daily_drawdown_limit_fraction")

        drawdown_scale = clamp(
            1.0 - (drawdown / max(self.config.daily_drawdown_limit_fraction, 1e-6)) * 0.75,
            self.config.min_risk_fraction_after_losses,
            1.0,
        )
        loss_scale = clamp(
            1.0 - risk_state["consecutive_losses"] * self.config.loss_streak_size_discount,
            self.config.min_risk_fraction_after_losses,
            1.0,
        )
        risk_fraction = clamp(base_fraction * drawdown_scale * loss_scale, 0.0, self.config.max_single_exposure_fraction)
        value_to_bet = min(
            portfolio.available_balance * risk_fraction,
            self.config.max_single_position_usd,
            tier.max_position_usd,
        )
        size = review.kelly_size or max(0, int(value_to_bet / max(signal.price, 1e-6)))
        if size <= 0:
            raise RiskBlockedError("kelly sizing returned zero")

        notional = size * signal.price
        max_position_usd = min(self.config.max_single_position_usd, tier.max_position_usd)
        if notional > max_position_usd:
            raise RiskBlockedError("single position notional exceeds max_single_position_usd")
        if portfolio.total_exposure + notional > self.config.max_total_exposure_usd:
            raise RiskBlockedError("portfolio exposure exceeds max_total_exposure_usd")
        existing_position = next(
            (
                item
                for item in positions
                if str(item.get("market_id")) == signal.market_id and str(item.get("direction")) == signal.direction
            ),
            None,
        )
        if portfolio.open_positions >= self.config.max_open_positions and existing_position is None:
            raise RiskBlockedError("max_open_positions reached")
        effective_daily_limit = max(self.config.max_daily_spend_usd, max_position_usd)
        if risk_state["daily_spend_usd"] + notional > effective_daily_limit:
            raise RiskBlockedError("daily spend would exceed max_daily_spend_usd")

        total_equity = max(bankroll, initial_bankroll * 0.25, 1.0)
        asset_exposure = sum(
            float(item.get("cost_basis_usd") or 0.0) for item in positions if str(item.get("asset_symbol")) == signal.asset_symbol
        )
        if asset_exposure + notional > total_equity * self.config.max_asset_exposure_fraction:
            raise RiskBlockedError("asset exposure exceeds max_asset_exposure_fraction")
        strategy_exposure = sum(
            float(item.get("cost_basis_usd") or 0.0) for item in positions if str(item.get("strategy_id")) == signal.strategy_id
        )
        if strategy_exposure + notional > total_equity * self.config.max_strategy_exposure_fraction:
            raise RiskBlockedError("strategy exposure exceeds max_strategy_exposure_fraction")

        buffer = self.config.default_limit_buffer_bps / 10000
        price_limit = review.corrected_price_limit or min(signal.price + buffer, 0.99)
        if price_limit > self.config.max_order_price:
            raise RiskBlockedError("price limit exceeds max_order_price")
        slippage_bps = max(price_limit - signal.price, 0) * 10000
        if slippage_bps > self.config.max_slippage_bps:
            raise RiskBlockedError("slippage exceeds max_slippage_bps")
        ask_depth = float(signal.liquidity_summary.get("ask_depth") or 0.0)
        if ask_depth and notional > ask_depth:
            raise RiskBlockedError("order notional exceeds observed orderbook depth")
        if review.news_validation and not review.news_validation.validated:
            raise RiskBlockedError("news validation rejected signal")

        return ExecutionGuard(
            size=size,
            price_limit=price_limit,
            notional_usd=notional,
            risk_fraction=round(risk_fraction, 4),
        )

    async def _open_positions(self) -> list[dict[str, Any]]:
        repository = self.context.repository
        if hasattr(repository, "get_open_positions"):
            return await repository.get_open_positions()
        return []

    async def _recent_execution_state(self) -> dict[str, Any]:
        default = {
            "daily_spend_usd": 0.0,
            "realized_pnl_usd": 0.0,
            "consecutive_losses": 0,
            "last_loss_at": None,
            "circuit_breaker_active": False,
        }
        repository = self.context.repository
        if hasattr(repository, "get_execution_risk_state"):
            state = await repository.get_execution_risk_state(hours=24)
            if state.get("last_loss_at") and state["realized_pnl_usd"] <= -self.config.circuit_breaker_loss_threshold_usd:
                last_loss = state["last_loss_at"]
                if isinstance(last_loss, str):
                    last_loss = datetime.fromisoformat(last_loss.replace("Z", "+00:00"))
                state["circuit_breaker_active"] = (
                    datetime.now(UTC) - last_loss.astimezone(UTC)
                ).total_seconds() < self.config.circuit_breaker_cooldown_seconds
            return {**default, **state}
        if hasattr(repository, "get_recent_orders"):
            start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            orders = await repository.get_recent_orders(limit=200)
            daily_orders = [
                item
                for item in orders
                if self._as_utc(item.get("created_at")) >= start_of_day and str(item.get("status")) == "simulated"
            ]
            consecutive_losses = 0
            for item in reversed(daily_orders):
                pnl = float(item.get("realized_pnl_usd") or 0.0)
                if pnl < 0:
                    consecutive_losses += 1
                elif pnl > 0:
                    break
            daily_spend = sum(
                float(item.get("notional_usd") or 0.0)
                for item in daily_orders
                if str(item.get("action") or "entry") in {"entry", "scale_in"}
            )
            realized = sum(float(item.get("realized_pnl_usd") or 0.0) for item in daily_orders)
            last_loss = next(
                (
                    self._as_utc(item.get("created_at"))
                    for item in reversed(daily_orders)
                    if float(item.get("realized_pnl_usd") or 0.0) < 0
                ),
                None,
            )
            return {
                "daily_spend_usd": daily_spend,
                "realized_pnl_usd": realized,
                "consecutive_losses": consecutive_losses,
                "last_loss_at": last_loss,
                "circuit_breaker_active": bool(
                    last_loss
                    and realized <= -self.config.circuit_breaker_loss_threshold_usd
                    and (datetime.now(UTC) - last_loss).total_seconds() < self.config.circuit_breaker_cooldown_seconds
                ),
            }
        return default

    @staticmethod
    def _as_utc(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        if value in (None, ""):
            return datetime.now(UTC) - timedelta(days=3650)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)

    async def record_block(self, agent: str, reason: str, details: dict[str, Any]) -> None:
        payload = {"reason": reason, **details}
        await self.context.repository.record_risk_event(str(uuid4()), agent, reason, payload)
        await self.context.bus.publish_event(
            "events:risk",
            {"event_type": "risk.blocked", "version": "v1", "agent": agent, "reason": reason, "details": details},
        )
