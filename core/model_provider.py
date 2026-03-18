from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import litellm

from core.exceptions import BudgetExceededError
from core.schemas import ModelResponse
from core.utils import extract_first_float

if TYPE_CHECKING:
    from core.app_context import AppContext


class ModelProvider:
    def __init__(self, agent_name: str, context: AppContext):
        self.agent_name = agent_name
        self.context = context
        self.reload_config()

    def reload_config(self) -> None:
        agent_cfg = self.context.agents_config.agents[self.agent_name]
        self.model = self._qualify_model_name(agent_cfg.model, agent_cfg.provider)
        self.provider = agent_cfg.provider
        self.temperature = agent_cfg.temperature
        self.max_tokens = agent_cfg.max_tokens
        self.fallback_model = self._qualify_model_name(agent_cfg.fallback_model, agent_cfg.provider)
        self.daily_cost_limit = agent_cfg.daily_cost_limit_usd

    async def call(self, prompt: str, system: str | None = None) -> ModelResponse:
        if self.context.settings.smoke_test_mode:
            return self._smoke_response(prompt)
        await self._guard_budget()
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        fallback_used = False
        try:
            response = await litellm.acompletion(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception:
            fallback_used = True
            response = await litellm.acompletion(
                model=self.fallback_model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

        usage = getattr(response, "usage", None)
        return ModelResponse(
            content=response.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "completion_tokens", 0),
            model=getattr(response, "model", self.fallback_model if fallback_used else self.model),
            cost_usd=float(litellm.completion_cost(completion_response=response) or 0.0),
            provider=self.provider,
            fallback_used=fallback_used,
        )

    async def sync_runtime_override(self) -> int:
        override = await self.context.bus.get_agent_runtime_override(self.agent_name)
        version = await self.context.bus.get_config_version()
        if override:
            agent_cfg = self.context.agents_config.agents[self.agent_name]
            if override.get("model"):
                agent_cfg.model = override["model"]
            if override.get("provider"):
                agent_cfg.provider = override["provider"]
            if override.get("fallback_model"):
                agent_cfg.fallback_model = override["fallback_model"]
        self.reload_config()
        return version

    async def _guard_budget(self) -> None:
        today = str(date.today())
        agent_cost = await self.context.bus.get_daily_cost(f"cost:{self.agent_name}:{today}")
        global_cost = await self.context.bus.get_daily_cost(f"cost:global:{today}")
        if agent_cost >= self.daily_cost_limit:
            raise BudgetExceededError(f"{self.agent_name} daily cost limit reached")
        if global_cost >= self.context.settings.max_daily_spend_usd:
            raise BudgetExceededError("global daily spend limit reached")

    def _smoke_response(self, prompt: str) -> ModelResponse:
        content = "{}"
        if self.agent_name == "claude":
            content = json_dumps(
                {"edge": 0.24, "direction": "YES", "confidence": 0.78, "reasoning": "smoke test stub response"}
            )
        elif self.agent_name == "codex":
            price = extract_first_float(prompt, [r"preco:\s*([0-9.]+)", r"price:\s*([0-9.]+)"], 0.40) or 0.40
            content = json_dumps(
                {"approved": True, "notes": "smoke test approved", "corrected_price_limit": round(price + 0.005, 4)}
            )
        elif self.agent_name == "claw":
            size = int(extract_first_float(prompt, [r"guarded_size:\s*([0-9.]+)"], 10) or 10)
            price = extract_first_float(prompt, [r"guarded_price_limit:\s*([0-9.]+)"], 0.40) or 0.40
            content = json_dumps(
                {"execute": True, "size": size, "price_limit": round(price, 4), "reason": "smoke test execution"}
            )
        return ModelResponse(
            content=content,
            input_tokens=0,
            output_tokens=0,
            model=f"smoke-{self.agent_name}",
            cost_usd=0.0,
            provider="smoke",
            fallback_used=False,
        )

    @staticmethod
    def _qualify_model_name(model: str, provider: str) -> str:
        if "/" in model:
            return model
        provider_prefix = provider.strip().lower()
        if not provider_prefix:
            return model
        return f"{provider_prefix}/{model}"


def json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=True)
