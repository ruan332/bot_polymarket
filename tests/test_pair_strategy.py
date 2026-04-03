from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

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


@pytest.mark.asyncio
async def test_pair_engine_blocks_low_confidence_before_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.signals: list[dict] = []
            self.pipeline: list[dict] = []
            self.snapshots: list[dict] = []

        async def get_market_snapshots(self, market_id: str, limit: int = 12):
            return [{"price_yes": 0.48}]

        async def get_pair_cycle(self, asset_symbol: str):
            return None

        async def has_recent_signal_duplicate(self, **kwargs):
            return False

        async def record_signal(self, signal_id: str, event_type: str, payload: dict):
            self.signals.append(payload)

        async def upsert_pair_cycle(self, payload):
            return None

        async def record_market_snapshots(self, snapshots):
            self.snapshots.extend(snapshot.model_dump(mode="json") for snapshot in snapshots)

        async def record_pipeline_telemetry(self, event_id: str, agent: str, event_type: str, payload: dict):
            self.pipeline.append(payload)

        async def get_open_positions(self):
            return [
                {
                    "market_id": "market-btc-15m",
                    "asset_symbol": "BTC",
                    "strategy_id": "momentum_15m",
                    "direction": "YES",
                },
                {
                    "market_id": "market-btc-15m",
                    "asset_symbol": "BTC",
                    "strategy_id": "pair_15m",
                    "direction": "NO",
                },
                {
                    "market_id": "market-eth-15m",
                    "asset_symbol": "ETH",
                    "strategy_id": "pair_15m",
                    "direction": "YES",
                },
            ]

    class FakeBus:
        async def publish_event(self, stream: str, payload: dict):
            return "1-0"

    class FakeConnector:
        async def resolve_copytrade_market(self, asset_symbol: str, cycle_start: datetime):
            return {
                "id": "market-btc-15m",
                "slug": "btc-updown-15m-1774271700",
                "question": "Will BTC be above current price in 15 minutes?",
                "asset_name": "Bitcoin",
                "token_id_yes": "token-yes",
                "token_id_no": "token-no",
                "volume_24h": 25000.0,
                "end_date": "2026-03-24T13:30:00Z",
            }

        async def get_orderbook_summary(self, token_id: str):
            if token_id == "token-yes":
                return {"best_bid": 0.47, "best_ask": 0.48, "spread_bps": 18.0, "bid_depth": 900.0, "ask_depth": 850.0}
            return {"best_bid": 0.51, "best_ask": 0.52, "spread_bps": 18.0, "bid_depth": 900.0, "ask_depth": 850.0}

        async def stream_market(self, asset_ids):
            if False:
                yield {}

    class FakeRisk:
        def __init__(self) -> None:
            self.blocks: list[str] = []

        async def validate_signal(self, signal):
            return None

        async def record_block(self, agent: str, reason: str, details: dict):
            self.blocks.append(reason)

    repository = FakeRepository()
    context = SimpleNamespace(
        settings=SimpleNamespace(
            copytrade_markets=["BTC"],
            copytrade_shares=2,
            copytrade_price_buffer=0.01,
            copytrade_second_leg_base_price=0.98,
            copytrade_signal_confidence_threshold=0.95,
            copytrade_noise_threshold=0.01,
            copytrade_min_history_points=4,
            copytrade_max_buy_counts_per_side=1,
            copytrade_wait_for_next_market_start=False,
            live_trading=False,
            news_validation_enabled=False,
            copytrade_enabled=True,
        ),
        risk_config=SimpleNamespace(max_order_price=0.9, max_spread_bps=250, max_slippage_bps=150),
        crypto_config=SimpleNamespace(major_assets=["ETH", "SOL"]),
        repository=repository,
        bus=FakeBus(),
    )
    engine = PairTradingEngine(context, FakeConnector())  # type: ignore[arg-type]
    engine.risk = FakeRisk()  # type: ignore[assignment]

    async def ensure_feed():
        return None

    engine._ensure_feed = ensure_feed  # type: ignore[method-assign,assignment]
    monkeypatch.setattr(
        AdaptivePricePredictor,
        "observe",
        lambda self, price: PredictorSignal(
            predicted_price=0.51,
            pole_price=0.48,
            direction="up",
            signal="BUY_UP",
            confidence=0.59,
            features={},
            stats={"accuracy": 0.5, "history_points": 4.0},
        ),
    )

    stats = await engine.tick()

    assert stats["persisted_signals"] == 0
    assert stats["risk_blocked"] == 1
    assert stats["risk_block_reasons"]["confidence_below_threshold"] == 1
    telemetry = repository.pipeline[0]["market_coexistence"]
    assert telemetry["has_momentum_pair_coexistence"] is True
    assert telemetry["momentum_pair_market_count"] == 1
    assert telemetry["momentum_pair_markets"][0]["market_id"] == "market-btc-15m"
    assert set(telemetry["momentum_pair_markets"][0]["strategy_ids"]) == {"momentum_15m", "pair_15m"}


