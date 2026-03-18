from __future__ import annotations

from core.backtest import build_replay_report


def test_build_replay_report_marks_positions_to_market() -> None:
    report = build_replay_report(
        initial_bankroll=1000.0,
        orders=[
            {
                "order_id": "ord-1",
                "market_id": "market-1",
                "direction": "YES",
                "size": 100,
                "price_limit": 0.40,
                "notional_usd": 40.0,
                "status": "simulated",
                "created_at": "2026-03-18T12:00:01Z",
            }
        ],
        snapshots=[
            {
                "market_id": "market-1",
                "price_yes": 0.40,
                "price_no": 0.60,
                "created_at": "2026-03-18T12:00:00Z",
            },
            {
                "market_id": "market-1",
                "price_yes": 0.55,
                "price_no": 0.45,
                "created_at": "2026-03-18T12:05:00Z",
            },
        ],
    )

    assert report["summary"]["final_equity"] == 1015.0
    assert report["summary"]["total_pnl"] == 15.0
    assert report["points"][-1]["current_market_value"] == 55.0
    assert report["points"][-1]["cash_balance"] == 960.0


def test_build_replay_report_returns_empty_curve_without_snapshots() -> None:
    report = build_replay_report(initial_bankroll=1000.0, orders=[], snapshots=[])

    assert report["summary"]["final_equity"] == 1000.0
    assert report["summary"]["points"] == 0
    assert report["points"] == []
