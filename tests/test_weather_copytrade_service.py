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

    async def call(self, prompt: str, system: str):
        return SimpleNamespace(
            content=self.content,
            model=self.model,
            provider=self.provider,
            fallback_used=False,
        )


class DummyCostTracker:
    async def record(self, response, prompt_type: str) -> None:
        return None


class DummyRepository:
    def __init__(self, *, available_balance: float = 100.0, total_equity: float = 100.0) -> None:
        self.runs: list[dict[str, object]] = []
        self.candidates: list[dict[str, object]] = []
        self.state: dict[str, object] | None = None
        self.orders: list[dict[str, object]] = []
        self.open_positions: list[dict[str, object]] = []
        self.performance_reports: dict[str, dict[str, object]] = {}
        self.profiles: dict[tuple[str, str], dict[str, object]] = {}
        self.available_balance = available_balance
        self.total_equity = total_equity

    async def record_weather_copytrade_run(self, payload: dict[str, object]) -> dict[str, object]:
        self.runs.append(dict(payload))
        return dict(payload)

    async def record_weather_copytrade_candidates(self, candidates: list[dict[str, object]]) -> None:
        run_id = str(candidates[0].get("run_id")) if candidates else None
        if run_id:
            self.candidates = [item for item in self.candidates if str(item.get("run_id")) != run_id]
        self.candidates.extend(dict(item) for item in candidates)

    async def upsert_weather_copytrade_state(self, payload: dict[str, object]) -> dict[str, object]:
        self.state = dict(payload)
        return dict(self.state)

    async def get_weather_copytrade_state(self, category: str = "WEATHER") -> dict[str, object] | None:
        if self.state is None:
            return None
        return dict(self.state)

    async def upsert_weather_copytrade_profile(self, payload: dict[str, object]) -> dict[str, object]:
        row = dict(payload)
        key = (str(row.get("category") or "WEATHER"), str(row.get("proxy_wallet") or ""))
        self.profiles[key] = row
        return dict(row)

    async def get_weather_copytrade_profile(self, category: str, proxy_wallet: str) -> dict[str, object] | None:
        row = self.profiles.get((category, proxy_wallet))
        return dict(row) if row else None

    async def list_weather_copytrade_profiles(self, *, category: str) -> list[dict[str, object]]:
        return [dict(row) for (row_category, _), row in self.profiles.items() if row_category == category]

    async def delete_weather_copytrade_profile(self, *, category: str, proxy_wallet: str) -> None:
        self.profiles.pop((category, proxy_wallet), None)

    async def get_latest_weather_copytrade_summary(self, *, limit: int = 12) -> dict[str, object] | None:
        if not self.runs:
            return None
        run = dict(self.runs[-1])
        run_id = str(run.get("run_id"))
        candidates = [dict(item) for item in self.candidates if str(item.get("run_id")) == run_id][-limit:]
        profiles = await self.list_weather_copytrade_profiles(category="WEATHER")
        return {
            "run": run,
            "candidates": candidates,
            "state": dict(self.state) if self.state else None,
            "profiles": profiles,
            "report": run.get("model_summary") or {},
            "selection_summary": run.get("selection_summary") or {},
            "scan_stats": run.get("scan_stats") or {},
            "metadata": run.get("metadata") or {},
            "portfolio_constraints": {},
        }

    async def get_recent_orders(
        self,
        limit: int = 20,
        *,
        strategy: str | None = None,
        asset: str | None = None,
        tier: str | None = None,
        cutoff_name: str | None = None,
    ):
        orders = list(self.orders)
        if strategy:
            orders = [item for item in orders if item.get("strategy_id") == strategy]
        return orders[:limit]

    async def record_paper_order(self, order_id, signal_id, market_id, status, payload):
        row = dict(payload)
        row["order_id"] = order_id
        row["signal_id"] = signal_id
        row["market_id"] = market_id
        row["status"] = status
        existing = next((index for index, item in enumerate(self.orders) if item.get("order_id") == order_id), None)
        if existing is None:
            self.orders.append(row)
        else:
            self.orders[existing] = row
        if row.get("strategy_id") == "weather_copytrade" and row.get("action") == "entry" and status in {"simulated", "live_submitted", "live_filled"}:
            self.open_positions.append(dict(row))

    async def get_portfolio_summary(self):
        return SimpleNamespace(
            available_balance=self.available_balance,
            total_equity=self.total_equity,
            bankroll=self.total_equity,
            balance_source="paper",
            mode="paper",
        )

    async def get_open_positions(self) -> list[dict[str, object]]:
        return [dict(item) for item in self.open_positions]

    async def get_performance_report(
        self,
        *,
        hours: int = 720,
        strategy: str | None = None,
        trade_group_id: str | None = None,
        **_: object,
    ) -> dict[str, object]:
        if trade_group_id and trade_group_id in self.performance_reports:
            return self.performance_reports[trade_group_id]
        filtered = list(self.orders)
        if strategy:
            filtered = [item for item in filtered if item.get("strategy_id") == strategy]
        if trade_group_id:
            filtered = [item for item in filtered if item.get("trade_group_id") == trade_group_id]
        total = len(filtered)
        filled = [item for item in filtered if str(item.get("status")) in {"simulated", "live_filled", "filled"}]
        pnl = sum(float(item.get("realized_pnl_usd") or 0.0) for item in filtered)
        return {
            "summary": {
                "orders": total,
                "signals": total,
                "risk_events": 0,
                "approval_rate": 1.0 if total else 0.0,
                "execution_rate": len(filled) / total if total else 0.0,
                "win_rate": 1.0 if total else 0.0,
                "realized_pnl_window": pnl,
                "max_drawdown": 0.0,
            }
        }


