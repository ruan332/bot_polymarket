from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from core.pair_strategy import AdaptivePricePredictor, PairCycleRuntime, PairTradingEngine, PredictorSignal, Quote, cycle_slug_for, floor_cycle_start


def test_floor_cycle_start_rounds_down_to_15_minutes() -> None:
    value = datetime(2026, 3, 23, 13, 17, 42, tzinfo=UTC)

    rounded = floor_cycle_start(value)

    assert rounded == datetime(2026, 3, 23, 13, 15, 0, tzinfo=UTC)


def test_cycle_slug_for_uses_cycle_epoch() -> None:
    cycle_start = datetime(2026, 3, 23, 13, 15, 0, tzinfo=UTC)

    slug = cycle_slug_for("BTC", cycle_start)

    assert slug == f"btc-updown-15m-{int(cycle_start.timestamp())}"


def test_adaptive_price_predictor_emits_signal_on_confirmed_pole() -> None:
    predictor = AdaptivePricePredictor(noise_threshold=0.01, min_history_points=4)
    signal = None
    for price in (0.40, 0.44, 0.49, 0.45, 0.41, 0.46, 0.51):
        signal = predictor.observe(price) or signal

    assert signal is not None
    assert signal.signal in {"BUY_UP", "BUY_DOWN"}
    assert 0.02 <= signal.predicted_price <= 0.98
    assert signal.confidence >= 0.5


def test_adaptive_price_predictor_snapshot_round_trip() -> None:
    predictor = AdaptivePricePredictor(noise_threshold=0.01, min_history_points=4)
    for price in (0.40, 0.44, 0.49, 0.45, 0.41, 0.46):
        predictor.observe(price)

    restored = AdaptivePricePredictor.from_snapshot(
        predictor.snapshot(),
        fallback_noise=0.02,
        fallback_history_points=6,
    )

    assert restored.history == predictor.history
    assert restored.min_history_points == predictor.min_history_points
    assert restored.noise_threshold == predictor.noise_threshold


def test_pair_signal_build_uses_primary_price_buffer_and_second_leg_formula() -> None:
    context = SimpleNamespace(
        settings=SimpleNamespace(
            copytrade_second_leg_base_price=0.98,
            copytrade_shares=2,
            copytrade_price_buffer=0.01,
        ),
        risk_config=SimpleNamespace(max_order_price=0.9),
    )
    engine = PairTradingEngine(context, connector=None)  # type: ignore[arg-type]
    cycle = PairCycleRuntime(
        asset_symbol="BTC",
        asset_name="Bitcoin",
        crypto_tier="btc",
        cycle_slug="btc-updown-15m-123",
        cycle_start=datetime(2026, 3, 23, 13, 15, tzinfo=UTC),
        market_id="pair-market-1",
        market_question="Will BTC be above current price in 15 minutes?",
        token_id_yes="token-yes-1",
        token_id_no="token-no-1",
        predictor=AdaptivePricePredictor(noise_threshold=0.01, min_history_points=4),
        side_counts={"yes": 0, "no": 0},
        max_buy_counts_per_side=1,
    )
    predictor_signal = PredictorSignal(
        predicted_price=0.53,
        pole_price=0.45,
        direction="up",
        signal="BUY_UP",
        confidence=0.72,
        features={},
        stats={},
    )
    yes_quote = Quote(token_id="token-yes-1", best_bid=0.44, best_ask=0.45, updated_at=datetime.now(UTC))
    no_quote = Quote(token_id="token-no-1", best_bid=0.52, best_ask=0.54, updated_at=datetime.now(UTC))

    signal = engine._build_signal(cycle, predictor_signal, yes_quote=yes_quote, no_quote=no_quote)

    assert signal.primary_leg.target_price == 0.46
    assert signal.primary_leg.reference_price == 0.45
    assert signal.hedge_leg.target_price == 0.53


def test_pair_signal_price_guard_blocks_extreme_leg_prices_before_publish() -> None:
    context = SimpleNamespace(
        settings=SimpleNamespace(
            copytrade_second_leg_base_price=0.98,
            copytrade_shares=1,
            copytrade_price_buffer=0.01,
        ),
        risk_config=SimpleNamespace(max_order_price=0.9),
    )
    engine = PairTradingEngine(context, connector=None)  # type: ignore[arg-type]
    cycle = PairCycleRuntime(
        asset_symbol="BTC",
        asset_name="Bitcoin",
        crypto_tier="btc",
        cycle_slug="btc-updown-15m-124",
        cycle_start=datetime(2026, 3, 23, 18, 30, tzinfo=UTC),
        market_id="pair-market-2",
        market_question="Will BTC be above current price in 15 minutes?",
        token_id_yes="token-yes-2",
        token_id_no="token-no-2",
        predictor=AdaptivePricePredictor(noise_threshold=0.01, min_history_points=4),
        side_counts={"yes": 0, "no": 0},
        max_buy_counts_per_side=1,
    )
    predictor_signal = PredictorSignal(
        predicted_price=0.96,
        pole_price=0.89,
        direction="up",
        signal="BUY_UP",
        confidence=0.76,
        features={},
        stats={},
    )
    yes_quote = Quote(token_id="token-yes-2", best_bid=0.88, best_ask=0.89, updated_at=datetime.now(UTC))
    no_quote = Quote(token_id="token-no-2", best_bid=0.12, best_ask=0.13, updated_at=datetime.now(UTC))

    signal = engine._build_signal(cycle, predictor_signal, yes_quote=yes_quote, no_quote=no_quote)

    assert signal.primary_leg.target_price == 0.9
    assert engine._signal_price_guard_reason(signal) is None

    extreme_yes_quote = Quote(token_id="token-yes-2", best_bid=0.95, best_ask=0.96, updated_at=datetime.now(UTC))
    extreme_signal = engine._build_signal(cycle, predictor_signal, yes_quote=extreme_yes_quote, no_quote=no_quote)

    assert extreme_signal.primary_leg.target_price == 0.97
    assert engine._signal_price_guard_reason(extreme_signal) == "primary leg exceeds max_order_price"


def test_pair_cycle_side_reservation_updates_local_state() -> None:
    cycle = PairCycleRuntime(
        asset_symbol="BTC",
        asset_name="Bitcoin",
        crypto_tier="btc",
        cycle_slug="btc-updown-15m-125",
        cycle_start=datetime(2026, 3, 23, 19, 0, tzinfo=UTC),
        market_id="pair-market-3",
        market_question="Will BTC be above current price in 15 minutes?",
        token_id_yes="token-yes-3",
        token_id_no="token-no-3",
        predictor=AdaptivePricePredictor(noise_threshold=0.01, min_history_points=4),
        side_counts={"yes": 0, "no": 0},
        max_buy_counts_per_side=1,
    )

    PairTradingEngine._reserve_cycle_side(cycle, "YES")
    PairTradingEngine._reserve_cycle_side(cycle, "YES")
    PairTradingEngine._reserve_cycle_side(cycle, "NO")

    assert cycle.side_counts == {"yes": 2, "no": 1}
