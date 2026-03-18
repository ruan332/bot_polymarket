from __future__ import annotations

from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.exceptions import InvalidModelResponseError, RiskBlockedError
from core.risk_engine import RiskEngine
from core.schemas import ReviewPayload, SignalPayload
from core.utils import parse_json_object


SYSTEM_PROMPT = """
Você é um corretor operacional.
Revise um sinal de trading e responda APENAS com JSON válido:
{"approved": true, "notes": "...", "corrected_price_limit": 0.55}
"""


class CodexAgent(BaseAgent):
    def __init__(self, context: AppContext):
        super().__init__("codex", context)
        self.risk = RiskEngine(context)
        self.consumer = f"codex-{uuid4().hex[:8]}"

    async def tick(self) -> None:
        await self.context.bus.ensure_group("signals:created", "codex_reviewers")
        events = await self.context.bus.read_group(
            "signals:created",
            "codex_reviewers",
            self.consumer,
            block_ms=250,
            count=1,
        )
        for event_id, payload in events:
            signal = SignalPayload.model_validate(payload)
            try:
                review = await self.review_signal(signal)
                if review.approved:
                    await self.context.repository.record_decision(
                        str(uuid4()),
                        signal.signal_id,
                        review.event_type,
                        review.model_dump(mode="json"),
                    )
                    await self.context.bus.publish_event("signals:reviewed", review.model_dump(mode="json"))
                else:
                    await self.risk.record_block(
                        self.name,
                        review.notes or "review rejected signal",
                        {"signal_id": signal.signal_id},
                    )
            finally:
                await self.context.bus.ack("signals:created", "codex_reviewers", event_id)

    async def review_signal(self, signal: SignalPayload) -> ReviewPayload:
        try:
            await self.risk.validate_signal(signal)
        except RiskBlockedError as exc:
            return ReviewPayload(signal_id=signal.signal_id, approved=False, notes=str(exc), original_signal=signal)

        portfolio = await self.risk.portfolio_state()
        kelly_size = self.risk.kelly_size(signal.edge, signal.price, portfolio.available_balance)
        prompt = f"""
Sinal:
- signal_id: {signal.signal_id}
- market_id: {signal.market_id}
- direction: {signal.direction}
- edge: {signal.edge:.4f}
- confidence: {signal.confidence:.4f}
- preco: {signal.price:.4f}
- kelly_size: {kelly_size}
"""
        response = await self.provider.call(prompt=prompt, system=SYSTEM_PROMPT)
        await self.cost_tracker.record(response, prompt_type="review_signal")

        try:
            payload = parse_json_object(response.content)
        except Exception as exc:
            raise InvalidModelResponseError(f"codex returned invalid JSON: {exc}") from exc

        corrected_price_limit = payload.get("corrected_price_limit")
        if corrected_price_limit is not None:
            corrected_price_limit = float(corrected_price_limit)

        return ReviewPayload(
            signal_id=signal.signal_id,
            approved=bool(payload.get("approved", True)) and kelly_size > 0,
            corrected_price_limit=corrected_price_limit,
            kelly_size=kelly_size,
            notes=str(payload.get("notes", "")),
            original_signal=signal,
        )
