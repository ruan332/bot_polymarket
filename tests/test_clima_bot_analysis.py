from datetime import UTC, datetime, timedelta

import pytest

from clima_bot.services.analysis import AnalysisService
from clima_bot.storage.repository import ClimaBotRepository


class DummyAnalysisConnector:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    async def get_trader_leaderboard(self, *, category: str, time_period: str, order_by: str, limit: int, user: str | None = None):
        if user:
            if user == "0xgood":
                if time_period == "WEEK":
                    return [{"pnl": 12}]
                if time_period == "MONTH":
                    return [{"pnl": 44}]
                return [{"pnl": 88}]
            return [{"pnl": 5}]
        return [
            {"proxyWallet": "0xgood", "userName": "good", "verifiedBadge": True},
            {"proxyWallet": "0xbad", "userName": "bad", "verifiedBadge": False},
        ][:limit]

    async def get_public_profile(self, address: str):
        return {"name": address, "displayUsernamePublic": True}

    async def get_user_trades(self, address: str, limit: int, offset: int = 0, taker_only: bool = False):
        base = self.now - timedelta(days=1)
        count = 30 if address == "0xgood" else 10
        return [{"timestamp": (base + timedelta(hours=i)).isoformat()} for i in range(count)]

    async def get_current_positions(self, address: str, limit: int = 100, offset: int = 0):
        return [{"currentValue": 20}]

    async def get_closed_positions(self, address: str, limit: int = 200, offset: int = 0):
        pnls = [5, 4, -1, 6, 8] if address == "0xgood" else [1, -4, 1]
        return [
            {
                "closedAt": (self.now - timedelta(days=index + 1)).isoformat(),
                "realizedPnl": pnl,
                "slug": f"weather-{index}",
            }
            for index, pnl in enumerate(pnls)
        ]


@pytest.mark.asyncio
async def test_analysis_shortlists_best_candidate(tmp_path) -> None:
    repo = ClimaBotRepository(tmp_path / "clima.sqlite3")
    repo.init_schema()
    service = AnalysisService(DummyAnalysisConnector(), repo)
    try:
        service.settings.min_trades_30d = 10
        service.settings.min_trades_7d = 5
        service.settings.min_closed_positions_30d = 3
        service.settings.min_positive_weeks_4 = 2
        result = await service.run_analysis(limit=5)
        assert result["run"]["shortlisted_count"] >= 1
        assert result["candidates"][0]["proxy_wallet"] == "0xgood"
        assert result["candidates"][0]["score"] > result["candidates"][-1]["score"]
    finally:
        repo.close()
