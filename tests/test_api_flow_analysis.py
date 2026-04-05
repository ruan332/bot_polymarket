from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from api import main as api_main


class _FakeRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def get_recent_flow_analyses(self, limit: int = 48, *, asset=None, market_id=None, strategy=None, cutoff_name=None):
        self.calls.append(
            {
                "limit": limit,
                "asset": asset,
                "market_id": market_id,
                "strategy": strategy,
                "cutoff_name": cutoff_name,
            }
        )
        return [
            {
                "flow_id": "flow-1",
                "signal_id": "sig-1",
                "trade_group_id": "pair-1",
                "market_id": "market-1",
                "cycle_slug": "btc-updown-15m-1",
                "market_question": "BTC up or down?",
                "asset_symbol": "BTC",
                "asset_name": "Bitcoin",
                "crypto_tier": "btc",
                "window_minutes": 15,
                "dominant_direction": "up",
                "dominance_score": 0.24,
                "confidence": 0.81,
                "up_trade_count": 8,
                "down_trade_count": 3,
                "up_notional": 1240.0,
                "down_notional": 420.0,
                "total_trades": 11,
                "total_notional": 1660.0,
                "freshness_seconds": 18.0,
                "source_used": "ws",
                "sample_count": 11,
                "last_trade_at": "2026-04-05T12:00:00Z",
                "updated_at": "2026-04-05T12:00:05Z",
                "created_at": "2026-04-05T12:00:05Z",
                "metadata": {"aligned_with_signal": True},
            }
        ]


class _FakeContext:
    def __init__(self) -> None:
        self.settings = SimpleNamespace()
        self.repository = _FakeRepository()

    async def close(self) -> None:
        return None


def test_recent_flow_analysis_endpoint_returns_series(monkeypatch) -> None:
    fake_context = _FakeContext()

    async def fake_create():
        return fake_context

    monkeypatch.setattr(api_main.AppContext, "create", fake_create)

    with TestClient(api_main.app) as client:
        response = client.get("/analysis/flow/recent?limit=12&asset=BTC")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["flow_id"] == "flow-1"
    assert body[0]["dominant_direction"] == "up"
    assert body[0]["metadata"]["aligned_with_signal"] is True
    assert fake_context.repository.calls[0]["limit"] == 12
    assert fake_context.repository.calls[0]["asset"] == "BTC"
