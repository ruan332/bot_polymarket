from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from core.exceptions import BudgetExceededError
from core.model_provider import ModelProvider


class FakeBus:
    def __init__(self) -> None:
        self.costs: dict[str, float] = {}

    async def get_daily_cost(self, key: str) -> float:
        return float(self.costs.get(key, 0.0))


def make_provider(*, agent_cost: float = 0.0, global_cost: float = 0.0, daily_limit: float = 1.0) -> ModelProvider:
    bus = FakeBus()
    today = str(date.today())
    bus.costs[f"cost:codex:{today}"] = agent_cost
    bus.costs[f"cost:global:{today}"] = global_cost
    context = SimpleNamespace(
        settings=SimpleNamespace(smoke_test_mode=False),
        agents_config=SimpleNamespace(
            agents={
                "codex": SimpleNamespace(
                    model="gpt-4o-mini",
                    provider="openai",
                    temperature=0.0,
                    max_tokens=32,
                    fallback_model="gpt-4o-mini",
                    daily_cost_limit_usd=daily_limit,
                )
            }
        ),
        bus=bus,
    )
    return ModelProvider("codex", context)


def test_global_budget_does_not_block_llm_calls() -> None:
    provider = make_provider(agent_cost=0.0, global_cost=999.0, daily_limit=1.0)

    asyncio.run(provider._guard_budget())


def test_agent_budget_still_blocks_llm_calls() -> None:
    provider = make_provider(agent_cost=1.1, global_cost=0.0, daily_limit=1.0)

    with pytest.raises(BudgetExceededError, match="codex daily cost limit reached"):
        asyncio.run(provider._guard_budget())
