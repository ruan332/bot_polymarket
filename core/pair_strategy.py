from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import fmean, pstdev
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from core.crypto import ASSET_NAMES
from core.schemas import (
    MarketSnapshotPayload,
    PairCycleStatePayload,
    PairLegPlan,
    PairSignalPayload,
)
from core.utils import clamp, sanitize_text

if TYPE_CHECKING:
    from core.app_context import AppContext
    from core.market_connector import MarketConnector


DirectionHint = Literal["up", "down"]


def floor_cycle_start(value: datetime) -> datetime:
    utc_value = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    minute_bucket = (utc_value.minute // 15) * 15
    return utc_value.replace(minute=minute_bucket, second=0, microsecond=0)


def cycle_slug_for(asset_symbol: str, cycle_start: datetime) -> str:
    return f"{asset_symbol.lower()}-updown-15m-{int(floor_cycle_start(cycle_start).timestamp())}"


@dataclass(slots=True)
class Quote:
    token_id: str
    best_bid: float
    best_ask: float
    updated_at: datetime
    bid_depth: float = 0.0
    ask_depth: float = 0.0

    @property
    def spread_bps(self) -> float:
        if self.best_bid <= 0 or self.best_ask <= 0:
            return 0.0
        mid = (self.best_bid + self.best_ask) / 2
        if mid <= 0:
            return 0.0
        return abs(self.best_ask - self.best_bid) / mid * 10000

    def as_summary(self) -> dict[str, float]:
        return {
            "best_bid": round(self.best_bid, 4),
            "best_ask": round(self.best_ask, 4),
            "spread_bps": round(self.spread_bps, 2),
            "bid_depth": round(self.bid_depth, 4),
            "ask_depth": round(self.ask_depth, 4),
        }


@dataclass(slots=True)
class PredictorSignal:
    predicted_price: float
    pole_price: float
    direction: DirectionHint
    signal: Literal["BUY_UP", "BUY_DOWN"]
    confidence: float
    features: dict[str, float]
    stats: dict[str, float]


class AdaptivePricePredictor:
    def __init__(
        self,
        *,
        noise_threshold: float = 0.02,
        min_history_points: int = 6,
        learning_rate: float = 0.12,
    ) -> None:
        self.noise_threshold = noise_threshold
        self.min_history_points = max(min_history_points, 4)
        self.learning_rate = learning_rate
        self.history: list[float] = []
        self.weights: dict[str, float] = {
            "lag1": 0.35,
            "lag2": 0.18,
            "lag3": 0.12,
            "momentum": 0.45,
            "volatility": -0.28,
            "trend": 0.32,
        }
        self.intercept = 0.0
        self.last_prediction: dict[str, Any] | None = None
        self.last_pole_price: float | None = None
        self.total_predictions = 0
        self.correct_predictions = 0

    def observe(self, price: float) -> PredictorSignal | None:
        price = float(price or 0.0)
        if price < 0.003 or price > 0.97:
            return None
        if self.history and abs(price - self.history[-1]) < self.noise_threshold:
            return None
        self.history.append(price)
        self.history = self.history[-24:]
        confirmed = self._confirmed_pole()
        if confirmed is None:
            return None
        pole_price, series = confirmed
        if self.last_pole_price is not None and abs(pole_price - self.last_pole_price) < self.noise_threshold:
            return None
        self.last_pole_price = pole_price
        if self.last_prediction is not None:
            self._learn(actual=pole_price)
        if len(series) < self.min_history_points:
            return None
        features = self._build_features(series)
        predicted_price = clamp(pole_price + self._raw_delta(features), 0.02, 0.98)
        direction: DirectionHint = "up" if predicted_price >= pole_price else "down"
        confidence = self._confidence(predicted_price=predicted_price, pole_price=pole_price, features=features)
        signal: Literal["BUY_UP", "BUY_DOWN"] = "BUY_UP" if direction == "up" else "BUY_DOWN"
        stats = {
            "accuracy": round(self.correct_predictions / self.total_predictions, 4) if self.total_predictions else 0.5,
            "history_points": float(len(series)),
        }
        self.last_prediction = {
            "predicted_price": predicted_price,
            "pole_price": pole_price,
            "direction": direction,
            "features": features,
        }
        return PredictorSignal(
            predicted_price=round(predicted_price, 4),
            pole_price=round(pole_price, 4),
            direction=direction,
            signal=signal,
            confidence=round(confidence, 4),
            features={key: round(value, 4) for key, value in features.items()},
            stats=stats,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "history": [round(item, 6) for item in self.history[-24:]],
            "weights": {key: round(value, 6) for key, value in self.weights.items()},
            "intercept": round(self.intercept, 6),
            "last_prediction": self.last_prediction,
            "last_pole_price": self.last_pole_price,
            "total_predictions": self.total_predictions,
            "correct_predictions": self.correct_predictions,
            "noise_threshold": self.noise_threshold,
            "min_history_points": self.min_history_points,
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any] | None, *, fallback_noise: float, fallback_history_points: int) -> "AdaptivePricePredictor":
        instance = cls(
            noise_threshold=float((payload or {}).get("noise_threshold") or fallback_noise),
            min_history_points=int((payload or {}).get("min_history_points") or fallback_history_points),
        )
        if not payload:
            return instance
        instance.history = [float(item) for item in payload.get("history", [])]
        instance.weights.update({key: float(value) for key, value in (payload.get("weights") or {}).items()})
        instance.intercept = float(payload.get("intercept") or 0.0)
        instance.last_prediction = payload.get("last_prediction") if isinstance(payload.get("last_prediction"), dict) else None
        instance.last_pole_price = None if payload.get("last_pole_price") in (None, "") else float(payload.get("last_pole_price"))
        instance.total_predictions = int(payload.get("total_predictions") or 0)
        instance.correct_predictions = int(payload.get("correct_predictions") or 0)
        return instance

    def _confirmed_pole(self) -> tuple[float, list[float]] | None:
        if len(self.history) < 4:
            return None
        prev2, prev1, current = self.history[-3], self.history[-2], self.history[-1]
        is_peak = prev1 > prev2 and prev1 > current
        is_valley = prev1 < prev2 and prev1 < current
        if not is_peak and not is_valley:
            return None
        series = self.history[:-1]
        if len(series) < 4:
            return None
        return float(series[-1]), series

    def _build_features(self, series: list[float]) -> dict[str, float]:
        latest = series[-1]
        lag1 = latest - series[-2]
        lag2 = series[-2] - series[-3]
        lag3 = series[-3] - series[-4]
        momentum = latest - series[max(0, len(series) - 4)]
        window = series[-6:]
        volatility = pstdev(window) if len(window) > 1 else 0.0
        short_window = series[-3:]
        ema_short = fmean(short_window)
        ema_long = fmean(window)
        trend = ema_short - ema_long
        return {
            "lag1": lag1,
            "lag2": lag2,
            "lag3": lag3,
            "momentum": momentum,
            "volatility": volatility,
            "trend": trend,
        }

    def _raw_delta(self, features: dict[str, float]) -> float:
        raw = self.intercept
        for key, value in features.items():
            raw += self.weights.get(key, 0.0) * value
        return clamp(raw, -0.18, 0.18)

    def _confidence(self, *, predicted_price: float, pole_price: float, features: dict[str, float]) -> float:
        volatility = abs(features.get("volatility", 0.0))
        momentum = abs(features.get("momentum", 0.0))
        trend = abs(features.get("trend", 0.0))
        accuracy = self.correct_predictions / self.total_predictions if self.total_predictions else 0.5
        confidence = 0.5
        confidence += min(abs(predicted_price - pole_price) * 3.5, 0.18)
        confidence += min((momentum + trend) * 2.4, 0.18)
        confidence += min(max(accuracy - 0.5, 0.0) * 0.3, 0.08)
        confidence -= min(volatility * 2.1, 0.14)
        if confidence > 0.9:
            confidence -= 0.03
        return clamp(confidence, 0.5, 0.95)

    def _learn(self, *, actual: float) -> None:
        if self.last_prediction is None:
            return
        predicted_price = float(self.last_prediction.get("predicted_price") or 0.0)
        pole_price = float(self.last_prediction.get("pole_price") or predicted_price)
        direction = str(self.last_prediction.get("direction") or "up")
        features = self.last_prediction.get("features") or {}
        actual_direction = "up" if actual >= pole_price else "down"
        self.total_predictions += 1
        if actual_direction == direction:
            self.correct_predictions += 1
        error = actual - predicted_price
        step = self.learning_rate + min(abs(error) * 0.8, 0.18)
        if actual_direction != direction:
            step *= 1.3
        for key, value in features.items():
            self.weights[key] = clamp((self.weights.get(key, 0.0) * 0.995) + (step * error * float(value)), -2.5, 2.5)
        self.intercept = clamp((self.intercept * 0.995) + (step * error), -0.3, 0.3)


