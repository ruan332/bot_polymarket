from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from agents.claude_agent import ClaudeAgent
from agents.claw_agent import ClawAgent
from agents.codex_agent import CodexAgent
from agents.news_validator_agent import NewsValidatorAgent
from api import main as api_main
from core.config import infer_provider_from_model, load_agents_config, load_crypto_config, load_risk_config
from core.schemas import MarketSnapshotPayload, ModelResponse, PortfolioSummary, ReviewPayload, SignalPayload


class FakeBus:
    def __init__(self):
        self.streams: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        self.hashes: dict[str, dict] = defaultdict(dict)
        self.config_version = 0
        self.runtime_overrides: dict[str, dict[str, str]] = {}
        self.published_control: list[tuple[str, dict]] = []
        self._counter = 0

    async def publish_event(self, stream: str, payload: dict) -> str:
        self._counter += 1
        event_id = f"{self._counter}-0"
        self.streams[stream].append((event_id, payload))
        if stream == "events:control":
            self.published_control.append((event_id, payload))
        return event_id

    async def ensure_group(self, stream: str, group: str) -> None:
        return None

    async def read_group(self, stream: str, group: str, consumer: str, block_ms: int = 1000, count: int = 1):
        events = self.streams.get(stream, [])
        if not events:
            return []
        taken = events[:count]
        self.streams[stream] = events[count:]
        return taken

    async def ack(self, stream: str, group: str, event_id: str) -> None:
        return None

    async def bootstrap_runtime_config(self, config) -> None:
        return None

    async def get_agent_runtime_override(self, agent_name: str) -> dict[str, str] | None:
        return self.runtime_overrides.get(agent_name)

    async def get_config_version(self) -> int:
        return self.config_version

    async def set_agent_runtime_override(self, agent_name: str, config) -> int:
        self.runtime_overrides[agent_name] = {
            "model": config.model,
            "provider": config.provider,
            "fallback_model": config.fallback_model,
        }
        self.config_version += 1
        return self.config_version

    async def get_daily_cost(self, key: str) -> float:
        return float(self.hashes.get(key, {}).get("cost_usd", 0.0))

    async def increment_cost_summary(self, key: str, *, cost_usd: float, input_tokens: int, output_tokens: int) -> None:
        bucket = self.hashes.setdefault(key, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0})
        bucket["cost_usd"] += cost_usd
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["calls"] += 1

    async def get_hash(self, key: str) -> dict:
        return self.hashes.get(key, {})


