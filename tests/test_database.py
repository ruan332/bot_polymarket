from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import AsyncMock

import pytest

from core.database import TradingRepository


def test_mark_price_for_position_prefers_best_bid_from_orderbook_summary() -> None:
    row = {"direction": "YES", "average_price": 0.44}
    latest = {
        "price_yes": 0.52,
        "price_no": 0.48,
        "payload": {
            "orderbook_summary_yes": {"best_bid": 0.5, "best_ask": 0.52, "spread_bps": 25.0},
            "orderbook_summary_no": {"best_bid": 0.46, "best_ask": 0.48, "spread_bps": 30.0},
        },
    }

    price, spread = TradingRepository._mark_price_for_position(row, latest)

    assert price == 0.5
    assert spread == 25.0


def test_mark_price_for_position_falls_back_to_average_price_on_incoherent_snapshot() -> None:
    row = {"direction": "NO", "average_price": 0.58}
    latest = {
        "price_yes": 0.99,
        "price_no": 0.99,
        "payload": {},
    }

    price, spread = TradingRepository._mark_price_for_position(row, latest)

    assert price == 0.58
    assert spread == 0.0


def test_mark_price_for_position_prefers_current_pair_cycle_for_matching_cycle_slug() -> None:
    row = {
        "direction": "YES",
        "average_price": 0.44,
        "cycle_slug": "btc-updown-15m-1774294200",
    }
    pair_cycle = {
        "cycle_slug": "btc-updown-15m-1774294200",
        "price_yes": 0.57,
        "price_no": 0.43,
    }
    latest = {
        "price_yes": 0.0,
        "price_no": 0.0,
        "payload": {},
    }

    price, spread = TradingRepository._mark_price_for_position(row, latest, pair_cycle)

    assert price == 0.57
    assert spread == 0.0


def test_mark_price_for_position_tolerates_rows_without_cycle_slug() -> None:
    row = {"direction": "YES", "average_price": 0.44}
    pair_cycle = {
        "cycle_slug": "btc-updown-15m-1774294200",
        "price_yes": 0.57,
        "price_no": 0.43,
    }
    latest = {
        "price_yes": 0.52,
        "price_no": 0.48,
        "payload": {
            "orderbook_summary_yes": {"best_bid": 0.5, "best_ask": 0.52, "spread_bps": 25.0},
        },
    }

    price, spread = TradingRepository._mark_price_for_position(row, latest, pair_cycle)

    assert price == 0.5
    assert spread == 25.0


def test_json_value_decodes_double_encoded_json_payload() -> None:
    payload = '{"trade_group_id": "pair-group-1", "cycle_slug": "btc-updown-15m-123"}'
    double_encoded = f'"{payload.replace(chr(34), chr(92) + chr(34))}"'

    decoded = TradingRepository._json_value(double_encoded)

    assert decoded["trade_group_id"] == "pair-group-1"
    assert decoded["cycle_slug"] == "btc-updown-15m-123"


def test_count_by_key_uses_unknown_for_missing_values() -> None:
    items = [
        {"reason": "spread"},
        {"reason": ""},
        {},
    ]

    breakdown = TradingRepository._count_by_key(items, "reason", limit=5, missing_label="unknown")

    assert breakdown == [
        {"label": "unknown", "count": 2},
        {"label": "spread", "count": 1},
    ]


def test_group_count_by_keys_groups_reasons_by_strategy() -> None:
    items = [
        {"strategy_id": "momentum_15m", "reason": "spread"},
        {"strategy_id": "momentum_15m", "reason": "spread"},
        {"strategy_id": "momentum_15m", "reason": "depth"},
        {"strategy_id": "pair_15m", "reason": "conflict"},
        {"reason": "unknown_reason"},
    ]

    breakdown = TradingRepository._group_count_by_keys(
        items,
        group_key="strategy_id",
        item_key="reason",
        group_limit=5,
        item_limit=3,
        missing_group_label="unknown",
    )

    assert breakdown[0]["label"] == "momentum_15m"
    assert breakdown[0]["count"] == 3
    assert breakdown[0]["reasons"][0] == {"label": "spread", "count": 2}
    assert breakdown[1]["label"] == "pair_15m"
    assert breakdown[2]["label"] == "unknown"


