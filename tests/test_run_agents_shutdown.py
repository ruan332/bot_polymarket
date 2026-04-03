from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import run_agents


class FakeContext:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            copytrade_enabled=False,
            news_validation_enabled=True,
        )
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _make_agent(label: str, *, fail: bool = False):
    class FakeAgent:
        instances: list["FakeAgent"] = []

        def __init__(self, context):
            self.context = context
            self.provider = SimpleNamespace(model=f"{label}-model")
            self.closed = False
            self.cancelled = False
            self.label = label
            FakeAgent.instances.append(self)

        async def run_loop(self, interval_seconds: float = 5.0) -> None:
            if fail:
                await asyncio.sleep(0)
                raise RuntimeError(f"{label} failed")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

        async def close(self) -> None:
            self.closed = True

    return FakeAgent


@pytest.mark.asyncio
async def test_main_cancels_tasks_and_closes_everything_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    context = FakeContext()
    claude_cls = _make_agent("claude")
    codex_cls = _make_agent("codex", fail=True)
    claw_cls = _make_agent("claw")
    news_validator_cls = _make_agent("news_validator")

    async def fake_create():
        return context

    monkeypatch.setattr(run_agents.AppContext, "create", fake_create)
    monkeypatch.setattr(run_agents, "ClaudeAgent", claude_cls)
    monkeypatch.setattr(run_agents, "CodexAgent", codex_cls)
    monkeypatch.setattr(run_agents, "ClawAgent", claw_cls)
    monkeypatch.setattr(run_agents, "NewsValidatorAgent", news_validator_cls)

    with pytest.raises(RuntimeError, match="codex failed"):
        await run_agents.main()

    assert context.closed is True
    assert all(agent.closed is True for agent in claude_cls.instances)
    assert all(agent.closed is True for agent in codex_cls.instances)
    assert all(agent.closed is True for agent in claw_cls.instances)
    assert all(agent.closed is True for agent in news_validator_cls.instances)
    assert all(agent.cancelled is True for agent in claude_cls.instances)
    assert all(agent.cancelled is True for agent in claw_cls.instances)
    assert all(agent.cancelled is True for agent in news_validator_cls.instances)
