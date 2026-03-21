from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.exceptions import RiskBlockedError
from core.market_connector import MarketConnector
from core.risk_engine import RiskEngine
from core.schemas import PaperOrderPayload, ReviewPayload


class ClawAgent(BaseAgent):
    def __init__(self, context: AppContext):
        super().__init__("claw", context)
        self.connector = MarketConnector(context)
        self.risk = RiskEngine(context)
        self.consumer = f"claw-{uuid4().hex[:8]}"

    async def tick(self) -> None:
        exit_stats = await self.process_exit_cycle()
        await self.context.bus.ensure_group("signals:reviewed", "claw_executors")
        events = await self.context.bus.read_group(
            "signals:reviewed",
            "claw_executors",
            self.consumer,
            block_ms=250,
            count=1,
        )
        executed_count = 0
        blocked_count = 0
        reviewed_assets: list[str] = []
        for event_id, payload in events:
            review = ReviewPayload.model_validate(payload)
            try:
                reviewed_assets.append(review.asset_symbol)
                if review.approved:
                    if await self.execute(review):
                        executed_count += 1
                    else:
                        blocked_count += 1
            finally:
                await self.context.bus.ack("signals:reviewed", "claw_executors", event_id)
        if events or exit_stats["exit_orders_count"] > 0:
            await self.context.repository.record_pipeline_telemetry(
                str(uuid4()),
                self.name,
                "executor.execute_cycle",
                {
                    "inbox_count": len(events),
                    "executed_count": executed_count,
                    "blocked_count": blocked_count,
                    "reviewed_assets": reviewed_assets[:6],
                    **exit_stats,
                },
            )

    async def execute(self, review: ReviewPayload) -> bool:
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
            return False

        signal = review.original_signal
        positions = await self.context.repository.get_open_positions()
        existing = next(
            (
                item
                for item in positions
                if str(item.get("market_id")) == signal.market_id and str(item.get("direction")) == signal.direction
            ),
            None,
        )
        action = "scale_in" if existing else "entry"
        size = guard.size
        price_limit = guard.price_limit
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
            action=action,
            position_key=f"{signal.market_id}:{signal.direction}",
            strategy_id=signal.strategy_id,
            regime=signal.regime,
            take_profit_price=review.take_profit_price,
            stop_loss_price=review.stop_loss_price,
            time_stop_minutes=review.time_stop_minutes,
            direction=signal.direction,
            size=size,
            price_limit=price_limit,
            notional_usd=round(size * signal.price, 4),
            realized_pnl_usd=0.0,
            status=str(order_result["status"]),
            reason=review.notes,
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
        return True

    async def process_exit_cycle(self) -> dict[str, object]:
        if not hasattr(self.context.repository, "get_open_positions"):
            return {"exit_orders_count": 0, "open_positions_seen": 0, "exit_actions": []}
        positions = await self.context.repository.get_open_positions()
        exit_orders_count = 0
        exit_actions: list[str] = []
        for position in positions:
            decision = self._exit_decision(position)
            if decision is None:
                continue
            exit_size = int(decision["size"])
            if exit_size <= 0:
                continue
            order_result = await self.connector.place_order(
                market_id=str(position["market_id"]),
                token_id=str(position["token_id"]),
                direction=str(position["direction"]),
                size=exit_size,
                price_limit=float(position["current_price"]),
            )
            realized = round(
                (float(position["current_price"]) - float(position["average_price"])) * exit_size,
                4,
            )
            payload = PaperOrderPayload(
                order_id=str(uuid4()),
                signal_id=str(uuid4()),
                market_id=str(position["market_id"]),
                token_id=str(position["token_id"]),
                market_question=str(position.get("market_question") or ""),
                asset_symbol=str(position.get("asset_symbol") or ""),
                crypto_tier=str(position.get("crypto_tier") or ""),
                action=str(decision["action"]),  # type: ignore[arg-type]
                position_key=str(position.get("position_key") or f"{position['market_id']}:{position['direction']}"),
                strategy_id=str(position.get("strategy_id") or ""),
                regime=str(position.get("regime") or ""),
                take_profit_price=position.get("take_profit_price"),
                stop_loss_price=position.get("stop_loss_price"),
                time_stop_minutes=position.get("time_stop_minutes"),
                direction=str(position["direction"]),  # type: ignore[arg-type]
                size=exit_size,
                price_limit=float(position["current_price"]),
                notional_usd=round(exit_size * float(position["current_price"]), 4),
                realized_pnl_usd=realized,
                exit_reason=str(decision["reason"]),
                status=str(order_result["status"]),
                reason=str(decision["reason"]),
                news_validation=None,
            )
            await self.context.repository.record_paper_order(
                payload.order_id,
                payload.signal_id,
                payload.market_id,
                payload.status,
                payload.model_dump(mode="json"),
            )
            await self.context.bus.publish_event("orders:paper", payload.model_dump(mode="json"))
            exit_orders_count += 1
            exit_actions.append(str(decision["reason"]))
        return {
            "exit_orders_count": exit_orders_count,
            "open_positions_seen": len(positions),
            "exit_actions": exit_actions[:6],
        }

    def _exit_decision(self, position: dict[str, object]) -> dict[str, object] | None:
        size = int(position.get("size") or 0)
        if size <= 0:
            return None
        current_price = float(position.get("current_price") or 0.0)
        average_price = float(position.get("average_price") or 0.0)
        take_profit = float(position.get("take_profit_price") or 0.0)
        stop_loss = float(position.get("stop_loss_price") or 0.0)
        time_stop_minutes = int(position.get("time_stop_minutes") or 0)
        scaled_out_count = int(position.get("scaled_out_count") or 0)
        latest_spread_bps = float(position.get("latest_spread_bps") or 0.0)
        holding_minutes = self._holding_minutes(position.get("opened_at"))

        if take_profit and current_price >= take_profit:
            return {"action": "close", "reason": "take_profit", "size": size}
        if stop_loss and current_price <= stop_loss:
            return {"action": "close", "reason": "stop_loss", "size": size}
        if time_stop_minutes and holding_minutes >= time_stop_minutes:
            return {"action": "close", "reason": "time_stop", "size": size}
        if (
            scaled_out_count == 0
            and latest_spread_bps >= self.context.risk_config.max_spread_bps * 0.75
            and current_price > average_price
            and size >= 2
        ):
            return {
                "action": "scale_out",
                "reason": "liquidity_exit",
                "size": max(int(size * self.context.risk_config.exit_scale_out_fraction), 1),
            }
        if (
            scaled_out_count == 0
            and time_stop_minutes
            and holding_minutes >= int(time_stop_minutes * 0.6)
            and current_price <= average_price
            and size >= 2
        ):
            return {
                "action": "scale_out",
                "reason": "confidence_decay",
                "size": max(int(size * self.context.risk_config.exit_scale_out_fraction), 1),
            }
        return None

    @staticmethod
    def _holding_minutes(value: object) -> int:
        if value in (None, ""):
            return 0
        if isinstance(value, datetime):
            created_at = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        else:
            created_at = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
        return max(int((datetime.now(UTC) - created_at).total_seconds() // 60), 0)

    async def close(self) -> None:
        await super().close()
        await self.connector.close()