@pytest.mark.asyncio
async def test_normalize_risk_payloads_prefers_signal_strategy() -> None:
    class DummyDb:
        async def fetch(self, query: str, *args):
            if "FROM signals" in query:
                return [
                    {
                        "id": UUID("11111111-1111-1111-1111-111111111111"),
                        "payload": {"strategy_id": "pair_15m"},
                    }
                ]
            return []

    repo = TradingRepository(DummyDb(), initial_bankroll=10.0)
    payloads = [
        {"signal_id": "11111111-1111-1111-1111-111111111111", "reason": "pair trade would exceed max_daily_spend_usd"},
        {"signal_id": "22222222-2222-2222-2222-222222222222", "reason": "non-pair position already open for this market"},
        {"error": "MomentumTradingEngine._increment_reason() missing required keyword-only arguments"},
        {"error": "TradingRepository.get_market_snapshots() takes 1 positional argument but 2 positional arguments were given"},
        {"signal_id": "33333333-3333-3333-3333-333333333333", "reason": "unmapped legacy event"},
    ]

    normalized = await repo._normalize_risk_payloads(payloads)

    assert normalized[0]["strategy_id"] == "pair_15m"
    assert normalized[1]["strategy_id"] == "momentum_15m"
    assert normalized[2]["strategy_id"] == "momentum_15m"
    assert normalized[3]["strategy_id"] == "momentum_15m"
    assert "strategy_id" not in normalized[4]


def test_signal_metrics_handles_pair_and_momentum_payloads() -> None:
    pair_item = {
        "strategy_id": "pair_15m",
        "predictor_confidence": 0.7616,
        "primary_leg": {"target_price": 0.90, "reference_price": 0.89},
    }
    momentum_item = {
        "strategy_id": "momentum_15m",
        "edge": 0.1642,
        "confidence": 0.7123,
    }

    pair_metrics = TradingRepository._signal_metrics(pair_item)
    momentum_metrics = TradingRepository._signal_metrics(momentum_item)

    assert pair_metrics["confidence"] == pytest.approx(0.7616)
    assert pair_metrics["edge"] == pytest.approx(0.01)
    assert momentum_metrics["confidence"] == pytest.approx(0.7123)
    assert momentum_metrics["edge"] == pytest.approx(0.1642)


def test_order_lifecycle_summary_tracks_latency_and_cancel_reasons() -> None:
    created_at = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)
    live_submitted_at = datetime(2026, 3, 24, 12, 4, tzinfo=UTC)
    live_filled_at = datetime(2026, 3, 24, 12, 1, tzinfo=UTC)
    orders = [
        {
            "status": "live_submitted",
            "created_at": created_at,
            "updated_at": live_submitted_at,
            "reason": "queued",
        },
        {
            "status": "live_filled",
            "created_at": created_at,
            "updated_at": live_filled_at,
            "reason": "filled",
        },
        {
            "status": "blocked",
            "created_at": created_at,
            "reason": "market volume below minimum",
        },
    ]
    pending_orders = [
        {"status": "cancelled", "reason": "hedge_timeout"},
        {"status": "expired", "reason": "cycle_rollover"},
    ]

    summary = TradingRepository._order_lifecycle_summary(orders, pending_orders)

    assert summary["tracked_orders"] == 3
    assert summary["live_submitted_orders"] == 1
    assert summary["live_filled_orders"] == 1
    assert summary["blocked_orders"] == 1
    assert summary["cancelled_orders"] == 2
    assert summary["fill_rate"] == pytest.approx(1.0)
    assert summary["cancel_rate"] == pytest.approx(0.6)
    assert summary["avg_fill_latency_seconds"] == pytest.approx(60.0)
    assert summary["avg_open_duration_seconds"] == pytest.approx(240.0)
    assert summary["cancel_reason_breakdown"][0] == {"label": "cycle_rollover", "count": 1}


def test_decode_record_merges_risk_metadata() -> None:
    repo = TradingRepository(object(), initial_bankroll=10.0)
    row = {
        "payload": {"error": "MomentumTradingEngine crash"},
        "agent": "claude",
        "reason": "agent_error",
        "created_at": datetime(2026, 3, 24, tzinfo=UTC),
    }

    decoded = repo._decode_record(row)  # type: ignore[arg-type]

    assert decoded["agent"] == "claude"
    assert decoded["reason"] == "agent_error"
    assert decoded["error"] == "MomentumTradingEngine crash"


