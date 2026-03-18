from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from core.config import AppSettings, AgentsConfig, RiskSettings, load_agents_config, load_risk_config
from core.database import Database, TradingRepository
from core.redis_bus import RedisBus


@dataclass
class AppContext:
    settings: AppSettings
    agents_config: AgentsConfig
    risk_config: RiskSettings
    db: Database
    repository: TradingRepository
    redis: Redis
    bus: RedisBus

    @classmethod
    async def create(cls) -> "AppContext":
        settings = AppSettings()
        agents_config = load_agents_config()
        risk_config = load_risk_config()
        db = Database(settings.database_url)
        await db.connect()
        await db.init_schema()
        repository = TradingRepository(db, settings.paper_bankroll_usd)
        redis = Redis.from_url(settings.redis_url, decode_responses=False)
        bus = RedisBus(redis)
        await bus.bootstrap_runtime_config(agents_config)
        return cls(settings, agents_config, risk_config, db, repository, redis, bus)

    async def reload_configs(self) -> None:
        self.agents_config = load_agents_config()
        self.risk_config = load_risk_config()

    async def close(self) -> None:
        await self.redis.close()
        await self.db.close()