@dataclass(slots=True)
class PairCycleRuntime:
    asset_symbol: str
    asset_name: str
    crypto_tier: Literal["btc", "major", "small_cap"]
    cycle_slug: str
    cycle_start: datetime
    market_id: str
    market_question: str
    token_id_yes: str
    token_id_no: str
    predictor: AdaptivePricePredictor
    side_counts: dict[str, int]
    max_buy_counts_per_side: int
    price_yes: float = 0.0
    price_no: float = 0.0
    status: Literal["active", "paused", "rolled"] = "active"
    last_signal_direction: Literal["YES", "NO"] | None = None
    last_signal_at: datetime | None = None
    last_quote_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> PairCycleStatePayload:
        return PairCycleStatePayload(
            asset_symbol=self.asset_symbol,
            asset_name=self.asset_name,
            crypto_tier=self.crypto_tier,
            cycle_slug=self.cycle_slug,
            cycle_start=self.cycle_start,
            market_id=self.market_id,
            market_question=self.market_question,
            token_id_yes=self.token_id_yes,
            token_id_no=self.token_id_no,
            price_yes=round(self.price_yes, 4),
            price_no=round(self.price_no, 4),
            status=self.status,
            side_counts={key: int(value) for key, value in self.side_counts.items()},
            max_buy_counts_per_side=self.max_buy_counts_per_side,
            last_signal_direction=self.last_signal_direction,
            last_signal_at=self.last_signal_at,
            last_quote_at=self.last_quote_at,
            predictor_state=self.predictor.snapshot(),
            metadata=self.metadata,
            updated_at=datetime.now(UTC),
        )


