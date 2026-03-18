from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agents.claude_agent import ClaudeAgent
from agents.claw_agent import ClawAgent
from agents.codex_agent import CodexAgent
from api import main as api_main
from core.config import infer_provider_from_model, load_agents_config, load_risk_config
from core.schemas import ModelResponse, PortfolioSummary


class FakeBus:
    def __init__(self):
        self.streams: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        self.hashes: dict[str, dict] = defaultdict(dict)
        self.config_version = 0
        self.model_overrides: dict[str, str] = {}
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

    async def get_agent_model_override(self, agent_name: str) -> str | None:
        return self.model_overrides.get(agent_name)

    async def get_config_version(self) -> int:
        return self.config_version

    async def set_agent_model(self, agent_name: str, model: str) -> int:
        self.model_overrides[agent_name] = model
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

    async def record_signal(self, signal_id: str, event_type: str, payload: dict) -> None:
        self.signals.append(payload)

    async def record_decision(self, decision_id: str, signal_id: str, event_type: str, payload: dict) -> None:
        self.decisions.append(payload)

    async def record_paper_order(self, order_id: str, signal_id: str, market_id: str, status: str, payload: dict) -> None:
        self.orders.append(payload)
        if status == "simulated":
            self.positions[market_id] = {
                "market_id": market_id,
                "token_id": payload["token_id"],
                "market_question": payload.get("market_question", ""),
                "direction": payload["direction"],
                "size": payload["size"],
                "average_price": payload["price_limit"],
                "exposure_usd": payload["notional_usd"],
            }

    async def record_llm_call(self, **payload) -> None:
        self.llm_calls.append(payload)

    async def record_risk_event(self, event_id: str, agent: str, reason: str, payload: dict) -> None:
        self.risk_events.append({"agent": agent, "reason": reason, **payload})

    async def upsert_heartbeat(self, heartbeat) -> None:
        self.heartbeats[heartbeat.agent] = heartbeat.model_dump()

    async def record_equity_snapshot(self, source: str = "system") -> None:
        portfolio = await self.get_portfolio_summary()
        self.equity_history.append({**portfolio.model_dump(), "source": source, "created_at": "2026-03-18T12:00:00Z"})

    async def record_market_snapshots(self, snapshots) -> None:
        self.market_snapshots.extend(snapshot.model_dump(mode="json") for snapshot in snapshots)

    async def get_portfolio_summary(self) -> PortfolioSummary:
        total_exposure = sum(position["exposure_usd"] for position in self.positions.values())
        current_market_value = total_exposure
        return PortfolioSummary(
            available_balance=max(self.bankroll - total_exposure, 0.0),
            total_exposure=total_exposure,
            current_market_value=current_market_value,
            total_equity=max(self.bankroll - total_exposure, 0.0) + current_market_value,
            total_pnl=current_market_value - total_exposure,
            open_positions=len(self.positions),
            realized_pnl=0.0,
            unrealized_pnl=current_market_value - total_exposure,
        )

    async def get_recent_signals(self, limit: int = 20):
        return list(reversed(self.signals[-limit:]))

    async def get_recent_orders(self, limit: int = 20):
        return list(reversed(self.orders[-limit:]))

    async def get_recent_risk_events(self, limit: int = 20):
        return list(reversed(self.risk_events[-limit:]))

    async def get_recent_decisions(self, limit: int = 20):
        return list(reversed(self.decisions[-limit:]))

    async def get_equity_history(self, limit: int = 100):
        return list(self.equity_history[-limit:])

    async def get_open_positions(self):
        return list(self.positions.values())

    async def get_agent_status(self):
        return list(self.heartbeats.values())

    async def metrics_overview(self):
        return {
            "signals": len(self.signals),
            "decisions": len(self.decisions),
            "orders": len(self.orders),
            "risk_events": len(self.risk_events),
            "portfolio": (await self.get_portfolio_summary()).model_dump(),
        }


class FakeContext:
    def __init__(self):
        self.settings = SimpleNamespace(
            max_daily_spend_usd=5.0,
            paper_bankroll_usd=1000.0,
            live_trading=False,
            smoke_test_mode=False,
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
        )
        self.agents_config = load_agents_config()
        self.risk_config = load_risk_config()
        self.repository = FakeRepository()
        self.bus = FakeBus()

    async def reload_configs(self) -> None:
        self.agents_config = load_agents_config()
        self.risk_config = load_risk_config()

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_signal_review_execute_flow_smoke(monkeypatch) -> None:
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

    async def fake_markets(limit: int = 20):
        return [market]

    monkeypatch.setattr(claude.connector, "get_active_markets", fake_markets)
    monkeypatch.setattr(claude.provider, "call", fake_scan)
    monkeypatch.setattr(codex.provider, "call", fake_review)
    monkeypatch.setattr(claw.provider, "call", fake_execute)

    await claude.tick()
    assert len(context.repository.signals) == 1
    assert len(context.repository.market_snapshots) == 1
    assert len(context.bus.streams["signals:created"]) == 1

    await codex.tick()
    assert len(context.repository.decisions) == 1
    assert len(context.bus.streams["signals:reviewed"]) == 1

    await claw.tick()
    assert len(context.repository.orders) == 1
    assert context.repository.orders[0]["status"] == "simulated"
    assert context.repository.orders[0]["token_id"] == "token-yes-1"
    assert context.repository.orders[0]["market_question"] == "Will BTC be above 100k?"
    portfolio = await context.repository.get_portfolio_summary()
    assert portfolio.open_positions == 1
    assert portfolio.total_exposure > 0


def test_api_smoke(monkeypatch) -> None:
    fake_context = FakeContext()
    fake_context.repository.signals.append(
        {
            "signal_id": "sig-1",
            "market_question": "Will ETH rally?",
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
            "direction": "YES",
            "size": 100,
            "price_limit": 0.44,
            "status": "simulated",
            "created_at": "2026-03-18T12:01:00Z",
            "notional_usd": 44.0,
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

    async def fake_update(agent: str, model: str):
        return None

    monkeypatch.setattr(api_main.AppContext, "create", fake_create)
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

    monkeypatch.setattr(api_main, "update_agent_model", fake_update)

    with TestClient(api_main.app) as client:
        assert client.get("/agents/status").status_code == 200
        assert client.get("/signals/recent").json()[0]["signal_id"] == "sig-1"
        assert client.get("/orders/recent").json()[0]["order_id"] == "ord-1"
        assert client.get("/risk-events/recent").status_code == 200
        assert client.get("/portfolio/equity-history").status_code == 200
        assert client.get("/portfolio/positions").status_code == 200
        assert client.get("/metrics/overview").json()["signals"] == 1
        response = client.post("/agents/swap-model", json={"agent": "claude", "model": "openai/gpt-4o-mini"})
        assert response.status_code == 200
        assert response.json()["provider"] == "openai"