class FakeRepository:
    def __init__(self, bankroll: float = 1000.0):
        self.bankroll = bankroll
        self.signals: list[dict] = []
        self.decisions: list[dict] = []
        self.orders: list[dict] = []
        self.risk_events: list[dict] = []
        self.llm_calls: list[dict] = []
        self.heartbeats: dict[str, dict] = {}
        self.positions: dict[str, dict] = {}
        self.market_snapshots: list[dict] = []
        self.equity_history: list[dict] = []
        self.news_validations: list[dict] = []
        self.pipeline_events: list[dict] = []
        self.pair_cycles: dict[str, dict] = {}
        self.pending_pair_orders: dict[str, dict] = {}
        self.settlement_events: list[dict] = []

    async def record_signal(self, signal_id: str, event_type: str, payload: dict) -> None:
        self.signals.append(payload)

    async def record_decision(self, decision_id: str, signal_id: str, event_type: str, payload: dict) -> None:
        self.decisions.append(payload)

    async def record_news_validation(self, validation) -> None:
        self.news_validations.append(validation.model_dump(mode="json"))

    async def attach_news_validation(self, signal_id: str, payload: dict) -> None:
        for signal in self.signals:
            if signal["signal_id"] == signal_id:
                signal["news_validation"] = payload

    async def has_recent_signal_duplicate(
        self,
        *,
        market_id: str,
        direction: str,
        thesis_hash: str,
        cooldown_minutes: int,
    ) -> bool:
        return any(
            signal.get("market_id") == market_id
            and signal.get("direction") == direction
            and signal.get("thesis_hash") == thesis_hash
            for signal in self.signals
        )

    async def record_paper_order(self, order_id: str, signal_id: str, market_id: str, status: str, payload: dict) -> None:
        self.orders.append(payload)
        if status in {"simulated", "live_filled"}:
            position_key = str(payload.get("position_key") or f"{market_id}:{payload['direction']}")
            action = payload.get("action", "entry")
            existing = self.positions.get(position_key)
            if action in {"entry", "scale_in"}:
                if existing is None:
                    self.positions[position_key] = {
                        "market_id": market_id,
                        "position_key": position_key,
                        "token_id": payload["token_id"],
                        "market_question": payload.get("market_question", ""),
                        "asset_symbol": payload.get("asset_symbol", ""),
                        "crypto_tier": payload.get("crypto_tier", ""),
                        "strategy_id": payload.get("strategy_id", ""),
                        "regime": payload.get("regime", ""),
                        "trade_group_id": payload.get("trade_group_id", ""),
                        "cycle_slug": payload.get("cycle_slug", ""),
                        "leg_role": payload.get("leg_role", ""),
                        "direction": payload["direction"],
                        "size": payload["size"],
                        "average_price": payload["price_limit"],
                        "current_price": payload["price_limit"],
                        "cost_basis_usd": payload["notional_usd"],
                        "current_value_usd": payload["notional_usd"],
                        "unrealized_pnl": 0.0,
                        "take_profit_price": payload.get("take_profit_price"),
                        "stop_loss_price": payload.get("stop_loss_price"),
                        "time_stop_minutes": payload.get("time_stop_minutes"),
                        "opened_at": payload.get("created_at", "2026-03-18T12:00:00Z"),
                        "scaled_out_count": 0,
                        "latest_spread_bps": 40.0,
                    }
                else:
                    total_size = existing["size"] + payload["size"]
                    weighted_price = (
                        ((existing["average_price"] * existing["size"]) + (payload["price_limit"] * payload["size"])) / total_size
                        if total_size
                        else payload["price_limit"]
                    )
                    existing["size"] = total_size
                    existing["average_price"] = weighted_price
                    existing["cost_basis_usd"] += payload["notional_usd"]
                    existing["current_price"] = payload["price_limit"]
                    existing["current_value_usd"] = existing["current_price"] * existing["size"]
                    existing["unrealized_pnl"] = existing["current_value_usd"] - existing["cost_basis_usd"]
            else:
                if existing is None:
                    return
                remaining_size = max(existing["size"] - payload["size"], 0)
                if remaining_size == 0:
                    self.positions.pop(position_key, None)
                else:
                    existing["size"] = remaining_size
                    existing["cost_basis_usd"] = existing["average_price"] * remaining_size
                    existing["current_value_usd"] = existing["current_price"] * remaining_size
                    existing["unrealized_pnl"] = existing["current_value_usd"] - existing["cost_basis_usd"]
                    if action == "scale_out":
                        existing["scaled_out_count"] += 1

    async def record_llm_call(self, **payload) -> None:
        self.llm_calls.append(payload)

    async def record_risk_event(self, event_id: str, agent: str, reason: str, payload: dict) -> None:
        self.risk_events.append({"agent": agent, "reason": reason, **payload})

    async def record_pipeline_telemetry(self, event_id: str, agent: str, event_type: str, payload: dict) -> None:
        self.pipeline_events.append({"agent": agent, "event_type": event_type, **payload, "created_at": "2026-03-18T12:00:00Z"})

    async def record_settlement_event(
        self,
        settlement_id: str,
        position_key: str,
        market_id: str,
        status: str,
        payload: dict,
    ) -> None:
        self.settlement_events.append(
            {
                "settlement_id": settlement_id,
                "position_key": position_key,
                "market_id": market_id,
                "status": status,
                **payload,
            }
        )

    async def upsert_heartbeat(self, heartbeat) -> None:
        self.heartbeats[heartbeat.agent] = heartbeat.model_dump()

    async def record_equity_snapshot(self, source: str = "system") -> None:
        portfolio = await self.get_portfolio_summary()
        self.equity_history.append({**portfolio.model_dump(), "source": source, "created_at": "2026-03-18T12:00:00Z"})

    async def record_market_snapshots(self, snapshots) -> None:
        self.market_snapshots.extend(snapshot.model_dump(mode="json") for snapshot in snapshots)

    async def upsert_pair_cycle(self, state) -> None:
        self.pair_cycles[state.asset_symbol] = state.model_dump(mode="json")

    async def get_pair_cycle(self, asset_symbol: str):
        return self.pair_cycles.get(asset_symbol)

    async def upsert_pending_pair_order(self, pending) -> None:
        self.pending_pair_orders[pending.pending_order_id] = pending.model_dump(mode="json")

    async def list_pending_pair_orders(self, status: str = "pending"):
        return [item for item in self.pending_pair_orders.values() if item.get("status") == status]

    async def resolve_pending_pair_order(self, pending_order_id: str, *, status: str, reason: str = "", payload: dict | None = None) -> None:
        order = self.pending_pair_orders.get(pending_order_id)
        if order is None:
            return
        order["status"] = status
        order["reason"] = reason
        if payload:
            order.update(payload)

    async def get_portfolio_summary(self) -> PortfolioSummary:
        total_exposure = sum(position["cost_basis_usd"] for position in self.positions.values())
        current_market_value = sum(position["current_value_usd"] for position in self.positions.values())
        realized_pnl = sum(float(item.get("realized_pnl_usd") or 0.0) for item in self.orders)
        return PortfolioSummary(
            available_balance=max(self.bankroll - total_exposure + realized_pnl, 0.0),
            total_exposure=total_exposure,
            current_market_value=current_market_value,
            total_equity=max(self.bankroll - total_exposure + realized_pnl, 0.0) + current_market_value,
            total_pnl=realized_pnl + current_market_value - total_exposure,
            open_positions=len(self.positions),
            realized_pnl=realized_pnl,
            unrealized_pnl=current_market_value - total_exposure,
        )

    @staticmethod
    def _matches(payload: dict, asset: str | None = None, tier: str | None = None, strategy: str | None = None) -> bool:
        if asset and str(payload.get("asset_symbol", "")).upper() != asset.upper():
            return False
        if tier and str(payload.get("crypto_tier", "")).lower() != tier.lower():
            return False
        if strategy and str(payload.get("strategy_id", "")) != strategy:
            return False
        return True

    async def get_recent_signals(
        self,
        limit: int = 20,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        cutoff_name: str | None = None,
    ):
        items = [item for item in self.signals if self._matches(item, asset, tier, strategy)]
        return list(reversed(items[-limit:]))

    async def get_recent_orders(
        self,
        limit: int = 20,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        cutoff_name: str | None = None,
    ):
        items = [item for item in self.orders if self._matches(item, asset, tier, strategy)]
        return list(reversed(items[-limit:]))

    async def get_recent_risk_events(
        self,
        limit: int = 20,
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        cutoff_name: str | None = None,
    ):
        return list(reversed(self.risk_events[-limit:]))

    async def get_risk_breakdown_report(
        self,
        hours: int = 24,
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        cutoff_name: str | None = None,
    ):
        return {
            "generated_at": "2026-03-18T12:00:00Z",
            "window_hours": hours,
            "asset_filter": asset or "",
            "tier_filter": tier or "",
            "strategy_filter": strategy or "",
            "total_events": len(self.risk_events),
            "by_reason": [{"label": "review rejected signal", "count": 1}] if self.risk_events else [],
            "by_strategy": [{"label": strategy or "trend_follow_bayes", "count": len(self.risk_events)}] if self.risk_events else [],
            "by_strategy_reason": [
                {
                    "label": strategy or "trend_follow_bayes",
                    "count": len(self.risk_events),
                    "reasons": [{"label": "review rejected signal", "count": 1}],
                }
            ]
            if self.risk_events
            else [],
        }

    async def get_recent_pipeline_telemetry(self, limit: int = 30, cutoff_name: str | None = None):
        return list(reversed(self.pipeline_events[-limit:]))

    async def get_recent_decisions(
        self,
        limit: int = 20,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        cutoff_name: str | None = None,
    ):
        items = [item for item in self.decisions if self._matches(item, asset, tier, strategy)]
        return list(reversed(items[-limit:]))

    async def get_equity_history(self, limit: int = 100):
        return list(self.equity_history[-limit:])

    async def get_recent_settlement_events(self, limit: int = 20):
        return list(reversed(self.settlement_events[-limit:]))

    async def get_open_positions(self):
        return list(self.positions.values())

    async def get_market_snapshots(self, market_id: str | None = None, limit: int = 5000, **_: object):
        items = self.market_snapshots
        if market_id:
            items = [item for item in items if item["market_id"] == market_id]
        return items[-limit:]

    async def get_latest_market_snapshot(self, market_id: str):
        items = [item for item in self.market_snapshots if item["market_id"] == market_id]
        if not items:
            return None
        latest = items[-1]
        metadata = latest.get("metadata") or latest.get("payload") or {}
        return {
            "market_id": latest["market_id"],
            "question": latest["question"],
            "token_id_yes": latest["token_id_yes"],
            "token_id_no": latest["token_id_no"],
            "price_yes": latest["price_yes"],
            "price_no": latest["price_no"],
            "volume_24h": latest.get("volume_24h", 0.0),
            "metadata": metadata,
            "created_at": latest["created_at"],
        }

    async def get_execution_risk_state(self, hours: int = 24) -> dict[str, object]:
        consecutive_losses = 0
        last_loss_at = None
        for item in reversed(self.orders):
            pnl = float(item.get("realized_pnl_usd") or 0.0)
            if pnl < 0:
                consecutive_losses += 1
                if last_loss_at is None:
                    last_loss_at = item.get("created_at")
            elif pnl > 0:
                break
        return {
            "daily_spend_usd": sum(
                float(item.get("notional_usd") or 0.0)
                for item in self.orders
                if str(item.get("action") or "entry") in {"entry", "scale_in"}
            ),
            "realized_pnl_usd": sum(float(item.get("realized_pnl_usd") or 0.0) for item in self.orders),
            "consecutive_losses": consecutive_losses,
            "last_loss_at": last_loss_at,
        }

    async def get_agent_status(self):
        return list(self.heartbeats.values())

    async def metrics_overview_since(self, cutoff_name: str | None = None):
        latest_scan = next((item for item in reversed(self.pipeline_events) if item["event_type"] == "scanner.scan_cycle"), None)
        latest_review = next((item for item in reversed(self.pipeline_events) if item["event_type"] == "reviewer.review_cycle"), None)
        latest_execution = next((item for item in reversed(self.pipeline_events) if item["event_type"] == "executor.execute_cycle"), None)
        return {
            "analysis_cutoff": None,
            "signals": len(self.signals),
            "decisions": len(self.decisions),
            "orders": len(self.orders),
            "risk_events": len(self.risk_events),
            "pending_pair_orders": sum(1 for item in self.pending_pair_orders.values() if item.get("status") == "pending"),
            "portfolio": (await self.get_portfolio_summary()).model_dump(),
            "flow_summary": {
                "window_minutes": 15,
                "gamma_markets_fetched": sum(int(item.get("gamma_markets_fetched") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "crypto_classified": sum(int(item.get("crypto_classified") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "selected_for_scan": sum(int(item.get("selected_for_scan") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "strategy_candidates": sum(int(item.get("strategy_candidates") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "reached_risk_engine": sum(int(item.get("reached_risk_engine") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "pre_risk_blocked": sum(int(item.get("pre_risk_blocked") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "risk_passed": sum(int(item.get("risk_passed") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "risk_blocked": sum(int(item.get("risk_blocked") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "duplicates_blocked": sum(int(item.get("duplicates_blocked") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "persisted_signals": sum(int(item.get("persisted_signals") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "reviewer_inbox": sum(int(item.get("inbox_count") or 0) for item in self.pipeline_events if item["event_type"] == "reviewer.review_cycle"),
                "reviewer_approved": sum(int(item.get("approved_count") or 0) for item in self.pipeline_events if item["event_type"] == "reviewer.review_cycle"),
                "reviewer_rejected": sum(int(item.get("rejected_count") or 0) for item in self.pipeline_events if item["event_type"] == "reviewer.review_cycle"),
                "executor_inbox": sum(int(item.get("inbox_count") or 0) for item in self.pipeline_events if item["event_type"] == "executor.execute_cycle"),
                "executor_executed": sum(int(item.get("executed_count") or 0) for item in self.pipeline_events if item["event_type"] == "executor.execute_cycle"),
                "executor_blocked": sum(int(item.get("blocked_count") or 0) for item in self.pipeline_events if item["event_type"] == "executor.execute_cycle"),
                "exit_orders_count": sum(int(item.get("exit_orders_count") or 0) for item in self.pipeline_events if item["event_type"] == "executor.execute_cycle"),
            },
            "latest_scan_telemetry": latest_scan,
            "latest_review_telemetry": latest_review,
            "latest_execution_telemetry": latest_execution,
        }

    async def metrics_overview(self):
        return await self.metrics_overview_since()

    async def get_performance_report(
        self,
        hours: int = 24,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        cutoff_name: str | None = None,
    ):
        signals = [item for item in self.signals if self._matches(item, asset, tier, strategy)]
        decisions = [item for item in self.decisions if self._matches(item, asset, tier, strategy)]
        orders = [item for item in self.orders if self._matches(item, asset, tier, strategy)]
        open_positions = [item for item in self.positions.values() if self._matches(item, asset, tier, strategy)]
        pending_pair_orders = [
            item for item in self.pending_pair_orders.values() if self._matches(item, asset, tier, strategy)
        ]
        pair_groups: dict[str, set[str]] = {}
        for item in orders:
            if str(item.get("strategy_id") or "") != "pair_15m":
                continue
            trade_group_id = str(item.get("trade_group_id") or "")
            if not trade_group_id:
                continue
            pair_groups.setdefault(trade_group_id, set()).add(str(item.get("leg_role") or ""))
        return {
            "generated_at": "2026-03-18T12:05:00Z",
            "window_hours": hours,
            "analysis_cutoff": None,
            "asset_filter": asset or "",
            "tier_filter": tier or "",
            "strategy_filter": strategy or "",
            "summary": {
                "signals": len(signals),
                "decisions": len(decisions),
                "orders": len(orders),
                "risk_events": len(self.risk_events),
                "approval_rate": 1.0 if signals else 0.0,
                "execution_rate": 1.0 if decisions else 0.0,
                "positive_position_rate": 0.0,
                "win_rate": 1.0 if orders else 0.0,
                "avg_edge": 0.22,
                "avg_confidence": 0.77,
                "total_order_notional": 44.0,
                "avg_order_notional": 44.0,
                "daily_spend_usd": 44.0,
                "realized_pnl_window": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "llm_cost_usd": 0.01,
                **(await self.get_portfolio_summary()).model_dump(),
            },
            "cost_by_agent": [{"agent": "claude", "cost_usd": 0.01, "calls": 1}],
            "risk_breakdown": [{"label": "review rejected signal", "count": 1}],
            "risk_breakdown_by_strategy": [
                {
                    "label": "trend_follow_bayes",
                    "count": 1,
                    "reasons": [{"label": "review rejected signal", "count": 1}],
                }
            ],
            "asset_breakdown": [{"label": "BTC", "count": len(signals)}],
            "tier_breakdown": [{"label": "btc", "count": len(signals)}],
            "strategy_breakdown": [{"label": "trend_follow_bayes", "signals": len(signals), "orders": len(orders), "realized_pnl_usd": 0.0}],
            "regime_breakdown": [{"label": "trend", "count": len(signals)}],
            "exit_reason_breakdown": [],
            "news_breakdown": [{"label": "validated", "count": len(signals)}],
            "news_provider_breakdown": [{"label": "smoke", "count": len(signals)}],
            "news_fallback_breakdown": [
                {"label": "primary_only", "count": len(signals)},
                {"label": "fallback_used", "count": 0},
                {"label": "unknown", "count": 0},
            ],
            "pair_trade_summary": {
                "groups": len(pair_groups),
                "fully_hedged_groups": sum(1 for legs in pair_groups.values() if {"primary", "hedge"}.issubset(legs)),
                "primary_only_groups": sum(1 for legs in pair_groups.values() if "primary" in legs and "hedge" not in legs),
                "orphan_hedge_groups": sum(1 for legs in pair_groups.values() if "hedge" in legs and "primary" not in legs),
                "pending_hedges": sum(1 for item in pending_pair_orders if item.get("status") == "pending"),
                "primary_notional": round(
                    sum(float(item.get("notional_usd") or 0.0) for item in orders if str(item.get("leg_role") or "") == "primary"),
                    4,
                ),
                "hedge_notional": round(
                    sum(float(item.get("notional_usd") or 0.0) for item in orders if str(item.get("leg_role") or "") == "hedge"),
                    4,
                ),
            },
            "last_news_provider": (
                {
                    "provider_used": "smoke",
                    "fallback_used": False,
                    "signal_id": signals[-1]["signal_id"],
                    "asset_symbol": signals[-1]["asset_symbol"],
                    "crypto_tier": signals[-1]["crypto_tier"],
                    "created_at": signals[-1]["created_at"],
                }
                if signals
                else None
            ),
            "top_markets": [
                {
                    "market_id": "m-1",
                    "market_question": "Will ETH rally?",
                    "asset_symbol": "BTC",
                    "crypto_tier": "btc",
                    "signal_count": len(signals),
                    "order_count": len(orders),
                    "avg_edge": 0.22,
                    "avg_confidence": 0.77,
                }
            ],
            "open_positions": open_positions,
            "mae_mfe": {"avg_mae": 0.0, "avg_mfe": 0.0},
            "time_series": {"pipeline": [], "equity": []},
        }

    async def get_analysis_cutoffs(self):
        return []

    async def get_analysis_cutoff(self, cutoff_name: str):
        return None

    async def create_analysis_cutoff(self, cutoff_name: str, *, created_at=None, metadata=None):
        return {
            "cutoff_name": cutoff_name,
            "created_at": created_at or "2026-03-18T12:00:00Z",
            "metadata": metadata or {},
        }


class FakeContext:
    def __init__(self):
        self.settings = SimpleNamespace(
            max_daily_spend_usd=5.0,
            paper_bankroll_usd=1000.0,
            live_trading=False,
            smoke_test_mode=True,
            news_validation_enabled=True,
            review_llm_enabled=False,
            execution_llm_enabled=False,
            review_llm_fail_open=False,
            execution_llm_fail_open=True,
            polymarket_gamma_url="https://gamma-api.polymarket.com",
            polymarket_clob_url="https://clob.polymarket.com",
            polymarket_market_ws="wss://ws-subscriptions-clob.polymarket.com/ws/market",
            polymarket_private_key="",
            polymarket_api_key="",
            polymarket_api_secret="",
            polymarket_api_passphrase="",
            polymarket_funder="",
            polymarket_signature_type=0,
            polymarket_chain_id=137,
            polymarket_live_min_order_size=5,
            news_provider_primary="marketaux",
            news_provider_fallback="alphavantage",
            news_lookback_hours=24,
            news_http_timeout_seconds=15,
            news_fallback_on_quota=True,
            news_fallback_on_rate_limit=True,
            news_fallback_on_upstream_error=True,
            news_fallback_on_empty_result=False,
            marketaux_api_key="",
            marketaux_base_url="https://api.marketaux.com/v1/news/all",
            marketaux_language="en",
            marketaux_limit_per_request=3,
            alphavantage_api_key="",
            alphavantage_base_url="https://www.alphavantage.co/query",
            alphavantage_news_limit=50,
            copytrade_enabled=False,
            copytrade_markets=[],
            copytrade_shares=2,
            copytrade_max_buy_counts_per_side=1,
            copytrade_wait_for_next_market_start=False,
            copytrade_price_buffer=0.01,
            copytrade_second_leg_base_price=0.98,
            copytrade_signal_confidence_threshold=0.5,
            copytrade_noise_threshold=0.02,
            copytrade_min_history_points=6,
            momentum_enabled=False,
            momentum_markets=[],
            momentum_trading_enabled=False,
            momentum_signal_confidence_threshold=0.62,
            momentum_min_history_points=6,
            momentum_cooldown_minutes=20,
            momentum_max_positions=2,
            momentum_wait_for_next_market_start=False,
            momentum_entry_notional_usd=1.0,
            momentum_take_profit_usd=1.0,
            momentum_sizing_mode="fixed_notional",
        )
        self.live_bootstrap_status = {
            "mode": "live",
            "ready": True,
            "reason": "live trading ready",
            "parsed_collateral": {"balance": 12.0, "allowance": 12.0},
        }
        self.agents_config = load_agents_config()
        self.risk_config = load_risk_config()
        self.risk_config.max_daily_spend_usd = 100.0
        self.crypto_config = load_crypto_config()
        self.repository = FakeRepository()
        self.bus = FakeBus()

    async def reload_configs(self) -> None:
        self.agents_config = load_agents_config()
        self.risk_config = load_risk_config()
        self.crypto_config = load_crypto_config()

    async def refresh_live_bootstrap_status(self, *, sync_allowance: bool = False):
        return dict(self.live_bootstrap_status)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_signal_news_review_execute_flow_smoke(monkeypatch) -> None:
    context = FakeContext()
    market = {
        "id": "market-1",
        "question": "Will BTC be above 100k?",
        "description": "test market",
        "price_yes": 0.40,
        "price_no": 0.60,
        "volume_24h": 50000.0,
        "token_id_yes": "token-yes-1",
        "token_id_no": "token-no-1",
        "clob_token_ids": ["token-yes-1", "token-no-1"],
        "asset_symbol": "BTC",
        "asset_name": "Bitcoin",
        "crypto_tier": "btc",
        "market_kind": "direct_coin",
        "question_type": "upside_target",
        "thesis_tags": ["btc", "btc", "upside_target"],
        "thesis_hash": "btc-btc",
        "orderbook_summary_yes": {"best_bid": 0.39, "best_ask": 0.41, "spread_bps": 40.0, "bid_depth": 520.0, "ask_depth": 160.0},
        "orderbook_summary_no": {"best_bid": 0.59, "best_ask": 0.61, "spread_bps": 40.0, "bid_depth": 20.0, "ask_depth": 430.0},
    }

    claude = ClaudeAgent(context)
    news_validator = NewsValidatorAgent(context)
    codex = CodexAgent(context)
    claw = ClawAgent(context)

    async def fake_scan(*args, **kwargs):
        return ModelResponse(
            content='{"edge": 0.30, "direction": "YES", "confidence": 0.80, "reasoning": "spread mispriced"}',
            input_tokens=10,
            output_tokens=15,
            model="test-scan",
            cost_usd=0.01,
            provider="test",
        )

    async def fake_review(*args, **kwargs):
        return ModelResponse(
            content='{"approved": true, "notes": "looks good", "corrected_price_limit": 0.405}',
            input_tokens=8,
            output_tokens=12,
            model="test-review",
            cost_usd=0.01,
            provider="test",
        )

    async def fake_execute(*args, **kwargs):
        return ModelResponse(
            content='{"execute": true, "size": 100, "price_limit": 0.41, "reason": "paper fill"}',
            input_tokens=8,
            output_tokens=12,
            model="test-exec",
            cost_usd=0.01,
            provider="test",
        )

    async def fake_markets(limit: int = 20, crypto_only: bool = True):
        return [market]

    monkeypatch.setattr(claude.connector, "get_active_markets", fake_markets)
    monkeypatch.setattr(claude.provider, "call", fake_scan)
    monkeypatch.setattr(codex.provider, "call", fake_review)
    monkeypatch.setattr(claw.provider, "call", fake_execute)

    await claude.tick()
    assert len(context.repository.signals) == 1
    assert len(context.repository.market_snapshots) == 1
    assert any(event["event_type"] == "scanner.scan_cycle" for event in context.repository.pipeline_events)
    assert len(context.bus.streams["signals:candidates"]) == 1

    await news_validator.tick()
    assert len(context.repository.news_validations) == 1
    assert context.repository.signals[0]["news_validation"]["validated"] is True
    assert context.repository.signals[0]["news_validation"]["provider_used"] == "smoke"
    assert context.repository.signals[0]["news_validation"]["fallback_used"] is False
    assert len(context.bus.streams["signals:validated"]) == 1

    await codex.tick()
    assert len(context.repository.decisions) == 1
    assert any(event["event_type"] == "reviewer.review_cycle" for event in context.repository.pipeline_events)
    assert len(context.bus.streams["signals:reviewed"]) == 1

    await claw.tick()
    assert len(context.repository.orders) == 1
    assert any(event["event_type"] == "executor.execute_cycle" for event in context.repository.pipeline_events)
    assert context.repository.orders[0]["status"] == "simulated"
    assert context.repository.orders[0]["token_id"] == "token-yes-1"
    assert context.repository.orders[0]["asset_symbol"] == "BTC"
    assert context.repository.orders[0]["crypto_tier"] == "btc"
    assert context.repository.orders[0]["news_validation"]["validated"] is True
    assert context.repository.orders[0]["news_validation"]["provider_used"] == "smoke"
    portfolio = await context.repository.get_portfolio_summary()
    assert portfolio.open_positions == 1
    assert portfolio.total_exposure > 0


@pytest.mark.asyncio
async def test_claude_agent_runs_pair_and_momentum_engines_when_both_enabled(monkeypatch) -> None:
    context = FakeContext()
    context.settings.copytrade_enabled = True
    context.settings.copytrade_markets = ["BTC"]
    context.settings.momentum_enabled = True
    context.settings.momentum_markets = ["BTC"]
    context.settings.momentum_trading_enabled = True
    agent = ClaudeAgent(context)
    calls: list[str] = []

    async def fake_pair_tick():
        calls.append("pair")
        return {"persisted_signals": 0}

    async def fake_momentum_tick():
        calls.append("momentum")
        return {"persisted_signals": 0}

    monkeypatch.setattr(agent.pair_engine, "tick", fake_pair_tick)
    monkeypatch.setattr(agent.momentum_engine, "tick", fake_momentum_tick)

    await agent.tick()

    assert calls == ["pair", "momentum"]


@pytest.mark.asyncio
async def test_signal_review_execute_flow_without_news_validation(monkeypatch) -> None:
    context = FakeContext()
    context.settings.news_validation_enabled = False
    market = {
        "id": "market-1",
        "question": "Will BTC be above 100k?",
        "description": "test market",
        "price_yes": 0.40,
        "price_no": 0.60,
        "volume_24h": 50000.0,
        "token_id_yes": "token-yes-1",
        "token_id_no": "token-no-1",
        "clob_token_ids": ["token-yes-1", "token-no-1"],
        "asset_symbol": "BTC",
        "asset_name": "Bitcoin",
        "crypto_tier": "btc",
        "market_kind": "direct_coin",
        "question_type": "upside_target",
        "thesis_tags": ["btc", "btc", "upside_target"],
        "thesis_hash": "btc-btc",
        "orderbook_summary_yes": {"best_bid": 0.39, "best_ask": 0.41, "spread_bps": 40.0, "bid_depth": 520.0, "ask_depth": 160.0},
        "orderbook_summary_no": {"best_bid": 0.59, "best_ask": 0.61, "spread_bps": 40.0, "bid_depth": 20.0, "ask_depth": 430.0},
    }

    claude = ClaudeAgent(context)
    codex = CodexAgent(context)
    claw = ClawAgent(context)

    async def fake_scan(*args, **kwargs):
        return ModelResponse(
            content='{"edge": 0.30, "direction": "YES", "confidence": 0.80, "reasoning": "spread mispriced"}',
            input_tokens=10,
            output_tokens=15,
            model="test-scan",
            cost_usd=0.01,
            provider="test",
        )

    async def fake_review(*args, **kwargs):
        return ModelResponse(
            content='{"approved": true, "notes": "looks good", "corrected_price_limit": 0.405}',
            input_tokens=8,
            output_tokens=12,
            model="test-review",
            cost_usd=0.01,
            provider="test",
        )

    async def fake_execute(*args, **kwargs):
        return ModelResponse(
            content='{"execute": true, "size": 100, "price_limit": 0.41, "reason": "paper fill"}',
            input_tokens=8,
            output_tokens=12,
            model="test-exec",
            cost_usd=0.01,
            provider="test",
        )

    async def fake_markets(limit: int = 20, crypto_only: bool = True):
        return [market]

    monkeypatch.setattr(claude.connector, "get_active_markets", fake_markets)
    monkeypatch.setattr(claude.provider, "call", fake_scan)
    monkeypatch.setattr(codex.provider, "call", fake_review)
    monkeypatch.setattr(claw.provider, "call", fake_execute)

    await claude.tick()
    assert len(context.repository.signals) == 1
    assert len(context.bus.streams["signals:candidates"]) == 0
    assert len(context.bus.streams["signals:validated"]) == 1

    await codex.tick()
    assert len(context.repository.decisions) == 1
    assert context.repository.decisions[0]["news_validation"] is None

    await claw.tick()
    assert len(context.repository.orders) == 1
    assert context.repository.orders[0]["news_validation"] is None


@pytest.mark.asyncio
async def test_pair_signal_review_execute_and_fill_hedge(monkeypatch) -> None:
    context = FakeContext()
    context.settings.review_llm_enabled = False
    await context.bus.publish_event(
        "signals:validated",
        {
            "event_type": "pair_signal.created",
            "signal_id": "pair-sig-1",
            "trade_group_id": "pair-group-1",
            "cycle_slug": "btc-updown-15m-123",
            "cycle_start": datetime.now(UTC).isoformat(),
            "market_id": "pair-market-1",
            "market_question": "Will BTC be above current price in 15 minutes?",
            "asset_symbol": "BTC",
            "asset_name": "Bitcoin",
            "crypto_tier": "btc",
            "strategy_id": "pair_15m",
            "strategy_version": "v1",
            "predictor_direction": "up",
            "predictor_signal": "BUY_UP",
            "predictor_confidence": 0.72,
            "side_count_state": {"yes": 0, "no": 0, "max_per_side": 2},
            "primary_leg": {
                "market_id": "pair-market-1",
                "token_id": "token-yes-1",
                "direction": "YES",
                "leg_role": "primary",
                "size": 2,
                "target_price": 0.45,
                "reference_price": 0.45,
                "current_ask": 0.45,
                "current_bid": 0.44,
            },
            "hedge_leg": {
                "market_id": "pair-market-1",
                "token_id": "token-no-1",
                "direction": "NO",
                "leg_role": "hedge",
                "size": 2,
                "target_price": 0.40,
                "reference_price": 0.45,
                "current_ask": 0.42,
                "current_bid": 0.41,
            },
            "reasoning": "pair signal",
            "metadata": {},
        },
    )

    codex = CodexAgent(context)
    claw = ClawAgent(context)
    placements: list[dict[str, object]] = []

    async def fake_place_order(**kwargs):
        placements.append(kwargs)
        open_position = bool(kwargs.get("open_position", True))
        status = "simulated" if open_position else "simulated_pending"
        return {"status": status, "exchange_order_id": f"paper-{len(placements)}", **kwargs}

    monkeypatch.setattr(claw.connector, "place_order", fake_place_order)

    await codex.tick()
    await claw.tick()

    assert len(context.repository.decisions) == 1
    assert len(context.repository.orders) == 1
    assert len(context.repository.pending_pair_orders) == 1
    assert len(placements) == 2
    assert context.repository.orders[0]["leg_role"] == "primary"
    pending_order = next(iter(context.repository.pending_pair_orders.values()))
    assert pending_order["submission_status"] == "simulated_pending"
    assert pending_order["exchange_order_id"] == "paper-2"

    await context.repository.record_market_snapshots(
        [
            MarketSnapshotPayload(
                market_id="pair-market-1",
                question="Will BTC be above current price in 15 minutes?",
                token_id_yes="token-yes-1",
                token_id_no="token-no-1",
                price_yes=0.46,
                price_no=0.39,
                volume_24h=50000.0,
                asset_symbol="BTC",
                asset_name="Bitcoin",
                crypto_tier="btc",
                market_kind="direct_coin",
                question_type="direction",
                thesis_tags=["btc", "pair_15m"],
                metadata={
                    "orderbook_summary_yes": {"best_bid": 0.45, "best_ask": 0.46, "spread_bps": 20.0},
                    "orderbook_summary_no": {"best_bid": 0.38, "best_ask": 0.39, "spread_bps": 20.0},
                },
            )
        ]
    )

    await claw.tick()

    assert len(context.repository.orders) == 2
    assert context.repository.orders[1]["leg_role"] == "hedge"
    assert next(iter(context.repository.pending_pair_orders.values()))["status"] == "filled"
    assert len(placements) == 2


@pytest.mark.asyncio
async def test_pair_position_closes_on_cycle_rollover() -> None:
    context = FakeContext()
    claw = ClawAgent(context)
    opened_at = datetime.now(UTC).isoformat()
    context.repository.positions["pair-group-1:YES"] = {
        "market_id": "pair-market-1",
        "position_key": "pair-group-1:YES",
        "token_id": "token-yes-1",
        "market_question": "Will BTC be above current price in 15 minutes?",
        "asset_symbol": "BTC",
        "crypto_tier": "btc",
        "strategy_id": "pair_15m",
        "regime": "",
        "trade_group_id": "pair-group-1",
        "cycle_slug": "btc-updown-15m-older",
        "leg_role": "primary",
        "direction": "YES",
        "size": 2,
        "average_price": 0.45,
        "current_price": 0.62,
        "cost_basis_usd": 0.9,
        "current_value_usd": 1.24,
        "unrealized_pnl": 0.34,
        "take_profit_price": None,
        "stop_loss_price": None,
        "time_stop_minutes": None,
        "opened_at": opened_at,
        "scaled_out_count": 0,
        "latest_spread_bps": 30.0,
    }
    context.repository.pair_cycles["BTC"] = {
        "asset_symbol": "BTC",
        "asset_name": "Bitcoin",
        "crypto_tier": "btc",
        "cycle_slug": "btc-updown-15m-newer",
        "cycle_start": datetime.now(UTC).isoformat(),
        "market_id": "pair-market-2",
        "market_question": "Will BTC be above current price in 15 minutes?",
        "token_id_yes": "token-yes-2",
        "token_id_no": "token-no-2",
        "price_yes": 0.0,
        "price_no": 0.0,
        "status": "active",
        "side_counts": {"yes": 0, "no": 0},
        "max_buy_counts_per_side": 1,
        "last_signal_direction": None,
        "last_signal_at": None,
        "last_quote_at": None,
        "predictor_state": {},
        "metadata": {},
        "updated_at": datetime.now(UTC).isoformat(),
    }

    exit_stats = await claw.process_exit_cycle()

    assert exit_stats["exit_orders_count"] == 1
    assert "cycle_rollover" in exit_stats["exit_actions"]
    assert "pair-group-1:YES" not in context.repository.positions
    assert context.repository.orders[-1]["exit_reason"] == "cycle_rollover"
    assert context.repository.orders[-1]["trade_group_id"] == "pair-group-1"
    assert context.repository.orders[-1]["cycle_slug"] == "btc-updown-15m-older"
    assert context.repository.orders[-1]["leg_role"] == "primary"


@pytest.mark.asyncio
async def test_claw_exit_cycle_records_uuid_signal_id(monkeypatch) -> None:
    context = FakeContext()
    claw = ClawAgent(context)
    context.repository.positions["market-1:YES"] = {
        "market_id": "market-1",
        "position_key": "market-1:YES",
        "token_id": "token-yes-1",
        "market_question": "Will BTC be above 100k?",
        "asset_symbol": "BTC",
        "crypto_tier": "btc",
        "strategy_id": "mean_revert_bayes",
        "regime": "mean_revert",
        "direction": "YES",
        "size": 2,
        "average_price": 0.40,
        "current_price": 0.50,
        "cost_basis_usd": 0.80,
        "current_value_usd": 1.0,
        "unrealized_pnl": 0.20,
        "take_profit_price": 0.45,
        "stop_loss_price": 0.30,
        "time_stop_minutes": 90,
        "opened_at": "2026-03-21T10:00:00Z",
        "scaled_out_count": 0,
        "latest_spread_bps": 40.0,
    }

    async def fake_place_order(**kwargs):
        return {"status": "simulated", **kwargs}

    monkeypatch.setattr(claw.connector, "place_order", fake_place_order)

    result = await claw.process_exit_cycle()

    assert result["exit_orders_count"] == 1
    assert len(context.repository.orders) == 1
    assert context.repository.orders[0]["action"] == "close"
    assert context.repository.orders[0]["exit_reason"] == "take_profit"
    UUID(context.repository.orders[0]["signal_id"])


def test_claw_exit_decision_uses_take_profit_usd_for_momentum() -> None:
    context = FakeContext()
    claw = ClawAgent(context)
    position = {
        "strategy_id": "momentum_15m",
        "size": 2,
        "average_price": 0.40,
        "current_price": 0.95,
        "take_profit_price": 0.99,
        "stop_loss_price": 0.2,
        "time_stop_minutes": 90,
        "opened_at": "2026-03-21T10:00:00Z",
        "scaled_out_count": 0,
        "latest_spread_bps": 20.0,
    }

    decision = claw._exit_decision(position)

    assert decision is not None
    assert decision["reason"] == "take_profit_usd"
    assert decision["action"] == "close"
    assert decision["size"] == 2


@pytest.mark.asyncio
async def test_claw_execute_records_momentum_entry_and_take_profit_targets(monkeypatch) -> None:
    context = FakeContext()
    claw = ClawAgent(context)
    signal = SignalPayload(
        signal_id="sig-momentum-1",
        market_id="market-momentum-1",
        token_id="token-yes-1",
        market_question="Will BTC move up?",
        direction="YES",
        edge=0.30,
        confidence=0.85,
        price=0.63,
        price_yes=0.63,
        price_no=0.37,
        volume_24h=50000.0,
        asset_symbol="BTC",
        asset_name="Bitcoin",
        crypto_tier="btc",
        market_kind="direct_coin",
        question_type="direction",
        strategy_id="momentum_15m",
        strategy_version="v1",
        model_probability=0.82,
        market_probability=0.63,
        regime="trend",
        expected_slippage_bps=50.0,
        expected_holding_minutes=45,
        thesis_tags=["btc", "momentum_15m"],
        thesis_hash="btc-momentum-1",
        reasoning="test",
        features_summary={"momentum_short": 0.04},
        liquidity_summary={"spread_bps": 40.0, "ask_depth": 500.0},
    )
    review = ReviewPayload(
        signal_id=signal.signal_id,
        approved=True,
        asset_symbol=signal.asset_symbol,
        crypto_tier=signal.crypto_tier,
        corrected_price_limit=0.635,
        kelly_size=0,
        risk_fraction=0.1,
        take_profit_price=0.70,
        stop_loss_price=0.58,
        time_stop_minutes=45,
        notes="ok",
        original_signal=signal,
    )

    async def fake_place_order(**kwargs):
        return {"status": "simulated", **kwargs}

    monkeypatch.setattr(claw.connector, "place_order", fake_place_order)

    executed, _ = await claw.execute(review)

    assert executed is True
    order = context.repository.orders[-1]
    assert order["strategy_id"] == "momentum_15m"
    assert order["entry_notional_target_usd"] == pytest.approx(1.0)
    assert order["entry_notional_actual_usd"] == pytest.approx(2 * 0.635)
    assert order["take_profit_target_usd"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_claw_blocks_live_buy_orders_below_polymarket_minimum(monkeypatch) -> None:
    context = FakeContext()
    context.settings.live_trading = True
    claw = ClawAgent(context)
    called = False

    async def fake_place_order(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("connector should not be called for sub-$1 live buy orders")

    monkeypatch.setattr(claw.connector, "place_order", fake_place_order)

    result = await claw._place_order_or_block(
        market_id="market-1",
        token_id="token-yes-1",
        direction="YES",
        size=2,
        price_limit=0.325,
        reason="test live buy minimum",
        details={"signal_id": "sig-1", "asset_symbol": "BTC"},
    )

    assert called is False
    assert result["status"] == "blocked"
    assert "minimum size" in str(result["error"])
    assert context.repository.risk_events[-1]["notional_usd"] == pytest.approx(0.65)
    assert context.bus.streams["events:risk"][-1][1]["reason"].startswith("live order size below Polymarket minimum size")


@pytest.mark.asyncio
async def test_claw_blocks_live_orders_when_bootstrap_is_not_ready(monkeypatch) -> None:
    context = FakeContext()
    context.settings.live_trading = True
    context.live_bootstrap_status = {
        "mode": "live",
        "ready": False,
        "reason": "insufficient collateral balance/allowance for live trading",
        "parsed_collateral": {"balance": 0.0, "allowance": 0.0},
    }
    claw = ClawAgent(context)
    called = False

    async def fake_place_order(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("connector should not be called when bootstrap is not ready")

    monkeypatch.setattr(claw.connector, "place_order", fake_place_order)

    result = await claw._place_order_or_block(
        market_id="market-1",
        token_id="token-yes-1",
        direction="YES",
        size=5,
        price_limit=0.325,
        reason="test live bootstrap",
        details={"signal_id": "sig-1", "asset_symbol": "BTC"},
    )

    assert called is False
    assert result["status"] == "blocked"
    assert "live trading not ready" in str(result["error"])
    assert context.repository.risk_events[-1]["bootstrap_status"]["ready"] is False


@pytest.mark.asyncio
async def test_codex_and_claw_use_llm_when_flags_enabled(monkeypatch) -> None:
    context = FakeContext()
    context.settings.review_llm_enabled = True
    context.settings.execution_llm_enabled = True
    await context.bus.publish_event(
        "signals:validated",
        {
            "signal_id": "sig-1",
            "market_id": "market-1",
            "token_id": "token-yes-1",
            "market_question": "Will BTC be above 100k?",
            "direction": "YES",
            "edge": 0.22,
            "confidence": 0.9,
            "price": 0.4,
            "price_yes": 0.4,
            "price_no": 0.6,
            "volume_24h": 50000.0,
            "asset_symbol": "BTC",
            "asset_name": "Bitcoin",
            "crypto_tier": "btc",
            "market_kind": "direct_coin",
            "question_type": "direction",
            "strategy_id": "mean_revert_bayes",
            "strategy_version": "v1",
            "model_probability": 0.66,
            "market_probability": 0.4,
            "regime": "mean_revert",
            "expected_slippage_bps": 50.0,
            "expected_holding_minutes": 90,
            "thesis_tags": ["btc"],
            "thesis_hash": "btc-1",
            "reasoning": "test",
            "features_summary": {"momentum_short": 0.01},
            "liquidity_summary": {"spread_bps": 40.0, "ask_depth": 500.0},
            "news_validation": None,
            "created_at": "2026-03-18T12:00:00Z",
            "metadata": {},
        },
    )

    codex = CodexAgent(context)
    claw = ClawAgent(context)

    async def fake_review(*args, **kwargs):
        return ModelResponse(
            content='{"approved": true, "notes": "llm review", "corrected_price_limit": 0.402}',
            input_tokens=8,
            output_tokens=10,
            model="test-review",
            cost_usd=0.01,
            provider="test",
        )

    async def fake_execute(*args, **kwargs):
        return ModelResponse(
            content='{"execute": true, "size": 2, "price_limit": 0.401, "reason": "llm exec"}',
            input_tokens=7,
            output_tokens=9,
            model="test-exec",
            cost_usd=0.01,
            provider="test",
        )

    async def fake_place_order(**kwargs):
        return {"status": "simulated", **kwargs}

    monkeypatch.setattr(codex.provider, "call", fake_review)
    monkeypatch.setattr(claw.provider, "call", fake_execute)
    monkeypatch.setattr(claw.connector, "place_order", fake_place_order)

    await codex.tick()
    await claw.tick()

    assert context.repository.decisions[0]["review_mode"] == "llm"
    assert context.repository.decisions[0]["llm_notes"] == "llm review"
    assert context.repository.orders[0]["execution_mode"] == "llm"
    assert context.repository.orders[0]["reason"] == "llm exec"
    assert len(context.repository.llm_calls) == 2


@pytest.mark.asyncio
async def test_codex_review_fallback_blocks_when_fail_open_disabled(monkeypatch) -> None:
    context = FakeContext()
    context.settings.review_llm_enabled = True
    context.settings.review_llm_fail_open = False
    await context.bus.publish_event(
        "signals:validated",
        {
            "signal_id": "sig-1",
            "market_id": "market-1",
            "token_id": "token-yes-1",
            "market_question": "Will BTC be above 100k?",
            "direction": "YES",
            "edge": 0.22,
            "confidence": 0.9,
            "price": 0.4,
            "price_yes": 0.4,
            "price_no": 0.6,
            "volume_24h": 50000.0,
            "asset_symbol": "BTC",
            "asset_name": "Bitcoin",
            "crypto_tier": "btc",
            "market_kind": "direct_coin",
            "question_type": "direction",
            "strategy_id": "mean_revert_bayes",
            "strategy_version": "v1",
            "model_probability": 0.66,
            "market_probability": 0.4,
            "regime": "mean_revert",
            "expected_slippage_bps": 50.0,
            "expected_holding_minutes": 90,
            "thesis_tags": ["btc"],
            "thesis_hash": "btc-1",
            "reasoning": "test",
            "features_summary": {"momentum_short": 0.01},
            "liquidity_summary": {"spread_bps": 40.0, "ask_depth": 500.0},
            "news_validation": None,
            "created_at": "2026-03-18T12:00:00Z",
            "metadata": {},
        },
    )

    codex = CodexAgent(context)

    async def fake_review(*args, **kwargs):
        return ModelResponse(
            content="not-json",
            input_tokens=8,
            output_tokens=10,
            model="test-review",
            cost_usd=0.01,
            provider="test",
        )

    monkeypatch.setattr(codex.provider, "call", fake_review)

    await codex.tick()

    assert len(context.repository.decisions) == 0
    assert context.repository.risk_events[0]["reason"].startswith("review LLM fallback:")


def test_api_smoke(monkeypatch) -> None:
    fake_context = FakeContext()
    fake_context.repository.signals.append(
        {
            "signal_id": "sig-1",
            "market_question": "Will BTC rally?",
            "asset_symbol": "BTC",
            "crypto_tier": "btc",
            "direction": "YES",
            "edge": 0.22,
            "confidence": 0.77,
            "created_at": "2026-03-18T12:00:00Z",
        }
    )
    fake_context.repository.orders.append(
        {
            "order_id": "ord-1",
            "signal_id": "sig-1",
            "market_id": "m-1",
            "asset_symbol": "BTC",
            "crypto_tier": "btc",
            "direction": "YES",
            "size": 100,
            "price_limit": 0.44,
            "status": "simulated",
            "created_at": "2026-03-18T12:01:00Z",
            "notional_usd": 44.0,
        }
    )
    fake_context.repository.decisions.append(
        {
            "signal_id": "sig-1",
            "asset_symbol": "BTC",
            "crypto_tier": "btc",
            "approved": True,
            "corrected_price_limit": 0.405,
            "kelly_size": 100,
            "notes": "good",
            "created_at": "2026-03-18T12:01:30Z",
        }
    )
    fake_context.repository.risk_events.append(
        {"agent": "codex", "reason": "review rejected signal", "created_at": "2026-03-18T12:02:00Z"}
    )
    fake_context.repository.heartbeats["claude"] = {
        "agent": "claude",
        "model": "claude-sonnet-4-6",
        "running": True,
        "config_version": 1,
        "last_seen": "2026-03-18T12:03:00Z",
        "meta": {"interval_seconds": 10},
    }

    async def fake_create():
        return fake_context

    def fake_update(agent: str, model: str, provider: str | None = None, fallback_model: str | None = None):
        selected_provider, normalized_model = infer_provider_from_model(model)
        agent_cfg = fake_context.agents_config.agents[agent]
        agent_cfg.model = normalized_model
        agent_cfg.provider = provider or selected_provider or agent_cfg.provider
        if fallback_model:
            _, normalized_fallback = infer_provider_from_model(fallback_model)
            agent_cfg.fallback_model = normalized_fallback
        elif agent_cfg.provider == "openai":
            agent_cfg.fallback_model = normalized_model
        return fake_context.agents_config

    monkeypatch.setattr(api_main.AppContext, "create", fake_create)
    monkeypatch.setattr(api_main, "update_agent_model", fake_update)

    with TestClient(api_main.app) as client:
        assert client.get("/agents/status").status_code == 200
        assert client.get("/signals/recent?asset=BTC&tier=btc").json()[0]["signal_id"] == "sig-1"
        assert client.get("/orders/recent?asset=BTC&tier=btc").json()[0]["order_id"] == "ord-1"
        assert client.get("/decisions/recent?asset=BTC&tier=btc").json()[0]["signal_id"] == "sig-1"
        assert client.get("/risk-events/recent").status_code == 200
        assert client.get("/portfolio/equity-history").status_code == 200
        assert client.get("/portfolio/positions").status_code == 200
        assert client.get("/metrics/overview").json()["signals"] == 1
        assert client.get("/metrics/pipeline/recent").status_code == 200
        performance = client.get("/metrics/performance?hours=24&asset=BTC&tier=btc").json()
        assert performance["summary"]["signals"] == 1
        assert performance["asset_filter"] == "BTC"
        assert performance["tier_filter"] == "btc"
        response = client.post("/agents/swap-model", json={"agent": "claude", "model": "openai/gpt-4o-mini"})
        assert response.status_code == 200
        assert response.json()["provider"] == "openai"


def test_api_agents_status_hides_news_validator_when_disabled(monkeypatch) -> None:
    fake_context = FakeContext()
    fake_context.settings.news_validation_enabled = False

    async def fake_create():
        return fake_context

    monkeypatch.setattr(api_main.AppContext, "create", fake_create)

    with TestClient(api_main.app) as client:
        status = client.get("/agents/status").json()
        costs = client.get("/costs/daily").json()
        assert "news_validator" not in status
        assert all(item["agent"] != "news_validator" for item in costs)


@pytest.mark.asyncio
async def test_settlement_service_closes_resolved_paper_position(monkeypatch) -> None:
    from core.market_connector import MarketConnector
    from core.settlement import SettlementService

    context = FakeContext()
    await context.repository.record_paper_order(
        "ord-entry-1",
        "sig-entry-1",
        "market-settle-1",
        "simulated",
        {
            "order_id": "ord-entry-1",
            "signal_id": "sig-entry-1",
            "market_id": "market-settle-1",
            "token_id": "token-yes-1",
            "market_question": "Will BTC finish green?",
            "asset_symbol": "BTC",
            "crypto_tier": "btc",
            "strategy_id": "pair_15m",
            "trade_group_id": "group-1",
            "cycle_slug": "btc-updown-15m-1",
            "leg_role": "primary",
            "action": "entry",
            "position_key": "group-1:primary",
            "direction": "YES",
            "size": 5,
            "price_limit": 0.42,
            "notional_usd": 2.1,
            "realized_pnl_usd": 0.0,
            "execution_mode": "deterministic",
            "reason": "entry",
        },
    )

    async def fake_resolution(self, market_id: str):
        assert market_id == "market-settle-1"
        return {
            "market_id": market_id,
            "found": True,
            "resolved": True,
            "winning_direction": "YES",
            "payout_yes": 1.0,
            "payout_no": 0.0,
        }

    monkeypatch.setattr(MarketConnector, "get_market_resolution", fake_resolution)

    connector = MarketConnector(context)
    service = SettlementService(context, connector)
    result = await service.process_redeem_cycle(dry_run=False, limit=10)

    assert result["processed_count"] == 1
    assert result["settled_count"] == 1
    assert result["realized_pnl_usd"] == pytest.approx(2.9)
    assert len(await context.repository.get_open_positions()) == 0
    assert context.repository.orders[-1]["exit_reason"] == "market_redeemed"
    assert context.repository.orders[-1]["realized_pnl_usd"] == pytest.approx(2.9)
    assert context.repository.settlement_events[-1]["status"] == "settled"
    await connector.close()


def test_api_settlement_endpoints(monkeypatch) -> None:
    fake_context = FakeContext()
    fake_context.repository.positions["group-2:primary"] = {
        "market_id": "market-settle-2",
        "position_key": "group-2:primary",
        "token_id": "token-no-2",
        "market_question": "Will ETH drop?",
        "asset_symbol": "ETH",
        "crypto_tier": "eth",
        "strategy_id": "pair_15m",
        "regime": "trend",
        "trade_group_id": "group-2",
        "cycle_slug": "eth-updown-15m-2",
        "leg_role": "hedge",
        "direction": "NO",
        "size": 4,
        "average_price": 0.25,
        "current_price": 0.25,
        "cost_basis_usd": 1.0,
        "current_value_usd": 1.0,
        "unrealized_pnl": 0.0,
        "take_profit_price": None,
        "stop_loss_price": None,
        "time_stop_minutes": None,
        "opened_at": "2026-03-18T12:00:00Z",
        "scaled_out_count": 0,
        "latest_spread_bps": 35.0,
    }
    fake_context.repository.settlement_events.append(
        {
            "settlement_id": "settled-1",
            "market_id": "market-settle-2",
            "position_key": "group-2:primary",
            "status": "dry_run",
            "realized_pnl_usd": 0.0,
        }
    )

    async def fake_create():
        return fake_context

    async def fake_resolution(self, market_id: str):
        return {
            "market_id": market_id,
            "found": True,
            "resolved": True,
            "winning_direction": "NO",
            "payout_yes": 0.0,
            "payout_no": 1.0,
        }

    monkeypatch.setattr(api_main.AppContext, "create", fake_create)
    monkeypatch.setattr("core.market_connector.MarketConnector.get_market_resolution", fake_resolution)

    with TestClient(api_main.app) as client:
        redeemables = client.get("/settlement/redeemables?limit=5")
        assert redeemables.status_code == 200
        assert redeemables.json()[0]["eligible"] is True
        process = client.post("/settlement/process?dry_run=true&limit=5")
        assert process.status_code == 200
        assert process.json()["dry_run"] is True
        assert process.json()["processed_count"] == 1
        recent = client.get("/settlement/events/recent?limit=5")
        assert recent.status_code == 200
        assert recent.json()[0]["status"] in {"dry_run", "settled"}
