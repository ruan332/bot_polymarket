from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

from redis.asyncio import Redis

from core.config import (
    AppSettings,
    AgentsConfig,
    CryptoSettings,
    RiskSettings,
    WeatherCopytradeSettings,
    load_agents_config,
    load_crypto_config,
    load_risk_config,
    load_weather_copytrade_config,
)
from core.database import Database, TradingRepository
from core.market_connector import MarketConnector
from core.redis_bus import RedisBus

T = TypeVar("T")


@dataclass
class AppContext:
    settings: AppSettings
    agents_config: AgentsConfig
    risk_config: RiskSettings
    crypto_config: CryptoSettings
    weather_copytrade_config: WeatherCopytradeSettings
    db: Database
    repository: TradingRepository
    redis: Redis
    bus: RedisBus
    market_connector: MarketConnector | None = field(default=None, repr=False)
    live_bootstrap_status: dict[str, object] = field(default_factory=dict)

    @classmethod
    async def create(cls) -> "AppContext":
        settings = AppSettings()
        agents_config = load_agents_config()
        risk_config = load_risk_config()
        _apply_runtime_risk_overrides(settings, risk_config)
        crypto_config = load_crypto_config()
        weather_copytrade_config = load_weather_copytrade_config()
        db = Database(settings.database_url)
        await _retry_async(
            "database startup",
            settings.startup_max_retries,
            settings.startup_retry_delay_seconds,
            _init_database(db),
        )
        repository = TradingRepository(db, settings.paper_bankroll_usd, settings)
        redis = Redis.from_url(settings.redis_url, decode_responses=False)
        await _retry_async(
            "redis startup",
            settings.startup_max_retries,
            settings.startup_retry_delay_seconds,
            _init_redis(redis),
        )
        bus = RedisBus(redis)
        await bus.bootstrap_runtime_config(agents_config)
        await bus.ensure_known_groups()
        context = cls(settings, agents_config, risk_config, crypto_config, weather_copytrade_config, db, repository, redis, bus)
        context.market_connector = MarketConnector(context)
        repository.bind_live_balance_provider(context.refresh_live_bootstrap_status)
        if settings.live_trading:
            context.live_bootstrap_status = await context.refresh_live_bootstrap_status(
                sync_allowance=settings.polymarket_sync_balance_allowance_on_startup
            )
            if not bool(context.live_bootstrap_status.get("ready")):
                context.live_bootstrap_status["fail_open"] = bool(settings.polymarket_live_bootstrap_fail_open)
                if not settings.polymarket_live_bootstrap_fail_open:
                    reason = str(context.live_bootstrap_status.get("reason") or "live bootstrap failed")
                    raise RuntimeError(reason)
        else:
            context.live_bootstrap_status = {
                "mode": "paper",
                "ready": True,
                "reason": "live trading disabled",
                "fail_open": False,
            }
        return context

    async def reload_configs(self) -> None:
        self.agents_config = load_agents_config()
        self.risk_config = load_risk_config()
        _apply_runtime_risk_overrides(self.settings, self.risk_config)
        self.crypto_config = load_crypto_config()
        self.weather_copytrade_config = load_weather_copytrade_config()

    async def refresh_live_bootstrap_status(self, *, sync_allowance: bool = False) -> dict[str, object]:
        if not self.settings.live_trading:
            self.live_bootstrap_status = {
                "mode": "paper",
                "ready": True,
                "reason": "live trading disabled",
                "fail_open": False,
            }
            return self.live_bootstrap_status

        connector = self.market_connector
        close_after = False
        if connector is None:
            connector = MarketConnector(self)
            close_after = True

        try:
            status = await connector.get_live_bootstrap_status(sync_allowance=sync_allowance)
            status["fail_open"] = bool(self.settings.polymarket_live_bootstrap_fail_open)
            self.live_bootstrap_status = status
            return status
        finally:
            if close_after:
                await connector.close()

    async def close(self) -> None:
        if self.market_connector is not None:
            await self.market_connector.close()
        await self.redis.close()
        await self.db.close()


def _init_database(db: Database) -> Callable[[], Awaitable[None]]:
    async def runner() -> None:
        await db.connect()
        await db.init_schema()

    return runner


def _init_redis(redis: Redis) -> Callable[[], Awaitable[None]]:
    async def runner() -> None:
        await redis.ping()

    return runner


async def _retry_async(
    label: str,
    max_retries: int,
    delay_seconds: float,
    operation: Callable[[], Awaitable[T]],
) -> T:
    attempts = max(max_retries, 1)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as exc:  # pragma: no cover - exercised by tests via monkeypatch
            last_error = exc
            if attempt >= attempts:
                break
            await asyncio.sleep(delay_seconds)
    assert last_error is not None
    raise RuntimeError(f"{label} failed after {attempts} attempts") from last_error


def _apply_runtime_risk_overrides(settings: AppSettings, risk_config: RiskSettings) -> None:
    # Keep env-driven paper controls in sync with the risk engine runtime.
    # A positive env override wins; zero or negative values preserve the YAML baseline.
    if settings.max_daily_spend_usd > 0:
        risk_config.max_daily_spend_usd = settings.max_daily_spend_usd
    if settings.max_single_position_usd > 0:
        risk_config.max_single_position_usd = settings.max_single_position_usd
