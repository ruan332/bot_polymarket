from __future__ import annotations

from core.backtest import BacktestExecutionConfig, build_backtest_report, build_replay_report


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


def test_build_replay_report_keeps_both_market_sides_separate() -> None:
    report = build_replay_report(
        initial_bankroll=1000.0,
        orders=[
            {
                "order_id": "ord-yes",
                "market_id": "market-1",
                "direction": "YES",
                "size": 100,
                "price_limit": 0.40,
                "notional_usd": 40.0,
                "status": "simulated",
                "created_at": "2026-03-18T12:00:01Z",
            },
            {
                "order_id": "ord-no",
                "market_id": "market-1",
                "direction": "NO",
                "size": 50,
                "price_limit": 0.60,
                "notional_usd": 30.0,
                "status": "simulated",
                "created_at": "2026-03-18T12:00:02Z",
            },
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

    assert report["summary"]["final_equity"] == 1007.5
    assert report["summary"]["total_pnl"] == 7.5
    assert report["points"][-1]["current_market_value"] == 77.5
    assert report["points"][-1]["cash_balance"] == 930.0
    assert report["points"][-1]["open_positions"] == 2


def test_build_backtest_report_computes_trade_and_regime_summaries() -> None:
    report = build_backtest_report(
        initial_bankroll=1000.0,
        orders=[
            {
                "order_id": "ord-entry",
                "position_key": "trade-1",
                "market_id": "market-1",
                "strategy_id": "momentum_15m",
                "regime": "trend",
                "direction": "YES",
                "size": 100,
                "price_limit": 0.40,
                "status": "simulated",
                "action": "entry",
                "created_at": "2026-03-18T12:00:01Z",
            },
            {
                "order_id": "ord-exit",
                "position_key": "trade-1",
                "market_id": "market-1",
                "strategy_id": "momentum_15m",
                "regime": "trend",
                "direction": "YES",
                "size": 100,
                "price_limit": 0.55,
                "status": "simulated",
                "action": "close",
                "created_at": "2026-03-18T12:05:00Z",
            },
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

    assert report["scenario"] == "baseline"
    assert report["summary"]["final_equity"] == 1015.0
    assert report["summary"]["win_rate"] == 1.0
    assert report["summary"]["closed_trade_count"] == 1
    assert report["summary"]["fill_rate"] == 1.0
    assert report["trade_summary"]["realized_pnl_usd"] == 15.0
    assert report["by_strategy"][0]["label"] == "momentum_15m"
    assert report["by_strategy"][0]["realized_pnl_usd"] == 15.0
    assert report["by_regime"][0]["label"] == "trend"


def test_build_backtest_report_applies_partial_fill_stress() -> None:
    report = build_backtest_report(
        initial_bankroll=1000.0,
        orders=[
            {
                "order_id": "ord-entry",
                "position_key": "trade-1",
                "market_id": "market-1",
                "strategy_id": "pair_15m",
                "regime": "mean_revert",
                "direction": "YES",
                "size": 100,
                "price_limit": 0.40,
                "status": "simulated",
                "action": "entry",
                "created_at": "2026-03-18T12:00:01Z",
            },
            {
                "order_id": "ord-exit",
                "position_key": "trade-1",
                "market_id": "market-1",
                "strategy_id": "pair_15m",
                "regime": "mean_revert",
                "direction": "YES",
                "size": 100,
                "price_limit": 0.55,
                "status": "simulated",
                "action": "close",
                "created_at": "2026-03-18T12:05:00Z",
            },
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
        execution=BacktestExecutionConfig(partial_fill_fraction=0.5),
        scenario_name="stress_fill",
    )

    assert report["scenario"] == "stress_fill"
    assert report["summary"]["fill_rate"] == 0.5
    assert report["summary"]["final_equity"] == 1007.5
    assert report["summary"]["closed_trade_count"] == 1
    assert report["trade_summary"]["realized_pnl_usd"] == 7.5
