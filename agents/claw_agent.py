from __future__ import annotations

from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.exceptions import InvalidModelResponseError, RiskBlockedError
from core.market_connector import MarketConnector
from core.risk_engine import RiskEngine
from core.schemas import PaperOrderPayload, ReviewPayload
from core.utils import parse_json_object


SYSTEM_PROMPT = """
Você é um executor em paper trading.
Responda APENAS com JSON válido:
{"execute": true, "size": 100, "price_limit": 0.42, "reason": "..."}
"""


class ClawAgent(BaseAgent):
    def __init__(self, context: AppContext):
        super().__init__("claw", context)
        self.connector = MarketConnector(context)
        self.risk = RiskEngine(context)
        self.consumer = f"claw-{uuid4().hex[:8]}"

    async def tick(self) -> None:
        await self.context.bus.ensure_group("signals:reviewed", "claw_executors")
        events = await self.context.bus.read_group(
            "signals:reviewed",
            "claw_executors",
            self.consumer,
            block_ms=250,
            count=1,
        )
        for event_id, payload in events:
            review = ReviewPayload.model_validate(payload)
            try:
                if review.approved:
                    await self.execute(review)
            finally:
                await self.context.bus.ack("signals:reviewed", "claw_executors", event_id)

    async def execute(self, review: ReviewPayload) -> None:
        try:
            guard = await self.risk.build_execution_guard(review)
        except RiskBlockedError as exc:
            await self.risk.record_block(
                self.name,
                str(exc),
                {
                    "signal_id": review.signal_id,
                    "asset_symbol": review.asset_symbol,
                    "crypto_tier": review.crypto_tier,
                },
            )
            return

        signal = review.original_signal
        prompt = f"""
Signal:
- signal_id: {signal.signal_id}
- market_id: {signal.market_id}
- asset_symbol: {signal.asset_symbol}
- crypto_tier: {signal.crypto_tier}
- direction: {signal.direction}
- edge: {signal.edge:.4f}
- price: {signal.price:.4f}
- liquidity_summary: {signal.liquidity_summary}
- news_validation: {review.news_validation.model_dump(mode='json') if review.news_validation else {}}
- guarded_size: {guard.size}
- guarded_price_limit: {guard.price_limit:.4f}
"""
        response = await self.provider.call(prompt=prompt, system=SYSTEM_PROMPT)
        await self.cost_tracker.record(response, prompt_type="execute_order")

        try:
            payload = parse_json_object(response.content)
        except Exception as exc:
            raise InvalidModelResponseError(f"claw returned invalid JSON: {exc}") from exc

        if not payload.get("execute", True):
            await self.risk.record_block(
                self.name,
                str(payload.get("reason", "executor declined")),
                {
                    "signal_id": review.signal_id,
                    "asset_symbol": review.asset_symbol,
                    "crypto_tier": review.crypto_tier,
                },
            )
            return

        size = min(int(payload.get("size", guard.size)), guard.size)
        price_limit = min(float(payload.get("price_limit", guard.price_limit)), guard.price_limit)
        order_result = await self.connector.place_order(
            market_id=signal.market_id,
            token_id=signal.token_id,
            direction=signal.direction,
            size=size,
            price_limit=price_limit,
        )
        paper_order = PaperOrderPayload(
            order_id=str(uuid4()),
            signal_id=signal.signal_id,
            market_id=signal.market_id,
            token_id=signal.token_id,
            market_question=signal.market_question,
            asset_symbol=signal.asset_symbol,
            crypto_tier=signal.crypto_tier,
            direction=signal.direction,
            size=size,
            price_limit=price_limit,
            notional_usd=round(size * signal.price, 4),
            status=str(order_result["status"]),
            reason=str(payload.get("reason", "")),
            news_validation=review.news_validation.model_dump(mode="json") if review.news_validation else None,
        )
        await self.context.repository.record_paper_order(
            paper_order.order_id,
            paper_order.signal_id,
            paper_order.market_id,
            paper_order.status,
            paper_order.model_dump(mode="json"),
        )
        await self.context.bus.publish_event("orders:paper", paper_order.model_dump(mode="json"))

    async def close(self) -> None:
        await super().close()
        await self.connector.close()
