from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

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
