from __future__ import annotations

from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.exceptions import InvalidModelResponseError, RiskBlockedError
from core.risk_engine import RiskEngine
from core.schemas import NewsValidationPayload, ReviewPayload, SignalPayload
from core.utils import parse_json_object, sanitize_text


SYSTEM_PROMPT = """
Voce e um corretor operacional conservador.
Revise o sinal recebido e responda APENAS com JSON valido:
{"approved": true, "notes": "...", "corrected_price_limit": 0.55}
Voce pode rejeitar ou reduzir agressividade, nunca ampliar risco acima dos guardrails.
"""


class CodexAgent(BaseAgent):
    def __init__(self, context: AppContext):
        super().__init__("codex", context)
        self.risk = RiskEngine(context)
        self.consumer = f"codex-{uuid4().hex[:8]}"

    async def tick(self) -> None:
        await self.context.bus.ensure_group("signals:validated", "codex_reviewers")
        events = await self.context.bus.read_group(
            "signals:validated",
            "codex_reviewers",
            self.consumer,
            block_ms=250,
            count=1,
        )
        approved_count = 0
        rejected_count = 0
        llm_used_count = 0
        fallback_count = 0
        reviewed_assets: list[str] = []
        for event_id, payload in events:
            signal = SignalPayload.model_validate(payload)
            reviewed_assets.append(signal.asset_symbol)
            try:
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
                    await self.risk.record_block(
                        self.name,
                        review.notes or "review rejected signal",
                        {
                            "signal_id": signal.signal_id,
                            "market_id": signal.market_id,
                            "asset_symbol": signal.asset_symbol,
                            "crypto_tier": signal.crypto_tier,
                        },
                    )
            finally:
                await self.context.bus.ack("signals:validated", "codex_reviewers", event_id)
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
