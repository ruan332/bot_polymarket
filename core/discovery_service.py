from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from statistics import fmean
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.exceptions import BudgetExceededError
from core.utils import clamp, parse_json_object, sanitize_text

if TYPE_CHECKING:
    from core.app_context import AppContext
    from core.cost_tracker import CostTracker
    from core.market_connector import MarketConnector
    from core.model_provider import ModelProvider
    from core.strategy_engine import StrategyEngine


RESEARCH_SYSTEM_PROMPT = """
You are a crypto market research gate for Polymarket discovery.
Return only valid JSON with this schema:
{
  "recommendation": "promote" | "watch" | "reject",
  "score": 0.0,
  "summary": "short sentence",
  "why": "short sentence",
  "risks": ["short risk", "..."],
  "follow_up": "short next action"
}
Use "promote" only when this market should move to the final Claude gate.
Use "watch" when the setup is interesting but not strong enough yet.
Use "reject" when the market does not fit our operational style.
Keep the response short and operational.
"""


FINAL_SYSTEM_PROMPT = """
You are the final Claude gate for Polymarket crypto discovery.
Return only valid JSON with this schema:
{
  "operable": true,
  "final_score": 0.0,
  "strategy_fit": "trend_follow_bayes" | "mean_revert_bayes" | "none",
  "summary": "short sentence",
  "blocked_reasons": ["short reason", "..."],
  "operator_note": "short sentence"
}
Mark "operable" true only if the market is fit for the current operational stack.
"""


