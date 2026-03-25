from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.exceptions import BudgetExceededError
from core.model_provider import ModelProvider


def make_context(
    *, llm_daily_spend_limit_enabled: bool = True, agent_cost: float = 0.0, global_cost: float = 0.0
):
    class Bus:
        async def get_daily_cost(self, key: str) -> float:
            if key.startswith("cost:global:"):
                return global_cost
            return agent_cost

    return SimpleNamespace(
        settings=SimpleNamespace(
            max_daily_spend_usd=1.0,
            llm_daily_spend_limit_enabled=llm_daily_spend_limit_enabled,
            smoke_test_mode=False,
        ),
        agents_config=SimpleNamespace(
            agents={
                "codex": SimpleNamespace(
                    model="gpt-4o-mini",
                    provider="openai",
                    temperature=0.0,
                    max_tokens=64,
                    fallback_model="gpt-4o-mini",
                    daily_cost_limit_usd=1.0,
                )
            }
        ),
        bus=Bus(),
    )


@pytest.mark.asyncio
async def test_guard_budget_blocks_global_spend_when_enabled() -> None:
    provider = ModelProvider("codex", make_context(global_cost=1.5))

    with pytest.raises(BudgetExceededError, match="global daily spend limit reached"):
        await provider._guard_budget()


@pytest.mark.asyncio
async def test_guard_budget_ignores_global_spend_when_disabled() -> None:
    provider = ModelProvider("codex", make_context(llm_daily_spend_limit_enabled=False, global_cost=1.5))

    await provider._guard_budget()
