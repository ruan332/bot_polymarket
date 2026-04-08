from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from clima_bot.storage.repository import ClimaBotRepository


class PerformanceService:
    def __init__(self, repository: ClimaBotRepository) -> None:
        self.repository = repository

    def refresh_wallet(self, proxy_wallet: str) -> dict[str, Any]:
        orders = self.repository.list_orders(proxy_wallet=proxy_wallet, limit=1000)
        snapshot = self._build_wallet_snapshot(proxy_wallet, orders)
        self.repository.upsert_wallet_performance(proxy_wallet, snapshot)
        return snapshot

    def refresh_all(self) -> dict[str, dict[str, Any]]:
        wallets = self.repository.list_wallets()
        snapshots: dict[str, dict[str, Any]] = {}
        for wallet in wallets:
            snapshots[wallet["proxy_wallet"]] = self.refresh_wallet(wallet["proxy_wallet"])
        return snapshots

    def dashboard_summary(self) -> dict[str, Any]:
        wallets = self.repository.list_wallets()
        snapshots = self.repository.list_wallet_performance()
        open_positions = 0
        realized = 0.0
        for snapshot in snapshots.values():
            open_positions += int(snapshot.get("open_positions") or 0)
            realized += float(snapshot.get("realized_pnl_window") or 0.0)
        return {
            "tracked_wallets": len(wallets),
            "active_wallets": sum(1 for item in wallets if item.get("active") and not item.get("paused")),
            "open_positions": open_positions,
            "realized_pnl_window": round(realized, 4),
            "wallets": wallets,
            "snapshots": snapshots,
        }

    def _build_wallet_snapshot(self, proxy_wallet: str, orders: list[dict[str, Any]]) -> dict[str, Any]:
        submitted_states = {"simulated", "simulated_pending", "live_submitted", "live_filled", "filled"}
        executed_states = {"simulated", "live_filled", "filled"}
        realized_pnl = sum(float(item.get("realized_pnl_usd") or 0.0) for item in orders)
        executed = [item for item in orders if str(item.get("status") or "") in executed_states]
        submitted = [item for item in orders if str(item.get("status") or "") in submitted_states]
        open_positions = self._count_open_positions(orders)
        drawdown = self._max_drawdown([float(item.get("realized_pnl_usd") or 0.0) for item in orders])
        wins = [item for item in executed if float(item.get("realized_pnl_usd") or 0.0) > 0]
        snapshot = {
            "proxy_wallet": proxy_wallet,
            "orders": len(orders),
            "signals": len(orders),
            "execution_rate": len(executed) / len(submitted) if submitted else 0.0,
            "win_rate": len(wins) / len(executed) if executed else 0.0,
            "realized_pnl_window": round(realized_pnl, 4),
            "max_drawdown": round(drawdown, 4),
            "open_positions": open_positions,
            "last_order_at": orders[0]["created_at"] if orders else None,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        return snapshot

    def _count_open_positions(self, orders: list[dict[str, Any]]) -> int:
        latest_by_position: dict[str, dict[str, Any]] = {}
        for order in sorted(orders, key=lambda item: str(item["created_at"])):
            latest_by_position[str(order["position_key"])] = order
        return sum(1 for item in latest_by_position.values() if bool(item.get("is_open_position")))

    def _max_drawdown(self, pnl_series: list[float]) -> float:
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for pnl in pnl_series:
            equity += pnl
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)
        return max_drawdown
