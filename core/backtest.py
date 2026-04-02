from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import inf, log, sqrt
from statistics import median
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


@dataclass(frozen=True)
class BacktestExecutionConfig:
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    latency_minutes: float = 0.0
    latency_slippage_bps_per_minute: float = 0.0
    partial_fill_fraction: float = 1.0
    latency_fill_decay_per_minute: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _BacktestPosition:
    trade_key: str
    market_id: str
    direction: str
    strategy_id: str
    regime: str
    entry_at: datetime | None = None
    last_action_at: datetime | None = None
    size: int = 0
    average_entry_price: float = 0.0
    entry_notional: float = 0.0
    exit_notional: float = 0.0
    realized_pnl: float = 0.0
    order_count: int = 0


def build_backtest_report(
    *,
    initial_bankroll: float,
    orders: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    execution: BacktestExecutionConfig | None = None,
    scenario_name: str = "baseline",
) -> dict[str, Any]:
    execution = execution or BacktestExecutionConfig()
    ordered_orders = sorted(orders, key=lambda item: _as_utc(item["created_at"]))
    ordered_snapshots = sorted(snapshots, key=lambda item: _as_utc(item["created_at"]))
    positions: dict[str, _BacktestPosition] = {}
    closed_trades: list[dict[str, Any]] = []
    latest_prices: dict[str, dict[str, float]] = {}
    cash_balance = initial_bankroll
    points: list[dict[str, Any]] = []
    order_index = 0
    filled_orders = 0
    filled_size_total = 0
    original_size_total = 0
    execution_cost_total = 0.0
    turnover_total = 0.0

    for snapshot in ordered_snapshots:
        snapshot_time = _as_utc(snapshot["created_at"])
        while order_index < len(ordered_orders) and _as_utc(ordered_orders[order_index]["created_at"]) <= snapshot_time:
            order = ordered_orders[order_index]
            order_index += 1
            if str(order.get("status", "")) != "simulated":
                continue

            action = _normalize_order_action(str(order.get("action") or "entry"))
            size = max(int(order.get("size") or 0), 0)
            if size <= 0:
                continue

            original_size_total += size
            fill_ratio = _effective_fill_ratio(execution)
            filled_size = _filled_size(size, fill_ratio)
            if filled_size <= 0:
                continue

            filled_orders += 1
            filled_size_total += filled_size
            price = float(order.get("price_limit") or order.get("price") or 0.0)
            effective_price = _effective_price(price, action, execution)
            execution_cost_total += abs(effective_price - price) * filled_size
            turnover_total += effective_price * filled_size

            trade_key = _trade_key(order)
            market_id = str(order.get("market_id") or "")
            direction = str(order.get("direction") or "YES").upper()
            strategy_id = str(order.get("strategy_id") or "")
            regime = str(order.get("regime") or "")
            state = positions.get(trade_key)

            if action in {"entry", "scale_in"}:
                cash_balance -= effective_price * filled_size
                if state is None:
                    positions[trade_key] = _BacktestPosition(
                        trade_key=trade_key,
                        market_id=market_id,
                        direction=direction,
                        strategy_id=strategy_id,
                        regime=regime,
                        entry_at=_as_utc(order["created_at"]),
                        last_action_at=_as_utc(order["created_at"]),
                        size=filled_size,
                        average_entry_price=effective_price,
                        entry_notional=effective_price * filled_size,
                        order_count=1,
                    )
                else:
                    total_size = state.size + filled_size
                    weighted_average = (
                        ((state.average_entry_price * state.size) + (effective_price * filled_size)) / total_size
                        if total_size
                        else effective_price
                    )
                    state.size = total_size
                    state.average_entry_price = weighted_average
                    state.entry_notional += effective_price * filled_size
                    state.order_count += 1
                    state.last_action_at = _as_utc(order["created_at"])
                    if not state.strategy_id and strategy_id:
                        state.strategy_id = strategy_id
                    if not state.regime and regime:
                        state.regime = regime
                    if state.entry_at is None:
                        state.entry_at = _as_utc(order["created_at"])
            else:
                if state is None or state.size <= 0:
                    continue
                exit_size = min(filled_size, state.size)
                cash_balance += effective_price * exit_size
                realized_pnl = exit_size * (effective_price - state.average_entry_price)
                state.realized_pnl += realized_pnl
                state.exit_notional += effective_price * exit_size
                state.size -= exit_size
                state.order_count += 1
                state.last_action_at = _as_utc(order["created_at"])
                if state.size <= 0:
                    trade = _finalize_backtest_trade(state, exit_at=_as_utc(order["created_at"]))
                    trade["realized_pnl_usd"] = round(state.realized_pnl, 4)
                    closed_trades.append(trade)
                    positions.pop(trade_key, None)

        latest_prices[str(snapshot["market_id"])] = {
            "YES": float(snapshot["price_yes"]),
            "NO": float(snapshot["price_no"]),
        }

        current_market_value = 0.0
        for position in positions.values():
            mark = latest_prices.get(position.market_id)
            if mark is None:
                current_market_value += position.entry_notional
                continue
            current_market_value += position.size * mark[position.direction]

        total_equity = cash_balance + current_market_value
        points.append(
            {
                "created_at": snapshot_time.isoformat(),
                "cash_balance": round(cash_balance, 4),
                "current_market_value": round(current_market_value, 4),
                "total_equity": round(total_equity, 4),
                "total_pnl": round(total_equity - initial_bankroll, 4),
                "realized_pnl": round(sum(trade["realized_pnl_usd"] for trade in closed_trades), 4),
                "open_positions": len(positions),
            }
        )

    if points:
        final_equity = float(points[-1]["total_equity"])
        total_pnl = float(points[-1]["total_pnl"])
        realized_pnl = float(points[-1]["realized_pnl"])
        running_peak = -inf
        max_drawdown = 0.0
        for point in points:
            total_equity_point = float(point["total_equity"])
            running_peak = max(running_peak, total_equity_point)
            if running_peak > 0:
                drawdown = (running_peak - total_equity_point) / running_peak
                max_drawdown = max(max_drawdown, drawdown)
        sharpe_ratio = _sharpe(
            [
                ReplayPoint(
                    created_at=_as_utc(point["created_at"]),
                    cash_balance=float(point["cash_balance"]),
                    current_market_value=float(point["current_market_value"]),
                    total_equity=float(point["total_equity"]),
                    total_pnl=float(point["total_pnl"]),
                    realized_pnl=float(point["realized_pnl"]),
                    open_positions=int(point["open_positions"]),
                )
                for point in points
            ]
        )
    else:
        final_equity = initial_bankroll + sum(trade["realized_pnl_usd"] for trade in closed_trades)
        total_pnl = final_equity - initial_bankroll
        realized_pnl = sum(trade["realized_pnl_usd"] for trade in closed_trades)
        max_drawdown = 0.0
        sharpe_ratio = 0.0

    remaining_trades = [
        _finalize_backtest_trade(position, exit_at=None, closed=False) for position in positions.values()
    ]
    all_trades = closed_trades + remaining_trades
    trade_summary = _summarize_backtest_trades(all_trades)
    strategy_summary = _group_backtest_trades(all_trades, group_key="strategy_id")
    regime_summary = _group_backtest_trades(all_trades, group_key="regime")
    fill_rate = round(filled_size_total / original_size_total, 4) if original_size_total else 0.0

    return {
        "scenario": scenario_name,
        "execution_assumptions": execution.as_dict(),
        "summary": {
            "initial_bankroll": initial_bankroll,
            "final_equity": round(final_equity, 4),
            "total_pnl": round(total_pnl, 4),
            "realized_pnl": round(realized_pnl, 4),
            "max_drawdown": round(max_drawdown, 6),
            "sharpe_ratio": sharpe_ratio,
            "points": len(points),
            "orders": len(ordered_orders),
            "filled_orders": filled_orders,
            "fill_rate": fill_rate,
            "trade_count": len(all_trades),
            "closed_trade_count": len(closed_trades),
            "open_trade_count": len(positions),
            "win_rate": trade_summary["win_rate"],
            "avg_hold_minutes": trade_summary["avg_hold_minutes"],
            "turnover_usd": round(turnover_total, 4),
            "estimated_execution_cost_usd": round(execution_cost_total, 4),
            "markets": len({snapshot["market_id"] for snapshot in ordered_snapshots}),
        },
        "trade_summary": trade_summary,
        "by_strategy": strategy_summary,
        "by_regime": regime_summary,
        "trades": all_trades,
        "points": points,
    }


