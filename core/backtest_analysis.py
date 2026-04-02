from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from core.cost_tracker import CostTracker
from core.model_provider import ModelProvider
from core.utils import parse_json_object, sanitize_text

if TYPE_CHECKING:
    from core.app_context import AppContext


SYSTEM_PROMPT = """
Voce e um analista quantitativo de estrategias.
Sua tarefa e ler o resultado de um backtest deterministico e produzir um JSON estrito.
Nao invente numeros. Use apenas o que estiver no input.
Priorize:
- regime de mercado
- falhas operacionais
- custo de execucao
- risco de overfitting
- recomendacoes praticas de melhoria
Retorne somente JSON valido.
"""


def build_backtest_analysis_prompt(report: dict[str, Any], *, max_trades: int = 8) -> str:
    closed_trades = [trade for trade in report.get("trades", []) if trade.get("closed")]
    largest_losses = sorted(closed_trades, key=lambda item: float(item.get("realized_pnl_usd") or 0.0))[:max_trades]
    largest_wins = sorted(closed_trades, key=lambda item: float(item.get("realized_pnl_usd") or 0.0), reverse=True)[
        :max_trades
    ]
    payload = {
        "scenario": report.get("scenario", "baseline"),
        "execution_assumptions": report.get("execution_assumptions", {}),
        "summary": report.get("summary", {}),
        "trade_summary": report.get("trade_summary", {}),
        "by_strategy": report.get("by_strategy", []),
        "by_regime": report.get("by_regime", []),
        "largest_losses": largest_losses,
        "largest_wins": largest_wins,
    }
    return (
        "Analise o backtest abaixo e responda em JSON estrito com as chaves:\n"
        "{\n"
        '  "verdict": string,\n'
        '  "confidence": number,\n'
        '  "strengths": [string],\n'
        '  "failure_modes": [string],\n'
        '  "regime_notes": [{"regime": string, "observation": string}],\n'
        '  "operational_rules": [string],\n'
        '  "recommended_experiments": [string],\n'
        '  "promotion_decision": string\n'
        "}\n\n"
        "Backtest:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}"
    )


async def analyze_backtest_with_llm(
    context: AppContext,
    report: dict[str, Any],
    *,
    agent_name: str = "codex",
    max_trades: int = 8,
) -> dict[str, Any]:
    provider = ModelProvider(agent_name, context)
    tracker = CostTracker(agent_name, context)
    prompt = build_backtest_analysis_prompt(report, max_trades=max_trades)
    try:
        response = await provider.call(prompt, system=SYSTEM_PROMPT)
        await tracker.record(response, "backtest.analysis")
        analysis = parse_json_object(response.content)
        if not isinstance(analysis, dict):
            raise ValueError("LLM analysis did not return a JSON object")
        return {
            "prompt_type": "backtest.analysis",
            "agent": agent_name,
            "model": response.model,
            "provider": response.provider,
            "fallback_used": response.fallback_used,
            "cost_usd": response.cost_usd,
            "analysis": analysis,
        }
    except Exception as exc:
        return {
            "prompt_type": "backtest.analysis",
            "agent": agent_name,
            "model": getattr(provider, "model", ""),
            "provider": getattr(provider, "provider", ""),
            "fallback_used": False,
            "cost_usd": 0.0,
            "analysis": {
                "verdict": "analysis_failed",
                "confidence": 0.0,
                "strengths": [],
                "failure_modes": [],
                "regime_notes": [],
                "operational_rules": [],
                "recommended_experiments": [],
                "promotion_decision": "defer",
                "error": sanitize_text(str(exc), 400),
            },
        }
