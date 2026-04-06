from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from core.crypto import ASSET_NAMES
from core.exceptions import RiskBlockedError
from core.pair_strategy import Quote, cycle_slug_for, floor_cycle_start
from core.risk_engine import RiskEngine
from core.schemas import MarketSnapshotPayload, SignalPayload
from core.utils import clamp, sanitize_text, stable_hash

if TYPE_CHECKING:
    from core.app_context import AppContext
    from core.market_connector import MarketConnector


@dataclass(slots=True)
class MomentumCycleRuntime:
    asset_symbol: str
    asset_name: str
    crypto_tier: Literal["btc", "major", "small_cap"]
    cycle_slug: str
    cycle_start: datetime
    market_id: str
    market_question: str
    token_id_yes: str
    token_id_no: str
    end_date: str = ""
    volume_24h: float = 0.0


@dataclass(slots=True)
class MomentumAnalysisResult:
    decision: dict[str, Any] | None
    pre_risk_reason: str | None = None


class MomentumTradingEngine:
    def __init__(self, context: AppContext, connector: MarketConnector):
        self.context = context
        self.connector = connector
        self.risk = RiskEngine(context)
        self.cycles: dict[str, MomentumCycleRuntime] = {}
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
            "strategy_id": "momentum_15m",
            "requested_limit": len(self.context.settings.momentum_markets),
            "gamma_markets_fetched": 0,
            "crypto_classified": 0,
            "selected_for_scan": 0,
            "strategy_candidates": 0,
            "reached_risk_engine": 0,
            "pre_risk_blocked": 0,
            "pre_risk_block_reasons": {},
            "risk_passed": 0,
            "risk_blocked": 0,
            "risk_block_reasons": {},
            "duplicates_blocked": 0,
            "persisted_signals": 0,
            "selected_markets": [],
            "discovery_source": "momentum_strategy",
            "news_validation_enabled": bool(self.context.settings.news_validation_enabled),
            "feed_error": "",
        }
        if not self.context.settings.momentum_trading_enabled:
            return stats

        await self._refresh_cycles(stats)
        stats["selected_for_scan"] = len(self.cycles)
        stats["crypto_classified"] = len(self.cycles)

        snapshots: list[MarketSnapshotPayload] = []
        for cycle in self.cycles.values():
            yes_quote, no_quote = await self._quotes_for_cycle(cycle)
            if yes_quote is None or no_quote is None or yes_quote.best_ask <= 0 or no_quote.best_ask <= 0:
                self._increment_reason(
                    stats,
                    "quote_unavailable",
                    bucket_name="pre_risk_blocked",
                    reasons_key="pre_risk_block_reasons",
                )
                continue

            market = {
                "id": cycle.market_id,
                "question": cycle.market_question,
                "token_id_yes": cycle.token_id_yes,
                "token_id_no": cycle.token_id_no,
                "price_yes": yes_quote.best_ask,
                "price_no": no_quote.best_ask,
                "volume_24h": cycle.volume_24h,
                "asset_symbol": cycle.asset_symbol,
                "asset_name": cycle.asset_name,
                "crypto_tier": cycle.crypto_tier,
                "market_kind": "direct_coin",
                "question_type": "direction",
                "thesis_tags": [cycle.asset_symbol.lower(), "momentum_15m", "updown", "15m"],
                "thesis_hash": stable_hash(f"{cycle.market_id}|{cycle.cycle_slug}|momentum_15m", length=16),
                "end_date": cycle.end_date,
                "orderbook_summary_yes": yes_quote.as_summary(),
                "orderbook_summary_no": no_quote.as_summary(),
            }
            snapshots.append(
                MarketSnapshotPayload(
                    market_id=cycle.market_id,
                    question=cycle.market_question,
                    token_id_yes=cycle.token_id_yes,
                    token_id_no=cycle.token_id_no,
                    price_yes=yes_quote.best_ask,
                    price_no=no_quote.best_ask,
                    volume_24h=cycle.volume_24h,
                    asset_symbol=cycle.asset_symbol,
                    asset_name=cycle.asset_name,
                    crypto_tier=cycle.crypto_tier,
                    market_kind="direct_coin",
                    question_type="direction",
                    thesis_tags=market["thesis_tags"],
                    metadata={
                        "source": "momentum_strategy",
                        "cycle_slug": cycle.cycle_slug,
                        "end_date": cycle.end_date,
                        "orderbook_summary_yes": market["orderbook_summary_yes"],
                        "orderbook_summary_no": market["orderbook_summary_no"],
                    },
                )
            )
            analysis = await self._analyze_market(market)
            if analysis is None:
                continue
            stats["strategy_candidates"] += 1
            if isinstance(analysis, MomentumAnalysisResult):
                if analysis.decision is None:
                    if analysis.pre_risk_reason:
                        self._increment_reason(
                            stats,
                            analysis.pre_risk_reason,
                            bucket_name="pre_risk_blocked",
                            reasons_key="pre_risk_block_reasons",
                        )
                    continue
                decision = analysis.decision
            else:
                decision = analysis

            if float(decision["confidence"]) < self.context.settings.momentum_signal_confidence_threshold:
                self._increment_reason(
                    stats,
                    "confidence_below_threshold",
                    bucket_name="pre_risk_blocked",
                    reasons_key="pre_risk_block_reasons",
                )
                continue

            signal = self._build_signal(cycle, market, decision)
            stats["reached_risk_engine"] += 1
            try:
                await self.risk.validate_signal(signal)
            except RiskBlockedError as exc:
                self._increment_reason(
                    stats,
                    str(exc),
                    bucket_name="risk_blocked",
                    reasons_key="risk_block_reasons",
                )
                await self.risk.record_block(
                    "claude",
                    str(exc),
                    {
                        "signal_id": signal.signal_id,
                        "market_id": signal.market_id,
                        "asset_symbol": signal.asset_symbol,
                        "crypto_tier": signal.crypto_tier,
                        "strategy_id": signal.strategy_id,
                    },
                )
                continue

            is_duplicate = await self.context.repository.has_recent_signal_duplicate(
                market_id=signal.market_id,
                direction=signal.direction,
                thesis_hash=signal.thesis_hash,
                cooldown_minutes=self.context.settings.momentum_cooldown_minutes,
            )
            if is_duplicate:
                stats["duplicates_blocked"] += 1
                continue

            await self.context.repository.record_signal(signal.signal_id, signal.event_type, signal.model_dump(mode="json"))
            target_stream = "signals:candidates" if self.context.settings.news_validation_enabled else "signals:validated"
            await self.context.bus.publish_event(target_stream, signal.model_dump(mode="json"))
            stats["risk_passed"] += 1
            stats["persisted_signals"] += 1
            stats["selected_markets"].append(
                {
                    "market_id": cycle.market_id,
                    "asset_symbol": cycle.asset_symbol,
                    "crypto_tier": cycle.crypto_tier,
                    "market_kind": "direct_coin",
                    "volume_24h": cycle.volume_24h,
                    "question": cycle.market_question,
                }
            )

        if snapshots:
            await self.context.repository.record_market_snapshots(snapshots)
        stats["feed_error"] = self.feed_error or ""
        market_coexistence = await self._market_coexistence_summary()
        await self.context.repository.record_pipeline_telemetry(
            str(uuid4()),
            "claude",
            "scanner.scan_cycle",
            {
                **stats,
                "selected_markets": stats["selected_markets"][:6],
                "market_coexistence": market_coexistence,
            },
        )
        return stats

    async def _refresh_cycles(self, stats: dict[str, Any]) -> None:
        desired_assets = [item.upper() for item in self.context.settings.momentum_markets]
        known_assets = set(self.cycles.keys())
        for asset_symbol in list(known_assets - set(desired_assets)):
            self.cycles.pop(asset_symbol, None)
        for asset_symbol in desired_assets:
            resolved = await self._resolve_cycle(asset_symbol)
            if resolved is not None:
                self.cycles[asset_symbol] = resolved
                stats["gamma_markets_fetched"] += 1

    async def _resolve_cycle(self, asset_symbol: str) -> MomentumCycleRuntime | None:
        now = datetime.now(UTC)
        cycle_start = floor_cycle_start(now)
        candidate_starts = [cycle_start]
        if (not self.context.settings.live_trading) or (not self.context.settings.momentum_wait_for_next_market_start):
            candidate_starts.append(cycle_start - timedelta(minutes=15))

        current = self.cycles.get(asset_symbol)
        for candidate_start in candidate_starts:
            cycle_slug = cycle_slug_for(asset_symbol, candidate_start)
            if current is not None and current.cycle_slug == cycle_slug:
                return current
            market = await self.connector.resolve_copytrade_market(asset_symbol, candidate_start)
            if market is None:
                continue
            return MomentumCycleRuntime(
                asset_symbol=asset_symbol,
                asset_name=str(market.get("asset_name") or ASSET_NAMES.get(asset_symbol, asset_symbol)),
                crypto_tier=self._crypto_tier(asset_symbol),
                cycle_slug=cycle_slug,
                cycle_start=candidate_start,
                market_id=str(market["id"]),
                market_question=str(market.get("question") or ""),
                token_id_yes=str(market["token_id_yes"]),
                token_id_no=str(market["token_id_no"]),
                end_date=str(market.get("end_date") or ""),
                volume_24h=float(market.get("volume_24h") or 0.0),
            )
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

    async def _quotes_for_cycle(self, cycle: MomentumCycleRuntime) -> tuple[Quote | None, Quote | None]:
        yes_quote = await self._quote_from_orderbook(cycle.token_id_yes)
        no_quote = await self._quote_from_orderbook(cycle.token_id_no)
        if yes_quote is None:
            yes_quote = self.quote_cache.get(cycle.token_id_yes)
        if no_quote is None:
            no_quote = self.quote_cache.get(cycle.token_id_no)
        if self._quotes_incoherent(yes_quote, no_quote):
            yes_quote = self.quote_cache.get(cycle.token_id_yes)
            no_quote = self.quote_cache.get(cycle.token_id_no)
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

    def _build_signal(self, cycle: MomentumCycleRuntime, market: dict[str, Any], decision: dict[str, Any]) -> SignalPayload:
        direction: Literal["YES", "NO"] = decision["direction"]
        price_key = "price_yes" if direction == "YES" else "price_no"
        token_key = "token_id_yes" if direction == "YES" else "token_id_no"
        reasoning = (
            f"momentum_15m {cycle.asset_symbol} {cycle.cycle_slug}: "
            f"regime {decision['regime']}, confidence {decision['confidence']:.3f}, "
            f"model {decision['model_probability']:.3f} vs mercado {decision['market_probability']:.3f}."
        )
        features_summary = {
            **dict(decision["features_summary"]),
            "engine_strategy_id": "momentum_15m",
            "cycle_slug": cycle.cycle_slug,
        }
        thesis_hash = stable_hash(
            f"{cycle.market_id}|{cycle.cycle_slug}|momentum_15m|{direction}|{decision['regime']}",
            length=16,
        )
        return SignalPayload(
            signal_id=str(uuid4()),
            market_id=cycle.market_id,
            token_id=str(market[token_key]),
            market_question=cycle.market_question,
            direction=direction,
            edge=float(decision["edge"]),
            confidence=float(decision["confidence"]),
            price=float(market[price_key]),
            price_yes=float(market["price_yes"]),
            price_no=float(market["price_no"]),
            volume_24h=float(market["volume_24h"]),
            asset_symbol=cycle.asset_symbol,
            asset_name=cycle.asset_name,
            crypto_tier=cycle.crypto_tier,
            market_kind="direct_coin",
            question_type="direction",
            strategy_id="momentum_15m",
            strategy_version="v1",
            model_probability=float(decision["model_probability"]),
            market_probability=float(decision["market_probability"]),
            regime=str(decision["regime"]),
            expected_slippage_bps=float(decision["expected_slippage_bps"]),
            expected_holding_minutes=int(decision["expected_holding_minutes"]),
            thesis_tags=[cycle.asset_symbol.lower(), "momentum_15m", "15m", str(direction).lower()],
            thesis_hash=thesis_hash,
            reasoning=sanitize_text(reasoning, 280),
            features_summary=features_summary,
            liquidity_summary=market["orderbook_summary_yes"] if direction == "YES" else market["orderbook_summary_no"],
            metadata={
                "source": "momentum_strategy",
                "cycle_slug": cycle.cycle_slug,
                "end_date": cycle.end_date,
                "base_strategy_id": "momentum_15m",
            },
        )

    async def _analyze_market(self, market: dict[str, Any]) -> MomentumAnalysisResult:
        snapshots = await self.context.repository.get_market_snapshots(market_id=str(market["id"]), limit=12)
        history = [
            float(item.get("price_yes") or 0.0)
            for item in snapshots
            if 0.02 <= float(item.get("price_yes") or 0.0) <= 0.98
        ]
        current_price = float(market.get("price_yes") or 0.0)
        if 0.02 <= current_price <= 0.98:
            history.append(current_price)
        history = history[-12:]
        min_history_points = max(int(getattr(self.context.settings, "momentum_min_history_points", 6) or 6), 4)
        if len(history) < min_history_points:
            return MomentumAnalysisResult(None, f"insufficient history ({len(history)} < {min_history_points})")

        latest = history[-1]
        short_anchor = history[max(0, len(history) - 4)]
        medium_anchor = history[max(0, len(history) - 7)]
        momentum_short = latest - short_anchor
        momentum_medium = latest - medium_anchor
        if abs(momentum_short) < 0.012 and abs(momentum_medium) < 0.02:
            return MomentumAnalysisResult(None, "momentum below threshold")
        if momentum_short == 0 or momentum_medium == 0:
            return MomentumAnalysisResult(None, "flat momentum")
        if momentum_short * momentum_medium < 0:
            return MomentumAnalysisResult(None, "momentum direction mismatch")

        direction: Literal["YES", "NO"] = "YES" if momentum_short > 0 else "NO"
        market_probability = float(market["price_yes"] if direction == "YES" else market["price_no"])
        book = market["orderbook_summary_yes"] if direction == "YES" else market["orderbook_summary_no"]
        spread_bps = float(book.get("spread_bps") or 0.0)
        bid_depth = float(book.get("bid_depth") or 0.0)
        ask_depth = float(book.get("ask_depth") or 0.0)
        depth_total = float(book.get("bid_depth") or 0.0) + float(book.get("ask_depth") or 0.0)
        if market_probability <= 0.06 or market_probability >= 0.94:
            return MomentumAnalysisResult(None, f"probability saturated ({market_probability:.3f})")
        if spread_bps <= 0 or spread_bps > min(float(self.risk.config.max_spread_bps), 220.0):
            return MomentumAnalysisResult(None, f"spread too wide ({spread_bps:.1f} bps)")
        if bid_depth < 15.0 or ask_depth < 15.0 or depth_total < 45.0:
            return MomentumAnalysisResult(
                None,
                f"book too shallow (bid {bid_depth:.1f}, ask {ask_depth:.1f}, total {depth_total:.1f})",
            )
        expected_move = min(abs(momentum_short) * 1.8 + abs(momentum_medium) * 1.15, 0.18)
        if expected_move < 0.025:
            return MomentumAnalysisResult(None, f"expected move too small ({expected_move:.3f})")

        model_probability = clamp(market_probability + expected_move, 0.02, 0.98)
        expected_slippage_bps = round(max(spread_bps * 0.35, 8.0), 2)
        edge = model_probability - market_probability - (expected_slippage_bps / 10000)
        quality_edge_floor = max(
            float(getattr(self.context.settings, "momentum_min_edge", 0.085) or 0.085),
            0.08 + min(spread_bps / 60000.0, 0.01),
        )
        if edge < quality_edge_floor:
            return MomentumAnalysisResult(None, f"edge below quality floor ({edge:.3f} < {quality_edge_floor:.3f})")
        confidence = clamp(
            0.54
            + min(expected_move * 1.9, 0.20)
            + min(depth_total / 1200.0, 0.07)
            - min(spread_bps / 1800.0, 0.12),
            0.5,
            0.95,
        )
        return MomentumAnalysisResult(
            {
                "direction": direction,
                "regime": "trend",
                "model_probability": round(model_probability, 4),
                "market_probability": round(market_probability, 4),
                "edge": round(edge, 4),
                "confidence": round(confidence, 4),
                "expected_slippage_bps": expected_slippage_bps,
                "expected_holding_minutes": 45,
                "features_summary": {
                    "momentum_short": round(momentum_short, 4),
                    "momentum_medium": round(momentum_medium, 4),
                    "expected_move": round(expected_move, 4),
                    "bid_depth": round(bid_depth, 4),
                    "ask_depth": round(ask_depth, 4),
                    "depth_total": round(depth_total, 4),
                    "selected_spread_bps": round(spread_bps, 2),
                },
            },
            None,
        )

    @staticmethod
    def _quote_stale(quote: Quote | None) -> bool:
        if quote is None:
            return True
        return (datetime.now(UTC) - quote.updated_at).total_seconds() > 15

    @staticmethod
    def _quotes_incoherent(yes_quote: Quote | None, no_quote: Quote | None) -> bool:
        if yes_quote is None or no_quote is None:
            return True
        if yes_quote.best_ask <= 0 or no_quote.best_ask <= 0:
            return True
        total = yes_quote.best_ask + no_quote.best_ask
        return total >= 1.35 or total <= 0.65

    @staticmethod
    def _extract_quotes(payload: Any) -> list[tuple[str, Quote]]:
        if isinstance(payload, list):
            results: list[tuple[str, Quote]] = []
            for item in payload:
                results.extend(MomentumTradingEngine._extract_quotes(item))
            return results
        if not isinstance(payload, dict):
            return []
        if "data" in payload:
            return MomentumTradingEngine._extract_quotes(payload["data"])
        token_id = str(
            payload.get("asset_id")
            or payload.get("assetId")
            or payload.get("token_id")
            or payload.get("tokenId")
            or ""
        ).strip()
        if not token_id:
            return []
        best_bid = MomentumTradingEngine._coerce_float(payload.get("best_bid") or payload.get("bestBid") or payload.get("bid"))
        best_ask = MomentumTradingEngine._coerce_float(payload.get("best_ask") or payload.get("bestAsk") or payload.get("ask"))
        if best_bid <= 0 and best_ask <= 0:
            bids = payload.get("bids") or []
            asks = payload.get("asks") or []
            if isinstance(bids, list) and bids:
                best_bid = MomentumTradingEngine._coerce_float((bids[0] or {}).get("price"))
            if isinstance(asks, list) and asks:
                best_ask = MomentumTradingEngine._coerce_float((asks[0] or {}).get("price"))
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
    def _coerce_float(value: Any) -> float:
        if value in (None, ""):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    async def _market_coexistence_summary(self) -> dict[str, Any]:
        repository = self.context.repository
        if not hasattr(repository, "get_open_positions"):
            return {
                "open_position_count": 0,
                "coexistence_market_count": 0,
                "coexistence_position_count": 0,
                "coexistence_markets": [],
                "has_momentum_pair_coexistence": False,
            }
        positions = await repository.get_open_positions()
        markets: dict[str, dict[str, Any]] = {}
        for item in positions:
            market_id = str(item.get("market_id") or "").strip()
            if not market_id:
                continue
            entry = markets.setdefault(
                market_id,
                {
                    "market_id": market_id,
                    "asset_symbol": str(item.get("asset_symbol") or "").strip(),
                    "position_count": 0,
                    "strategy_ids": set(),
                },
            )
            entry["position_count"] += 1
            strategy_id = str(item.get("strategy_id") or "").strip()
            if strategy_id:
                entry["strategy_ids"].add(strategy_id)
            asset_symbol = str(item.get("asset_symbol") or "").strip()
            if asset_symbol and not entry["asset_symbol"]:
                entry["asset_symbol"] = asset_symbol

        coexistence_markets = []
        momentum_pair_markets = []
        for entry in markets.values():
            strategy_ids = sorted(entry["strategy_ids"])
            if len(strategy_ids) < 2:
                continue
            market_payload = {
                "market_id": entry["market_id"],
                "asset_symbol": entry["asset_symbol"],
                "position_count": entry["position_count"],
                "strategy_ids": strategy_ids,
            }
            coexistence_markets.append(market_payload)
            if "momentum_15m" in strategy_ids and "pair_15m" in strategy_ids:
                momentum_pair_markets.append(market_payload)

        return {
            "open_position_count": len(positions),
            "coexistence_market_count": len(coexistence_markets),
            "coexistence_position_count": sum(item["position_count"] for item in coexistence_markets),
            "coexistence_markets": coexistence_markets[:6],
            "momentum_pair_market_count": len(momentum_pair_markets),
            "momentum_pair_markets": momentum_pair_markets[:6],
            "has_momentum_pair_coexistence": bool(momentum_pair_markets),
        }

    @staticmethod
    def _increment_reason(
        stats: dict[str, Any],
        reason: str,
        *,
        bucket_name: str,
        reasons_key: str,
    ) -> None:
        stats[bucket_name] += 1
        bucket = stats.setdefault(reasons_key, {})
        bucket[reason] = int(bucket.get(reason, 0)) + 1

    def _crypto_tier(self, asset_symbol: str) -> Literal["btc", "major", "small_cap"]:
        symbol = asset_symbol.upper()
        if symbol == "BTC":
            return "btc"
        if symbol in {item.upper() for item in self.context.crypto_config.major_assets}:
            return "major"
        return "small_cap"
