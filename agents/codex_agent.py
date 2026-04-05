from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.exceptions import InvalidModelResponseError, RiskBlockedError
from core.risk_engine import RiskEngine
from core.schemas import (
    NewsValidationPayload,
    PairCycleStatePayload,
    PairLegPlan,
    PairReviewPayload,
    PairSignalPayload,
    ReviewPayload,
    SignalPayload,
)
from core.utils import parse_json_object, sanitize_text


SYSTEM_PROMPT = """
Voce e um corretor operacional conservador.
Revise o sinal recebido e responda APENAS com JSON valido:
{"approved": true, "notes": "...", "corrected_price_limit": 0.55}
Voce pode rejeitar ou reduzir agressividade, nunca ampliar risco acima dos guardrails.
"""


PAIR_SYSTEM_PROMPT = """
Voce revisa pair trades conservadores em mercados 15m up/down.
Responda APENAS com JSON valido:
{"approved": true, "notes": "...", "primary_price_limit": null, "hedge_price_limit": null}
Avalie o par pelo risco combinado, nao por tetos fixos arbitrarios por perna.
Se o plano original ja respeita os guardrails, prefira manter os precos originais.
So reduza limite de preco quando houver um motivo concreto de execucao, liquidez ou risco.
Nunca aumentar size ou preco acima do plano original.
"""

PAIR_PRICE_REDUCTION_RATIO_FLOOR = 0.90
PAIR_PRICE_REDUCTION_ABSOLUTE_FLOOR = 0.04


