from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from core.app_context import AppContext
from core.market_connector import MarketConnector
from core.schemas import PaperOrderPayload, SettlementEventPayload


@dataclass
class SettlementService:
    context: AppContext
    connector: MarketConnector

    async def preview_redeemable_positions(self, limit: int = 20) -> list[dict[str, object]]:
        positions = await self.context.repository.get_open_positions()
        candidates: list[dict[str, object]] = []
        for position in positions:
            resolution = await self.connector.get_market_resolution(str(position["market_id"]))
            payout_price = self._payout_price(position, resolution)
            resolved = bool(resolution.get("resolved"))
            candidates.append(
                {
                    "market_id": str(position["market_id"]),
                    "position_key": str(position["position_key"]),
                    "token_id": str(position.get("token_id") or ""),
                    "market_question": str(position.get("market_question") or ""),
                    "asset_symbol": str(position.get("asset_symbol") or ""),
                    "strategy_id": str(position.get("strategy_id") or ""),
                    "trade_group_id": str(position.get("trade_group_id") or ""),
                    "cycle_slug": str(position.get("cycle_slug") or ""),
                    "leg_role": str(position.get("leg_role") or ""),
                    "direction": str(position.get("direction") or ""),
                    "size": int(position.get("size") or 0),
                    "average_price": float(position.get("average_price") or 0.0),
                    "cost_basis_usd": float(position.get("cost_basis_usd") or 0.0),
                    "resolved": resolved,
                    "eligible": resolved and payout_price is not None,
                    "payout_price": payout_price,
                    "payout_usd": round(int(position.get("size") or 0) * payout_price, 4) if payout_price is not None else 0.0,
                    "resolution": resolution,
                }
            )
            if len(candidates) >= limit:
                break
        return candidates

    async def process_redeem_cycle(self, *, dry_run: bool | None = None, limit: int = 20) -> dict[str, object]:
        candidates = await self.preview_redeemable_positions(limit=limit)
        live_mode = bool(self.context.settings.live_trading)
        should_dry_run = True if live_mode else (bool(dry_run) if dry_run is not None else False)
        processed = 0
        skipped = 0
        redeemed = 0
        realized_pnl_usd = 0.0
        events: list[dict[str, object]] = []
        for candidate in candidates:
            if not bool(candidate["eligible"]):
                skipped += 1
                continue
            processed += 1
            payout_price = float(candidate["payout_price"] or 0.0)
            payout_usd = float(candidate["payout_usd"] or 0.0)
            cost_basis = float(candidate["cost_basis_usd"] or 0.0)
            realized_pnl = round(payout_usd - cost_basis, 4)
            settlement = SettlementEventPayload(
                settlement_id=str(uuid4()),
                market_id=str(candidate["market_id"]),
                position_key=str(candidate["position_key"]),
                token_id=str(candidate["token_id"]),
                market_question=str(candidate["market_question"]),
                asset_symbol=str(candidate["asset_symbol"]),
                strategy_id=str(candidate["strategy_id"]),
                trade_group_id=str(candidate["trade_group_id"]),
                cycle_slug=str(candidate["cycle_slug"]),
                leg_role=str(candidate["leg_role"]),
                direction=str(candidate["direction"]),  # type: ignore[arg-type]
                size=int(candidate["size"]),
                average_price=float(candidate["average_price"]),
                payout_price=payout_price,
                payout_usd=payout_usd,
                cost_basis_usd=cost_basis,
                realized_pnl_usd=realized_pnl,
                status="skipped" if live_mode else ("dry_run" if should_dry_run else "settled"),
                reason="live redemption requires explicit on-chain redeem flow" if live_mode else "paper market redeemed",
                resolution=dict(candidate["resolution"]),
            )
            if live_mode:
                skipped += 1
            elif not should_dry_run:
                close_order = PaperOrderPayload(
                    order_id=str(uuid4()),
                    signal_id=str(uuid4()),
                    market_id=str(candidate["market_id"]),
                    token_id=str(candidate["token_id"]),
                    market_question=str(candidate["market_question"]),
                    asset_symbol=str(candidate["asset_symbol"]),
                    strategy_id=str(candidate["strategy_id"]),
                    trade_group_id=str(candidate["trade_group_id"]),
                    cycle_slug=str(candidate["cycle_slug"]),
                    leg_role=str(candidate["leg_role"]),
                    direction=str(candidate["direction"]),  # type: ignore[arg-type]
                    size=int(candidate["size"]),
                    price_limit=payout_price,
                    notional_usd=payout_usd,
                    realized_pnl_usd=realized_pnl,
                    action="close",
                    exit_reason="market_redeemed",
                    execution_mode="deterministic",
                    status="simulated",
                    reason="market_redeemed",
                    position_key=str(candidate["position_key"]),
                )
                await self.context.repository.record_paper_order(
                    close_order.order_id,
                    close_order.signal_id,
                    close_order.market_id,
                    close_order.status,
                    close_order.model_dump(mode="json"),
                )
                await self.context.bus.publish_event("orders:paper", close_order.model_dump(mode="json"))
                redeemed += 1
                realized_pnl_usd += realized_pnl
            else:
                skipped += 1
            await self.context.repository.record_settlement_event(
                settlement.settlement_id,
                settlement.position_key,
                settlement.market_id,
                settlement.status,
                settlement.model_dump(mode="json"),
            )
            events.append(settlement.model_dump(mode="json"))
        return {
            "dry_run": should_dry_run,
            "processed_count": processed,
            "settled_count": redeemed,
            "skipped_count": skipped,
            "realized_pnl_usd": round(realized_pnl_usd, 4),
            "events": events[:10],
        }

    @staticmethod
    def _payout_price(position: dict[str, object], resolution: dict[str, object]) -> float | None:
        if not bool(resolution.get("resolved")):
            return None
        direction = str(position.get("direction") or "")
        winning_direction = str(resolution.get("winning_direction") or "")
        if winning_direction in {"YES", "NO"}:
            return 1.0 if winning_direction == direction else 0.0
        payout_yes = resolution.get("payout_yes")
        payout_no = resolution.get("payout_no")
        if direction == "YES" and payout_yes is not None:
            return float(payout_yes)
        if direction == "NO" and payout_no is not None:
            return float(payout_no)
        return None