@pytest.mark.asyncio
async def test_pair_engine_blocks_same_direction_during_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.signals: list[dict] = []
            self.pipeline: list[dict] = []
            self.snapshots: list[dict] = []

        async def get_market_snapshots(self, market_id: str, limit: int = 12):
            return [{"price_yes": 0.48}]

        async def get_pair_cycle(self, asset_symbol: str):
            cycle_start = floor_cycle_start(datetime.now(UTC))
            return {
                "asset_name": "Bitcoin",
                "crypto_tier": "btc",
                "cycle_slug": cycle_slug_for(asset_symbol, cycle_start),
                "cycle_start": cycle_start,
                "market_id": "market-btc-15m",
                "market_question": "Will BTC be above current price in 15 minutes?",
                "token_id_yes": "token-yes",
                "token_id_no": "token-no",
                "predictor_state": None,
                "side_counts": {"yes": 0, "no": 0},
                "max_buy_counts_per_side": 1,
                "price_yes": 0.48,
                "price_no": 0.52,
                "status": "active",
                "last_signal_direction": "YES",
                "last_signal_at": datetime.now(UTC),
                "last_quote_at": datetime.now(UTC),
                "metadata": {"volume_24h": 25000.0, "end_date": "2026-03-24T13:30:00Z"},
            }

        async def has_recent_signal_duplicate(self, **kwargs):
            return False

        async def record_signal(self, signal_id: str, event_type: str, payload: dict):
            self.signals.append(payload)

        async def upsert_pair_cycle(self, payload):
            self.snapshots.append(payload.model_dump(mode="json"))

        async def record_market_snapshots(self, snapshots):
            self.snapshots.extend(snapshot.model_dump(mode="json") for snapshot in snapshots)

        async def record_pipeline_telemetry(self, event_id: str, agent: str, event_type: str, payload: dict):
            self.pipeline.append(payload)

        async def get_open_positions(self):
            return []

    class FakeBus:
        async def publish_event(self, stream: str, payload: dict):
            return "1-0"

    class FakeConnector:
        async def resolve_copytrade_market(self, asset_symbol: str, cycle_start: datetime):
            return {
                "id": "market-btc-15m",
                "slug": "btc-updown-15m-1774271700",
                "question": "Will BTC be above current price in 15 minutes?",
                "asset_name": "Bitcoin",
                "token_id_yes": "token-yes",
                "token_id_no": "token-no",
                "volume_24h": 25000.0,
                "end_date": "2026-03-24T13:30:00Z",
            }

        async def get_orderbook_summary(self, token_id: str):
            if token_id == "token-yes":
                return {"best_bid": 0.47, "best_ask": 0.48, "spread_bps": 18.0, "bid_depth": 900.0, "ask_depth": 850.0}
            return {"best_bid": 0.51, "best_ask": 0.52, "spread_bps": 18.0, "bid_depth": 900.0, "ask_depth": 850.0}

        async def stream_market(self, asset_ids):
            if False:
                yield {}

    class FakeRisk:
        def __init__(self) -> None:
            self.blocks: list[str] = []

        async def validate_signal(self, signal):
            return None

        async def record_block(self, agent: str, reason: str, details: dict):
            self.blocks.append(reason)

    repository = FakeRepository()
    context = SimpleNamespace(
        settings=SimpleNamespace(
            copytrade_markets=["BTC"],
            copytrade_shares=2,
            copytrade_price_buffer=0.01,
            copytrade_second_leg_base_price=0.98,
            copytrade_signal_confidence_threshold=0.62,
            copytrade_noise_threshold=0.03,
            copytrade_min_history_points=8,
            copytrade_signal_cooldown_minutes=30,
            copytrade_max_buy_counts_per_side=1,
            copytrade_wait_for_next_market_start=False,
            live_trading=False,
            news_validation_enabled=False,
            copytrade_enabled=True,
        ),
        risk_config=SimpleNamespace(max_order_price=0.9, max_spread_bps=250, max_slippage_bps=150),
        crypto_config=SimpleNamespace(major_assets=["ETH", "SOL"]),
        repository=repository,
        bus=FakeBus(),
    )
    engine = PairTradingEngine(context, FakeConnector())  # type: ignore[arg-type]
    engine.risk = FakeRisk()  # type: ignore[assignment]

    async def ensure_feed():
        return None

    engine._ensure_feed = ensure_feed  # type: ignore[method-assign,assignment]
    monkeypatch.setattr(
        AdaptivePricePredictor,
        "observe",
        lambda self, price: PredictorSignal(
            predicted_price=0.51,
            pole_price=0.48,
            direction="up",
            signal="BUY_UP",
            confidence=0.72,
            features={},
            stats={"accuracy": 0.5, "history_points": 8.0},
        ),
    )

    stats = await engine.tick()

    assert stats["persisted_signals"] == 0
    assert stats["risk_blocked"] == 1
    assert stats["risk_block_reasons"]["signal_cooldown_active"] == 1
