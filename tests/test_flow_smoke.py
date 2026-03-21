from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agents.claude_agent import ClaudeAgent
from agents.claw_agent import ClawAgent
from agents.codex_agent import CodexAgent
from agents.news_validator_agent import NewsValidatorAgent
from api import main as api_main
from core.config import infer_provider_from_model, load_agents_config, load_crypto_config, load_risk_config
from core.schemas import ModelResponse, PortfolioSummary


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
        if status == "simulated":
            position_key = f"{market_id}:{payload['direction']}"
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

    async def upsert_heartbeat(self, heartbeat) -> None:
        self.heartbeats[heartbeat.agent] = heartbeat.model_dump()

    async def record_equity_snapshot(self, source: str = "system") -> None:
        portfolio = await self.get_portfolio_summary()
        self.equity_history.append({**portfolio.model_dump(), "source": source, "created_at": "2026-03-18T12:00:00Z"})

    async def record_market_snapshots(self, snapshots) -> None:
        self.market_snapshots.extend(snapshot.model_dump(mode="json") for snapshot in snapshots)

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

    async def get_recent_signals(self, limit: int = 20, asset: str | None = None, tier: str | None = None, strategy: str | None = None):
        items = [item for item in self.signals if self._matches(item, asset, tier, strategy)]
        return list(reversed(items[-limit:]))

    async def get_recent_orders(self, limit: int = 20, asset: str | None = None, tier: str | None = None, strategy: str | None = None):
        items = [item for item in self.orders if self._matches(item, asset, tier, strategy)]
        return list(reversed(items[-limit:]))

    async def get_recent_risk_events(self, limit: int = 20):
        return list(reversed(self.risk_events[-limit:]))

    async def get_recent_pipeline_telemetry(self, limit: int = 30):
        return list(reversed(self.pipeline_events[-limit:]))

    async def get_recent_decisions(self, limit: int = 20, asset: str | None = None, tier: str | None = None, strategy: str | None = None):
        items = [item for item in self.decisions if self._matches(item, asset, tier, strategy)]
        return list(reversed(items[-limit:]))

    async def get_equity_history(self, limit: int = 100):
        return list(self.equity_history[-limit:])

    async def get_open_positions(self):
        return list(self.positions.values())

    async def get_market_snapshots(self, market_id: str | None = None, limit: int = 5000, **_: object):
        items = self.market_snapshots
        if market_id:
            items = [item for item in items if item["market_id"] == market_id]
        return items[-limit:]

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

    async def metrics_overview(self):
        latest_scan = next((item for item in reversed(self.pipeline_events) if item["event_type"] == "scanner.scan_cycle"), None)
        latest_review = next((item for item in reversed(self.pipeline_events) if item["event_type"] == "reviewer.review_cycle"), None)
        latest_execution = next((item for item in reversed(self.pipeline_events) if item["event_type"] == "executor.execute_cycle"), None)
        return {
            "signals": len(self.signals),
            "decisions": len(self.decisions),
            "orders": len(self.orders),
            "risk_events": len(self.risk_events),
            "portfolio": (await self.get_portfolio_summary()).model_dump(),
            "flow_summary": {
                "window_minutes": 15,
                "gamma_markets_fetched": sum(int(item.get("gamma_markets_fetched") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "crypto_classified": sum(int(item.get("crypto_classified") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "selected_for_scan": sum(int(item.get("selected_for_scan") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "strategy_candidates": sum(int(item.get("strategy_candidates") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
                "reached_risk_engine": sum(int(item.get("reached_risk_engine") or 0) for item in self.pipeline_events if item["event_type"] == "scanner.scan_cycle"),
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

    async def get_performance_report(
        self,
        hours: int = 24,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
    ):
        signals = [item for item in self.signals if self._matches(item, asset, tier, strategy)]
        decisions = [item for item in self.decisions if self._matches(item, asset, tier, strategy)]
        orders = [item for item in self.orders if self._matches(item, asset, tier, strategy)]
        open_positions = [item for item in self.positions.values() if self._matches(item, asset, tier, strategy)]
        return {
            "generated_at": "2026-03-18T12:05:00Z",
            "window_hours": hours,
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


class FakeContext:
    def __init__(self):
        self.settings = SimpleNamespace(
            max_daily_spend_usd=5.0,
            paper_bankroll_usd=1000.0,
            live_trading=False,
            smoke_test_mode=True,
            news_validation_enabled=True,
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
        )
        self.agents_config = load_agents_config()
        self.risk_config = load_risk_config()
        self.crypto_config = load_crypto_config()
        self.repository = FakeRepository()
        self.bus = FakeBus()

    async def reload_configs(self) -> None:
        self.agents_config = load_agents_config()
        self.risk_config = load_risk_config()
        self.crypto_config = load_crypto_config()

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