@pytest.mark.asyncio
async def test_get_latest_weather_copytrade_summary_normalizes_nested_json_payloads() -> None:
    run_id = UUID("11111111-1111-1111-1111-111111111111")

    class DummyDb:
        async def fetchrow(self, query: str, *args):
            if "FROM weather_copytrade_runs" in query:
                return {
                    "run_id": run_id,
                    "category": "WEATHER",
                    "leaderboard_limit": 10,
                    "universe_count": 12,
                    "shortlisted_count": 3,
                    "selected_count": 1,
                    "selected_proxy_wallet": "0xabc",
                    "selected_user_name": "ColdMath",
                    "candidate_count": 6,
                    "stage_counts": '[{"label":"universe","count":12}]',
                    "rejected_breakdown": '{"invalid_profile":2}',
                    "model_summary": '{"summary":"ok","why":"because","risks":["low"],"selection_reason":"strong fit","selected_proxy_wallet":"0xabc","selected_user_name":"ColdMath"}',
                    "selection_summary": '{"pnl_30d":42.5,"max_drawdown":0.08}',
                    "scan_stats": '{"enriched_count":6}',
                    "metadata": '{"copy_trade_fraction":0.08}',
                    "created_at": datetime(2026, 3, 24, tzinfo=UTC),
                }
            if "FROM weather_copytrade_state" in query:
                return {
                    "category": "WEATHER",
                    "run_id": run_id,
                    "selected_proxy_wallet": "0xabc",
                    "selected_user_name": "ColdMath",
                    "selected_profile": '{"display_username_public":true,"name":"ColdMath"}',
                    "selection": '{"proxy_wallet":"0xabc","score":98.5}',
                    "report": '{"summary":"ok","why":"because","risks":["low"],"selection_reason":"strong fit","selected_proxy_wallet":"0xabc","selected_user_name":"ColdMath"}',
                    "approved": True,
                    "active": True,
                    "paused": False,
                    "approved_at": datetime(2026, 3, 24, tzinfo=UTC),
                    "activated_at": datetime(2026, 3, 24, tzinfo=UTC),
                    "last_trade_seen_at": datetime(2026, 3, 24, tzinfo=UTC),
                    "last_trade_seen_hash": "trade-1",
                    "processed_trade_hashes": '["trade-1"]',
                    "metadata": '{"last_run_id":"11111111-1111-1111-1111-111111111111"}',
                    "created_at": datetime(2026, 3, 24, tzinfo=UTC),
                    "updated_at": datetime(2026, 3, 24, tzinfo=UTC),
                }
            raise AssertionError(f"unexpected fetchrow query: {query}")

        async def fetch(self, query: str, *args):
            if "FROM weather_copytrade_candidates" in query:
                return [
                    {
                        "run_id": run_id,
                        "rank": 1,
                        "proxy_wallet": "0xabc",
                        "user_name": "ColdMath",
                        "verified_badge": True,
                        "profile": '{"display_username_public":true,"name":"ColdMath"}',
                        "metrics": '{"pnl_30d":42.5,"max_drawdown":0.08,"profit_factor":2.5,"trades_30d":30}',
                        "score": 98.5,
                        "rationale": "consistent pnl=42.50 pf=2.50 dd=8.00% weeks=4/4",
                        "selected": True,
                        "created_at": datetime(2026, 3, 24, tzinfo=UTC),
                    }
                ]
            if "FROM weather_copytrade_profiles" in query:
                return [
                    {
                        "category": "WEATHER",
                        "run_id": run_id,
                        "proxy_wallet": "0xabc",
                        "user_name": "ColdMath",
                        "profile": '{"display_username_public":true,"name":"ColdMath"}',
                        "selection_snapshot": '{"proxy_wallet":"0xabc","score":98.5}',
                        "approved": True,
                        "active": True,
                        "paused": False,
                        "approved_at": datetime(2026, 3, 24, tzinfo=UTC),
                        "activated_at": datetime(2026, 3, 24, tzinfo=UTC),
                        "last_trade_seen_at": datetime(2026, 3, 24, tzinfo=UTC),
                        "last_trade_seen_hash": "trade-1",
                        "processed_trade_hashes": '["trade-1"]',
                        "metrics_snapshot": '{"pnl_30d":42.5,"profit_factor":2.5}',
                        "performance_snapshot": '{"orders":4,"realized_pnl_window":5.2}',
                        "metadata": '{"last_sync_at":"2026-03-24T00:00:00+00:00"}',
                        "created_at": datetime(2026, 3, 24, tzinfo=UTC),
                        "updated_at": datetime(2026, 3, 24, tzinfo=UTC),
                    }
                ]
            raise AssertionError(f"unexpected fetch query: {query}")

    repo = TradingRepository(DummyDb(), initial_bankroll=10.0)
    summary = await repo.get_latest_weather_copytrade_summary()

    assert summary is not None
    assert summary["run"]["model_summary"]["summary"] == "ok"
    assert summary["report"]["summary"] == "ok"
    assert summary["selection_summary"]["pnl_30d"] == 42.5
    assert summary["metadata"]["copy_trade_fraction"] == 0.08
    assert summary["candidates"][0]["metrics"]["profit_factor"] == 2.5
    assert summary["state"]["report"]["selection_reason"] == "strong fit"
    assert summary["profiles"][0]["proxy_wallet"] == "0xabc"
    assert summary["profiles"][0]["performance_snapshot"]["orders"] == 4


