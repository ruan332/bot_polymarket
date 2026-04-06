from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from core.config import WeatherCopytradeSettings
from core.weather_copytrade_service import WeatherCopytradeService


class DummyProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.model = "gpt-4o-mini"
        self.provider = "openai"

    async def call(self, prompt: str, system: str):  # pragma: no cover - exercised by service
        return SimpleNamespace(
            content=self.content,
            model=self.model,
            provider=self.provider,
            fallback_used=False,
        )


class DummyCostTracker:
    async def record(self, response, prompt_type: str) -> None:  # pragma: no cover - exercised by service
        return None


class DummyRepository:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []
        self.candidates: list[dict[str, object]] = []
        self.state: dict[str, object] | None = None
        self.orders: list[dict[str, object]] = []

    async def record_weather_copytrade_run(self, payload: dict[str, object]) -> dict[str, object]:
        self.runs.append(payload)
        return payload

    async def record_weather_copytrade_candidates(self, candidates: list[dict[str, object]]) -> None:
        self.candidates.extend(candidates)

    async def upsert_weather_copytrade_state(self, payload: dict[str, object]) -> dict[str, object]:
        self.state = payload
        return payload

    async def get_weather_copytrade_state(self, category: str = "WEATHER") -> dict[str, object] | None:
        if self.state is None:
            return None
        return self.state

    async def get_latest_weather_copytrade_summary(self, *, limit: int = 12) -> dict[str, object] | None:
        if not self.runs:
            return None
        return {
            "run": self.runs[-1],
            "candidates": self.candidates[-limit:],
            "state": self.state,
        }

    async def record_paper_order(self, order_id, signal_id, market_id, status, payload):  # pragma: no cover - exercised by service
        self.orders.append(payload)


class DummyConnector:
    def __init__(self, *, leaderboard: list[dict[str, object]], profiles: dict[str, dict[str, object]], metrics: dict[str, dict[str, list[dict[str, object]]]], trades: dict[str, list[dict[str, object]]], markets: dict[str, dict[str, object]], books: dict[str, dict[str, object]]) -> None:
        self.leaderboard = leaderboard
        self.profiles = profiles
        self.metrics = metrics
        self.trades = trades
        self.markets = markets
        self.books = books
        self.orders: list[dict[str, object]] = []

    async def close(self) -> None:
        return None

    async def get_trader_leaderboard(self, *, category: str, time_period: str, order_by: str, limit: int, user: str | None = None):
        if user:
            return self.metrics.get(user, {}).get(time_period, [])
        return self.leaderboard[:limit]

    async def get_public_profile(self, address: str):
        return self.profiles.get(address, {})

    async def get_user_trades(self, address: str, limit: int, offset: int = 0, taker_only: bool = False):
        return list(self.trades.get(address, []))[:limit]

    async def get_current_positions(self, address: str, limit: int = 100, offset: int = 0):
        return []

    async def get_closed_positions(self, address: str, limit: int = 200, offset: int = 0):
        return list(self.metrics.get(address, {}).get("closed", []))[:limit]

    async def get_market_by_id(self, condition_id: str):
        return self.markets.get(condition_id)

    async def get_orderbook_summary(self, token_id: str):
        return self.books.get(token_id)

    async def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"status": "simulated"}


def make_service(connector: DummyConnector, repository: DummyRepository, *, report_content: str = '{"summary":"ok","why":"consistente","risks":["baixo risco"],"selection_reason":"score elevado"}'):
    settings = WeatherCopytradeSettings(
        leaderboard_limit=10,
        shortlist_limit=5,
        min_trades_30d=25,
        min_trades_7d=8,
        min_closed_positions_30d=4,
        min_positive_weeks_4=3,
        min_pnl_7d=0.0,
        min_pnl_30d=0.0,
        min_pnl_all=0.0,
        max_drawdown=0.15,
        min_profit_factor=1.25,
        min_win_rate=0.5,
        max_pnl_concentration=0.4,
        max_spread_bps=150.0,
        min_notional_usd=1.0,
        max_notional_usd=2.5,
        copy_trade_fraction=0.08,
        max_open_copied_positions=3,
        scan_interval_minutes=60,
        poll_interval_seconds=20,
        trade_lookback_days=30,
    )
    context = SimpleNamespace(weather_copytrade_config=settings, repository=repository)
    return WeatherCopytradeService(
        context,
        connector=connector,
        provider=DummyProvider(report_content),
        cost_tracker=DummyCostTracker(),
    )


