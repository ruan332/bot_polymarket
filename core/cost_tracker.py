from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import uuid4

from core.schemas import ModelResponse

if TYPE_CHECKING:
    from core.app_context import AppContext


class CostTracker:
    def __init__(self, agent_name: str, context: AppContext):
        self.agent_name = agent_name
        self.context = context

    async def record(self, response: ModelResponse, prompt_type: str) -> None:
        today = str(date.today())
        agent_key = f"cost:{self.agent_name}:{today}"
        global_key = f"cost:global:{today}"

        await self.context.bus.increment_cost_summary(
            agent_key,
            cost_usd=response.cost_usd,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        await self.context.bus.increment_cost_summary(
            global_key,
            cost_usd=response.cost_usd,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        await self.context.repository.record_llm_call(
            call_id=str(uuid4()),
            agent=self.agent_name,
            model=response.model,
            provider=response.provider,
            cost_usd=response.cost_usd,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            fallback_used=response.fallback_used,
            prompt_type=prompt_type,
        )

    async def get_daily_summary(self) -> dict[str, float | str]:
        today = str(date.today())
        payload = await self.context.bus.get_hash(f"cost:{self.agent_name}:{today}")
        return {
            "agent": self.agent_name,
            "date": today,
            "cost_usd": float(payload.get("cost_usd", 0.0)),
            "input_tokens": int(payload.get("input_tokens", 0.0)),
            "output_tokens": int(payload.get("output_tokens", 0.0)),
            "calls": int(payload.get("calls", 0.0)),
        }
