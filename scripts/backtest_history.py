from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.app_context import AppContext
from core.backtest import BacktestExecutionConfig, build_backtest_report
from core.backtest_analysis import analyze_backtest_with_llm


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic backtests from persisted historical data.")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours when --start is omitted.")
    parser.add_argument("--start", type=str, default="", help="Backtest window start in ISO-8601.")
    parser.add_argument("--end", type=str, default="", help="Backtest window end in ISO-8601.")
    parser.add_argument("--market-id", type=str, default="", help="Restrict the backtest to a single market_id.")
    parser.add_argument("--asset", type=str, default="", help="Restrict to a single asset symbol, e.g. BTC.")
    parser.add_argument("--tier", type=str, default="", help="Restrict to a crypto tier, e.g. btc, major, small_cap.")
    parser.add_argument("--strategy", type=str, default="", help="Restrict to a strategy_id, e.g. pair_15m.")
    parser.add_argument("--regime", type=str, default="", help="Restrict to a regime, e.g. trend or mean_revert.")
    parser.add_argument("--limit", type=int, default=10000, help="Maximum snapshots and orders to read.")
    parser.add_argument("--walk-forward-slices", type=int, default=3, help="Number of sequential slices to report.")
    parser.add_argument(
        "--stress-suite",
        action="store_true",
        help="Also build conservative stress scenarios for execution cost and partial fills.",
    )
    parser.add_argument("--llm-analysis", action="store_true", help="Run a post-backtest LLM analysis pass.")
    parser.add_argument("--llm-agent", type=str, default="codex", help="Configured agent name to use for analysis.")
    parser.add_argument("--export-json", type=str, default="", help="Optional path to write the full report as JSON.")
    parser.add_argument("--export-csv", type=str, default="", help="Optional path to write the baseline curve as CSV.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    start_at = _parse_datetime(args.start) if args.start else datetime.now(UTC) - timedelta(hours=args.hours)
    end_at = _parse_datetime(args.end) if args.end else datetime.now(UTC)

    context = await AppContext.create()
    try:
        snapshots = await context.repository.get_market_snapshots(
            market_id=args.market_id or None,
            start_at=start_at,
            end_at=end_at,
            limit=args.limit,
        )
        orders = await context.repository.get_replay_orders(
            market_id=args.market_id or None,
            start_at=start_at,
            end_at=end_at,
            limit=args.limit,
        )

        filtered_orders = _filter_orders(
            orders,
            asset=args.asset or None,
            tier=args.tier or None,
            strategy=args.strategy or None,
            regime=args.regime or None,
        )
        market_ids = {str(order.get("market_id") or "") for order in filtered_orders if str(order.get("market_id") or "")}
        filtered_snapshots = _filter_snapshots(
            snapshots,
            asset=args.asset or None,
            tier=args.tier or None,
            market_ids=market_ids or None,
        )

        reports: dict[str, dict[str, Any]] = {
            "baseline": build_backtest_report(
                initial_bankroll=context.settings.paper_bankroll_usd,
                orders=filtered_orders,
                snapshots=filtered_snapshots,
                scenario_name="baseline",
            )
        }

        if args.stress_suite:
            for scenario_name, execution in _stress_scenarios().items():
                reports[scenario_name] = build_backtest_report(
                    initial_bankroll=context.settings.paper_bankroll_usd,
                    orders=filtered_orders,
                    snapshots=filtered_snapshots,
                    execution=execution,
                    scenario_name=scenario_name,
                )

        slices = []
        if args.walk_forward_slices > 1:
            for label, slice_start, slice_end in _split_window(start_at, end_at, args.walk_forward_slices):
                slice_orders = [
                    order
                    for order in filtered_orders
                    if slice_start <= _parse_created_at(order["created_at"]) < slice_end
                ]
                slice_snapshots = [
                    snapshot
                    for snapshot in filtered_snapshots
                    if slice_start <= _parse_created_at(snapshot["created_at"]) < slice_end
                ]
                slices.append(
                    {
                        "label": label,
                        "start_at": slice_start.isoformat(),
                        "end_at": slice_end.isoformat(),
                        "report": build_backtest_report(
                            initial_bankroll=context.settings.paper_bankroll_usd,
                            orders=slice_orders,
                            snapshots=slice_snapshots,
                            scenario_name=label,
                        ),
                    }
                )

        analysis = None
        if args.llm_analysis:
            analysis = await analyze_backtest_with_llm(
                context,
                reports["baseline"],
                agent_name=args.llm_agent,
            )

        output: dict[str, Any] = {
            "window": {
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "market_id": args.market_id or None,
                "asset": args.asset or None,
                "tier": args.tier or None,
                "strategy": args.strategy or None,
                "regime": args.regime or None,
            },
            "reports": reports,
            "walk_forward": slices,
            "analysis": analysis,
        }

        print(json.dumps(reports["baseline"]["summary"], indent=2))
        if args.stress_suite:
            print(json.dumps({name: report["summary"] for name, report in reports.items() if name != "baseline"}, indent=2))
        if analysis is not None:
            print(json.dumps(analysis["analysis"], indent=2))

        if args.export_json:
            json_path = Path(args.export_json)
            json_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
            print(f"json_export={json_path}")
        if args.export_csv:
            csv_path = Path(args.export_csv)
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "created_at",
                        "cash_balance",
                        "current_market_value",
                        "total_equity",
                        "total_pnl",
                        "open_positions",
                    ],
                )
                writer.writeheader()
                writer.writerows(reports["baseline"]["points"])
            print(f"csv_export={csv_path}")
    finally:
        await context.close()