def _normalize_order_action(action: str) -> str:
    normalized = action.strip().lower()
    if normalized in {"entry", "scale_in"}:
        return normalized
    if normalized in {"scale_out", "close", "exit"}:
        return "close"
    return "entry"


def _effective_fill_ratio(execution: BacktestExecutionConfig) -> float:
    base = max(0.0, min(1.0, execution.partial_fill_fraction))
    decayed = base - max(0.0, execution.latency_minutes) * max(0.0, execution.latency_fill_decay_per_minute)
    return max(0.0, min(1.0, decayed))


def _filled_size(size: int, fill_ratio: float) -> int:
    if size <= 0 or fill_ratio <= 0:
        return 0
    return max(1, int(size * fill_ratio))


def _effective_price(price: float, action: str, execution: BacktestExecutionConfig) -> float:
    adverse_bps = max(0.0, execution.spread_bps * 0.5 + execution.slippage_bps)
    adverse_bps += max(0.0, execution.latency_minutes) * max(0.0, execution.latency_slippage_bps_per_minute)
    shift = adverse_bps / 10000.0
    if action == "entry":
        return round(price * (1 + shift), 6)
    return round(price * max(0.0, 1 - shift), 6)


def _trade_key(order: dict[str, Any]) -> str:
    market_id = str(order.get("market_id") or "")
    direction = str(order.get("direction") or "YES").upper()
    return str(order.get("position_key") or order.get("trade_group_id") or f"{market_id}:{direction}")


