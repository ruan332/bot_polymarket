from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import inf, log, sqrt
from typing import Any


@dataclass
class ReplayPosition:
    market_id: str
    direction: str
    size: int
    average_price: float
    cost_basis_usd: float


@dataclass
class ReplayPoint:
    created_at: datetime
    cash_balance: float
    current_market_value: float
    total_equity: float
    total_pnl: float
    realized_pnl: float
    open_positions: int


def build_replay_report(
    *,
    initial_bankroll: float,
    orders: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered_orders = sorted(orders, key=lambda item: _as_utc(item["created_at"]))
    ordered_snapshots = sorted(snapshots, key=lambda item: _as_utc(item["created_at"]))
    positions: dict[str, ReplayPosition] = {}
    latest_prices: dict[str, dict[str, float]] = {}
    cash_balance = initial_bankroll
    realized_pnl = 0.0
    points: list[ReplayPoint] = []
    order_index = 0

    for snapshot in ordered_snapshots:
        snapshot_time = _as_utc(snapshot["created_at"])
        while order_index < len(ordered_orders) and _as_utc(ordered_orders[order_index]["created_at"]) <= snapshot_time:
            order = ordered_orders[order_index]
            if str(order.get("status", "")) == "simulated":
                key = _position_key(str(order["market_id"]), str(order["direction"]))
                size = int(order["size"])
                price = float(order["price_limit"])
                notional = float(order.get("notional_usd") or size * price)
                action = str(order.get("action") or "entry")
                existing = positions.get(key)
                if action in {"entry", "scale_in"}:
                    cash_balance -= notional
                    if existing is None:
                        positions[key] = ReplayPosition(
                            market_id=str(order["market_id"]),
                            direction=str(order["direction"]),
                            size=size,
                            average_price=price,
                            cost_basis_usd=notional,
                        )
                    else:
                        total_size = existing.size + size
                        weighted_price = (
                            ((existing.average_price * existing.size) + (price * size)) / total_size if total_size else price
                        )
                        positions[key] = ReplayPosition(
                            market_id=existing.market_id,
                            direction=existing.direction,
                            size=total_size,
                            average_price=weighted_price,
                            cost_basis_usd=existing.cost_basis_usd + notional,
                        )
                else:
                    if existing is None:
                        order_index += 1
                        continue
                    exit_size = min(size, existing.size)
                    removed_cost_basis = existing.average_price * exit_size
                    cash_balance += notional
                    realized_pnl += float(order.get("realized_pnl_usd") or (notional - removed_cost_basis))
                    remaining_size = existing.size - exit_size
                    if remaining_size <= 0:
                        positions.pop(key, None)
                    else:
                        positions[key] = ReplayPosition(
                            market_id=existing.market_id,
                            direction=existing.direction,
                            size=remaining_size,
                            average_price=existing.average_price,
                            cost_basis_usd=existing.average_price * remaining_size,
                        )
            order_index += 1

        latest_prices[str(snapshot["market_id"])] = {
            "YES": float(snapshot["price_yes"]),
            "NO": float(snapshot["price_no"]),
        }

        current_market_value = 0.0
        for position in positions.values():
            mark = latest_prices.get(position.market_id)
            if mark is None:
                current_market_value += position.cost_basis_usd
                continue
            current_market_value += position.size * mark[position.direction]

        total_equity = cash_balance + current_market_value
        points.append(
            ReplayPoint(
                created_at=snapshot_time,
                cash_balance=cash_balance,
                current_market_value=current_market_value,
                total_equity=total_equity,
                total_pnl=total_equity - initial_bankroll,
                realized_pnl=realized_pnl,
                open_positions=len(positions),
            )
        )

    if not points:
        return {
            "summary": {
                "initial_bankroll": initial_bankroll,
                "final_equity": initial_bankroll,
                "total_pnl": 0.0,
                "realized_pnl": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "points": 0,
                "orders": len(ordered_orders),
                "markets": 0,
            },
            "points": [],
        }

    running_peak = -inf
    max_drawdown = 0.0
    for point in points:
        running_peak = max(running_peak, point.total_equity)
        if running_peak > 0:
            drawdown = (running_peak - point.total_equity) / running_peak
            max_drawdown = max(max_drawdown, drawdown)
    sharpe_ratio = _sharpe(points)

    return {
        "summary": {
            "initial_bankroll": initial_bankroll,
            "final_equity": round(points[-1].total_equity, 4),
            "total_pnl": round(points[-1].total_pnl, 4),
            "realized_pnl": round(points[-1].realized_pnl, 4),
            "max_drawdown": round(max_drawdown, 6),
            "sharpe_ratio": sharpe_ratio,
            "points": len(points),
            "orders": len(ordered_orders),
            "markets": len({snapshot["market_id"] for snapshot in ordered_snapshots}),
        },
        "points": [
            {
                "created_at": point.created_at.isoformat(),
                "cash_balance": round(point.cash_balance, 4),
                "current_market_value": round(point.current_market_value, 4),
                "total_equity": round(point.total_equity, 4),
                "total_pnl": round(point.total_pnl, 4),
                "realized_pnl": round(point.realized_pnl, 4),
                "open_positions": point.open_positions,
            }
            for point in points
        ],
    }


def _position_key(market_id: str, direction: str) -> str:
    return f"{market_id}:{direction}"


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _sharpe(points: list[ReplayPoint]) -> float:
    if len(points) < 2:
        return 0.0
    returns: list[float] = []
    for previous, current in zip(points, points[1:]):
        if previous.total_equity > 0 and current.total_equity > 0:
            returns.append(log(current.total_equity / previous.total_equity))
    if len(returns) < 2:
        return 0.0
    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
    std = sqrt(variance)
    return round(mean_return / std, 4) if std > 0 else 0.0
