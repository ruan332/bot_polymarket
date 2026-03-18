from __future__ import annotations

from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.exceptions import InvalidModelResponseError, RiskBlockedError
from core.market_connector import MarketConnector
from core.risk_engine import RiskEngine
from core.schemas import MarketSnapshotPayload, SignalPayload
from core.utils import parse_json_object, sanitize_text


SYSTEM_PROMPT = """
Você analisa mercados de predição.
Responda APENAS com JSON válido:
{"edge": 0.23, "direction": "YES", "confidence": 0.8, "reasoning": "..."}
"""


class ClaudeAgent(BaseAgent):
    def __init__(self, context: AppContext):
        super().__init__("claude", context)
        self.connector = MarketConnector(context)
        self.risk = RiskEngine(context)

    async def tick(self) -> None:
        agent_cfg = self.context.agents_config.agents[self.name]
        markets = await self.connector.get_active_markets(limit=agent_cfg.scan_limit or 20)
        await self.context.repository.record_market_snapshots(
            [
                MarketSnapshotPayload(
                    market_id=str(market["id"]),
                    question=str(market["question"]),
                    token_id_yes=str(market["token_id_yes"]),
                    token_id_no=str(market["token_id_no"]),
                    price_yes=float(market["price_yes"]),
                    price_no=float(market["price_no"]),
                    volume_24h=float(market["volume_24h"]),
                    metadata={"source": "gamma", "clob_token_ids": market.get("clob_token_ids", [])},
                )
                for market in markets
            ]
        )
        await self.context.repository.record_equity_snapshot(source="scan_cycle")
        for market in markets:
            signal = await self.calc_edge(market)
            if signal is None:
                continue
            try:
                await self.risk.validate_signal(signal)
            except RiskBlockedError as exc:
                await self.risk.record_block(
                    self.name,
                    str(exc),
                    {"signal_id": signal.signal_id, "market_id": signal.market_id},
                )
                continue

            await self.context.repository.record_signal(signal.signal_id, signal.event_type, signal.model_dump(mode="json"))
            await self.context.bus.publish_event("signals:created", signal.model_dump(mode="json"))

    async def calc_edge(self, market: dict) -> SignalPayload | None:
        prompt = f"""
Mercado: {sanitize_text(market['question'], 200)}
Preco YES: {market['price_yes']:.4f}
Preco NO: {market['price_no']:.4f}
Volume 24h: {market['volume_24h']:.2f}
Contexto: {sanitize_text(market.get('description', ''), 300)}
"""
        response = await self.provider.call(prompt=prompt, system=SYSTEM_PROMPT)
        await self.cost_tracker.record(response, prompt_type="scan_market")

        try:
            payload = parse_json_object(response.content)
            direction = payload["direction"]
            return SignalPayload(
                signal_id=str(uuid4()),
                market_id=market["id"],
                token_id=str(market["token_id_yes"] if direction == "YES" else market["token_id_no"]),
                market_question=market["question"],
                direction=direction,
                edge=float(payload["edge"]),
                confidence=float(payload["confidence"]),
                price=float(market[f"price_{direction.lower()}"]),
                price_yes=float(market["price_yes"]),
                price_no=float(market["price_no"]),
                reasoning=sanitize_text(payload.get("reasoning", ""), 300),
                metadata={"source": "claude_agent", "clob_token_ids": market.get("clob_token_ids", [])},
            )
        except Exception as exc:
            raise InvalidModelResponseError(f"claude returned invalid JSON: {exc}") from exc

    async def close(self) -> None:
        await super().close()
        await self.connector.close()
