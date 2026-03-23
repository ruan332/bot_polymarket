from __future__ import annotations

from types import SimpleNamespace

from agents.codex_agent import CodexAgent
from core.schemas import PairLegPlan


def _build_pair_leg(target_price: float) -> PairLegPlan:
    return PairLegPlan(
        market_id="market-1",
        token_id="token-1",
        direction="YES",
        leg_role="hedge",
        size=1,
        target_price=target_price,
        reference_price=target_price,
        current_ask=target_price,
        current_bid=max(target_price - 0.01, 0.01),
    )


def test_pair_leg_with_price_ignores_overly_aggressive_llm_reduction() -> None:
    agent = CodexAgent.__new__(CodexAgent)
    agent.context = SimpleNamespace(risk_config=SimpleNamespace(max_order_price=0.9))
    leg = _build_pair_leg(0.58)

    corrected = agent._pair_leg_with_price(leg, 0.41, leg.target_price)

    assert corrected.target_price == 0.58


def test_pair_leg_with_price_accepts_modest_llm_reduction_within_floor() -> None:
    agent = CodexAgent.__new__(CodexAgent)
    agent.context = SimpleNamespace(risk_config=SimpleNamespace(max_order_price=0.9))
    leg = _build_pair_leg(0.58)

    corrected = agent._pair_leg_with_price(leg, 0.55, leg.target_price)

    assert corrected.target_price == 0.55