class DiscoveryService:
    def __init__(
        self,
        context: AppContext,
        *,
        connector: MarketConnector | None = None,
        strategy: StrategyEngine | None = None,
        research_provider: ModelProvider | None = None,
        final_provider: ModelProvider | None = None,
        research_cost_tracker: CostTracker | None = None,
        final_cost_tracker: CostTracker | None = None,
    ):
        self.context = context
        if connector is None:
            from core.market_connector import MarketConnector as _MarketConnector

            connector = _MarketConnector(context)
        if strategy is None:
            from core.strategy_engine import StrategyEngine as _StrategyEngine

            strategy = _StrategyEngine(context)
        if research_provider is None:
            from core.model_provider import ModelProvider as _ModelProvider

            research_provider = _ModelProvider("news_validator", context)
        if final_provider is None:
            from core.model_provider import ModelProvider as _ModelProvider

            final_provider = _ModelProvider("claude", context)
        if research_cost_tracker is None:
            from core.cost_tracker import CostTracker as _CostTracker

            research_cost_tracker = _CostTracker("news_validator", context)
        if final_cost_tracker is None:
            from core.cost_tracker import CostTracker as _CostTracker

            final_cost_tracker = _CostTracker("claude", context)
        self.connector = connector
        self.strategy = strategy
        self.research_provider = research_provider
        self.final_provider = final_provider
        self.research_cost_tracker = research_cost_tracker
        self.final_cost_tracker = final_cost_tracker

    async def close(self) -> None:
        await self.connector.close()

    async def run(self, *, limit: int | None = None) -> dict[str, Any]:
        requested_limit = limit or self._default_limit()
        markets = await self.connector.get_active_markets(limit=requested_limit, crypto_only=self.context.crypto_config.enabled)
        scan_stats = deepcopy(getattr(self.connector, "last_scan_stats", {}))

        deterministic_candidates: list[dict[str, Any]] = []
        rejected_breakdown: Counter[str] = Counter()
        for market in markets:
            candidate = await self._evaluate_deterministic(market)
            deterministic_candidates.append(candidate)
            if not candidate["deterministic_pass"]:
                rejected_breakdown[candidate["reason"] or "deterministic_reject"] += 1

        deterministic_candidates.sort(key=lambda item: (-item["score"], -item["confidence"], -item["edge"], item["market_id"]))
        deterministic_passed = [item for item in deterministic_candidates if item["deterministic_pass"]]

        shortlisted = deterministic_passed[: self._research_limit()]
        shortlisted_ids = {item["market_id"] for item in shortlisted}
        research_passed_ids: set[str] = set()
        claude_passed_ids: set[str] = set()
        research_cost_usd = 0.0
        research_calls = 0
        claude_cost_usd = 0.0
        claude_calls = 0

        for candidate in shortlisted:
            research = await self._research_gate(candidate)
            research_calls += 1
            research_cost_usd += float(research.get("cost_usd") or 0.0)
            candidate["stage_payload"]["research"] = research
            if research["recommendation"] == "promote":
                research_passed_ids.add(candidate["market_id"])
            else:
                rejected_breakdown[research.get("reason") or research["recommendation"] or "research_reject"] += 1

        final_queue = [item for item in shortlisted if item["market_id"] in research_passed_ids][: self._final_limit()]
        final_queue_ids = {item["market_id"] for item in final_queue}
        for candidate in final_queue:
            final_verdict = await self._final_gate(candidate)
            claude_calls += 1
            claude_cost_usd += float(final_verdict.get("cost_usd") or 0.0)
            candidate["stage_payload"]["claude"] = final_verdict
            if final_verdict["operable"]:
                claude_passed_ids.add(candidate["market_id"])
            else:
                rejected_breakdown[final_verdict.get("reason") or "claude_reject"] += 1

        run_id = str(uuid4())
        candidate_records = self._build_candidate_records(
            deterministic_candidates,
            run_id=run_id,
            shortlisted_ids=shortlisted_ids,
            research_passed_ids=research_passed_ids,
            claude_passed_ids=claude_passed_ids,
            final_queue_ids=final_queue_ids,
        )
        stage_counts = self._build_stage_counts(candidate_records)
        dropoff_counts = self._build_dropoff_counts(candidate_records, rejected_breakdown)
        operable_count = len(claude_passed_ids)
        run_payload = {
            "run_id": run_id,
            "requested_limit": requested_limit,
            "universe_count": len(markets),
            "crypto_classified_count": int(scan_stats.get("crypto_classified") or 0),
            "deterministic_passed_count": len(deterministic_passed),
            "research_passed_count": len(research_passed_ids),
            "claude_passed_count": len(claude_passed_ids),
            "operable_count": operable_count,
            "stage_counts": stage_counts,
            "dropoff_counts": dropoff_counts,
            "rejected_breakdown": dict(sorted(rejected_breakdown.items(), key=lambda pair: (-pair[1], pair[0]))),
            "cost_summary": {
                "research_cost_usd": round(research_cost_usd, 6),
                "research_calls": research_calls,
                "claude_cost_usd": round(claude_cost_usd, 6),
                "claude_calls": claude_calls,
                "total_cost_usd": round(research_cost_usd + claude_cost_usd, 6),
            },
            "scan_stats": scan_stats,
            "metadata": {
                "shortlist_limit": self._research_limit(),
                "final_limit": self._final_limit(),
                "model_research": self.research_provider.model,
                "model_final": self.final_provider.model,
            },
            "created_at": datetime.now(UTC),
        }

        stored_run = await self.context.repository.record_discovery_run(run_payload)
        candidate_insert_payloads = [candidate["persist_payload"] for candidate in candidate_records]
        await self.context.repository.record_discovery_candidates(candidate_insert_payloads)
        latest = await self.context.repository.get_latest_discovery_funnel(limit=16)
        return {
            "run": stored_run,
            "candidates": (latest or {}).get("candidates", candidate_records[:16]),
            "latest_scan_stats": scan_stats,
            "stage_counts": stage_counts,
            "dropoff_counts": dropoff_counts,
            "rejected_breakdown": run_payload["rejected_breakdown"],
            "cost_summary": run_payload["cost_summary"],
        }

    async def _evaluate_deterministic(self, market: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC)
        market_id = str(market.get("id") or "")
        asset_symbol = str(market.get("asset_symbol") or "")
        asset_name = str(market.get("asset_name") or "")
        crypto_tier = str(market.get("crypto_tier") or "small_cap")
        market_kind = str(market.get("market_kind") or "")
        question = str(market.get("question") or "")
        strategy_id = ""
        direction = ""
        edge = 0.0
        confidence = 0.0
        liquidity_score = 0.0
        volatility_score = 0.0
        spread_bps = self._spread_bps(market)
        time_to_expiry_hours = self._time_to_expiry_hours(market)
        reason_parts: list[str] = []
        tier_cfg = self.context.crypto_config.tier(crypto_tier)
        min_edge = tier_cfg.min_edge
        min_confidence = tier_cfg.min_confidence
        min_volume = tier_cfg.min_volume_24h
        if market_kind != "direct_coin":
            min_edge += self.context.crypto_config.indirect_min_edge_buffer
            min_confidence += self.context.crypto_config.indirect_min_confidence_buffer
            min_volume *= self.context.crypto_config.indirect_min_volume_multiplier

        if float(market.get("volume_24h") or 0.0) < min_volume:
            reason_parts.append("volume_below_threshold")
        if spread_bps > float(self.context.risk_config.max_spread_bps):
            reason_parts.append("spread_too_wide")

        decision = await self.strategy.analyze_market(market)
        if decision is None:
            reason_parts.append("strategy_engine_reject")
        else:
            strategy_id = decision.strategy_id
            direction = decision.direction
            edge = float(decision.edge)
            confidence = float(decision.confidence)
            features_summary = getattr(decision, "features_summary", {}) or {}
            momentum_short = abs(float(features_summary.get("momentum_short") or 0.0))
            momentum_medium = abs(float(features_summary.get("momentum_medium") or 0.0))
            volatility_score = clamp((momentum_short + momentum_medium) / 0.12, 0.0, 1.0)
            yes_book = market.get("orderbook_summary_yes") or {}
            no_book = market.get("orderbook_summary_no") or {}
            yes_depth = float(yes_book.get("bid_depth") or 0.0) + float(yes_book.get("ask_depth") or 0.0)
            no_depth = float(no_book.get("bid_depth") or 0.0) + float(no_book.get("ask_depth") or 0.0)
            depth_total = yes_depth + no_depth
            liquidity_score = clamp(depth_total / max(tier_cfg.max_position_usd * 8, 1.0), 0.0, 2.0)
            if edge < min_edge:
                reason_parts.append("edge_below_threshold")
            if confidence < min_confidence:
                reason_parts.append("confidence_below_threshold")
            if liquidity_score < 0.35:
                reason_parts.append("liquidity_too_low")

        passed = not reason_parts
        normalized_edge = clamp(edge / max(min_edge, 0.01), 0.0, 1.5)
        normalized_confidence = clamp(confidence / max(min_confidence, 0.01), 0.0, 1.5)
        normalized_volume = clamp(float(market.get("volume_24h") or 0.0) / max(min_volume, 1.0), 0.0, 2.0)
        normalized_spread = clamp(1 - (spread_bps / max(float(self.context.risk_config.max_spread_bps), 1.0)), 0.0, 1.0)
        score = clamp(
            0.38 * normalized_edge
            + 0.14 * normalized_confidence
            + 0.12 * clamp(normalized_volume / 2.0, 0.0, 1.0)
            + 0.14 * clamp(liquidity_score / 1.0, 0.0, 1.0)
            + 0.06 * normalized_spread
            + 0.16 * volatility_score,
            0.0,
            1.0,
        )

        stage_payload = {
            "deterministic": {
                "passed": passed,
                "score": round(score, 4),
                "reason": "; ".join(reason_parts) if reason_parts else "deterministic_passed",
                "strategy_id": strategy_id,
                "direction": direction,
                "edge": round(edge, 4),
                "confidence": round(confidence, 4),
                "spread_bps": round(spread_bps, 2),
                "liquidity_score": round(liquidity_score, 4),
                "volatility_score": round(volatility_score, 4),
                "time_to_expiry_hours": None if time_to_expiry_hours is None else round(time_to_expiry_hours, 2),
                "market_kind": market_kind,
                "volume_24h": float(market.get("volume_24h") or 0.0),
                "evaluated_at": now.isoformat(),
            }
        }
        return {
            "market_id": market_id,
            "question": question,
            "asset_symbol": asset_symbol,
            "asset_name": asset_name,
            "crypto_tier": crypto_tier,
            "market_kind": market_kind,
            "volume_24h": float(market.get("volume_24h") or 0.0),
            "spread_bps": round(spread_bps, 2),
            "edge": round(edge, 4),
            "confidence": round(confidence, 4),
            "liquidity_score": round(liquidity_score, 4),
            "volatility_score": round(volatility_score, 4),
            "time_to_expiry_hours": time_to_expiry_hours,
            "strategy_id": strategy_id,
            "direction": direction,
            "deterministic_pass": passed,
            "score": round(score, 4),
            "reason": stage_payload["deterministic"]["reason"],
            "stage_payload": stage_payload,
            "created_at": now.isoformat(),
            "market": market,
        }

    async def _research_gate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        market = candidate["market"]
        prompt = self._build_research_prompt(candidate)
        try:
            response = await self.research_provider.call(prompt=prompt, system=RESEARCH_SYSTEM_PROMPT)
            await self.research_cost_tracker.record(response, prompt_type="discovery_research")
            payload = parse_json_object(response.content)
        except BudgetExceededError as exc:
            return self._llm_fallback(candidate, stage="research", reason=str(exc))
        except Exception as exc:
            return self._llm_fallback(candidate, stage="research", reason=f"research_parse_error:{exc}")

        recommendation = str(payload.get("recommendation") or "reject").strip().lower()
        if recommendation not in {"promote", "watch", "reject"}:
            recommendation = "reject"
        score = clamp(float(payload.get("score") or 0.0), 0.0, 1.0)
        summary = sanitize_text(str(payload.get("summary") or ""), 180)
        why = sanitize_text(str(payload.get("why") or ""), 180)
        risks = [sanitize_text(str(item), 96) for item in payload.get("risks", []) if str(item).strip()]
        follow_up = sanitize_text(str(payload.get("follow_up") or ""), 160)
        reason = why or summary or recommendation
        return {
            "passed": recommendation == "promote",
            "recommendation": recommendation,
            "score": round(score, 4),
            "summary": summary,
            "why": why,
            "risks": risks[:4],
            "follow_up": follow_up,
            "model": response.model,
            "provider": response.provider,
            "cost_usd": round(float(response.cost_usd or 0.0), 6),
            "fallback_used": bool(response.fallback_used),
            "reason": reason,
            "evaluated_at": datetime.now(UTC).isoformat(),
            "market_id": candidate["market_id"],
            "asset_symbol": candidate["asset_symbol"],
            "market_question": market.get("question", ""),
        }

    async def _final_gate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        market = candidate["market"]
        prompt = self._build_final_prompt(candidate)
        try:
            response = await self.final_provider.call(prompt=prompt, system=FINAL_SYSTEM_PROMPT)
            await self.final_cost_tracker.record(response, prompt_type="discovery_final")
            payload = parse_json_object(response.content)
        except BudgetExceededError as exc:
            return self._llm_fallback(candidate, stage="claude", reason=str(exc))
        except Exception as exc:
            return self._llm_fallback(candidate, stage="claude", reason=f"claude_parse_error:{exc}")

        operable = bool(payload.get("operable"))
        final_score = clamp(float(payload.get("final_score") or 0.0), 0.0, 1.0)
        strategy_fit = str(payload.get("strategy_fit") or "none").strip() or "none"
        blocked_reasons = [sanitize_text(str(item), 96) for item in payload.get("blocked_reasons", []) if str(item).strip()]
        summary = sanitize_text(str(payload.get("summary") or ""), 180)
        operator_note = sanitize_text(str(payload.get("operator_note") or ""), 180)
        reason = operator_note or summary or ("operable" if operable else "not_operable")
        return {
            "passed": operable,
            "operable": operable,
            "final_score": round(final_score, 4),
            "strategy_fit": strategy_fit,
            "summary": summary,
            "blocked_reasons": blocked_reasons[:4],
            "operator_note": operator_note,
            "model": response.model,
            "provider": response.provider,
            "cost_usd": round(float(response.cost_usd or 0.0), 6),
            "fallback_used": bool(response.fallback_used),
            "reason": reason,
            "evaluated_at": datetime.now(UTC).isoformat(),
            "market_id": candidate["market_id"],
            "asset_symbol": candidate["asset_symbol"],
            "market_question": market.get("question", ""),
        }

    def _build_research_prompt(self, candidate: dict[str, Any]) -> str:
        market = candidate["market"]
        return (
            "Assess whether this crypto market deserves a final Claude review.\n"
            f"Market ID: {candidate['market_id']}\n"
            f"Question: {sanitize_text(str(market.get('question') or ''), 240)}\n"
            f"Asset: {candidate['asset_symbol']} ({candidate['asset_name']})\n"
            f"Tier: {candidate['crypto_tier']}\n"
            f"Kind: {candidate['market_kind']}\n"
            f"Direction: {candidate['direction'] or 'n/a'}\n"
            f"Strategy: {candidate['strategy_id'] or 'n/a'}\n"
            f"Edge: {candidate['edge']:.4f}\n"
            f"Confidence: {candidate['confidence']:.4f}\n"
            f"Spread bps: {candidate['spread_bps']:.2f}\n"
            f"Liquidity score: {candidate['liquidity_score']:.4f}\n"
            f"Volatility score: {candidate['volatility_score']:.4f}\n"
            f"Volume 24h: {candidate['volume_24h']:.2f}\n"
            f"Time to expiry (h): {candidate['time_to_expiry_hours'] if candidate['time_to_expiry_hours'] is not None else 'unknown'}\n"
            f"Strategy reasoning: {sanitize_text(str(candidate['stage_payload']['deterministic'].get('reason') or ''), 220)}\n"
            f"Thesis tags: {', '.join(str(tag) for tag in market.get('thesis_tags', [])[:8]) or 'n/a'}\n"
        )

    def _build_final_prompt(self, candidate: dict[str, Any]) -> str:
        market = candidate["market"]
        research = candidate["stage_payload"].get("research") or {}
        return (
            "Decide if this crypto market is operable in our stack.\n"
            f"Market ID: {candidate['market_id']}\n"
            f"Question: {sanitize_text(str(market.get('question') or ''), 240)}\n"
            f"Asset: {candidate['asset_symbol']} ({candidate['asset_name']})\n"
            f"Tier: {candidate['crypto_tier']}\n"
            f"Kind: {candidate['market_kind']}\n"
            f"Deterministic score: {candidate['score']:.4f}\n"
            f"Research recommendation: {research.get('recommendation', 'unknown')}\n"
            f"Research score: {float(research.get('score') or 0.0):.4f}\n"
            f"Research summary: {sanitize_text(str(research.get('summary') or ''), 220)}\n"
            f"Research risks: {', '.join(str(item) for item in research.get('risks', [])[:6]) or 'n/a'}\n"
            f"Direction: {candidate['direction'] or 'n/a'}\n"
            f"Edge: {candidate['edge']:.4f}\n"
            f"Confidence: {candidate['confidence']:.4f}\n"
            f"Spread bps: {candidate['spread_bps']:.2f}\n"
            f"Liquidity score: {candidate['liquidity_score']:.4f}\n"
            f"Volatility score: {candidate['volatility_score']:.4f}\n"
            f"Volume 24h: {candidate['volume_24h']:.2f}\n"
            f"Time to expiry (h): {candidate['time_to_expiry_hours'] if candidate['time_to_expiry_hours'] is not None else 'unknown'}\n"
        )

    def _llm_fallback(self, candidate: dict[str, Any], *, stage: str, reason: str) -> dict[str, Any]:
        if stage == "research":
            return {
                "passed": False,
                "recommendation": "reject",
                "score": 0.0,
                "summary": "",
                "why": reason,
                "risks": [reason],
                "follow_up": "",
                "model": self.research_provider.model,
                "provider": self.research_provider.provider,
                "cost_usd": 0.0,
                "fallback_used": True,
                "reason": reason,
                "evaluated_at": datetime.now(UTC).isoformat(),
                "market_id": candidate["market_id"],
                "asset_symbol": candidate["asset_symbol"],
                "market_question": candidate["market"].get("question", ""),
            }
        return {
            "passed": False,
            "operable": False,
            "final_score": 0.0,
            "strategy_fit": "none",
            "summary": "",
            "blocked_reasons": [reason],
            "operator_note": reason,
            "model": self.final_provider.model,
            "provider": self.final_provider.provider,
            "cost_usd": 0.0,
            "fallback_used": True,
            "reason": reason,
            "evaluated_at": datetime.now(UTC).isoformat(),
            "market_id": candidate["market_id"],
            "asset_symbol": candidate["asset_symbol"],
            "market_question": candidate["market"].get("question", ""),
        }

    def _build_candidate_records(
        self,
        candidates: list[dict[str, Any]],
        *,
        run_id: str,
        shortlisted_ids: set[str],
        research_passed_ids: set[str],
        claude_passed_ids: set[str],
        final_queue_ids: set[str],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for candidate in candidates:
            stage_payload = deepcopy(candidate["stage_payload"])
            deterministic_pass = bool(candidate["deterministic_pass"])
            shortlisted = candidate["market_id"] in shortlisted_ids
            research_pass = candidate["market_id"] in research_passed_ids
            claude_pass = candidate["market_id"] in claude_passed_ids
            if not deterministic_pass:
                verdict = "reject"
                reason = stage_payload["deterministic"]["reason"]
            elif not shortlisted:
                verdict = "watch"
                reason = "shortlist_limit"
                stage_payload["research"] = {
                    "passed": False,
                    "recommendation": "watch",
                    "reason": "shortlist_limit",
                    "score": 0.0,
                    "summary": "",
                    "why": "not shortlisted for cheap research",
                    "risks": ["shortlist_limit"],
                    "follow_up": "",
                    "evaluated_at": datetime.now(UTC).isoformat(),
                }
            elif not research_pass:
                verdict = "reject"
                research = stage_payload.get("research") or {}
                reason = str(research.get("reason") or research.get("recommendation") or "research_reject")
            elif not claude_pass:
                if candidate["market_id"] in final_queue_ids:
                    verdict = "reject"
                    reason = "claude_gate_reject"
                else:
                    verdict = "watch"
                    reason = "claude_shortlist_limit"
            else:
                verdict = "operable"
                reason = "operable"

            stage_payload.setdefault("research", stage_payload.get("research") or {})
            stage_payload.setdefault("claude", stage_payload.get("claude") or {})
            records.append(
                {
                    "run_id": run_id,
                    "market_id": candidate["market_id"],
                    "question": candidate["question"],
                    "asset_symbol": candidate["asset_symbol"],
                    "asset_name": candidate["asset_name"],
                    "crypto_tier": candidate["crypto_tier"],
                    "market_kind": candidate["market_kind"],
                    "volume_24h": candidate["volume_24h"],
                    "spread_bps": candidate["spread_bps"],
                    "edge": candidate["edge"],
                    "confidence": candidate["confidence"],
                    "liquidity_score": candidate["liquidity_score"],
                    "volatility_score": candidate["volatility_score"],
                    "time_to_expiry_hours": candidate["time_to_expiry_hours"],
                    "strategy_id": candidate["strategy_id"],
                    "direction": candidate["direction"],
                    "deterministic_pass": deterministic_pass,
                    "research_pass": research_pass,
                    "claude_pass": claude_pass,
                    "verdict": verdict,
                    "score": candidate["score"],
                    "reason": reason,
                    "stage_payload": stage_payload,
                    "created_at": candidate["created_at"],
                    "persist_payload": {
                        "run_id": run_id,
                        "market_id": candidate["market_id"],
                        "question": candidate["question"],
                        "asset_symbol": candidate["asset_symbol"],
                        "asset_name": candidate["asset_name"],
                        "crypto_tier": candidate["crypto_tier"],
                        "market_kind": candidate["market_kind"],
                        "volume_24h": candidate["volume_24h"],
                        "spread_bps": candidate["spread_bps"],
                        "edge": candidate["edge"],
                        "confidence": candidate["confidence"],
                        "liquidity_score": candidate["liquidity_score"],
                        "volatility_score": candidate["volatility_score"],
                        "time_to_expiry_hours": candidate["time_to_expiry_hours"],
                        "strategy_id": candidate["strategy_id"],
                        "direction": candidate["direction"],
                        "deterministic_pass": deterministic_pass,
                        "research_pass": research_pass,
                        "claude_pass": claude_pass,
                        "verdict": verdict,
                        "score": candidate["score"],
                        "reason": reason,
                        "stage_payload": stage_payload,
                        "created_at": candidate["created_at"],
                    },
                }
            )
        return records

    @staticmethod
    def _build_stage_counts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stage_counts = {
            "universe": len(records),
            "crypto_classified": len([item for item in records if item["asset_symbol"]]),
            "deterministic_passed": len([item for item in records if item["deterministic_pass"]]),
            "cheap_llm_passed": len([item for item in records if item["research_pass"] and item["stage_payload"].get("research", {}).get("recommendation") == "promote"]),
            "claude_passed": len([item for item in records if item["claude_pass"]]),
            "operable": len([item for item in records if item["verdict"] == "operable"]),
        }
        return [{"label": label, "count": count} for label, count in stage_counts.items()]

    @staticmethod
    def _build_dropoff_counts(records: list[dict[str, Any]], rejected_breakdown: Counter[str]) -> list[dict[str, Any]]:
        counts = Counter(rejected_breakdown)
        counts["deterministic_reject"] += len([item for item in records if not item["deterministic_pass"]])
        counts["shortlist_limit"] += len(
            [item for item in records if item["deterministic_pass"] and item["verdict"] == "watch" and item["reason"] == "shortlist_limit"]
        )
        counts["claude_shortlist_limit"] += len(
            [item for item in records if item["deterministic_pass"] and item["research_pass"] and item["verdict"] == "watch" and item["reason"] == "claude_shortlist_limit"]
        )
        counts["claude_gate_reject"] += len(
            [item for item in records if item["deterministic_pass"] and item["research_pass"] and not item["claude_pass"]]
        )
        ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        return [{"label": label, "count": count} for label, count in ordered if count > 0]

    @staticmethod
    def _spread_bps(market: dict[str, Any]) -> float:
        books = [market.get("orderbook_summary_yes") or {}, market.get("orderbook_summary_no") or {}]
        spreads = [float(book.get("spread_bps") or 0.0) for book in books]
        return fmean(spreads) if spreads else 0.0

    @staticmethod
    def _time_to_expiry_hours(market: dict[str, Any]) -> float | None:
        candidates = [market.get("end_date"), market.get("endDate"), market.get("closeTime")]
        for value in candidates:
            if not value:
                continue
            try:
                raw = str(value).strip().replace("Z", "+00:00")
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return max((parsed.astimezone(UTC) - datetime.now(UTC)).total_seconds() / 3600, 0.0)
            except Exception:
                continue
        return None

    def _default_limit(self) -> int:
        agent_cfg = self.context.agents_config.agents.get("claude")
        return int(agent_cfg.scan_limit or 24) if agent_cfg else 24

    @staticmethod
    def _research_limit() -> int:
        return 8

    @staticmethod
    def _final_limit() -> int:
        return 4
