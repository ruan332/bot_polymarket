from datetime import UTC, datetime

import pytest

from clima_bot.config.settings import ClimaBotSettings
from clima_bot.services.mirror import MirrorService
from clima_bot.storage.repository import ClimaBotRepository


class DummyMirrorConnector:
    def __init__(self) -> None:
        self.orders = []

    async def get_user_trades(self, address: str, limit: int, offset: int = 0, taker_only: bool = False):
        now = datetime.now(UTC).isoformat()
        return [
            {
                "transactionHash": "hash-1",
                "conditionId": "cond-weather",
                "side": "BUY",
                "outcomeIndex": 0,
                "price": 0.52,
                "size": 10,
                "slug": "weather-rain-market",
                "timestamp": now,
            },
            {
                "transactionHash": "hash-2",
                "conditionId": "cond-other",
                "side": "BUY",
                "outcomeIndex": 0,
                "price": 0.52,
                "size": 10,
                "slug": "sports-market",
                "timestamp": now,
            },
        ]

    async def get_market_by_id(self, condition_id: str):
        if condition_id == "cond-weather":
            return {"id": condition_id, "slug": "weather-rain-market", "clobTokenIds": ["yes", "no"], "question": "Weather?"}
        if condition_id == "cond-other":
            return {"id": condition_id, "slug": "sports-market", "clobTokenIds": ["yes", "no"], "question": "Sports?"}
        return None

    async def get_orderbook_summary(self, token_id: str):
        return {"best_bid": 0.49, "best_ask": 0.51, "spread_bps": 40}

    async def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"status": "simulated", "exchange_order_id": f"paper-{len(self.orders)}"}

    async def get_collateral_snapshot(self, sync_allowance: bool = False):
        return None


@pytest.mark.asyncio
async def test_mirror_sync_deduplicates_and_filters_non_weather(tmp_path) -> None:
    repo = ClimaBotRepository(tmp_path / "clima.sqlite3")
    repo.init_schema()
    repo.upsert_wallet(
        {
            "proxy_wallet": "0xwallet",
            "user_name": "wallet",
            "score": 90,
            "approved": True,
            "active": True,
            "paused": False,
            "profile": {},
            "metrics": {},
            "selection": {},
        }
    )
    settings = ClimaBotSettings(_env_file=None)
    service = MirrorService(DummyMirrorConnector(), repo, settings)
    try:
        first = await service.sync_once()
        second = await service.sync_once()

        assert first["copied"] == 1
        assert first["reasons"]["non_weather_market"] == 1
        assert second["reasons"]["duplicate"] >= 1
        orders = repo.list_orders(limit=10)
        assert len(orders) == 1
        assert orders[0]["trade_hash"] == "hash-1"
    finally:
        repo.close()
