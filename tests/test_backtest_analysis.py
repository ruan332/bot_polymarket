from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import backtest_analysis


class _DummyProviderResponse(SimpleNamespace):
    pass


@pytest.mark.asyncio
async def test_analyze_backtest_with_llm_returns_parsed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    async def fake_call(self, prompt: str, system: str | None = None):  # type: ignore[override]
        captured["prompt"] = prompt
        captured["system"] = system or ""
        return _DummyProviderResponse(
            content=(
                "{\"verdict\":\"tighten_pair\",\"confidence\":0.81,"
                "\"strengths\":[\"positive edge\"],"
                "\"failure_modes\":[\"execution cost\"],"
                "\"regime_notes\":[{\"regime\":\"trend\",\"observation\":\"works\"}],"
                "\"operational_rules\":[\"raise min confidence\"],"
                "\"recommended_experiments\":[\"test lower fill ratio\"],"
                "\"promotion_decision\":\"promote\"}"
            ),
            model="openai/gpt-4o-mini",
            provider="openai",
            fallback_used=False,
            cost_usd=0.01,
        )

    async def fake_record(self, response, prompt_type: str) -> None:  # type: ignore[override]
        captured["prompt_type"] = prompt_type

    monkeypatch.setattr(backtest_analysis.ModelProvider, "call", fake_call)
    monkeypatch.setattr(backtest_analysis.CostTracker, "record", fake_record)

    context = SimpleNamespace(
        settings=SimpleNamespace(smoke_test_mode=False),
        agents_config=SimpleNamespace(
            agents={
                "codex": SimpleNamespace(
                    model="gpt-4o-mini",
                    provider="openai",
                    temperature=0.0,
                    max_tokens=512,
                    fallback_model="gpt-4o-mini",
                    daily_cost_limit_usd=1.0,
                )
            }
        ),
        bus=SimpleNamespace(),
        repository=SimpleNamespace(),
    )

    report = {
        "scenario": "baseline",
        "execution_assumptions": {},
        "summary": {"final_equity": 1015.0, "total_pnl": 15.0, "win_rate": 1.0},
        "trade_summary": {"realized_pnl_usd": 15.0, "win_rate": 1.0},
        "by_strategy": [{"label": "momentum_15m", "realized_pnl_usd": 15.0}],
        "by_regime": [{"label": "trend", "realized_pnl_usd": 15.0}],
        "trades": [
            {
                "closed": True,
                "strategy_id": "momentum_15m",
                "regime": "trend",
                "realized_pnl_usd": 15.0,
                "hold_minutes": 5.0,
            }
        ],
    }

    analysis = await backtest_analysis.analyze_backtest_with_llm(context, report)

    assert analysis["prompt_type"] == "backtest.analysis"
    assert analysis["analysis"]["verdict"] == "tighten_pair"
    assert analysis["analysis"]["confidence"] == pytest.approx(0.81)
    assert captured["prompt_type"] == "backtest.analysis"
    assert "Backtest:" in captured["prompt"]
    assert "tighten_pair" not in captured["prompt"]
    assert "quantitativo" in captured["system"]
