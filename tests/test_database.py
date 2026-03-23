from __future__ import annotations

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
