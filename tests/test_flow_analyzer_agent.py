from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agents.flow_analyzer_agent import FlowAnalyzerAgent, FlowCycleRuntime, FlowSample


def _make_cycle() -> FlowCycleRuntime:
    return FlowCycleRuntime(
        asset_symbol="BTC",
        asset_name="Bitcoin",
        crypto_tier="btc",
        cycle_slug="btc-updown-15m-test",
        cycle_start=datetime.now(UTC) - timedelta(minutes=5),
        market_id="market-1",
        market_question="BTC up or down?",
        token_id_yes="token-up",
        token_id_no="token-down",
        volume_24h=1000.0,
        end_date="2026-04-05T12:15:00Z",
    )


@pytest.mark.asyncio
async def test_flow_analysis_prefers_up_when_buy_pressure_dominates() -> None:
    agent = FlowAnalyzerAgent.__new__(FlowAnalyzerAgent)
    agent.context = SimpleNamespace()
    agent.connector = SimpleNamespace()
    agent.quote_cache = {}
    agent.trade_buffers = {
        "market-1": deque(
            [
                FlowSample(direction="up", notional=120.0, price=0.61, created_at=datetime.now(UTC) - timedelta(minutes=2), source="ws"),
                FlowSample(direction="up", notional=80.0, price=0.63, created_at=datetime.now(UTC) - timedelta(minutes=1), source="ws"),
                FlowSample(direction="down", notional=20.0, price=0.57, created_at=datetime.now(UTC) - timedelta(minutes=1, seconds=10), source="ws"),
            ],
            maxlen=750,
        )
    }

    async def no_quotes(token_id: str):  # noqa: ARG001
        return None

    agent._quote_for_token = no_quotes  # type: ignore[method-assign]

    analysis = await agent._analyze_cycle(_make_cycle(), trigger="unit_test")

    assert analysis is not None
    assert analysis.dominant_direction == "up"
    assert analysis.dominance_score > 0
    assert analysis.confidence > 0.5


@pytest.mark.asyncio
async def test_flow_analysis_prefers_down_when_sell_pressure_dominates() -> None:
    agent = FlowAnalyzerAgent.__new__(FlowAnalyzerAgent)
    agent.context = SimpleNamespace()
    agent.connector = SimpleNamespace()
    agent.quote_cache = {}
    agent.trade_buffers = {
        "market-1": deque(
            [
                FlowSample(direction="down", notional=110.0, price=0.39, created_at=datetime.now(UTC) - timedelta(minutes=2), source="ws"),
                FlowSample(direction="down", notional=95.0, price=0.38, created_at=datetime.now(UTC) - timedelta(minutes=1), source="ws"),
                FlowSample(direction="up", notional=15.0, price=0.61, created_at=datetime.now(UTC) - timedelta(minutes=1, seconds=10), source="ws"),
            ],
            maxlen=750,
        )
    }

    async def no_quotes(token_id: str):  # noqa: ARG001
        return None

    agent._quote_for_token = no_quotes  # type: ignore[method-assign]

    analysis = await agent._analyze_cycle(_make_cycle(), trigger="unit_test")

    assert analysis is not None
    assert analysis.dominant_direction == "down"
    assert analysis.dominance_score < 0
    assert analysis.confidence > 0.5
