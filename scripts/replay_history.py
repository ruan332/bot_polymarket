from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.app_context import AppContext
from core.backtest import build_replay_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay paper-trading history from persisted market snapshots.")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours when --start is omitted.")
    parser.add_argument("--start", type=str, default="", help="Replay window start in ISO-8601.")
    parser.add_argument("--end", type=str, default="", help="Replay window end in ISO-8601.")
    parser.add_argument("--market-id", type=str, default="", help="Restrict replay to a single market_id.")
    parser.add_argument("--limit", type=int, default=5000, help="Maximum snapshots and orders to read.")
    parser.add_argument("--export-json", type=str, default="", help="Optional path to write the replay report as JSON.")
    parser.add_argument("--export-csv", type=str, default="", help="Optional path to write the replay curve as CSV.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    start_at = _parse_datetime(args.start) if args.start else datetime.now(UTC) - timedelta(hours=args.hours)
    end_at = _parse_datetime(args.end) if args.end else None
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
        report = build_replay_report(
            initial_bankroll=context.settings.paper_bankroll_usd,
            orders=orders,
            snapshots=snapshots,
        )
        print(json.dumps(report["summary"], indent=2))
        if args.export_json:
            json_path = Path(args.export_json)
            json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
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
                writer.writerows(report["points"])
            print(f"csv_export={csv_path}")
    finally:
        await context.close()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


if __name__ == "__main__":
    asyncio.run(main())