def _finalize_backtest_trade(position: _BacktestPosition, *, exit_at: datetime | None, closed: bool = True) -> dict[str, Any]:
    held_minutes = 0.0
    if position.entry_at is not None and exit_at is not None:
        held_minutes = max((exit_at - position.entry_at).total_seconds() / 60.0, 0.0)
    return {
        "trade_key": position.trade_key,
        "market_id": position.market_id,
        "direction": position.direction,
        "strategy_id": position.strategy_id or "",
        "regime": position.regime or "",
        "entry_at": None if position.entry_at is None else position.entry_at.isoformat(),
        "exit_at": None if exit_at is None else exit_at.isoformat(),
        "entry_notional_usd": round(position.entry_notional, 4),
        "exit_notional_usd": round(position.exit_notional, 4),
        "realized_pnl_usd": round(position.realized_pnl, 4),
        "hold_minutes": round(held_minutes, 2),
        "closed": closed,
        "order_count": position.order_count,
        "open_size": position.size,
        "win": bool(position.realized_pnl > 0),
    }


def _summarize_backtest_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    closed_trades = [trade for trade in trades if trade.get("closed")]
    winning_trades = [trade for trade in closed_trades if float(trade.get("realized_pnl_usd") or 0.0) > 0]
    hold_minutes = [float(trade.get("hold_minutes") or 0.0) for trade in closed_trades if trade.get("hold_minutes") is not None]
    return {
        "trades": len(trades),
        "closed_trades": len(closed_trades),
        "winning_trades": len(winning_trades),
        "win_rate": round(len(winning_trades) / len(closed_trades), 4) if closed_trades else 0.0,
        "avg_hold_minutes": round(sum(hold_minutes) / len(hold_minutes), 2) if hold_minutes else 0.0,
        "median_hold_minutes": round(median(hold_minutes), 2) if hold_minutes else 0.0,
        "realized_pnl_usd": round(sum(float(trade.get("realized_pnl_usd") or 0.0) for trade in closed_trades), 4),
    }


def _group_backtest_trades(trades: list[dict[str, Any]], *, group_key: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        label = str(trade.get(group_key) or "").strip() or "unknown"
        buckets.setdefault(label, []).append(trade)
    summary: list[dict[str, Any]] = []
    for label in sorted(buckets):
        items = buckets[label]
        closed_trades = [trade for trade in items if trade.get("closed")]
        wins = [trade for trade in closed_trades if float(trade.get("realized_pnl_usd") or 0.0) > 0]
        hold_minutes = [float(trade.get("hold_minutes") or 0.0) for trade in closed_trades if trade.get("hold_minutes") is not None]
        summary.append(
            {
                "label": label,
                "trades": len(items),
                "closed_trades": len(closed_trades),
                "win_rate": round(len(wins) / len(closed_trades), 4) if closed_trades else 0.0,
                "realized_pnl_usd": round(sum(float(trade.get("realized_pnl_usd") or 0.0) for trade in closed_trades), 4),
                "avg_hold_minutes": round(sum(hold_minutes) / len(hold_minutes), 2) if hold_minutes else 0.0,
                "median_hold_minutes": round(median(hold_minutes), 2) if hold_minutes else 0.0,
            }
        )
    return summary