def _trade(ts: datetime, *, side: str = "BUY", condition_id: str = "cond-weather", outcome_index: int = 0, trade_hash: str = "hash-1", price: float = 0.5, size: float = 6.0, slug: str = "weather-rain-market") -> dict[str, object]:
    return {
        "transactionHash": trade_hash,
        "timestamp": ts.isoformat(),
        "side": side,
        "conditionId": condition_id,
        "outcomeIndex": outcome_index,
        "price": price,
        "size": size,
        "slug": slug,
        "title": "Weather market",
    }


def _closed_position(ts: datetime, pnl: float, slug: str) -> dict[str, object]:
    return {
        "closedAt": ts.isoformat(),
        "realizedPnl": pnl,
        "slug": slug,
    }


def _week_start(dt: datetime) -> datetime:
    return (dt - timedelta(days=dt.weekday())).replace(hour=12, minute=0, second=0, microsecond=0)


@pytest.mark.asyncio
async def test_run_analysis_selects_consistent_weather_trader() -> None:
    now = datetime.now(UTC)
    week_anchor = _week_start(now)
    wallet = "0xweather1"
    other_wallet = "0xweather2"
    leaderboard = [
        {"proxyWallet": wallet, "userName": "weather-alpha", "verifiedBadge": True},
        {"proxyWallet": other_wallet, "userName": "weather-beta", "verifiedBadge": False},
    ]
    metrics = {
        wallet: {
            "WEEK": [{"pnl": 12.0}],
            "MONTH": [{"pnl": 45.0}],
            "ALL": [{"pnl": 92.0}],
            "closed": [
                _closed_position(week_anchor, 6.0, "weather-a"),
                _closed_position(week_anchor - timedelta(weeks=1), 5.0, "weather-b"),
                _closed_position(week_anchor - timedelta(weeks=2), 6.0, "weather-c"),
                _closed_position(week_anchor - timedelta(weeks=3), -1.0, "weather-d"),
            ],
        },
        other_wallet: {
            "WEEK": [{"pnl": -2.0}],
            "MONTH": [{"pnl": 1.0}],
            "ALL": [{"pnl": 3.0}],
            "closed": [
                _closed_position(now - timedelta(days=1), 1.0, "weather-x"),
                _closed_position(now - timedelta(days=3), -2.0, "weather-y"),
            ],
        },
    }
    trades = {
        wallet: [
            _trade(now - timedelta(days=2), trade_hash=f"hash-{idx}", slug="weather-rain-market")
            for idx in range(1, 31)
        ],
        other_wallet: [
            _trade(now - timedelta(days=2), trade_hash=f"bad-{idx}", slug="weather-rain-market")
            for idx in range(1, 5)
        ],
    }
    profiles = {
        wallet: {"displayUsernamePublic": True, "name": "weather-alpha", "pseudonym": "alpha"},
        other_wallet: {"displayUsernamePublic": True, "name": "weather-beta", "pseudonym": "beta"},
    }
    connector = DummyConnector(
        leaderboard=leaderboard,
        profiles=profiles,
        metrics=metrics,
        trades=trades,
        markets={},
        books={},
    )
    repository = DummyRepository()
    service = make_service(connector, repository)

    result = await service.run_analysis(limit=10)

    assert result["selected"]["proxy_wallet"] == wallet
    assert result["selected"]["passed"] is True
    assert result["report"]["selected_proxy_wallet"] == wallet
    assert repository.runs[-1]["selected_proxy_wallet"] == wallet
    assert repository.state is not None
    assert repository.state["selected_proxy_wallet"] == wallet