@pytest.mark.asyncio
async def test_get_portfolio_summary_uses_live_polymarket_balance_when_live() -> None:
    class DummyDb:
        async def fetch(self, query: str, *args):
            if "FROM positions" in query:
                return []
            return []

        async def fetchrow(self, query: str, *args):
            return {"realized_pnl": 0.0}

    repo = TradingRepository(
        DummyDb(),
        initial_bankroll=1000.0,
        settings=SimpleNamespace(live_trading=True, polymarket_funder="0xabc"),
    )

    async def live_status() -> dict[str, object]:
        return {
            "funder": "0xdef",
            "parsed_collateral": {"balance": 12.5, "allowance": 25.0},
        }

    repo.bind_live_balance_provider(live_status)

    summary = await repo.get_portfolio_summary()

    assert summary.mode == "live"
    assert summary.balance_source == "polymarket_live"
    assert summary.available_balance == pytest.approx(12.5)
    assert summary.total_equity == pytest.approx(12.5)
    assert summary.live_balance == pytest.approx(12.5)
    assert summary.live_allowance == pytest.approx(25.0)
    assert summary.funder == "0xdef"


@pytest.mark.asyncio
async def test_get_portfolio_summary_keeps_paper_bankroll_when_paper() -> None:
    class DummyDb:
        async def fetch(self, query: str, *args):
            if "FROM positions" in query:
                return []
            return []

        async def fetchrow(self, query: str, *args):
            return {"realized_pnl": 5.0}

    repo = TradingRepository(DummyDb(), initial_bankroll=100.0, settings=SimpleNamespace(live_trading=False))

    summary = await repo.get_portfolio_summary()

    assert summary.mode == "paper"
    assert summary.balance_source == "paper_ledger"
    assert summary.available_balance == pytest.approx(105.0)
    assert summary.total_equity == pytest.approx(105.0)
    assert summary.realized_pnl == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_record_equity_snapshot_persists_canonical_source_and_trigger_source() -> None:
    class DummyDb:
        def __init__(self):
            self.executed: list[tuple[str, tuple[object, ...]]] = []

        async def fetch(self, query: str, *args):
            if "FROM positions" in query:
                return []
            return []

        async def fetchrow(self, query: str, *args):
            return {"realized_pnl": 0.0}

        async def execute(self, query: str, *args):
            self.executed.append((query, args))
            return "OK"

    db = DummyDb()
    repo = TradingRepository(
        db,
        initial_bankroll=1000.0,
        settings=SimpleNamespace(live_trading=True, polymarket_funder="0xabc"),
    )

    async def live_status() -> dict[str, object]:
        return {
            "funder": "0xabc",
            "parsed_collateral": {"balance": 9.5, "allowance": 11.0},
        }

    repo.bind_live_balance_provider(live_status)

    await repo.record_equity_snapshot(source="scan_cycle")

    assert db.executed
    query, args = db.executed[-1]
    assert "INSERT INTO equity_snapshots" in query
    assert args[7] == "polymarket_live"
    assert args[8] == "scan_cycle"


@pytest.mark.asyncio
async def test_record_paper_order_upserts_live_status_without_double_counting_positions() -> None:
    class DummyDb:
        def __init__(self):
            self.status_lookup_calls = 0
            self.executed: list[tuple[str, tuple[object, ...]]] = []

        async def fetch(self, query: str, *args):
            if "FROM positions" in query:
                return []
            return []

        async def fetchrow(self, query: str, *args):
            if "SELECT status FROM paper_orders" in query:
                self.status_lookup_calls += 1
                if self.status_lookup_calls == 1:
                    return None
                if self.status_lookup_calls == 2:
                    return {"status": "live_submitted"}
                return {"status": "live_filled"}
            raise AssertionError(f"unexpected fetchrow query: {query}")

        async def execute(self, query: str, *args):
            self.executed.append((query, args))
            return "OK"

    db = DummyDb()
    repo = TradingRepository(db, initial_bankroll=100.0)
    position_effect = AsyncMock()
    snapshot_effect = AsyncMock()
    repo._upsert_position = position_effect  # type: ignore[assignment]
    repo.record_equity_snapshot = snapshot_effect  # type: ignore[assignment]

    payload = {
        "action": "entry",
        "direction": "YES",
        "size": 10,
        "price_limit": 0.55,
        "notional_usd": 5.5,
        "market_question": "Will BTC go up?",
        "asset_symbol": "BTC",
        "crypto_tier": "btc",
        "strategy_id": "weather_copytrade",
        "trade_group_id": "wallet-1",
        "cycle_slug": "btc-updown-15m-1",
        "leg_role": "primary",
    }

    await repo.record_paper_order("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "market-1", "live_submitted", payload)
    await repo.record_paper_order("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "market-1", "live_filled", payload)
    await repo.record_paper_order("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "market-1", "live_filled", payload)

    assert position_effect.await_count == 1
    assert snapshot_effect.await_count == 1
    assert len(db.executed) == 3
