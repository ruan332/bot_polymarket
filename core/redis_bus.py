from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from core.config import AgentsConfig


class RedisBus:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def publish_event(self, stream: str, payload: dict[str, Any]) -> str:
        encoded = {key: json.dumps(value, default=str) for key, value in payload.items()}
        return await self.redis.xadd(stream, encoded, maxlen=1000, approximate=True)

    async def ensure_group(self, stream: str, group: str) -> None:
        try:
            await self.redis.xgroup_create(stream, group, id="$", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_group(
        self,
        stream: str,
        group: str,
        consumer: str,
        block_ms: int = 1000,
        count: int = 1,
    ) -> list[tuple[str, dict[str, Any]]]:
        response = await self.redis.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
        if not response:
            return []
        items: list[tuple[str, dict[str, Any]]] = []
        for _, events in response:
            for event_id, data in events:
                decoded = {
                    key.decode() if isinstance(key, bytes) else key: self._json_load(value)
                    for key, value in data.items()
                }
                items.append((event_id.decode() if isinstance(event_id, bytes) else event_id, decoded))
        return items

    async def ack(self, stream: str, group: str, event_id: str) -> None:
        await self.redis.xack(stream, group, event_id)

    async def bootstrap_runtime_config(self, config: AgentsConfig) -> None:
        exists = await self.redis.exists("runtime:agents:version")
        if exists:
            return
        payload = {agent: cfg.model for agent, cfg in config.agents.items()}
        if payload:
            await self.redis.hset("runtime:agents:models", mapping=payload)
        await self.redis.set("runtime:agents:version", 1)

    async def get_agent_model_override(self, agent_name: str) -> str | None:
        value = await self.redis.hget("runtime:agents:models", agent_name)
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def get_config_version(self) -> int:
        value = await self.redis.get("runtime:agents:version")
        if value is None:
            return 0
        return int(value.decode() if isinstance(value, bytes) else value)

    async def set_agent_model(self, agent_name: str, model: str) -> int:
        await self.redis.hset("runtime:agents:models", agent_name, model)
        return await self.redis.incr("runtime:agents:version")

    async def get_daily_cost(self, key: str) -> float:
        value = await self.redis.hget(key, "cost_usd")
        if value is None:
            return 0.0
        return float(value)

    async def increment_cost_summary(
        self,
        key: str,
        *,
        cost_usd: float,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        await self.redis.hincrbyfloat(key, "cost_usd", cost_usd)
        await self.redis.hincrby(key, "input_tokens", input_tokens)
        await self.redis.hincrby(key, "output_tokens", output_tokens)
        await self.redis.hincrby(key, "calls", 1)

    async def get_hash(self, key: str) -> dict[str, Any]:
        data = await self.redis.hgetall(key)
        decoded: dict[str, Any] = {}
        for raw_key, raw_value in data.items():
            key_str = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
            value_str = raw_value.decode() if isinstance(raw_value, bytes) else raw_value
            try:
                decoded[key_str] = float(value_str)
            except (TypeError, ValueError):
                decoded[key_str] = value_str
        return decoded

    @staticmethod
    def _json_load(value: Any) -> Any:
        if isinstance(value, bytes):
            value = value.decode()
        try:
            return json.loads(value)
        except Exception:
            return value