class PairTradingEngine:
    def __init__(self, context: AppContext, connector: MarketConnector):
        self.context = context
        self.connector = connector
        self.cycles: dict[str, PairCycleRuntime] = {}
        self.quote_cache: dict[str, Quote] = {}
        self.feed_task: asyncio.Task[None] | None = None
        self.feed_tokens: tuple[str, ...] = ()
        self.feed_error: str | None = None

    async def close(self) -> None:
        if self.feed_task is not None:
            self.feed_task.cancel()
            try:
                await self.feed_task
            except asyncio.CancelledError:
                pass
            self.feed_task = None
            self.feed_tokens = ()

    async def tick(self) -> dict[str, Any]:
        stats = {
            "requested_limit": len(self.context.settings.copytrade_markets),
            "gamma_markets_fetched": 0,
            "crypto_classified": 0,
            "selected_for_scan": 0,
            "strategy_candidates": 0,
            "reached_risk_engine": 0,
            "risk_passed": 0,
            "risk_blocked": 0,
            "duplicates_blocked": 0,
            "persisted_signals": 0,
            "selected_markets": [],
            "risk_block_reasons": {},
            "discovery_source": "pair_strategy",
        }
        if not self.context.settings.copytrade_enabled:
            return stats
        await self._refresh_cycles(stats)
        await self._ensure_feed()
        stats["selected_for_scan"] = len(self.cycles)
        stats["crypto_classified"] = len(self.cycles)
        snapshots: list[MarketSnapshotPayload] = []
        for asset_symbol, cycle in self.cycles.items():
            await self._sync_cycle_state(cycle)
            yes_quote, no_quote = await self._quotes_for_cycle(cycle)
            if yes_quote is None or no_quote is None or yes_quote.best_ask <= 0 or no_quote.best_ask <= 0:
                self._increment_reason(stats, "quote_unavailable")
                continue
            cycle.price_yes = yes_quote.best_ask
            cycle.price_no = no_quote.best_ask
            cycle.last_quote_at = max(yes_quote.updated_at, no_quote.updated_at)
            snapshots.append(
                MarketSnapshotPayload(
                    market_id=cycle.market_id,
                    question=cycle.market_question,
                    token_id_yes=cycle.token_id_yes,
                    token_id_no=cycle.token_id_no,
                    price_yes=cycle.price_yes,
                    price_no=cycle.price_no,
                    volume_24h=float(cycle.metadata.get("volume_24h") or 0.0),
                    asset_symbol=cycle.asset_symbol,
                    asset_name=cycle.asset_name,
                    crypto_tier=cycle.crypto_tier,
                    market_kind="direct_coin",
                    question_type="direction",
                    thesis_tags=[cycle.asset_symbol.lower(), "pair_15m", "updown", "15m"],
                    metadata={
                        **cycle.metadata,
                        "source": "pair_strategy",
                        "cycle_slug": cycle.cycle_slug,
                        "orderbook_summary_yes": yes_quote.as_summary(),
                        "orderbook_summary_no": no_quote.as_summary(),
                    },
                )
            )
            predictor_signal = cycle.predictor.observe(yes_quote.best_ask)
            if predictor_signal is None:
                await self.context.repository.upsert_pair_cycle(cycle.to_payload())
                continue
            stats["strategy_candidates"] += 1
            stats["reached_risk_engine"] += 1
            if predictor_signal.confidence < self.context.settings.copytrade_signal_confidence_threshold:
                self._increment_reason(stats, "confidence_below_threshold")
                await self.context.repository.upsert_pair_cycle(cycle.to_payload())
                continue
            primary_direction: Literal["YES", "NO"] = "YES" if predictor_signal.signal == "BUY_UP" else "NO"
            side_key = primary_direction.lower()
            if int(cycle.side_counts.get(side_key, 0)) >= cycle.max_buy_counts_per_side:
                cycle.status = "paused"
                self._increment_reason(stats, "side_limit_reached")
                await self.context.repository.upsert_pair_cycle(cycle.to_payload())
                continue
            if self._is_duplicate_signal(cycle, primary_direction):
                stats["duplicates_blocked"] += 1
                await self.context.repository.upsert_pair_cycle(cycle.to_payload())
                continue
            signal = self._build_signal(cycle, predictor_signal, yes_quote=yes_quote, no_quote=no_quote)
            price_guard_reason = self._signal_price_guard_reason(signal)
            if price_guard_reason is not None:
                stats["risk_blocked"] += 1
                self._increment_reason(stats, price_guard_reason)
                await self.context.repository.upsert_pair_cycle(cycle.to_payload())
                continue
            self._reserve_cycle_side(cycle, primary_direction)
            cycle.status = "active"
            cycle.last_signal_direction = primary_direction
            cycle.last_signal_at = datetime.now(UTC)
            await self.context.repository.record_signal(signal.signal_id, signal.event_type, signal.model_dump(mode="json"))
            await self.context.bus.publish_event("signals:validated", signal.model_dump(mode="json"))
            await self.context.repository.upsert_pair_cycle(cycle.to_payload())
            stats["risk_passed"] += 1
            stats["persisted_signals"] += 1
            stats["selected_markets"].append(
                {
                    "market_id": cycle.market_id,
                    "asset_symbol": cycle.asset_symbol,
                    "crypto_tier": cycle.crypto_tier,
                    "market_kind": "direct_coin",
                    "volume_24h": float(cycle.metadata.get("volume_24h") or 0.0),
                    "question": cycle.market_question,
                }
            )
        if snapshots:
            await self.context.repository.record_market_snapshots(snapshots)
        await self.context.repository.record_pipeline_telemetry(
            str(uuid4()),
            "claude",
            "scanner.scan_cycle",
            {
                **stats,
                "selected_markets": stats["selected_markets"][:6],
                "news_validation_enabled": False,
                "feed_error": self.feed_error or "",
            },
        )
        return stats

    async def _refresh_cycles(self, stats: dict[str, Any]) -> None:
        desired_assets = [item.upper() for item in self.context.settings.copytrade_markets]
        known_assets = set(self.cycles.keys())
        for asset_symbol in list(known_assets - set(desired_assets)):
            self.cycles.pop(asset_symbol, None)
        for asset_symbol in desired_assets:
            resolved = await self._resolve_cycle(asset_symbol)
            if resolved is not None:
                self.cycles[asset_symbol] = resolved
                stats["gamma_markets_fetched"] += 1

    async def _resolve_cycle(self, asset_symbol: str) -> PairCycleRuntime | None:
        now = datetime.now(UTC)
        cycle_start = floor_cycle_start(now)
        candidate_starts = [cycle_start]
        if (not self.context.settings.live_trading) or (not self.context.settings.copytrade_wait_for_next_market_start):
            candidate_starts.append(cycle_start - timedelta(minutes=15))
        current = self.cycles.get(asset_symbol)
        if current is not None and current.cycle_slug == cycle_slug_for(asset_symbol, cycle_start):
            return current
        repo_state = await self.context.repository.get_pair_cycle(asset_symbol)
        for candidate_start in candidate_starts:
            cycle_slug = cycle_slug_for(asset_symbol, candidate_start)
            if current is not None and current.cycle_slug == cycle_slug:
                return current
            if repo_state and str(repo_state.get("cycle_slug") or "") == cycle_slug:
                if not repo_state.get("market_id") or not repo_state.get("token_id_yes") or not repo_state.get("token_id_no"):
                    repo_state = None
                else:
                    predictor = AdaptivePricePredictor.from_snapshot(
                        repo_state.get("predictor_state"),
                        fallback_noise=self.context.settings.copytrade_noise_threshold,
                        fallback_history_points=self.context.settings.copytrade_min_history_points,
                    )
                    return PairCycleRuntime(
                        asset_symbol=asset_symbol,
                        asset_name=str(repo_state.get("asset_name") or ASSET_NAMES.get(asset_symbol, asset_symbol)),
                        crypto_tier=str(repo_state.get("crypto_tier") or self._crypto_tier(asset_symbol)),  # type: ignore[arg-type]
                        cycle_slug=cycle_slug,
                        cycle_start=repo_state.get("cycle_start") or candidate_start,
                        market_id=str(repo_state.get("market_id") or ""),
                        market_question=str(repo_state.get("market_question") or ""),
                        token_id_yes=str(repo_state.get("token_id_yes") or ""),
                        token_id_no=str(repo_state.get("token_id_no") or ""),
                        predictor=predictor,
                        side_counts={key: int(value) for key, value in (repo_state.get("side_counts") or {}).items()},
                        max_buy_counts_per_side=int(repo_state.get("max_buy_counts_per_side") or self.context.settings.copytrade_max_buy_counts_per_side),
                        price_yes=float(repo_state.get("price_yes") or 0.0),
                        price_no=float(repo_state.get("price_no") or 0.0),
                        status=str(repo_state.get("status") or "active"),  # type: ignore[arg-type]
                        last_signal_direction=repo_state.get("last_signal_direction"),
                        last_signal_at=repo_state.get("last_signal_at"),
                        last_quote_at=repo_state.get("last_quote_at"),
                        metadata=dict(repo_state.get("metadata") or {}),
                    )
            market = await self.connector.resolve_copytrade_market(asset_symbol, candidate_start)
            if market is None:
                continue
            runtime = PairCycleRuntime(
                asset_symbol=asset_symbol,
                asset_name=str(market.get("asset_name") or ASSET_NAMES.get(asset_symbol, asset_symbol)),
                crypto_tier=self._crypto_tier(asset_symbol),
                cycle_slug=cycle_slug,
                cycle_start=candidate_start,
                market_id=str(market["id"]),
                market_question=str(market.get("question") or ""),
                token_id_yes=str(market["token_id_yes"]),
                token_id_no=str(market["token_id_no"]),
                predictor=AdaptivePricePredictor(
                    noise_threshold=self.context.settings.copytrade_noise_threshold,
                    min_history_points=self.context.settings.copytrade_min_history_points,
                ),
                side_counts={"yes": 0, "no": 0},
                max_buy_counts_per_side=self.context.settings.copytrade_max_buy_counts_per_side,
                metadata={
                    "slug": market.get("slug", cycle_slug),
                    "volume_24h": float(market.get("volume_24h") or 0.0),
                    "end_date": market.get("end_date") or "",
                },
            )
            await self.context.repository.upsert_pair_cycle(runtime.to_payload())
            return runtime
        return current

    async def _ensure_feed(self) -> None:
        desired_tokens = tuple(
            sorted(
                {
                    token_id
                    for cycle in self.cycles.values()
                    for token_id in (cycle.token_id_yes, cycle.token_id_no)
                    if token_id
                }
            )
        )
        if not desired_tokens:
            await self.close()
            return
        if self.feed_task is not None and not self.feed_task.done() and desired_tokens == self.feed_tokens:
            return
        await self.close()
        self.feed_tokens = desired_tokens
        self.feed_task = asyncio.create_task(self._feed_loop(list(desired_tokens)))

    async def _feed_loop(self, token_ids: list[str]) -> None:
        while token_ids:
            try:
                async for message in self.connector.stream_market(token_ids):
                    for token_id, quote in self._extract_quotes(message):
                        self.quote_cache[token_id] = quote
                    self.feed_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.feed_error = sanitize_text(str(exc), 200)
                await asyncio.sleep(1.0)

    async def _quotes_for_cycle(self, cycle: PairCycleRuntime) -> tuple[Quote | None, Quote | None]:
        yes_quote = self.quote_cache.get(cycle.token_id_yes)
        no_quote = self.quote_cache.get(cycle.token_id_no)
        if self._quote_stale(yes_quote):
            yes_quote = await self._quote_from_orderbook(cycle.token_id_yes)
        if self._quote_stale(no_quote):
            no_quote = await self._quote_from_orderbook(cycle.token_id_no)
        return yes_quote, no_quote

    async def _quote_from_orderbook(self, token_id: str) -> Quote | None:
        summary = await self.connector.get_orderbook_summary(token_id)
        if not summary:
            return None
        quote = Quote(
            token_id=token_id,
            best_bid=float(summary.get("best_bid") or 0.0),
            best_ask=float(summary.get("best_ask") or 0.0),
            updated_at=datetime.now(UTC),
            bid_depth=float(summary.get("bid_depth") or 0.0),
            ask_depth=float(summary.get("ask_depth") or 0.0),
        )
        self.quote_cache[token_id] = quote
        return quote

    def _build_signal(
        self,
        cycle: PairCycleRuntime,
        predictor_signal: PredictorSignal,
        *,
        yes_quote: Quote,
        no_quote: Quote,
    ) -> PairSignalPayload:
        primary_direction: Literal["YES", "NO"] = "YES" if predictor_signal.signal == "BUY_UP" else "NO"
        primary_quote = yes_quote if primary_direction == "YES" else no_quote
        hedge_direction: Literal["YES", "NO"] = "NO" if primary_direction == "YES" else "YES"
        hedge_token_id = cycle.token_id_no if hedge_direction == "NO" else cycle.token_id_yes
        hedge_reference = primary_quote.best_ask
        primary_target = clamp(
            primary_quote.best_ask + self.context.settings.copytrade_price_buffer,
            0.01,
            0.99,
        )
        hedge_target = clamp(
            self.context.settings.copytrade_second_leg_base_price - hedge_reference,
            0.01,
            0.97,
        )
        shares = max(self.context.settings.copytrade_shares, 1)
        primary_leg = PairLegPlan(
            market_id=cycle.market_id,
            token_id=primary_quote.token_id,
            direction=primary_direction,
            leg_role="primary",
            size=shares,
            target_price=round(primary_target, 4),
            reference_price=round(primary_quote.best_ask, 4),
            current_ask=round(primary_quote.best_ask, 4),
            current_bid=round(primary_quote.best_bid, 4),
        )
        hedge_quote = no_quote if hedge_direction == "NO" else yes_quote
        hedge_leg = PairLegPlan(
            market_id=cycle.market_id,
            token_id=hedge_token_id,
            direction=hedge_direction,
            leg_role="hedge",
            size=shares,
            target_price=round(hedge_target, 4),
            reference_price=round(hedge_reference, 4),
            current_ask=round(hedge_quote.best_ask, 4),
            current_bid=round(hedge_quote.best_bid, 4),
        )
        side_counts = {
            "yes": int(cycle.side_counts.get("yes", 0)),
            "no": int(cycle.side_counts.get("no", 0)),
            "max_per_side": cycle.max_buy_counts_per_side,
        }
        reasoning = (
            f"pair_15m {cycle.asset_symbol} {cycle.cycle_slug}: "
            f"sinal {predictor_signal.signal} em polo {predictor_signal.pole_price:.4f}, "
            f"predito {predictor_signal.predicted_price:.4f}, "
            f"confianca {predictor_signal.confidence:.3f}."
        )
        return PairSignalPayload(
            signal_id=str(uuid4()),
            trade_group_id=str(uuid4()),
            cycle_slug=cycle.cycle_slug,
            cycle_start=cycle.cycle_start,
            market_id=cycle.market_id,
            market_question=cycle.market_question,
            asset_symbol=cycle.asset_symbol,
            asset_name=cycle.asset_name,
            crypto_tier=cycle.crypto_tier,
            predictor_direction=predictor_signal.direction,
            predictor_signal=predictor_signal.signal,
            predictor_confidence=predictor_signal.confidence,
            side_count_state=side_counts,
            primary_leg=primary_leg,
            hedge_leg=hedge_leg,
            reasoning=sanitize_text(reasoning, 280),
            metadata={
                **cycle.metadata,
                "predictor_features": predictor_signal.features,
                "predictor_stats": predictor_signal.stats,
                "news_validation_bypassed": True,
            },
        )

    def _signal_price_guard_reason(self, signal: PairSignalPayload) -> str | None:
        max_order_price = self.context.risk_config.max_order_price
        if signal.primary_leg.target_price > max_order_price:
            return "primary leg exceeds max_order_price"
        if signal.hedge_leg.target_price > max_order_price:
            return "hedge leg exceeds max_order_price"
        return None

    async def _sync_cycle_state(self, cycle: PairCycleRuntime) -> None:
        repo_state = await self.context.repository.get_pair_cycle(cycle.asset_symbol)
        if not repo_state or str(repo_state.get("cycle_slug") or "") != cycle.cycle_slug:
            return
        cycle.side_counts = {key: int(value) for key, value in (repo_state.get("side_counts") or {}).items()}
        cycle.status = str(repo_state.get("status") or cycle.status)  # type: ignore[assignment]
        cycle.last_signal_direction = repo_state.get("last_signal_direction")
        cycle.last_signal_at = repo_state.get("last_signal_at")
        cycle.last_quote_at = repo_state.get("last_quote_at")

    @staticmethod
    def _reserve_cycle_side(cycle: PairCycleRuntime, direction: Literal["YES", "NO"]) -> None:
        side_key = direction.lower()
        cycle.side_counts[side_key] = int(cycle.side_counts.get(side_key, 0)) + 1

    def _extract_quotes(self, payload: Any) -> list[tuple[str, Quote]]:
        if isinstance(payload, list):
            results: list[tuple[str, Quote]] = []
            for item in payload:
                results.extend(self._extract_quotes(item))
            return results
        if not isinstance(payload, dict):
            return []
        if "data" in payload:
            return self._extract_quotes(payload["data"])
        token_id = str(
            payload.get("asset_id")
            or payload.get("assetId")
            or payload.get("token_id")
            or payload.get("tokenId")
            or ""
        ).strip()
        if not token_id:
            return []
        best_bid = self._coerce_float(payload.get("best_bid") or payload.get("bestBid") or payload.get("bid"))
        best_ask = self._coerce_float(payload.get("best_ask") or payload.get("bestAsk") or payload.get("ask"))
        if best_bid <= 0 and best_ask <= 0:
            bids = payload.get("bids") or []
            asks = payload.get("asks") or []
            if isinstance(bids, list) and bids:
                best_bid = self._coerce_float((bids[0] or {}).get("price"))
            if isinstance(asks, list) and asks:
                best_ask = self._coerce_float((asks[0] or {}).get("price"))
        if best_bid <= 0 and best_ask <= 0:
            return []
        if best_bid <= 0:
            best_bid = best_ask
        if best_ask <= 0:
            best_ask = best_bid
        return [
            (
                token_id,
                Quote(
                    token_id=token_id,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    updated_at=datetime.now(UTC),
                ),
            )
        ]

    @staticmethod
    def _quote_stale(quote: Quote | None) -> bool:
        if quote is None:
            return True
        return (datetime.now(UTC) - quote.updated_at).total_seconds() > 15

    @staticmethod
    def _coerce_float(value: Any) -> float:
        if value in (None, ""):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _increment_reason(stats: dict[str, Any], reason: str) -> None:
        stats["risk_blocked"] += 1
        bucket = stats.setdefault("risk_block_reasons", {})
        bucket[reason] = int(bucket.get(reason, 0)) + 1

    @staticmethod
    def _is_duplicate_signal(cycle: PairCycleRuntime, direction: Literal["YES", "NO"]) -> bool:
        if cycle.last_signal_direction != direction:
            return False
        if cycle.last_signal_at is None:
            return False
        return (datetime.now(UTC) - cycle.last_signal_at).total_seconds() < 45

    def _crypto_tier(self, asset_symbol: str) -> Literal["btc", "major", "small_cap"]:
        symbol = asset_symbol.upper()
        if symbol == "BTC":
            return "btc"
        if symbol in {item.upper() for item in self.context.crypto_config.major_assets}:
            return "major"
        return "small_cap"