def _stress_scenarios() -> dict[str, BacktestExecutionConfig]:
    return {
        "stress_spread": BacktestExecutionConfig(spread_bps=35.0, slippage_bps=12.0, partial_fill_fraction=0.9),
        "stress_latency": BacktestExecutionConfig(
            spread_bps=20.0,
            slippage_bps=20.0,
            latency_minutes=3.0,
            latency_slippage_bps_per_minute=4.0,
            partial_fill_fraction=0.85,
            latency_fill_decay_per_minute=0.04,
        ),
        "stress_liquidity": BacktestExecutionConfig(
            spread_bps=45.0,
            slippage_bps=18.0,
            latency_minutes=2.0,
            latency_slippage_bps_per_minute=3.0,
            partial_fill_fraction=0.75,
            latency_fill_decay_per_minute=0.03,
        ),
    }


def _split_window(start_at: datetime, end_at: datetime, segments: int) -> list[tuple[str, datetime, datetime]]:
    if segments <= 1:
        return []
    duration = (end_at - start_at) / segments
    windows: list[tuple[str, datetime, datetime]] = []
    for index in range(segments):
        slice_start = start_at + duration * index
        slice_end = end_at if index == segments - 1 else start_at + duration * (index + 1)
        windows.append((f"slice_{index + 1}", slice_start, slice_end))
    return windows


def _filter_orders(
    orders: list[dict[str, Any]],
    *,
    asset: str | None,
    tier: str | None,
    strategy: str | None,
    regime: str | None,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for order in orders:
        if asset and str(order.get("asset_symbol") or "").upper() != asset.upper():
            continue
        if tier and str(order.get("crypto_tier") or "").lower() != tier.lower():
            continue
        if strategy and str(order.get("strategy_id") or "").strip() != strategy.strip():
            continue
        if regime and str(order.get("regime") or "").strip().lower() != regime.strip().lower():
            continue
        filtered.append(order)
    return filtered


def _filter_snapshots(
    snapshots: list[dict[str, Any]],
    *,
    asset: str | None,
    tier: str | None,
    market_ids: set[str] | None,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if market_ids and str(snapshot.get("market_id") or "") not in market_ids:
            continue
        if asset and str(snapshot.get("asset_symbol") or "").upper() != asset.upper():
            continue
        if tier and str(snapshot.get("crypto_tier") or "").lower() != tier.lower():
            continue
        filtered.append(snapshot)
    return filtered


def _parse_created_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


if __name__ == "__main__":
    asyncio.run(main())
