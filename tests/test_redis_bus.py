from __future__ import annotations

import pytest

from core.redis_bus import RedisBus


class FakeRedis:
    def __init__(self) -> None:
        self.ensure_calls: list[tuple[str, str]] = []
        self.read_calls = 0

    async def xgroup_create(self, stream: str, group: str, id: str = "$", mkstream: bool = True) -> None:
        self.ensure_calls.append((stream, group))

    async def xreadgroup(self, group: str, consumer: str, streams: dict[str, str], count: int = 1, block: int = 1000):
        self.read_calls += 1
        if self.read_calls == 1:
            raise RuntimeError(f"NOGROUP No such key '{next(iter(streams))}' or consumer group '{group}' in XREADGROUP with GROUP option")
        return [(b"signals:validated", [(b"1-0", {b"event_type": b'"signal.reviewed"', b"signal_id": b'"sig-1"'})])]

    async def xack(self, stream: str, group: str, event_id: str) -> None:
        return None


@pytest.mark.asyncio
async def test_read_group_recreates_missing_group_once() -> None:
    redis = FakeRedis()
    bus = RedisBus(redis)  # type: ignore[arg-type]

    events = await bus.read_group("signals:validated", "codex_reviewers", "codex-1")

    assert redis.ensure_calls == [("signals:validated", "codex_reviewers")]
    assert events == [("1-0", {"event_type": "signal.reviewed", "signal_id": "sig-1"})]
