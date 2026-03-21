from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.exceptions import InvalidModelResponseError, RiskBlockedError
from core.market_connector import MarketConnector
from core.risk_engine import RiskEngine
from core.schemas import MarketSnapshotPayload, SignalPayload
from core.strategy_engine import StrategyEngine


class ClaudeAgent(BaseAgent):
    def __init__(self, context: AppContext):
        super().__init__("claude", context)
        self.connector = MarketConnector(context)
        self.risk = RiskEngine(context)
        self.strategy = StrategyEngine(context)

    async def tick(self) -> None:
        agent_cfg = self.context.agents_config.agents[self.name]
        markets = await self.connector.get_active_markets(
            limit=agent_cfg.scan_limit or 20,
            crypto_only=self.context.crypto_config.enabled,
        )
        await self.context.repository.record_market_snapshots(
            [
                MarketSnapshotPayload(
                    market_id=str(market["id"]),
                    question=str(market["question"]),
                    token_id_yes=str(market["token_id_yes"]),
                    token_id_no=str(market["token_id_no"]),
                    price_yes=float(market["price_yes"]),
                    price_no=float(market["price_no"]),
                    volume_24h=float(market["volume_24h"]),
                    asset_symbol=str(market.get("asset_symbol", "")),
                    asset_name=str(market.get("asset_name", "")),
                    crypto_tier=str(market.get("crypto_tier", "")),
                    market_kind=str(market.get("market_kind", "")),
                    question_type=str(market.get("question_type", "")),
                    thesis_tags=[str(item) for item in market.get("thesis_tags", [])],
                    metadata={
                        "source": "gamma",
                        "clob_token_ids": market.get("clob_token_ids", []),
                        "thesis_hash": market.get("thesis_hash", ""),
                        "end_date": market.get("end_date") or market.get("endDate") or market.get("closeTime"),
                        "orderbook_summary_yes": market.get("orderbook_summary_yes", {}),
                        "orderbook_summary_no": market.get("orderbook_summary_no", {}),
                    },
                )
                for market in markets
            ]
        )
        await self.context.repository.record_equity_snapshot(source="scan_cycle")
        strategy_candidates = 0
        risk_passed = 0
        risk_blocked = 0
        duplicates_blocked = 0
        persisted_signals = 0
        risk_block_reasons: dict[str, int] = {}
        for market in markets:
            signal = await self.calc_edge(market)
            if signal is None:
                continue
            strategy_candidates += 1
            try:
                await self.risk.validate_signal(signal)
            except RiskBlockedError as exc:
                risk_blocked += 1
                reason = str(exc)
                risk_block_reasons[reason] = risk_block_reasons.get(reason, 0) + 1
                await self.risk.record_block(
                    self.name,
                    reason,
                    {
                        "signal_id": signal.signal_id,
                        "market_id": signal.market_id,
                        "asset_symbol": signal.asset_symbol,
                        "crypto_tier": signal.crypto_tier,
                    },
                )
                continue
            risk_passed += 1

            cooldown_minutes = self.context.crypto_config.tier(signal.crypto_tier).cooldown_minutes
            is_duplicate = await self.context.repository.has_recent_signal_duplicate(
                market_id=signal.market_id,
                direction=signal.direction,
                thesis_hash=signal.thesis_hash,
                cooldown_minutes=cooldown_minutes,
            )
            if is_duplicate:
                await self.risk.record_block(
                    self.name,
                    "duplicate signal inside cooldown window",
                    {
                        "signal_id": signal.signal_id,
                        "market_id": signal.market_id,
                        "asset_symbol": signal.asset_symbol,
                        "crypto_tier": signal.crypto_tier,
                    },
                )
                continue

            persisted_signals += 1
            await self.context.repository.record_signal(signal.signal_id, signal.event_type, signal.model_dump(mode="json"))
            target_stream = (
                "signals:candidates" if self.context.settings.news_validation_enabled else "signals:validated"
            )
            await self.context.bus.publish_event(target_stream, signal.model_dump(mode="json"))

        scan_stats = deepcopy(getattr(self.connector, "last_scan_stats", {}))
        rejection_breakdown = dict(scan_stats.get("rejection_breakdown") or {})
        await self.context.repository.record_pipeline_telemetry(
            str(uuid4()),
            self.name,
            "scanner.scan_cycle",
            {
                **scan_stats,
                "rejection_breakdown": rejection_breakdown,
                "selected_for_scan": len(markets),
                "strategy_candidates": strategy_candidates,
                "reached_risk_engine": strategy_candidates,
                "risk_passed": risk_passed,
                "risk_blocked": risk_blocked,
                "duplicates_blocked": duplicates_blocked,
                "persisted_signals": persisted_signals,
                "risk_block_reasons": risk_block_reasons,
                "news_validation_enabled": bool(self.context.settings.news_validation_enabled),
            },
        )

    async def calc_edge(self, market: dict) -> SignalPayload | None:
        decision = await self.strategy.analyze_market(market)
        if decision is None:
            return None
        try:
            direction = decision.direction
            liquidity_summary = market["orderbook_summary_yes"] if direction == "YES" else market["orderbook_summary_no"]
            return SignalPayload(
                signal_id=str(uuid4()),
                market_id=market["id"],
                token_id=str(market["token_id_yes"] if direction == "YES" else market["token_id_no"]),
                market_question=market["question"],
                direction=direction,
                edge=float(decision.edge),
                confidence=float(decision.confidence),
                price=float(market[f"price_{direction.lower()}"]),
                price_yes=float(market["price_yes"]),
                price_no=float(market["price_no"]),
                volume_24h=float(market["volume_24h"]),
                asset_symbol=str(market["asset_symbol"]),
                asset_name=str(market["asset_name"]),
                crypto_tier=str(market["crypto_tier"]),
                market_kind=str(market["market_kind"]),
                question_type=str(market["question_type"]),
                strategy_id=decision.strategy_id,
                strategy_version=decision.strategy_version,
                model_probability=decision.model_probability,
                market_probability=decision.market_probability,
                regime=decision.regime,
                expected_slippage_bps=decision.expected_slippage_bps,
                expected_holding_minutes=decision.expected_holding_minutes,
                thesis_tags=[str(item) for item in market.get("thesis_tags", [])],
                thesis_hash=str(market.get("thesis_hash", "")),
                reasoning=decision.reasoning,
                features_summary=decision.features_summary,
                liquidity_summary=liquidity_summary,
                metadata={
                    "source": "claude_agent",
                    "clob_token_ids": market.get("clob_token_ids", []),
                    "end_date": market.get("end_date") or market.get("endDate") or market.get("closeTime"),
                },
            )
        except Exception as exc:
            raise InvalidModelResponseError(f"strategy engine returned invalid signal: {exc}") from exc

    async def close(self) -> None:
        await super().close()
        await self.connector.close()