class CodexAgent(BaseAgent):
    def __init__(self, context: AppContext):
        super().__init__("codex", context)
        self.risk = RiskEngine(context)
        self.consumer = f"codex-{uuid4().hex[:8]}"

    async def tick(self) -> None:
        await self.context.bus.ensure_group("signals:flow_analyzed", "codex_reviewers")
        await self.context.bus.ensure_group("signals:validated", "codex_reviewers")
        events = await self.context.bus.read_group(
            "signals:flow_analyzed",
            "codex_reviewers",
            self.consumer,
            block_ms=250,
            count=1,
        )
        source_stream = "signals:flow_analyzed"
        if not events:
            events = await self.context.bus.read_group(
                "signals:validated",
                "codex_reviewers",
                self.consumer,
                block_ms=1,
                count=1,
            )
            source_stream = "signals:validated"
        approved_count = 0
        rejected_count = 0
        llm_used_count = 0
        fallback_count = 0
        reviewed_assets: list[str] = []
        for event_id, payload in events:
            event_type = str(payload.get("event_type") or "")
            try:
                if event_type == "pair_signal.created":
                    signal = PairSignalPayload.model_validate(payload)
                    reviewed_assets.append(signal.asset_symbol)
                    review = await self.review_pair_signal(signal)
                else:
                    signal = SignalPayload.model_validate(payload)
                    reviewed_assets.append(signal.asset_symbol)
                    review = await self.review_signal(signal)
                if review.review_mode == "llm":
                    llm_used_count += 1
                elif review.review_mode == "llm_fallback":
                    fallback_count += 1
                if review.approved:
                    approved_count += 1
                    await self.context.repository.record_decision(
                        str(uuid4()),
                        signal.signal_id,
                        review.event_type,
                        review.model_dump(mode="json"),
                    )
                    await self.context.bus.publish_event("signals:reviewed", review.model_dump(mode="json"))
                else:
                    rejected_count += 1
                    if event_type == "pair_signal.created":
                        await self._release_pair_cycle_count(
                            signal.asset_symbol,
                            signal.cycle_slug,
                            signal.primary_leg.direction,
                        )
                    await self.risk.record_block(
                        self.name,
                        review.notes or "review rejected signal",
                        {
                            "signal_id": signal.signal_id,
                            "market_id": signal.market_id,
                            "asset_symbol": signal.asset_symbol,
                            "crypto_tier": getattr(signal, "crypto_tier", None),
                        },
                    )
            finally:
                await self.context.bus.ack(source_stream, "codex_reviewers", event_id)
        if events:
            await self.context.repository.record_pipeline_telemetry(
                str(uuid4()),
                self.name,
                "reviewer.review_cycle",
                {
                    "inbox_count": len(events),
                    "approved_count": approved_count,
                    "rejected_count": rejected_count,
                    "llm_used_count": llm_used_count,
                    "fallback_count": fallback_count,
                    "reviewed_assets": reviewed_assets[:6],
                },
            )

    async def review_signal(self, signal: SignalPayload) -> ReviewPayload:
        try:
            await self.risk.validate_signal(signal)
        except RiskBlockedError as exc:
            return ReviewPayload(
                signal_id=signal.signal_id,
                asset_symbol=signal.asset_symbol,
                crypto_tier=signal.crypto_tier,
                approved=False,
                notes=str(exc),
                original_signal=signal,
            )

        portfolio = await self.risk.portfolio_state()
        risk_fraction = self.risk.kelly_fraction(signal.edge, signal.price)
        kelly_size = self.risk.kelly_size(signal.edge, signal.price, portfolio.available_balance)
        exit_plan = self.risk.build_exit_plan(signal)
        corrected_price_limit = min(
            signal.price + max(signal.expected_slippage_bps / 10000, self.context.risk_config.default_limit_buffer_bps / 10000),
            self.context.risk_config.max_order_price,
        )
        deterministic_notes = (
            f"{signal.strategy_id} em regime {signal.regime} com posterior {signal.model_probability:.3f} "
            f"vs mercado {signal.market_probability:.3f}."
        )
        approved = kelly_size > 0
        review_mode = "deterministic"
        llm_notes = ""

        if approved and self.context.settings.review_llm_enabled:
            try:
                payload = await self._review_with_llm(
                    signal=signal,
                    kelly_size=kelly_size,
                    risk_fraction=round(risk_fraction, 4),
                    corrected_price_limit=corrected_price_limit,
                    deterministic_notes=deterministic_notes,
                )
                approved = bool(payload.get("approved", True)) and approved
                corrected_candidate = payload.get("corrected_price_limit")
                if corrected_candidate is not None:
                    corrected_price_limit = min(
                        float(corrected_candidate),
                        corrected_price_limit,
                        self.context.risk_config.max_order_price,
                    )
                llm_notes = sanitize_text(str(payload.get("notes", "") or ""), 280)
                review_mode = "llm"
            except Exception as exc:
                if not self.context.settings.review_llm_fail_open:
                    approved = False
                llm_notes = sanitize_text(f"review LLM fallback: {exc}", 280)
                review_mode = "llm_fallback"

        return ReviewPayload(
            signal_id=signal.signal_id,
            asset_symbol=signal.asset_symbol,
            crypto_tier=signal.crypto_tier,
            approved=approved,
            corrected_price_limit=corrected_price_limit,
            kelly_size=kelly_size,
            risk_fraction=round(risk_fraction, 4),
            take_profit_price=float(exit_plan["take_profit_price"]),
            stop_loss_price=float(exit_plan["stop_loss_price"]),
            time_stop_minutes=int(exit_plan["time_stop_minutes"]),
            review_mode=review_mode,  # type: ignore[arg-type]
            notes=llm_notes or deterministic_notes,
            llm_notes=llm_notes,
            original_signal=signal,
            news_validation=(
                None
                if not signal.news_validation
                else NewsValidationPayload(
                    validation_id="attached",
                    signal_id=signal.signal_id,
                    market_id=signal.market_id,
                    asset_symbol=signal.asset_symbol,
                    asset_name=signal.asset_name,
                    crypto_tier=signal.crypto_tier,
                    validated=bool(signal.news_validation.get("validated")),
                    support_score=float(signal.news_validation.get("support_score", 0.0)),
                    conflict_score=float(signal.news_validation.get("conflict_score", 0.0)),
                    source_count=int(signal.news_validation.get("source_count", 0)),
                    provider_used=str(signal.news_validation.get("provider_used", "")),
                    fallback_used=bool(signal.news_validation.get("fallback_used", False)),
                    provider_attempts=list(signal.news_validation.get("provider_attempts", [])),
                    primary_error_type=(
                        None
                        if signal.news_validation.get("primary_error_type") in (None, "")
                        else str(signal.news_validation.get("primary_error_type"))
                    ),
                    primary_error_message=(
                        None
                        if signal.news_validation.get("primary_error_message") in (None, "")
                        else str(signal.news_validation.get("primary_error_message"))
                    ),
                    freshness_minutes=signal.news_validation.get("freshness_minutes"),
                    headlines=list(signal.news_validation.get("headlines", [])),
                    reason=str(signal.news_validation.get("reason", "")),
                )
            ),
        )

    async def review_pair_signal(self, signal: PairSignalPayload) -> PairReviewPayload:
        try:
            await self.risk.validate_pair_signal(signal)
        except RiskBlockedError as exc:
            return PairReviewPayload(
                signal_id=signal.signal_id,
                trade_group_id=signal.trade_group_id,
                asset_symbol=signal.asset_symbol,
                crypto_tier=signal.crypto_tier,
                approved=False,
                notes=str(exc),
                approved_primary_leg=signal.primary_leg,
                approved_hedge_leg=signal.hedge_leg,
                original_signal=signal,
            )

        approved = True
        approved_primary = signal.primary_leg.model_copy()
        approved_hedge = signal.hedge_leg.model_copy()
        review_mode = "deterministic"
        deterministic_notes = (
            f"pair_15m {signal.asset_symbol} {signal.cycle_slug} com sinal {signal.predictor_signal} "
            f"e confianca {signal.predictor_confidence:.3f}."
        )
        llm_notes = ""
        if approved and self.context.settings.review_llm_enabled:
            try:
                payload = await self._review_pair_with_llm(signal=signal, deterministic_notes=deterministic_notes)
                approved = bool(payload.get("approved", True)) and approved
                approved_primary = self._pair_leg_with_price(
                    approved_primary,
                    payload.get("primary_price_limit"),
                    signal.primary_leg.target_price,
                )
                approved_hedge = self._pair_leg_with_price(
                    approved_hedge,
                    payload.get("hedge_price_limit"),
                    signal.hedge_leg.target_price,
                )
                llm_notes = sanitize_text(str(payload.get("notes", "") or ""), 280)
                review_mode = "llm"
            except Exception as exc:
                if not self.context.settings.review_llm_fail_open:
                    approved = False
                llm_notes = sanitize_text(f"review LLM fallback: {exc}", 280)
                review_mode = "llm_fallback"

        return PairReviewPayload(
            signal_id=signal.signal_id,
            trade_group_id=signal.trade_group_id,
            asset_symbol=signal.asset_symbol,
            crypto_tier=signal.crypto_tier,
            approved=approved,
            review_mode=review_mode,  # type: ignore[arg-type]
            notes=llm_notes or deterministic_notes,
            llm_notes=llm_notes,
            approved_primary_leg=approved_primary,
            approved_hedge_leg=approved_hedge,
            original_signal=signal,
        )

    async def _review_with_llm(
        self,
        *,
        signal: SignalPayload,
        kelly_size: int,
        risk_fraction: float,
        corrected_price_limit: float,
        deterministic_notes: str,
    ) -> dict:
        prompt = f"""
Sinal:
- signal_id: {signal.signal_id}
- market_id: {signal.market_id}
- asset_symbol: {signal.asset_symbol}
- crypto_tier: {signal.crypto_tier}
- direction: {signal.direction}
- edge: {signal.edge:.4f}
- confidence: {signal.confidence:.4f}
- price: {signal.price:.4f}
- volume_24h: {signal.volume_24h:.2f}
- market_probability: {signal.market_probability:.4f}
- model_probability: {signal.model_probability:.4f}
- expected_slippage_bps: {signal.expected_slippage_bps:.2f}
- expected_holding_minutes: {signal.expected_holding_minutes}
- liquidity_summary: {signal.liquidity_summary}
- features_summary: {signal.features_summary}
- news_validation: {signal.news_validation}

Guardrails:
- max_kelly_size: {kelly_size}
- risk_fraction: {risk_fraction:.4f}
- max_price_limit: {corrected_price_limit:.4f}
- deterministic_notes: {sanitize_text(deterministic_notes, 240)}
"""
        response = await self.provider.call(prompt=prompt, system=SYSTEM_PROMPT)
        await self.cost_tracker.record(response, prompt_type="review_signal")
        try:
            return parse_json_object(response.content)
        except Exception as exc:
            raise InvalidModelResponseError(f"codex returned invalid JSON: {exc}") from exc

    async def _review_pair_with_llm(self, *, signal: PairSignalPayload, deterministic_notes: str) -> dict:
        pair_total_target = signal.primary_leg.target_price + signal.hedge_leg.target_price
        prompt = f"""
Pair signal:
- signal_id: {signal.signal_id}
- trade_group_id: {signal.trade_group_id}
- market_id: {signal.market_id}
- cycle_slug: {signal.cycle_slug}
- asset_symbol: {signal.asset_symbol}
- crypto_tier: {signal.crypto_tier}
- predictor_signal: {signal.predictor_signal}
- predictor_confidence: {signal.predictor_confidence:.4f}
- primary_leg: {signal.primary_leg.model_dump(mode='json')}
- hedge_leg: {signal.hedge_leg.model_dump(mode='json')}
- side_count_state: {signal.side_count_state}
- reasoning: {sanitize_text(signal.reasoning, 240)}

Guardrails:
- max_order_price: {self.context.risk_config.max_order_price:.4f}
- pair_total_target: {pair_total_target:.4f}
- deterministic_notes: {sanitize_text(deterministic_notes, 240)}
"""
        response = await self.provider.call(prompt=prompt, system=PAIR_SYSTEM_PROMPT)
        await self.cost_tracker.record(response, prompt_type="review_pair_signal")
        try:
            return parse_json_object(response.content)
        except Exception as exc:
            raise InvalidModelResponseError(f"codex returned invalid JSON for pair signal: {exc}") from exc

    def _pair_leg_with_price(self, leg: PairLegPlan, candidate: object, original_cap: float) -> PairLegPlan:
        if candidate in (None, ""):
            return leg
        corrected = min(float(candidate), original_cap, self.context.risk_config.max_order_price)
        min_viable = max(
            0.01,
            original_cap * PAIR_PRICE_REDUCTION_RATIO_FLOOR,
            original_cap - PAIR_PRICE_REDUCTION_ABSOLUTE_FLOOR,
        )
        if corrected < min_viable:
            return leg
        return leg.model_copy(update={"target_price": round(corrected, 4)})

    async def _release_pair_cycle_count(self, asset_symbol: str, cycle_slug: str, direction: str) -> None:
        state = await self.context.repository.get_pair_cycle(asset_symbol)
        if not state or str(state.get("cycle_slug") or "") != cycle_slug:
            return
        side_counts = {key: int(value) for key, value in (state.get("side_counts") or {}).items()}
        side_key = direction.lower()
        current = int(side_counts.get(side_key, 0))
        if current <= 0:
            return
        side_counts[side_key] = current - 1
        payload = PairCycleStatePayload(
            asset_symbol=str(state["asset_symbol"]),
            asset_name=str(state.get("asset_name") or asset_symbol),
            crypto_tier=str(state.get("crypto_tier") or "btc"),  # type: ignore[arg-type]
            cycle_slug=str(state["cycle_slug"]),
            cycle_start=state["cycle_start"],
            market_id=str(state["market_id"]),
            market_question=str(state.get("market_question") or ""),
            token_id_yes=str(state.get("token_id_yes") or ""),
            token_id_no=str(state.get("token_id_no") or ""),
            price_yes=float(state.get("price_yes") or 0.0),
            price_no=float(state.get("price_no") or 0.0),
            status=str(state.get("status") or "active"),  # type: ignore[arg-type]
            side_counts=side_counts,
            max_buy_counts_per_side=int(state.get("max_buy_counts_per_side") or 1),
            last_signal_direction=state.get("last_signal_direction"),
            last_signal_at=state.get("last_signal_at"),
            last_quote_at=state.get("last_quote_at"),
            predictor_state=dict(state.get("predictor_state") or {}),
            metadata=dict(state.get("metadata") or {}),
            updated_at=datetime.now(UTC),
        )
        await self.context.repository.upsert_pair_cycle(payload)
