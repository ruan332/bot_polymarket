from __future__ import annotations

from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.exceptions import RiskBlockedError
from core.risk_engine import RiskEngine
from core.schemas import NewsValidationPayload, ReviewPayload, SignalPayload


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
        reviewed_assets: list[str] = []
        for event_id, payload in events:
            signal = SignalPayload.model_validate(payload)
            reviewed_assets.append(signal.asset_symbol)
            try:
                review = await self.review_signal(signal)
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
        notes = (
            f"{signal.strategy_id} em regime {signal.regime} com posterior {signal.model_probability:.3f} "
            f"vs mercado {signal.market_probability:.3f}."
        )

        return ReviewPayload(
            signal_id=signal.signal_id,
            asset_symbol=signal.asset_symbol,
            crypto_tier=signal.crypto_tier,
            approved=kelly_size > 0,
            corrected_price_limit=corrected_price_limit,
            kelly_size=kelly_size,
            risk_fraction=round(risk_fraction, 4),
            take_profit_price=float(exit_plan["take_profit_price"]),
            stop_loss_price=float(exit_plan["stop_loss_price"]),
            time_stop_minutes=int(exit_plan["time_stop_minutes"]),
            notes=notes,
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
