from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from uuid import uuid4

from core.app_context import AppContext
from core.cost_tracker import CostTracker
from core.model_provider import ModelProvider
from core.schemas import AgentHeartbeat


class BaseAgent(ABC):
    def __init__(self, name: str, context: AppContext):
        self.name = name
        self.context = context
        self.provider = ModelProvider(name, context)
        self.cost_tracker = CostTracker(name, context)
        self.running = False
        self.last_config_version = 0

    async def run_loop(self, interval_seconds: float = 5.0) -> None:
        self.running = True
        while self.running:
            try:
                await self.sync_runtime()
                await self.tick()
            except Exception as exc:
                await self.on_error(exc)
            finally:
                await self.heartbeat(interval_seconds)
            await asyncio.sleep(interval_seconds)

    async def sync_runtime(self) -> None:
        version = await self.provider.sync_runtime_override()
        if version != self.last_config_version:
            await self.context.reload_configs()
            self.provider.reload_config()
            self.last_config_version = version

    async def heartbeat(self, interval_seconds: float) -> None:
        await self.context.repository.upsert_heartbeat(
            AgentHeartbeat(
                agent=self.name,
                model=self.provider.model,
                running=self.running,
                config_version=self.last_config_version,
                last_seen=datetime.utcnow(),
                meta={"interval_seconds": interval_seconds},
            )
        )

    async def on_error(self, error: Exception) -> None:
        await self.context.repository.record_risk_event(
            event_id=str(uuid4()),
            agent=self.name,
            reason="agent_error",
            payload={"error": str(error)},
        )

    async def close(self) -> None:
        self.running = False

    @abstractmethod
    async def tick(self) -> None:
        raise NotImplementedError