class DummyConnector:
    def __init__(
        self,
        *,
        leaderboard: list[dict[str, object]],
        profiles: dict[str, dict[str, object]],
        metrics: dict[str, dict[str, list[dict[str, object]]]],
        trades: dict[str, list[dict[str, object]]],
        markets: dict[str, dict[str, object]],
        books: dict[str, dict[str, object]],
    ) -> None:
        self.leaderboard = leaderboard
        self.profiles = profiles
        self.metrics = metrics
        self.trades = trades
        self.markets = markets
        self.books = books
        self.orders: list[dict[str, object]] = []
        self.live_orders: dict[str, dict[str, object]] = {}

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

    async def get_order(self, order_id: str):
        return self.live_orders.get(order_id)

    async def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"status": "simulated"}


def make_service(
    connector: DummyConnector,
    repository: DummyRepository,
    *,
    report_content: str = '{"summary":"ok","why":"consistente","risks":["baixo risco"],"selection_reason":"score elevado"}',
):
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
    context = SimpleNamespace(
        weather_copytrade_config=settings,
        repository=repository,
        settings=SimpleNamespace(live_trading=False),
    )
    return WeatherCopytradeService(
        context,
        connector=connector,
        provider=DummyProvider(report_content),
        cost_tracker=DummyCostTracker(),
    )


def _trade(
    ts: datetime,
    *,
    side: str = "BUY",
    condition_id: str = "cond-weather",
    outcome_index: int = 0,
    trade_hash: str = "hash-1",
    price: float = 0.5,
    size: float = 6.0,
    slug: str = "weather-rain-market",
) -> dict[str, object]:
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


def _market(condition_id: str, slug: str) -> dict[str, object]:
    return {
        "id": condition_id,
        "question": "Weather market",
        "slug": slug,
        "description": slug,
        "eventSlug": slug,
        "clobTokenIds": [f"{condition_id}-yes", f"{condition_id}-no"],
    }


def _book(condition_id: str) -> dict[str, dict[str, float]]:
    return {
        f"{condition_id}-yes": {"best_bid": 0.46, "best_ask": 0.50, "spread_bps": 80.0},
        f"{condition_id}-no": {"best_bid": 0.48, "best_ask": 0.52, "spread_bps": 80.0},
    }