@pytest.mark.asyncio
async def test_approve_selection_and_sync_mirror_trades() -> None:
    now = datetime.now(UTC)
    week_anchor = _week_start(now)
    wallet = "0xweather1"
    leaderboard = [{"proxyWallet": wallet, "userName": "weather-alpha", "verifiedBadge": True}]
    metrics = {
        wallet: {
            "WEEK": [{"pnl": 12.0}],
            "MONTH": [{"pnl": 45.0}],
            "ALL": [{"pnl": 92.0}],
            "closed": [
                _closed_position(week_anchor, 6.0, "weather-a"),
                _closed_position(week_anchor - timedelta(weeks=1), 5.0, "weather-b"),
                _closed_position(week_anchor - timedelta(weeks=2), 6.0, "weather-c"),
                _closed_position(week_anchor - timedelta(weeks=3), -1.0, "weather-d"),
            ],
        }
    }
    future_trade_time = now + timedelta(minutes=5)
    connector = DummyConnector(
        leaderboard=leaderboard,
        profiles={wallet: {"displayUsernamePublic": True, "name": "weather-alpha"}},
        metrics=metrics,
        trades={
            wallet: [
                *[
                    _trade(
                        future_trade_time,
                        trade_hash="trade-weather",
                        condition_id="cond-weather",
                        slug="weather-rain-market",
                        price=0.5,
                        size=6.0,
                    )
                    for _ in range(25)
                ],
                _trade(
                    future_trade_time,
                    trade_hash="trade-other",
                    condition_id="cond-other",
                    slug="sports-market",
                    price=0.5,
                    size=6.0,
                ),
            ]
        },
        markets={
            "cond-weather": {
                "id": "cond-weather",
                "question": "Weather market",
                "slug": "weather-rain-market",
                "description": "weather market",
                "eventSlug": "weather-rain-market",
                "clobTokenIds": ["token-yes", "token-no"],
            },
            "cond-other": {
                "id": "cond-other",
                "question": "Sports market",
                "slug": "sports-market",
                "description": "sports market",
                "eventSlug": "sports-market",
                "clobTokenIds": ["token-yes-2", "token-no-2"],
            },
        },
        books={
            "token-yes": {"best_bid": 0.46, "best_ask": 0.50, "spread_bps": 80.0},
            "token-no": {"best_bid": 0.48, "best_ask": 0.52, "spread_bps": 80.0},
        },
    )
    repository = DummyRepository()
    service = make_service(connector, repository)

    await service.run_analysis(limit=10)
    approved = await service.approve_selection()
    sync = await service.sync_mirror_trades()

    assert approved["state"]["approved"] is True
    assert approved["state"]["active"] is True
    assert sync["copied"] == 1
    assert sync["reasons"].get("non_weather_market", 0) == 1
    assert len(repository.orders) == 1


@pytest.mark.asyncio
async def test_new_analysis_keeps_approved_state_active() -> None:
    now = datetime.now(UTC)
    week_anchor = _week_start(now)
    wallet = "0xweather1"
    leaderboard = [{"proxyWallet": wallet, "userName": "weather-alpha", "verifiedBadge": True}]
    metrics = {
        wallet: {
            "WEEK": [{"pnl": 12.0}],
            "MONTH": [{"pnl": 45.0}],
            "ALL": [{"pnl": 92.0}],
            "closed": [
                _closed_position(week_anchor, 6.0, "weather-a"),
                _closed_position(week_anchor - timedelta(weeks=1), 5.0, "weather-b"),
                _closed_position(week_anchor - timedelta(weeks=2), 6.0, "weather-c"),
                _closed_position(week_anchor - timedelta(weeks=3), -1.0, "weather-d"),
            ],
        }
    }
    trades = {
        wallet: [
            _trade(now - timedelta(days=2), trade_hash=f"hash-{idx}", slug="weather-rain-market")
            for idx in range(1, 31)
        ],
    }
    connector = DummyConnector(
        leaderboard=leaderboard,
        profiles={wallet: {"displayUsernamePublic": True, "name": "weather-alpha"}},
        metrics=metrics,
        trades=trades,
        markets={
            "cond-weather": {
                "id": "cond-weather",
                "question": "Weather market",
                "slug": "weather-rain-market",
                "description": "weather market",
                "eventSlug": "weather-rain-market",
                "clobTokenIds": ["token-yes", "token-no"],
            }
        },
        books={"token-yes": {"best_bid": 0.46, "best_ask": 0.50, "spread_bps": 80.0}, "token-no": {"best_bid": 0.48, "best_ask": 0.52, "spread_bps": 80.0}},
    )
    repository = DummyRepository()
    service = make_service(connector, repository)

    await service.run_analysis(limit=10)
    approved = await service.approve_selection()
    refreshed = await service.run_analysis(limit=10)

    assert approved["state"]["approved"] is True
    assert approved["state"]["active"] is True
    assert refreshed["state"]["approved"] is True
    assert refreshed["state"]["active"] is True
    assert refreshed["state"]["paused"] is False
    assert refreshed["state"]["selected_proxy_wallet"] == wallet
