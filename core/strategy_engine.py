from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import fmean
from typing import TYPE_CHECKING, Any, Literal

from core.utils import clamp, logit, sanitize_text, sigmoid

if TYPE_CHECKING:
    from core.app_context import AppContext


Regime = Literal["trend", "mean_revert", "illiquid_choppy"]
Direction = Literal["YES", "NO"]


@dataclass(slots=True)
class StrategyDecision:
    direction: Direction
    strategy_id: str
    strategy_version: str
    regime: Regime
    model_probability: float
    market_probability: float
    edge: float
    confidence: float
    expected_slippage_bps: float
    expected_holding_minutes: int
    reasoning: str
    features_summary: dict[str, Any]


class StrategyEngine:
    def __init__(self, context: AppContext):
        self.context = context

    async def analyze_market(self, market: dict[str, Any]) -> StrategyDecision | None:
        tier = self.context.crypto_config.tier(str(market.get("crypto_tier") or "small_cap"))
        history = await self._recent_market_history(str(market.get("id") or ""))
        price_yes = float(market.get("price_yes") or 0.0)
        price_no = float(market.get("price_no") or max(1 - price_yes, 0.0))
        momentum_min_edge = max(float(self.context.settings.momentum_min_edge), 0.10)
        momentum_min_volume = max(float(self.context.settings.momentum_min_volume_24h), 750.0)
        volume_24h = float(market.get("volume_24h") or 0.0)
        if volume_24h < momentum_min_volume:
            return None
        yes_book = market.get("orderbook_summary_yes") or {}
        no_book = market.get("orderbook_summary_no") or {}
        avg_spread_bps = fmean(
            [
                float(yes_book.get("spread_bps") or 0.0),
                float(no_book.get("spread_bps") or 0.0),
            ]
        )
        yes_depth = float(yes_book.get("bid_depth") or 0.0) + float(yes_book.get("ask_depth") or 0.0)
        no_depth = float(no_book.get("bid_depth") or 0.0) + float(no_book.get("ask_depth") or 0.0)
        depth_total = yes_depth + no_depth
        liquidity_score = clamp(depth_total / max(tier.max_position_usd * 8, 1.0), 0.0, 2.0)
        volume_ratio = clamp(float(market.get("volume_24h") or 0.0) / max(tier.min_volume_24h, 1.0), 0.0, 3.0)
        momentum_short = self._momentum(history, "price_yes", periods=3)
        momentum_medium = self._momentum(history, "price_yes", periods=6)
        mean_price = self._mean_price(history, fallback=price_yes)
        reversion_gap = price_yes - mean_price
        orderbook_bias = self._orderbook_bias(yes_book, no_book)
        regime = self._detect_regime(
            avg_spread_bps=avg_spread_bps,
            liquidity_score=liquidity_score,
            momentum_short=momentum_short,
            momentum_medium=momentum_medium,
        )
        if regime == "illiquid_choppy":
            return None

        time_to_expiry_hours = self._time_to_expiry_hours(market)
        time_penalty = 0.0 if time_to_expiry_hours is None else clamp((24 - time_to_expiry_hours) / 24, 0.0, 1.0)

        market_probability_yes = clamp(price_yes, 0.01, 0.99)
        evidence = 0.0
        evidence += clamp(orderbook_bias * 3.0, -0.85, 0.85)
        evidence += clamp(momentum_short * 8.0, -0.25, 0.25)
        evidence += clamp(momentum_medium * 6.0, -0.18, 0.18)
        evidence += clamp((volume_ratio - 1.0) * 0.12, -0.10, 0.18)
        evidence -= clamp(avg_spread_bps / 1200.0, 0.0, 0.18)
        evidence -= time_penalty * 0.08
        if regime == "mean_revert":
            evidence -= clamp(reversion_gap * 4.0, -0.18, 0.18)

        posterior_yes = clamp(sigmoid(logit(market_probability_yes) + evidence), 0.02, 0.98)
        expected_slippage_bps = round(max(avg_spread_bps * 0.35, 8.0), 2)

        direction: Direction = "YES"
        model_probability = posterior_yes
        market_probability = price_yes
        edge = posterior_yes - price_yes - (expected_slippage_bps / 10000)
        strategy_id = "trend_follow_bayes"
        if regime == "mean_revert":
            strategy_id = "mean_revert_bayes"
        if posterior_yes < 0.5:
            direction = "NO"
            model_probability = 1 - posterior_yes
            market_probability = price_no
            edge = (1 - posterior_yes) - price_no - (expected_slippage_bps / 10000)

        if edge < momentum_min_edge:
            return None

        confidence = clamp(
            0.52
            + min(abs(model_probability - market_probability) * 2.4, 0.22)
            + min(liquidity_score * 0.06, 0.12)
            + min(volume_ratio * 0.04, 0.10),
            0.5,
            0.95,
        )
        expected_holding_minutes = 180 if regime == "trend" else 90
        features_summary = {
            "market_price_yes": round(price_yes, 4),
            "market_price_no": round(price_no, 4),
            "posterior_yes": round(posterior_yes, 4),
            "momentum_short": round(momentum_short, 4),
            "momentum_medium": round(momentum_medium, 4),
            "mean_reversion_gap": round(reversion_gap, 4),
            "orderbook_bias": round(orderbook_bias, 4),
            "volume_ratio": round(volume_ratio, 4),
            "liquidity_score": round(liquidity_score, 4),
            "avg_spread_bps": round(avg_spread_bps, 2),
            "time_to_expiry_hours": None if time_to_expiry_hours is None else round(time_to_expiry_hours, 2),
        }
        reasoning = (
            f"{strategy_id} em regime {regime}: "
            f"posterior {model_probability:.3f} vs mercado {market_probability:.3f}, "
            f"momentum {momentum_short:.3f}/{momentum_medium:.3f}, "
            f"bias livro {orderbook_bias:.3f}, spread {avg_spread_bps:.0f}bps."
        )
        return StrategyDecision(
            direction=direction,
            strategy_id=strategy_id,
            strategy_version="v1",
            regime=regime,
            model_probability=round(model_probability, 4),
            market_probability=round(market_probability, 4),
            edge=round(edge, 4),
            confidence=round(confidence, 4),
            expected_slippage_bps=expected_slippage_bps,
            expected_holding_minutes=expected_holding_minutes,
            reasoning=sanitize_text(reasoning, 280),
            features_summary=features_summary,
        )

    async def _recent_market_history(self, market_id: str) -> list[dict[str, Any]]:
        if not market_id:
            return []
        repository = self.context.repository
        if not hasattr(repository, "get_market_snapshots"):
            return []
        try:
            snapshots = await repository.get_market_snapshots(market_id=market_id, limit=12)
        except Exception:
            return []
        return snapshots[-12:]

    @staticmethod
    def _momentum(history: list[dict[str, Any]], key: str, *, periods: int) -> float:
        values = [float(item.get(key) or 0.0) for item in history if item.get(key) is not None]
        if len(values) < 2:
            return 0.0
        anchor_index = max(0, len(values) - periods - 1)
        anchor = values[anchor_index]
        latest = values[-1]
        return latest - anchor

    @staticmethod
    def _mean_price(history: list[dict[str, Any]], *, fallback: float) -> float:
        values = [float(item.get("price_yes") or 0.0) for item in history if item.get("price_yes") is not None]
        if not values:
            return fallback
        return fmean(values[-6:])

    @staticmethod
    def _orderbook_bias(yes_book: dict[str, Any], no_book: dict[str, Any]) -> float:
        yes_support = float(yes_book.get("bid_depth") or 0.0) - float(yes_book.get("ask_depth") or 0.0)
        no_support = float(no_book.get("bid_depth") or 0.0) - float(no_book.get("ask_depth") or 0.0)
        denominator = max(
            abs(yes_support) + abs(no_support) + float(yes_book.get("bid_depth") or 0.0) + float(no_book.get("bid_depth") or 0.0),
            1.0,
        )
        return clamp((yes_support - no_support) / denominator, -1.0, 1.0)

    @staticmethod
    def _detect_regime(
        *,
        avg_spread_bps: float,
        liquidity_score: float,
        momentum_short: float,
        momentum_medium: float,
    ) -> Regime:
        if avg_spread_bps >= 180 or liquidity_score < 0.4:
            return "illiquid_choppy"
        if abs(momentum_short) >= 0.035 or abs(momentum_medium) >= 0.055:
            return "trend"
        return "mean_revert"

    @staticmethod
    def _time_to_expiry_hours(market: dict[str, Any]) -> float | None:
        candidates = [
            market.get("end_date"),
            market.get("endDate"),
            market.get("close_date"),
            market.get("closeTime"),
        ]
        for value in candidates:
            if not value:
                continue
            try:
                raw = str(value).strip().replace("Z", "+00:00")
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return max((parsed.astimezone(UTC) - datetime.now(UTC)).total_seconds() / 3600, 0.0)
            except Exception:
                continue
        return None
