from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.crypto import ASSET_NAMES
from core.market_connector import MarketConnector
from core.pair_strategy import Quote, cycle_slug_for, floor_cycle_start
from core.schemas import FlowAnalysisPayload, PairSignalPayload, SignalPayload
from core.utils import clamp, sanitize_text


Direction = Literal["up", "down", "neutral"]


@dataclass(slots=True)
class FlowCycleRuntime:
    asset_symbol: str
    asset_name: str
    crypto_tier: Literal["btc", "major", "small_cap"]
    cycle_slug: str
    cycle_start: datetime
    market_id: str
    market_question: str
    token_id_yes: str
    token_id_no: str
    volume_24h: float = 0.0
    end_date: str = ""
    last_snapshot_sig: str = ""
    last_snapshot_at: datetime | None = None


@dataclass(slots=True)
class FlowSample:
    direction: Direction
    notional: float
    price: float
    created_at: datetime
    source: str


class FlowAnalyzerAgent(BaseAgent):
    def __init__(self, context: AppContext):
        super().__init__("flow_15m", context)
        self.connector = MarketConnector(context)
        self.consumer = f"flow-{uuid4().hex[:8]}"
        self.cycles: dict[str, FlowCycleRuntime] = {}
        self.trade_buffers: dict[str, deque[FlowSample]] = {}
        self.quote_cache: dict[str, Quote] = {}
        self.feed_task: asyncio.Task[None] | None = None
        self.feed_tokens: tuple[str, ...] = ()
        self.feed_error: str | None = None
        self.backfilled: set[str] = set()

    async def close(self) -> None:
        await self._stop_feed()
        await super().close()
        await self.connector.close()

    async def tick(self) -> None:
        if not self.context.settings.copytrade_enabled:
            return

        await self._refresh_cycles()
        await self._ensure_feed()

        events = await self.context.bus.read_group(
            "signals:validated", "flow_15m_analyzers", self.consumer, block_ms=250, count=1
        )
        enriched = 0
        for event_id, payload in events:
            try:
                signal = PairSignalPayload.model_validate(payload) if payload.get("event_type") == "pair_signal.created" else SignalPayload.model_validate(payload)
                flow = await self._analyze_signal(signal, trigger="signal_enrichment")
                await self.context.repository.record_flow_analysis(flow)
                payload["flow_analysis"] = flow.model_dump(mode="json")
                payload.setdefault("metadata", {})
                payload["metadata"] = {
                    **dict(payload["metadata"] or {}),
                    "flow_analysis_id": flow.flow_id,
                    "flow_dominant_direction": flow.dominant_direction,
                    "flow_aligned_with_signal": flow.metadata.get("aligned_with_signal", False),
                }
                await self.context.bus.publish_event("signals:flow_analyzed", payload)
                enriched += 1
            finally:
                await self.context.bus.ack("signals:validated", "flow_15m_analyzers", event_id)

        persisted = 0
        for cycle in self.cycles.values():
            snapshot = await self._analyze_cycle(cycle, trigger="snapshot")
            if snapshot is None:
                continue
            sig = self._snapshot_signature(snapshot)
            now = datetime.now(UTC)
            if sig == cycle.last_snapshot_sig and cycle.last_snapshot_at and (now - cycle.last_snapshot_at).total_seconds() < 10:
                continue
            await self.context.repository.record_flow_analysis(snapshot)
            cycle.last_snapshot_sig = sig
            cycle.last_snapshot_at = now
            persisted += 1

        await self.context.repository.record_pipeline_telemetry(
            str(uuid4()),
            self.name,
            "flow.scan_cycle",
            {
                "enriched_signals": enriched,
                "persisted_snapshots": persisted,
                "feed_error": self.feed_error or "",
                "selected_markets": [
                    {
                        "market_id": c.market_id,
                        "asset_symbol": c.asset_symbol,
                        "crypto_tier": c.crypto_tier,
                        "cycle_slug": c.cycle_slug,
                    }
                    for c in list(self.cycles.values())[:6]
                ],
            },
        )

    async def _refresh_cycles(self) -> None:
        desired = [asset.upper() for asset in self.context.settings.copytrade_markets]
        for asset in list(self.cycles.keys()):
            if asset not in desired:
                cycle = self.cycles.pop(asset)
                self.trade_buffers.pop(cycle.market_id, None)
                self.backfilled.discard(cycle.cycle_slug)
        for asset in desired:
            resolved = await self._resolve_cycle(asset)
            if resolved is None:
                continue
            self.cycles[asset] = resolved
            self.trade_buffers.setdefault(resolved.market_id, deque(maxlen=750))
            if resolved.cycle_slug not in self.backfilled:
                await self._backfill_cycle(resolved)
                self.backfilled.add(resolved.cycle_slug)

    async def _resolve_cycle(self, asset_symbol: str) -> FlowCycleRuntime | None:
        now = datetime.now(UTC)
        current = self.cycles.get(asset_symbol)
        for candidate in [floor_cycle_start(now), floor_cycle_start(now) - timedelta(minutes=15)]:
            cycle_slug = cycle_slug_for(asset_symbol, candidate)
            if current is not None and current.cycle_slug == cycle_slug:
                return current
            market = await self.connector.resolve_copytrade_market(asset_symbol, candidate)
            if market is None:
                continue
            return FlowCycleRuntime(
                asset_symbol=asset_symbol,
                asset_name=str(market.get("asset_name") or ASSET_NAMES.get(asset_symbol, asset_symbol)),
                crypto_tier=self._crypto_tier(asset_symbol),
                cycle_slug=cycle_slug,
                cycle_start=candidate,
                market_id=str(market["id"]),
                market_question=str(market.get("question") or ""),
                token_id_yes=str(market["token_id_yes"]),
                token_id_no=str(market["token_id_no"]),
                volume_24h=float(market.get("volume_24h") or 0.0),
                end_date=str(market.get("end_date") or ""),
            )
        return current

    async def _ensure_feed(self) -> None:
        token_ids = tuple(sorted({t for c in self.cycles.values() for t in (c.token_id_yes, c.token_id_no) if t}))
        if not token_ids:
            await self._stop_feed()
            return
        if self.feed_task is not None and not self.feed_task.done() and token_ids == self.feed_tokens:
            return
        await self._stop_feed()
        self.feed_tokens = token_ids
        self.feed_task = asyncio.create_task(self._feed_loop(list(token_ids)))

    async def _stop_feed(self) -> None:
        if self.feed_task is None:
            return
        self.feed_task.cancel()
        try:
            await self.feed_task
        except asyncio.CancelledError:
            pass
        self.feed_task = None
        self.feed_tokens = ()

    async def _feed_loop(self, token_ids: list[str]) -> None:
        while token_ids:
            try:
                async for message in self.connector.stream_market(token_ids):
                    self._ingest_market_payload(message)
                    self.feed_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.feed_error = sanitize_text(str(exc), 200)
                await asyncio.sleep(1.0)

    def _ingest_market_payload(self, payload: Any) -> None:
        if isinstance(payload, list):
            for item in payload:
                self._ingest_market_payload(item)
            return
        if not isinstance(payload, dict):
            return
        if "data" in payload:
            self._ingest_market_payload(payload["data"])
            return
        event_type = str(payload.get("event_type") or payload.get("type") or "")
        if event_type == "last_trade_price":
            self._ingest_trade(payload, source="ws")
        elif event_type in {"book", "price_change", "best_bid_ask"}:
            quote = self._quote_from_payload(payload)
            if quote is not None:
                self.quote_cache[quote.token_id] = quote

    def _ingest_trade(self, payload: dict[str, Any], *, source: str) -> None:
        token_id = str(payload.get("asset_id") or payload.get("assetId") or payload.get("token_id") or payload.get("tokenId") or "").strip()
        if not token_id:
            return
        cycle, token_side = self._cycle_for_token(token_id)
        if cycle is None:
            return
        price = self._float(payload.get("price") or payload.get("trade_price") or payload.get("last_trade_price"))
        if price <= 0:
            return
        side = str(payload.get("side") or payload.get("maker_side") or payload.get("order_side") or "").upper()
        direction: Direction = token_side
        if side in {"BUY", "SELL"}:
            if token_side == "up":
                direction = "up" if side == "BUY" else "down"
            else:
                direction = "down" if side == "BUY" else "up"
        size = self._float(payload.get("size") or payload.get("qty") or payload.get("quantity") or 1.0)
        notional = self._float(payload.get("notional") or payload.get("amount")) or round(price * max(size, 1.0), 4)
        created_at = self._parse_trade_datetime(
            payload.get("timestamp") or payload.get("time") or payload.get("ts") or payload.get("t") or payload.get("created_at")
        )
        self.trade_buffers.setdefault(cycle.market_id, deque(maxlen=750)).append(
            FlowSample(direction=direction, notional=notional, price=price, created_at=created_at, source=source)
        )

    async def _backfill_cycle(self, cycle: FlowCycleRuntime) -> None:
        start_ts = int((datetime.now(UTC) - timedelta(minutes=15)).timestamp())
        end_ts = int(datetime.now(UTC).timestamp())
        samples = 0
        for token_id in (cycle.token_id_yes, cycle.token_id_no):
            trades = await self.connector.get_trade_history(token_id, start_ts=start_ts, end_ts=end_ts, limit=250)
            if trades:
                for trade in trades:
                    sample = self._parse_trade_record(cycle, token_id, trade, source="data_api")
                    if sample is not None:
                        self.trade_buffers.setdefault(cycle.market_id, deque(maxlen=750)).append(sample)
                        samples += 1
                continue
            history = await self.connector.get_prices_history(token_id, start_ts=start_ts, end_ts=end_ts, fidelity=1)
            for sample in self._price_history_to_samples(cycle, token_id, history):
                self.trade_buffers.setdefault(cycle.market_id, deque(maxlen=750)).append(sample)
                samples += 1
        if samples == 0:
            await self._seed_orderbook(cycle)

    async def _seed_orderbook(self, cycle: FlowCycleRuntime) -> None:
        yes_quote = await self._quote_for_token(cycle.token_id_yes)
        no_quote = await self._quote_for_token(cycle.token_id_no)
        if yes_quote is not None:
            self.quote_cache[yes_quote.token_id] = yes_quote
        if no_quote is not None:
            self.quote_cache[no_quote.token_id] = no_quote

    def _parse_trade_record(self, cycle: FlowCycleRuntime, token_id: str, trade: dict[str, Any], *, source: str) -> FlowSample | None:
        price = self._float(trade.get("price") or trade.get("p") or trade.get("avgPrice") or trade.get("avg_price"))
        if price <= 0:
            return None
        size = self._float(trade.get("size") or trade.get("quantity") or trade.get("q") or 1.0)
        notional = self._float(trade.get("notional") or trade.get("amount")) or round(price * max(size, 1.0), 4)
        side = str(trade.get("side") or trade.get("order_side") or trade.get("maker_side") or "").upper()
        direction: Direction = "up" if token_id == cycle.token_id_yes else "down"
        if side in {"BUY", "SELL"}:
            if token_id == cycle.token_id_yes:
                direction = "up" if side == "BUY" else "down"
            else:
                direction = "down" if side == "BUY" else "up"
        return FlowSample(
            direction=direction,
            notional=notional,
            price=price,
            created_at=self._parse_trade_datetime(
                trade.get("created_at") or trade.get("timestamp") or trade.get("time") or trade.get("ts") or trade.get("t")
            ),
            source=source,
        )

    def _price_history_to_samples(self, cycle: FlowCycleRuntime, token_id: str, history: list[dict[str, Any]]) -> list[FlowSample]:
        samples: list[FlowSample] = []
        prev: float | None = None
        for item in history:
            price = self._float(item.get("p") or item.get("price") or item.get("mid") or item.get("value"))
            if price <= 0:
                continue
            if prev is None:
                prev = price
                continue
            delta = price - prev
            if abs(delta) < 0.0005:
                prev = price
                continue
            direction: Direction = "up" if token_id == cycle.token_id_yes else "down"
            if delta < 0:
                direction = "down" if token_id == cycle.token_id_yes else "up"
            samples.append(
                FlowSample(
                    direction=direction,
                    notional=round(abs(delta) * 100.0, 4),
                    price=price,
                    created_at=self._parse_trade_datetime(item.get("t") or item.get("timestamp") or item.get("time")),
                    source="data_api",
                )
            )
            prev = price
        return samples

    async def _analyze_signal(self, signal: SignalPayload | PairSignalPayload, *, trigger: str) -> FlowAnalysisPayload:
        cycle = self._cycle_for_signal(signal)
        if cycle is None:
            return self._neutral_flow(signal, trigger=trigger)
        analysis = await self._analyze_cycle(cycle, trigger=trigger, signal=signal)
        return analysis or self._neutral_flow(signal, trigger=trigger, cycle=cycle)

    async def _analyze_cycle(
        self,
        cycle: FlowCycleRuntime,
        *,
        trigger: str,
        signal: SignalPayload | PairSignalPayload | None = None,
    ) -> FlowAnalysisPayload | None:
        samples = self._window_samples(cycle.market_id)
        if not samples:
            return None
        cutoff = datetime.now(UTC) - timedelta(minutes=15)
        samples = [sample for sample in samples if sample.created_at >= cutoff]
        if not samples:
            return None

        up_trade_count = sum(1 for s in samples if s.direction == "up")
        down_trade_count = sum(1 for s in samples if s.direction == "down")
        up_notional = sum(s.notional for s in samples if s.direction == "up")
        down_notional = sum(s.notional for s in samples if s.direction == "down")
        total_trades = len(samples)
        total_notional = up_notional + down_notional
        last_trade_at = max(s.created_at for s in samples)
        freshness_seconds = max((datetime.now(UTC) - last_trade_at).total_seconds(), 0.0)

        yes_quote = self.quote_cache.get(cycle.token_id_yes) or await self._quote_for_token(cycle.token_id_yes)
        no_quote = self.quote_cache.get(cycle.token_id_no) or await self._quote_for_token(cycle.token_id_no)
        if yes_quote is not None:
            self.quote_cache[yes_quote.token_id] = yes_quote
        if no_quote is not None:
            self.quote_cache[no_quote.token_id] = no_quote

        book_bias = self._book_bias(yes_quote, no_quote)
        if total_notional > 0:
            dominance = clamp((up_notional - down_notional) / max(total_notional, 1.0), -1.0, 1.0)
            source_used = self._source_used(samples)
        else:
            price_bias = 0.0
            if yes_quote is not None and no_quote is not None:
                price_bias = clamp((yes_quote.best_ask - no_quote.best_ask) * 2.5, -1.0, 1.0)
            dominance = clamp(book_bias * 0.7 + price_bias * 0.3, -1.0, 1.0)
            source_used = "mixed" if yes_quote is not None and no_quote is not None else "data_api"

        dominant_direction: Direction = "neutral"
        if abs(dominance) >= 0.08 or abs(up_trade_count - down_trade_count) > 1:
            dominant_direction = "up" if dominance > 0 else "down"

        signal_direction = self._signal_direction(signal)
        aligned = dominant_direction != "neutral" and signal_direction == dominant_direction
        confidence = clamp(
            0.5
            + min(abs(dominance) * 0.35, 0.28)
            + min(total_trades / 16.0, 0.12)
            + min(total_notional / 120.0, 0.10)
            + min(max(45.0 - freshness_seconds, 0.0) / 300.0, 0.05),
            0.5,
            0.95,
        )
        return FlowAnalysisPayload(
            flow_id=str(uuid4()),
            signal_id=getattr(signal, "signal_id", None),
            trade_group_id=getattr(signal, "trade_group_id", None),
            market_id=cycle.market_id,
            cycle_slug=cycle.cycle_slug,
            market_question=cycle.market_question,
            asset_symbol=cycle.asset_symbol,
            asset_name=cycle.asset_name,
            crypto_tier=cycle.crypto_tier,
            window_minutes=15,
            dominant_direction=dominant_direction,
            dominance_score=round(dominance, 4),
            confidence=round(confidence, 4),
            up_trade_count=up_trade_count,
            down_trade_count=down_trade_count,
            up_notional=round(up_notional, 4),
            down_notional=round(down_notional, 4),
            total_trades=total_trades,
            total_notional=round(total_notional, 4),
            freshness_seconds=round(freshness_seconds, 2),
            source_used=source_used if source_used in {"ws", "data_api", "mixed"} else "mixed",
            sample_count=total_trades,
            last_trade_at=last_trade_at,
            metadata={
                "trigger": trigger,
                "aligned_with_signal": aligned,
                "signal_direction": signal_direction,
                "book_bias": round(book_bias, 4),
                "sample_sources": sorted({s.source for s in samples}),
                "up_share": round(up_notional / max(total_notional, 1.0), 4) if total_notional > 0 else 0.5,
                "down_share": round(down_notional / max(total_notional, 1.0), 4) if total_notional > 0 else 0.5,
            },
        )

    def _neutral_flow(self, signal: SignalPayload | PairSignalPayload, *, trigger: str, cycle: FlowCycleRuntime | None = None) -> FlowAnalysisPayload:
        asset_symbol = cycle.asset_symbol if cycle is not None else str(getattr(signal, "asset_symbol", "") or "CRYPTO")
        direction = self._signal_direction(signal)
        return FlowAnalysisPayload(
            flow_id=str(uuid4()),
            signal_id=getattr(signal, "signal_id", None),
            trade_group_id=getattr(signal, "trade_group_id", None),
            market_id=cycle.market_id if cycle is not None else str(getattr(signal, "market_id", "") or ""),
            cycle_slug=cycle.cycle_slug if cycle is not None else "",
            market_question=cycle.market_question if cycle is not None else str(getattr(signal, "market_question", "") or ""),
            asset_symbol=asset_symbol,
            asset_name=cycle.asset_name if cycle is not None else ASSET_NAMES.get(asset_symbol.upper(), asset_symbol.upper()),
            crypto_tier=cycle.crypto_tier if cycle is not None else self._crypto_tier(asset_symbol),
            window_minutes=15,
            dominant_direction="neutral",
            dominance_score=0.0,
            confidence=0.5,
            up_trade_count=0,
            down_trade_count=0,
            up_notional=0.0,
            down_notional=0.0,
            total_trades=0,
            total_notional=0.0,
            freshness_seconds=0.0,
            source_used="ws",
            sample_count=0,
            last_trade_at=None,
            metadata={"trigger": trigger, "aligned_with_signal": False, "signal_direction": direction, "source": "neutral_fallback"},
        )

    async def _quote_for_token(self, token_id: str) -> Quote | None:
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

    def _quote_from_payload(self, payload: dict[str, Any]) -> Quote | None:
        token_id = str(payload.get("asset_id") or payload.get("assetId") or payload.get("token_id") or payload.get("tokenId") or "").strip()
        if not token_id:
            return None
        best_bid = self._float(payload.get("best_bid") or payload.get("bestBid") or payload.get("bid"))
        best_ask = self._float(payload.get("best_ask") or payload.get("bestAsk") or payload.get("ask"))
        if best_bid <= 0 and best_ask <= 0:
            bids = payload.get("bids") or []
            asks = payload.get("asks") or []
            if isinstance(bids, list) and bids:
                best_bid = self._float((bids[0] or {}).get("price"))
            if isinstance(asks, list) and asks:
                best_ask = self._float((asks[0] or {}).get("price"))
        if best_bid <= 0 and best_ask <= 0:
            return None
        if best_bid <= 0:
            best_bid = best_ask
        if best_ask <= 0:
            best_ask = best_bid
        quote = Quote(token_id=token_id, best_bid=best_bid, best_ask=best_ask, updated_at=datetime.now(UTC))
        self.quote_cache[token_id] = quote
        return quote

    def _window_samples(self, market_id: str) -> list[FlowSample]:
        cutoff = datetime.now(UTC) - timedelta(minutes=15)
        return [sample for sample in self.trade_buffers.get(market_id, deque()) if sample.created_at >= cutoff]

    def _snapshot_signature(self, analysis: FlowAnalysisPayload) -> str:
        return f"{analysis.market_id}|{analysis.cycle_slug}|{analysis.dominant_direction}|{analysis.up_trade_count}|{analysis.down_trade_count}|{analysis.up_notional:.4f}|{analysis.down_notional:.4f}|{analysis.source_used}"

    def _cycle_for_signal(self, signal: SignalPayload | PairSignalPayload) -> FlowCycleRuntime | None:
        market_id = str(getattr(signal, "market_id", "") or "").strip()
        return next((cycle for cycle in self.cycles.values() if cycle.market_id == market_id), None) if market_id else None

    def _cycle_for_token(self, token_id: str) -> tuple[FlowCycleRuntime | None, Direction]:
        for cycle in self.cycles.values():
            if token_id == cycle.token_id_yes:
                return cycle, "up"
            if token_id == cycle.token_id_no:
                return cycle, "down"
        return None, "neutral"

    def _parse_trade_datetime(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        if value in (None, ""):
            return datetime.now(UTC)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            try:
                return datetime.fromtimestamp(float(value), tz=UTC)
            except Exception:
                return datetime.now(UTC)

    def _source_used(self, samples: list[FlowSample]) -> Literal["ws", "data_api", "mixed"]:
        sources = {sample.source for sample in samples}
        if sources == {"ws"}:
            return "ws"
        if sources <= {"data_api"}:
            return "data_api"
        return "mixed"

    @staticmethod
    def _book_bias(yes_quote: Quote | None, no_quote: Quote | None) -> float:
        if yes_quote is None or no_quote is None:
            return 0.0
        yes_support = yes_quote.bid_depth - yes_quote.ask_depth
        no_support = no_quote.bid_depth - no_quote.ask_depth
        denom = max(abs(yes_support) + abs(no_support) + yes_quote.bid_depth + no_quote.bid_depth, 1.0)
        return clamp((yes_support - no_support) / denom, -1.0, 1.0)

    @staticmethod
    def _signal_direction(signal: SignalPayload | PairSignalPayload | None) -> Direction:
        if signal is None:
            return "neutral"
        if isinstance(signal, PairSignalPayload):
            return str(signal.predictor_direction)
        return "up" if signal.direction == "YES" else "down"

    @staticmethod
    def _crypto_tier(asset_symbol: str) -> Literal["btc", "major", "small_cap"]:
        symbol = asset_symbol.upper()
        if symbol == "BTC":
            return "btc"
        if symbol in {"ETH", "SOL", "XRP", "DOGE"}:
            return "major"
        return "small_cap"

    @staticmethod
    def _float(value: Any) -> float:
        if value in (None, ""):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