def _candidate_dataset(now: datetime) -> tuple[list[dict[str, object]], dict[str, dict[str, object]], dict[str, dict[str, list[dict[str, object]]]], dict[str, list[dict[str, object]]]]:
    week_anchor = _week_start(now)
    wallets = ["0xweather1", "0xweather2", "0xweather3"]
    leaderboard = [
        {"proxyWallet": wallets[0], "userName": "weather-alpha", "verifiedBadge": True},
        {"proxyWallet": wallets[1], "userName": "weather-beta", "verifiedBadge": True},
        {"proxyWallet": wallets[2], "userName": "weather-gamma", "verifiedBadge": False},
    ]
    profiles = {
        wallets[0]: {"displayUsernamePublic": True, "name": "weather-alpha", "pseudonym": "alpha"},
        wallets[1]: {"displayUsernamePublic": True, "name": "weather-beta", "pseudonym": "beta"},
        wallets[2]: {"displayUsernamePublic": True, "name": "weather-gamma", "pseudonym": "gamma"},
    }
    metrics = {
        wallets[0]: {
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
        wallets[1]: {
            "WEEK": [{"pnl": 10.0}],
            "MONTH": [{"pnl": 32.0}],
            "ALL": [{"pnl": 81.0}],
            "closed": [
                _closed_position(week_anchor, 4.0, "weather-e"),
                _closed_position(week_anchor - timedelta(weeks=1), 3.0, "weather-f"),
                _closed_position(week_anchor - timedelta(weeks=2), 4.0, "weather-g"),
                _closed_position(week_anchor - timedelta(weeks=3), 2.0, "weather-h"),
            ],
        },
        wallets[2]: {
            "WEEK": [{"pnl": -1.0}],
            "MONTH": [{"pnl": 1.0}],
            "ALL": [{"pnl": 3.0}],
            "closed": [
                _closed_position(now - timedelta(days=1), 1.0, "weather-x"),
                _closed_position(now - timedelta(days=3), -2.0, "weather-y"),
            ],
        },
    }
    trades = {
        wallets[0]: [_trade(now - timedelta(days=2), trade_hash=f"alpha-{idx}") for idx in range(1, 31)],
        wallets[1]: [_trade(now - timedelta(days=2), trade_hash=f"beta-{idx}", condition_id="cond-weather-2", slug="weather-sun-market") for idx in range(1, 29)],
        wallets[2]: [_trade(now - timedelta(days=2), trade_hash=f"gamma-{idx}") for idx in range(1, 5)],
    }
    return leaderboard, profiles, metrics, trades


@pytest.mark.asyncio
async def test_run_analysis_marks_multi_profile_candidate_statuses() -> None:
    now = datetime.now(UTC)
    leaderboard, profiles, metrics, trades = _candidate_dataset(now)
    connector = DummyConnector(leaderboard=leaderboard, profiles=profiles, metrics=metrics, trades=trades, markets={}, books={})
    repository = DummyRepository()
    service = make_service(connector, repository)

    first = await service.run_analysis(limit=10)
    approve = await service.approve_selection(proxy_wallets=["0xweather1", "0xweather2"])
    refreshed = await service.run_analysis(limit=10)

    assert first["selected"]["proxy_wallet"] == "0xweather1"
    assert approve["approved_count"] == 2
    assert len(approve["profiles"]) == 2
    statuses = {item["proxy_wallet"]: item["status"] for item in refreshed["candidates"]}
    assert statuses["0xweather1"] == "already_copying"
    assert statuses["0xweather2"] == "already_copying"
    assert statuses["0xweather3"] == "rejected"
    assert len(refreshed["profiles"]) == 2
    assert refreshed["portfolio_constraints"]["active_profiles_count"] == 2


@pytest.mark.asyncio
async def test_approve_selection_is_idempotent_for_existing_profile() -> None:
    now = datetime.now(UTC)
    leaderboard, profiles, metrics, trades = _candidate_dataset(now)
    connector = DummyConnector(leaderboard=leaderboard, profiles=profiles, metrics=metrics, trades=trades, markets={}, books={})
    repository = DummyRepository()
    service = make_service(connector, repository)

    await service.run_analysis(limit=10)
    first = await service.approve_selection(proxy_wallets=["0xweather1"])
    second = await service.approve_selection(proxy_wallets=["0xweather1"])

    rows = await repository.list_weather_copytrade_profiles(category="WEATHER")
    assert first["approved_count"] == 1
    assert second["approved_count"] == 1
    assert len(rows) == 1
    assert rows[0]["proxy_wallet"] == "0xweather1"


@pytest.mark.asyncio
async def test_approve_selection_tolerates_stale_run_id() -> None:
    now = datetime.now(UTC)
    leaderboard, profiles, metrics, trades = _candidate_dataset(now)
    connector = DummyConnector(leaderboard=leaderboard, profiles=profiles, metrics=metrics, trades=trades, markets={}, books={})
    repository = DummyRepository()
    service = make_service(connector, repository)

    first = await service.run_analysis(limit=10)
    stale_run_id = str(first["run"]["run_id"])
    await service.run_analysis(limit=10)
    approved = await service.approve_selection(run_id=stale_run_id, proxy_wallets=["0xweather1"])

    assert approved["approved_count"] == 1
    assert approved["stale_run_id"] is True
    assert approved["requested_run_id"] == stale_run_id
    assert approved["used_run_id"] != stale_run_id
    rows = await repository.list_weather_copytrade_profiles(category="WEATHER")
    assert len(rows) == 1
    assert rows[0]["proxy_wallet"] == "0xweather1"


@pytest.mark.asyncio
async def test_sync_mirror_trades_processes_multiple_profiles_without_hash_collisions() -> None:
    now = datetime.now(UTC)
    leaderboard, profiles, metrics, trades = _candidate_dataset(now)
    future_trade_time = now + timedelta(minutes=5)
    trades["0xweather1"] = [_trade(future_trade_time, trade_hash="alpha-live", condition_id="cond-weather-1", slug="weather-rain-market")]
    trades["0xweather2"] = [_trade(future_trade_time, trade_hash="beta-live", condition_id="cond-weather-2", slug="weather-sun-market")]
    markets = {
        "cond-weather-1": _market("cond-weather-1", "weather-rain-market"),
        "cond-weather-2": _market("cond-weather-2", "weather-sun-market"),
    }
    books = {}
    books.update(_book("cond-weather-1"))
    books.update(_book("cond-weather-2"))
    connector = DummyConnector(leaderboard=leaderboard, profiles=profiles, metrics=metrics, trades=trades, markets=markets, books=books)
    repository = DummyRepository()
    service = make_service(connector, repository)

    await service.run_analysis(limit=10)
    await service.approve_selection(proxy_wallets=["0xweather1", "0xweather2"])
    sync = await service.sync_mirror_trades()

    assert sync["copied"] == 2
    assert len(repository.orders) == 2
    groups = {item["trade_group_id"] for item in repository.orders}
    assert groups == {"0xweather1", "0xweather2"}
    rows = await repository.list_weather_copytrade_profiles(category="WEATHER")
    hashes = {row["proxy_wallet"]: set(row["processed_trade_hashes"]) for row in rows}
    assert hashes["0xweather1"] == {"alpha-live"}
    assert hashes["0xweather2"] == {"beta-live"}


@pytest.mark.asyncio
async def test_sync_mirror_trades_enforces_two_percent_bankroll_limit() -> None:
    now = datetime.now(UTC)
    leaderboard, profiles, metrics, trades = _candidate_dataset(now)
    trades["0xweather1"] = [
        _trade(
            now + timedelta(minutes=5),
            trade_hash="alpha-risk",
            condition_id="cond-weather-1",
            slug="weather-rain-market",
            price=0.50,
            size=100.0,
        )
    ]
    connector = DummyConnector(
        leaderboard=leaderboard,
        profiles=profiles,
        metrics=metrics,
        trades=trades,
        markets={"cond-weather-1": _market("cond-weather-1", "weather-rain-market")},
        books=_book("cond-weather-1"),
    )
    repository = DummyRepository(available_balance=60.0, total_equity=200.0)
    service = make_service(connector, repository)

    await service.run_analysis(limit=10)
    await service.approve_selection(proxy_wallets=["0xweather1"])
    sync = await service.sync_mirror_trades()

    assert sync["copied"] == 1
    assert len(repository.orders) == 1
    order = repository.orders[0]
    assert float(order["notional_usd"]) <= 1.22
    assert order["metadata"]["per_trade_limit_usd"] == pytest.approx(1.2)


@pytest.mark.asyncio
async def test_sync_mirror_trades_blocks_when_available_balance_is_below_minimum() -> None:
    now = datetime.now(UTC)
    leaderboard, profiles, metrics, trades = _candidate_dataset(now)
    trades["0xweather1"] = [_trade(now + timedelta(minutes=5), trade_hash="alpha-blocked", condition_id="cond-weather-1", slug="weather-rain-market")]
    connector = DummyConnector(
        leaderboard=leaderboard,
        profiles=profiles,
        metrics=metrics,
        trades=trades,
        markets={"cond-weather-1": _market("cond-weather-1", "weather-rain-market")},
        books=_book("cond-weather-1"),
    )
    repository = DummyRepository(available_balance=0.4, total_equity=100.0)
    service = make_service(connector, repository)

    await service.run_analysis(limit=10)
    await service.approve_selection(proxy_wallets=["0xweather1"])
    sync = await service.sync_mirror_trades()

    assert sync["copied"] == 0
    assert sync["reasons"]["insufficient_available_balance"] == 1
    assert repository.orders == []


@pytest.mark.asyncio
async def test_pause_resume_and_remove_profile_update_summary() -> None:
    now = datetime.now(UTC)
    leaderboard, profiles, metrics, trades = _candidate_dataset(now)
    connector = DummyConnector(leaderboard=leaderboard, profiles=profiles, metrics=metrics, trades=trades, markets={}, books={})
    repository = DummyRepository()
    service = make_service(connector, repository)

    await service.run_analysis(limit=10)
    await service.approve_selection(proxy_wallets=["0xweather1", "0xweather2"])
    paused = await service.pause_profile(proxy_wallet="0xweather1", paused=True)
    summary = await service.summary()
    removed = await service.remove_profile(proxy_wallet="0xweather2")
    resumed = await service.pause_profile(proxy_wallet="0xweather1", paused=False)

    assert paused["profile"]["paused"] is True
    assert len(summary["profiles"]) == 2
    assert any(profile["proxy_wallet"] == "0xweather1" and profile["paused"] is True for profile in summary["profiles"])
    assert removed["removed_proxy_wallet"] == "0xweather2"
    assert len(removed["profiles"]) == 1
    assert resumed["profile"]["paused"] is False


@pytest.mark.asyncio
async def test_sync_live_order_statuses_promotes_submitted_orders_to_filled() -> None:
    now = datetime.now(UTC)
    wallet = "0xweather1"
    connector = DummyConnector(leaderboard=[], profiles={}, metrics={}, trades={}, markets={}, books={})
    connector.live_orders["order-123"] = {
        "status": "FILLED",
        "size_matched": 8,
        "size": 8,
        "avgPrice": 0.52,
    }
    repository = DummyRepository()
    repository.orders = [
        {
            "order_id": "order-123",
            "signal_id": "signal-123",
            "market_id": "cond-weather",
            "asset_symbol": "WEATHER",
            "strategy_id": "weather_copytrade",
            "trade_group_id": wallet,
            "direction": "YES",
            "action": "entry",
            "size": 8,
            "price_limit": 0.52,
            "notional_usd": 4.16,
            "realized_pnl_usd": 0.0,
            "status": "live_submitted",
            "exchange_order_id": "order-123",
            "created_at": now.isoformat(),
        }
    ]
    service = make_service(connector, repository)
    service.context.settings = SimpleNamespace(live_trading=True)

    result = await service.sync_live_order_statuses()

    assert result["scanned"] == 1
    assert result["synced"] == 1
    assert result["filled"] == 1
    assert repository.orders[0]["status"] == "live_filled"
