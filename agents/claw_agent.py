from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.exceptions import InvalidModelResponseError, RiskBlockedError
from core.market_connector import MarketConnector
from core.risk_engine import RiskEngine
from core.settlement import SettlementService
from core.schemas import PairCycleStatePayload, PairOrderPayload, PairReviewPayload, PaperOrderPayload, PendingPairOrderPayload, ReviewPayload
from core.utils import parse_json_object, sanitize_text


SYSTEM_PROMPT = """
Voce e um executor em paper trading conservador.
Responda APENAS com JSON valido:
{"execute": true, "size": 100, "price_limit": 0.42, "reason": "..."}
Nunca aumente size ou price_limit acima dos limites ja calculados.
"""


class ClawAgent(BaseAgent):
    def __init__(self, context: AppContext):
        super().__init__("claw", context)
        self.connector = MarketConnector(context)
        self.risk = RiskEngine(context)
        self.settlement = SettlementService(context, self.connector)
        self.consumer = f"claw-{uuid4().hex[:8]}"

    async def _place_order_or_block(
        self,
        *,
        market_id: str,
        token_id: str,
        direction: str,
        size: int,
        price_limit: float,
        open_position: bool = True,
        reason: str,
        details: dict[str, object],
    ) -> dict[str, object]:
        if self.context.settings.live_trading:
            notional = round(float(size) * float(price_limit), 4)
            if notional < 1.0:
                message = f"live order below Polymarket minimum size: ${notional:.2f} < $1.00"
                await self.risk.record_block(
                    self.name,
                    message,
                    {
                        **details,
                        "market_id": market_id,
                        "token_id": token_id,
                        "direction": direction,
                        "size": size,
                        "price_limit": price_limit,
                        "reason": reason,
                        "notional_usd": notional,
                        "min_notional_usd": 1.0,
                    },
                )
                return {
                    "status": "blocked",
                    "error": message,
                    "exchange_order_id": "",
                }
        try:
            return await self.connector.place_order(
                market_id=market_id,
                token_id=token_id,
                direction=direction,
                size=size,
                price_limit=price_limit,
                open_position=open_position,
            )
        except Exception as exc:
            await self.risk.record_block(
                self.name,
                f"exchange order failed: {exc}",
                {
                    **details,
                    "market_id": market_id,
                    "token_id": token_id,
                    "direction": direction,
                    "size": size,
                    "price_limit": price_limit,
                    "reason": reason,
                },
            )
            return {
                "status": "blocked",
                "error": str(exc),
                "exchange_order_id": "",
            }

    async def tick(self) -> None:
        exit_stats = await self.process_exit_cycle()
        pending_stats = await self.process_pending_pair_orders()
        settlement_stats = await self.settlement.process_redeem_cycle(limit=10)
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
        llm_used_count = 0
        fallback_count = 0
        reviewed_assets: list[str] = []
        for event_id, payload in events:
            event_type = str(payload.get("event_type") or "")
            try:
                if event_type == "pair_signal.reviewed":
                    review = PairReviewPayload.model_validate(payload)
                    reviewed_assets.append(review.asset_symbol)
                    if review.approved:
                        executed, execution_mode = await self.execute_pair(review)
                    else:
                        executed, execution_mode = False, "deterministic"
                else:
                    review = ReviewPayload.model_validate(payload)
                    reviewed_assets.append(review.asset_symbol)
                    if review.approved:
                        executed, execution_mode = await self.execute(review)
                    else:
                        executed, execution_mode = False, "deterministic"
                if execution_mode == "llm":
                    llm_used_count += 1
                elif execution_mode == "llm_fallback":
                    fallback_count += 1
                if executed:
                    executed_count += 1
                elif getattr(review, "approved", False):
                    blocked_count += 1
            finally:
                await self.context.bus.ack("signals:reviewed", "claw_executors", event_id)
        if (
            events
            or exit_stats["exit_orders_count"] > 0
            or pending_stats["filled_hedges"] > 0
            or pending_stats["cancelled_hedges"] > 0
            or settlement_stats["processed_count"] > 0
        ):
            await self.context.repository.record_pipeline_telemetry(
                str(uuid4()),
                self.name,
                "executor.execute_cycle",
                {
                    "inbox_count": len(events),
                    "executed_count": executed_count,
                    "blocked_count": blocked_count,
                    "llm_used_count": llm_used_count,
                    "fallback_count": fallback_count,
                    "reviewed_assets": reviewed_assets[:6],
                    **exit_stats,
                    **pending_stats,
                    **settlement_stats,
                },
            )

    async def execute(self, review: ReviewPayload) -> tuple[bool, str]:
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
            return False, "deterministic"

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
        reason = review.notes
        execution_mode = "deterministic"

        if self.context.settings.execution_llm_enabled:
            try:
                payload = await self._execute_with_llm(
                    review=review,
                    action=action,
                    guard_size=guard.size,
                    guard_price_limit=guard.price_limit,
                )
                if not bool(payload.get("execute", True)):
                    await self.risk.record_block(
                        self.name,
                        str(payload.get("reason", "executor declined")),
                        {
                            "signal_id": review.signal_id,
                            "asset_symbol": review.asset_symbol,
                            "crypto_tier": review.crypto_tier,
                        },
                    )
                    return False, "llm"
                size = min(max(int(payload.get("size", guard.size)), 1), guard.size)
                price_limit = min(float(payload.get("price_limit", guard.price_limit)), guard.price_limit)
                reason = sanitize_text(str(payload.get("reason", "") or review.notes), 280)
                execution_mode = "llm"
            except Exception as exc:
                if not self.context.settings.execution_llm_fail_open:
                    await self.risk.record_block(
                        self.name,
                        f"execution LLM fallback: {exc}",
                        {
                            "signal_id": review.signal_id,
                            "asset_symbol": review.asset_symbol,
                            "crypto_tier": review.crypto_tier,
                        },
                    )
                    return False, "llm_fallback"
                reason = sanitize_text(f"{review.notes} | execution LLM fallback: {exc}", 280)
                execution_mode = "llm_fallback"

        order_result = await self._place_order_or_block(
            market_id=signal.market_id,
            token_id=signal.token_id,
            direction=signal.direction,
            size=size,
            price_limit=price_limit,
            reason=reason,
            details={
                "signal_id": signal.signal_id,
                "asset_symbol": signal.asset_symbol,
                "crypto_tier": signal.crypto_tier,
                "strategy_id": signal.strategy_id,
            },
        )
        if str(order_result.get("status")) == "blocked" and order_result.get("error"):
            return False, execution_mode
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
            entry_notional_target_usd=(
                guard.entry_notional_target_usd if signal.strategy_id == "momentum_15m" and guard.entry_notional_target_usd > 0 else None
            ),
            entry_notional_actual_usd=round(size * price_limit, 4),
            take_profit_target_usd=(
                float(getattr(self.context.settings, "momentum_take_profit_usd", 1.0) or 1.0)
                if signal.strategy_id == "momentum_15m"
                else None
            ),
            realized_pnl_usd=0.0,
            execution_mode=execution_mode,  # type: ignore[arg-type]
            status=str(order_result["status"]),
            reason=reason,
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
        return True, execution_mode

    async def execute_pair(self, review: PairReviewPayload) -> tuple[bool, str]:
        try:
            guard = await self.risk.build_pair_execution_guard(review)
        except RiskBlockedError as exc:
            await self._release_pair_cycle_count(
                review.original_signal.asset_symbol,
                review.original_signal.cycle_slug,
                review.approved_primary_leg.direction,
            )
            await self.risk.record_block(
                self.name,
                str(exc),
                {
                    "signal_id": review.signal_id,
                    "asset_symbol": review.asset_symbol,
                    "crypto_tier": review.crypto_tier,
                },
            )
            return False, "deterministic"

        signal = review.original_signal
        primary_leg = review.approved_primary_leg
        order_result = await self._place_order_or_block(
            market_id=signal.market_id,
            token_id=primary_leg.token_id,
            direction=primary_leg.direction,
            size=primary_leg.size,
            price_limit=primary_leg.target_price,
            open_position=True,
            reason=review.notes,
            details={
                "signal_id": signal.signal_id,
                "trade_group_id": review.trade_group_id,
                "asset_symbol": signal.asset_symbol,
                "crypto_tier": signal.crypto_tier,
                "strategy_id": signal.strategy_id,
                "cycle_slug": signal.cycle_slug,
                "leg_role": "primary",
            },
        )
        if str(order_result.get("status")) == "blocked" and order_result.get("error"):
            return False, "deterministic"
        hedge_submission_reason = "hedge_submitted"
        try:
            hedge_submission = await self._place_order_or_block(
                market_id=signal.market_id,
                token_id=review.approved_hedge_leg.token_id,
                direction=review.approved_hedge_leg.direction,
                size=review.approved_hedge_leg.size,
                price_limit=review.approved_hedge_leg.target_price,
                open_position=False,
                reason=review.notes,
                details={
                    "signal_id": signal.signal_id,
                    "trade_group_id": review.trade_group_id,
                    "asset_symbol": signal.asset_symbol,
                    "crypto_tier": signal.crypto_tier,
                    "strategy_id": signal.strategy_id,
                    "cycle_slug": signal.cycle_slug,
                    "leg_role": "hedge",
                },
            )
            if str(hedge_submission.get("status")) == "blocked" and hedge_submission.get("error"):
                hedge_submission_reason = "hedge_submission_failed"
        except Exception:
            hedge_submission_reason = "hedge_submission_failed"
            hedge_submission = {
                "status": "blocked",
                "error": "unexpected hedge submission failure",
                "exchange_order_id": "",
            }
        primary_order = PairOrderPayload(
            order_id=str(uuid4()),
            signal_id=signal.signal_id,
            trade_group_id=review.trade_group_id,
            cycle_slug=signal.cycle_slug,
            market_id=signal.market_id,
            token_id=primary_leg.token_id,
            market_question=signal.market_question,
            asset_symbol=signal.asset_symbol,
            crypto_tier=signal.crypto_tier,
            position_key=guard.primary_position_key,
            leg_role="primary",
            direction=primary_leg.direction,
            size=primary_leg.size,
            price_limit=primary_leg.target_price,
            reference_price=primary_leg.reference_price,
            notional_usd=guard.primary_notional_usd,
            hedge_status=hedge_submission_reason,
            status=str(order_result["status"]),
            reason=review.notes,
        )
        await self.context.repository.record_paper_order(
            primary_order.order_id,
            primary_order.signal_id,
            primary_order.market_id,
            primary_order.status,
            {
                **primary_order.model_dump(mode="json"),
                "action": "entry",
                "strategy_id": "pair_15m",
                "trade_group_id": review.trade_group_id,
                "cycle_slug": signal.cycle_slug,
                "leg_role": "primary",
            },
        )
        await self.context.bus.publish_event("orders:paper", primary_order.model_dump(mode="json"))
        pending = PendingPairOrderPayload(
            pending_order_id=str(uuid4()),
            trade_group_id=review.trade_group_id,
            signal_id=signal.signal_id,
            cycle_slug=signal.cycle_slug,
            market_id=signal.market_id,
            market_question=signal.market_question,
            asset_symbol=signal.asset_symbol,
            crypto_tier=signal.crypto_tier,
            position_key=guard.hedge_position_key,
            token_id=review.approved_hedge_leg.token_id,
            direction=review.approved_hedge_leg.direction,
            size=review.approved_hedge_leg.size,
            target_price=review.approved_hedge_leg.target_price,
            reference_price=review.approved_hedge_leg.reference_price,
            exchange_order_id=str(hedge_submission.get("exchange_order_id") or ""),
            submission_status=str(hedge_submission.get("status") or ""),
            submission_created_at=datetime.now(UTC),
            status="pending" if hedge_submission_reason == "hedge_submitted" else "cancelled",
            reason=hedge_submission_reason,
            metadata={"submission": hedge_submission},
        )
        await self.context.repository.upsert_pending_pair_order(pending)
        return True, "deterministic"

    async def process_pending_pair_orders(self) -> dict[str, object]:
        if not hasattr(self.context.repository, "list_pending_pair_orders"):
            return {"filled_hedges": 0, "cancelled_hedges": 0, "pending_pair_orders": 0, "hedge_actions": []}
        pending_orders = await self.context.repository.list_pending_pair_orders()
        filled_hedges = 0
        cancelled_hedges = 0
        hedge_actions: list[str] = []
        for pending in pending_orders:
            cycle_state = await self.context.repository.get_pair_cycle(str(pending.get("asset_symbol") or ""))
            if cycle_state and str(cycle_state.get("cycle_slug") or "") != str(pending.get("cycle_slug") or ""):
                await self._cancel_pending_live_order(pending, reason="cycle_rollover")
                await self.context.repository.resolve_pending_pair_order(
                    str(pending["pending_order_id"]),
                    status="expired",
                    reason="cycle_rollover",
                    payload={"resolved_at": datetime.now(UTC).isoformat()},
                )
                cancelled_hedges += 1
                hedge_actions.append("cycle_rollover")
                continue
            created_at = pending.get("created_at")
            if created_at and self._holding_minutes(created_at) >= 20:
                await self._cancel_pending_live_order(pending, reason="hedge_timeout")
                await self.context.repository.resolve_pending_pair_order(
                    str(pending["pending_order_id"]),
                    status="cancelled",
                    reason="hedge_timeout",
                    payload={"resolved_at": datetime.now(UTC).isoformat()},
                )
                cancelled_hedges += 1
                hedge_actions.append("hedge_timeout")
                continue
            live_status = await self._live_pending_fill_state(pending)
            if live_status is not None:
                if live_status["state"] == "open":
                    continue
                if live_status["state"] == "cancelled":
                    await self.context.repository.resolve_pending_pair_order(
                        str(pending["pending_order_id"]),
                        status="cancelled",
                        reason=str(live_status["reason"]),
                        payload=dict(live_status.get("payload") or {}),
                    )
                    cancelled_hedges += 1
                    hedge_actions.append(str(live_status["reason"]))
                    continue
                fill_state = live_status
            else:
                fill_state = await self._paper_pending_fill_state(pending)
                if fill_state is None:
                    continue

            target_price = float(fill_state.get("fill_price") or pending.get("target_price") or 0.0)
            hedge_order = PairOrderPayload(
                order_id=str(uuid4()),
                signal_id=str(pending["signal_id"]),
                trade_group_id=str(pending["trade_group_id"]),
                cycle_slug=str(pending["cycle_slug"]),
                market_id=str(pending["market_id"]),
                token_id=str(pending["token_id"]),
                market_question=str(pending.get("market_question") or ""),
                asset_symbol=str(pending.get("asset_symbol") or ""),
                crypto_tier=pending.get("crypto_tier"),
                position_key=str(pending.get("position_key") or ""),
                leg_role="hedge",
                direction=str(pending["direction"]),  # type: ignore[arg-type]
                size=int(pending["size"]),
                price_limit=target_price,
                reference_price=float(pending.get("reference_price") or 0.0),
                notional_usd=round(int(pending["size"]) * target_price, 4),
                hedge_status="hedge_filled",
                status=str(fill_state.get("order_status") or "simulated"),
                reason="hedge_filled",
            )
            await self.context.repository.record_paper_order(
                hedge_order.order_id,
                hedge_order.signal_id,
                hedge_order.market_id,
                hedge_order.status,
                {
                    **hedge_order.model_dump(mode="json"),
                    "action": "entry",
                    "strategy_id": "pair_15m",
                    "trade_group_id": str(pending["trade_group_id"]),
                    "cycle_slug": str(pending["cycle_slug"]),
                    "leg_role": "hedge",
                },
            )
            await self.context.repository.resolve_pending_pair_order(
                str(pending["pending_order_id"]),
                status="filled",
                reason="hedge_filled",
                payload={
                    "exchange_order_id": str(pending.get("exchange_order_id") or ""),
                    "fill_order_id": hedge_order.order_id,
                    "fill_price": target_price,
                    "fill_source": str(fill_state.get("fill_source") or "unknown"),
                    "resolved_at": datetime.now(UTC).isoformat(),
                },
            )
            await self.context.bus.publish_event("orders:paper", hedge_order.model_dump(mode="json"))
            filled_hedges += 1
            hedge_actions.append("hedge_filled")
        return {
            "filled_hedges": filled_hedges,
            "cancelled_hedges": cancelled_hedges,
            "pending_pair_orders": len(pending_orders),
            "hedge_actions": hedge_actions[:6],
        }

    async def _paper_pending_fill_state(self, pending: dict[str, object]) -> dict[str, object] | None:
        snapshot = await self.context.repository.get_latest_market_snapshot(str(pending["market_id"]))
        if snapshot is None:
            return None
        metadata = snapshot.get("metadata") or {}
        summary_key = "orderbook_summary_yes" if str(pending.get("direction")) == "YES" else "orderbook_summary_no"
        summary = metadata.get(summary_key) or {}
        current_ask = float(summary.get("best_ask") or 0.0)
        if current_ask <= 0:
            current_ask = float(snapshot["price_yes"] if str(pending.get("direction")) == "YES" else snapshot["price_no"])
        target_price = float(pending.get("target_price") or 0.0)
        if current_ask <= 0 or current_ask > target_price:
            return None
        return {
            "state": "filled",
            "fill_price": target_price,
            "fill_source": "market_snapshot",
            "order_status": "simulated",
        }

    async def _live_pending_fill_state(self, pending: dict[str, object]) -> dict[str, object] | None:
        exchange_order_id = str(pending.get("exchange_order_id") or "")
        if not self.context.settings.live_trading or not exchange_order_id:
            return None
        order = await self.connector.get_order(exchange_order_id)
        if not order:
            return None
        status = str(order.get("status") or order.get("order_status") or "").upper()
        size_matched = self._as_float(
            order.get("size_matched") or order.get("sizeMatched") or order.get("filled_size") or order.get("matched_size")
        )
        requested_size = max(self._as_float(order.get("size") or order.get("original_size")), float(pending.get("size") or 0))
        fill_price = self._as_float(
            order.get("avgPrice") or order.get("avg_price") or order.get("price") or pending.get("target_price")
        )
        if status in {"FILLED", "MATCHED"} or (requested_size > 0 and size_matched >= requested_size):
            return {
                "state": "filled",
                "fill_price": fill_price or float(pending.get("target_price") or 0.0),
                "fill_source": "exchange_status",
                "order_status": "live_filled",
            }
        if status in {"CANCELLED", "CANCELED", "REJECTED", "FAILED"}:
            return {
                "state": "cancelled",
                "reason": status.lower(),
                "payload": {"exchange_order_id": exchange_order_id, "exchange_status": status},
            }
        return {"state": "open", "fill_source": "exchange_status", "order_status": "live_submitted"}

    async def _cancel_pending_live_order(self, pending: dict[str, object], *, reason: str) -> None:
        exchange_order_id = str(pending.get("exchange_order_id") or "")
        if not self.context.settings.live_trading or not exchange_order_id:
            return
        try:
            response = await self.connector.cancel_order(exchange_order_id)
            await self.context.repository.resolve_pending_pair_order(
                str(pending["pending_order_id"]),
                status="cancelled" if reason == "hedge_timeout" else "expired",
                reason=reason,
                payload={
                    "exchange_order_id": exchange_order_id,
                    "cancel_response": response or {},
                    "cancel_requested_at": datetime.now(UTC).isoformat(),
                },
            )
        except Exception:
            return

    async def process_exit_cycle(self) -> dict[str, object]:
        if not hasattr(self.context.repository, "get_open_positions"):
            return {"exit_orders_count": 0, "open_positions_seen": 0, "exit_actions": []}
        positions = await self.context.repository.get_open_positions()
        exit_orders_count = 0
        exit_actions: list[str] = []
        pair_cycle_cache: dict[str, dict[str, object] | None] = {}
        for position in positions:
            decision = await self._pair_exit_decision(position, pair_cycle_cache)
            if decision is None:
                decision = self._exit_decision(position)
            if decision is None:
                continue
            exit_size = int(decision["size"])
            if exit_size <= 0:
                continue
            order_result = await self._place_order_or_block(
                market_id=str(position["market_id"]),
                token_id=str(position["token_id"]),
                direction=str(position["direction"]),
                size=exit_size,
                price_limit=float(position["current_price"]),
                reason=str(decision["reason"]),
                details={
                    "position_key": str(position.get("position_key") or ""),
                    "asset_symbol": str(position.get("asset_symbol") or ""),
                    "crypto_tier": str(position.get("crypto_tier") or ""),
                    "strategy_id": str(position.get("strategy_id") or ""),
                    "leg_role": str(position.get("leg_role") or ""),
                },
            )
            if str(order_result.get("status")) == "blocked" and order_result.get("error"):
                continue
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
                trade_group_id=str(position.get("trade_group_id") or ""),
                cycle_slug=str(position.get("cycle_slug") or ""),
                leg_role=str(position.get("leg_role") or ""),
                take_profit_price=position.get("take_profit_price"),
                stop_loss_price=position.get("stop_loss_price"),
                time_stop_minutes=position.get("time_stop_minutes"),
                direction=str(position["direction"]),  # type: ignore[arg-type]
                size=exit_size,
                price_limit=float(position["current_price"]),
                notional_usd=round(exit_size * float(position["current_price"]), 4),
                entry_notional_target_usd=position.get("entry_notional_target_usd"),
                entry_notional_actual_usd=position.get("entry_notional_actual_usd"),
                take_profit_target_usd=position.get("take_profit_target_usd"),
                realized_pnl_usd=realized,
                exit_reason=str(decision["reason"]),
                execution_mode="deterministic",
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

    async def _pair_exit_decision(
        self,
        position: dict[str, object],
        pair_cycle_cache: dict[str, dict[str, object] | None],
    ) -> dict[str, object] | None:
        if str(position.get("strategy_id") or "") != "pair_15m":
            return None
        size = int(position.get("size") or 0)
        if size <= 0:
            return None
        asset_symbol = str(position.get("asset_symbol") or "")
        cycle_slug = str(position.get("cycle_slug") or "")
        if asset_symbol and hasattr(self.context.repository, "get_pair_cycle"):
            if asset_symbol not in pair_cycle_cache:
                pair_cycle_cache[asset_symbol] = await self.context.repository.get_pair_cycle(asset_symbol)
            cycle_state = pair_cycle_cache[asset_symbol]
            if cycle_state and str(cycle_state.get("cycle_slug") or "") not in {"", cycle_slug}:
                return {"action": "close", "reason": "cycle_rollover", "size": size}
        if self._holding_minutes(position.get("opened_at")) >= 20:
            return {"action": "close", "reason": "cycle_timeout", "size": size}
        return None

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
        strategy_id = str(position.get("strategy_id") or "")

        if strategy_id == "momentum_15m":
            take_profit_target_usd = float(getattr(self.context.settings, "momentum_take_profit_usd", 1.0) or 1.0)
            if take_profit_target_usd > 0:
                pnl_bruto = (current_price - average_price) * size
                if pnl_bruto >= take_profit_target_usd:
                    return {"action": "close", "reason": "take_profit_usd", "size": size}

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

    @staticmethod
    def _as_float(value: object) -> float:
        if value in (None, ""):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

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
            max_buy_counts_per_side=int(state.get("max_buy_counts_per_side") or self.context.settings.copytrade_max_buy_counts_per_side),
            last_signal_direction=direction,  # type: ignore[arg-type]
            last_signal_at=datetime.now(UTC),
            last_quote_at=state.get("last_quote_at"),
            predictor_state=dict(state.get("predictor_state") or {}),
            metadata=dict(state.get("metadata") or {}),
            updated_at=datetime.now(UTC),
        )
        await self.context.repository.upsert_pair_cycle(payload)

    async def _execute_with_llm(
        self,
        *,
        review: ReviewPayload,
        action: str,
        guard_size: int,
        guard_price_limit: float,
    ) -> dict:
        signal = review.original_signal
        prompt = f"""
Review:
- signal_id: {signal.signal_id}
- market_id: {signal.market_id}
- asset_symbol: {signal.asset_symbol}
- crypto_tier: {signal.crypto_tier}
- direction: {signal.direction}
- action: {action}
- edge: {signal.edge:.4f}
- confidence: {signal.confidence:.4f}
- price: {signal.price:.4f}
- liquidity_summary: {signal.liquidity_summary}
- features_summary: {signal.features_summary}
- review_mode: {review.review_mode}
- review_notes: {sanitize_text(review.notes, 240)}
- news_validation: {review.news_validation.model_dump(mode='json') if review.news_validation else {}}

Guardrails:
- guarded_size: {guard_size}
- guarded_price_limit: {guard_price_limit:.4f}
"""
        response = await self.provider.call(prompt=prompt, system=SYSTEM_PROMPT)
        await self.cost_tracker.record(response, prompt_type="execute_order")
        try:
            return parse_json_object(response.content)
        except Exception as exc:
            raise InvalidModelResponseError(f"claw returned invalid JSON: {exc}") from exc

    async def close(self) -> None:
        await super().close()
        await self.connector.close()
