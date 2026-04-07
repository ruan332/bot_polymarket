from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.momentum_strategy import MomentumTradingEngine


class FakeConnector:
    def __init__(self):
        self.orderbook_calls = 0
        self.market = {
            "id": "market-btc-15m",
            "slug": "btc-updown-15m-1774271700",
            "question": "Will BTC be above current price in 15 minutes?",
            "asset_name": "Bitcoin",
            "token_id_yes": "token-yes",
            "token_id_no": "token-no",
            "price_yes": 0.54,
            "price_no": 0.46,
            "volume_24h": 25000.0,
            "end_date": "2026-03-24T13:30:00Z",
        }
        self.yes_summary = {"best_bid": 0.53, "best_ask": 0.54, "spread_bps": 18.0, "bid_depth": 900.0, "ask_depth": 850.0}
        self.no_summary = {"best_bid": 0.45, "best_ask": 0.46, "spread_bps": 18.0, "bid_depth": 900.0, "ask_depth": 850.0}

    async def resolve_copytrade_market(self, asset_symbol: str, cycle_start: datetime):
        return dict(self.market)

    async def get_orderbook_summary(self, token_id: str):
        self.orderbook_calls += 1
        if token_id == "token-yes":
            return dict(self.yes_summary)
        return dict(self.no_summary)

    async def stream_market(self, asset_ids):
        if False:
            yield {}


class FakeBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def publish_event(self, stream: str, payload: dict):
        self.events.append((stream, payload))
        return "1-0"


class FakeRepository:
    def __init__(self):
        self.signals: list[dict] = []
        self.pipeline: list[dict] = []
        self.snapshots: list[dict] = []

    async def get_market_snapshots(self, market_id: str, limit: int = 12):
        return [
            {"price_yes": 0.42},
            {"price_yes": 0.44},
            {"price_yes": 0.47},
            {"price_yes": 0.49},
            {"price_yes": 0.51},
            {"price_yes": 0.54},
        ]

    async def has_recent_signal_duplicate(self, **kwargs):
        return False

    async def record_signal(self, signal_id: str, event_type: str, payload: dict):
        self.signals.append(payload)

    async def record_market_snapshots(self, snapshots):
        self.snapshots.extend(snapshot.model_dump(mode="json") for snapshot in snapshots)

    async def record_pipeline_telemetry(self, event_id: str, agent: str, event_type: str, payload: dict):
        self.pipeline.append(payload)


class FakeRisk:
    def __init__(self):
        self.blocks: list[str] = []

    async def validate_signal(self, signal):
        return None

    async def record_block(self, agent: str, reason: str, details: dict):
        self.blocks.append(reason)


@pytest.mark.asyncio
async def test_momentum_engine_publishes_signal_for_valid_market() -> None:
    repository = FakeRepository()
    bus = FakeBus()
    context = SimpleNamespace(
        settings=SimpleNamespace(
            momentum_enabled=True,
            momentum_markets=["BTC"],
            momentum_trading_enabled=True,
            momentum_signal_confidence_threshold=0.55,
            momentum_min_history_points=6,
            momentum_cooldown_minutes=20,
            momentum_wait_for_next_market_start=False,
            live_trading=False,
            news_validation_enabled=False,
        ),
        risk_config=SimpleNamespace(
            min_edge=0.05,
            min_confidence=0.5,
            max_spread_bps=250,
            max_slippage_bps=150,
            max_order_price=0.9,
            min_market_volume_24h=1000.0,
        ),
        crypto_config=SimpleNamespace(major_assets=["ETH", "SOL"]),
        repository=repository,
        bus=bus,
    )
    engine = MomentumTradingEngine(context, FakeConnector())  # type: ignore[arg-type]
    engine.risk = FakeRisk()  # type: ignore[assignment]

    async def ensure_feed():
        return None

    engine._ensure_feed = ensure_feed  # type: ignore[method-assign,assignment]

    async def analyze_market(_market):
        return {
            "direction": "YES",
            "regime": "trend",
            "model_probability": 0.63,
            "market_probability": 0.54,
            "edge": 0.08,
            "confidence": 0.74,
            "expected_slippage_bps": 15.0,
            "expected_holding_minutes": 45,
            "features_summary": {"momentum_short": 0.03, "momentum_medium": 0.05, "orderbook_bias": 0.12},
        }

    engine._analyze_market = analyze_market  # type: ignore[method-assign,assignment]

    stats = await engine.tick()

    assert stats["persisted_signals"] == 1
    assert repository.signals[0]["strategy_id"] == "momentum_15m"
    assert repository.signals[0]["market_id"] == "market-btc-15m"
    assert bus.events[0][0] == "signals:validated"
    assert repository.pipeline[0]["strategy_id"] == "momentum_15m"


@pytest.mark.asyncio
async def test_momentum_engine_records_coexistence_summary_in_telemetry() -> None:
    repository = FakeRepository()
    bus = FakeBus()
    context = SimpleNamespace(
        settings=SimpleNamespace(
            momentum_enabled=True,
            momentum_markets=["BTC"],
            momentum_trading_enabled=True,
            momentum_signal_confidence_threshold=0.55,
            momentum_min_history_points=6,
            momentum_cooldown_minutes=20,
            momentum_wait_for_next_market_start=False,
            live_trading=False,
            news_validation_enabled=False,
        ),
        risk_config=SimpleNamespace(
            min_edge=0.05,
            min_confidence=0.5,
            max_spread_bps=250,
            max_slippage_bps=150,
            max_order_price=0.9,
            min_market_volume_24h=1000.0,
        ),
        crypto_config=SimpleNamespace(major_assets=["ETH", "SOL"]),
        repository=repository,
        bus=bus,
    )
    engine = MomentumTradingEngine(context, FakeConnector())  # type: ignore[arg-type]
    engine.risk = FakeRisk()  # type: ignore[assignment]

    async def ensure_feed():
        return None

    async def get_open_positions():
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
                "strategy_id": "momentum_15m",
                "direction": "YES",
            },
        ]

    repository.get_open_positions = get_open_positions  # type: ignore[attr-defined,method-assign]
    engine._ensure_feed = ensure_feed  # type: ignore[method-assign,assignment]

    async def analyze_market(_market):
        return {
            "direction": "YES",
            "regime": "trend",
            "model_probability": 0.63,
            "market_probability": 0.54,
            "edge": 0.08,
            "confidence": 0.74,
            "expected_slippage_bps": 15.0,
            "expected_holding_minutes": 45,
            "features_summary": {"momentum_short": 0.03, "momentum_medium": 0.05, "orderbook_bias": 0.12},
        }

    engine._analyze_market = analyze_market  # type: ignore[method-assign,assignment]

    stats = await engine.tick()

    assert stats["persisted_signals"] == 1
    telemetry = repository.pipeline[0]["market_coexistence"]
    assert telemetry["has_momentum_pair_coexistence"] is True
    assert telemetry["momentum_pair_market_count"] == 1
    assert telemetry["momentum_pair_markets"][0]["market_id"] == "market-btc-15m"
    assert set(telemetry["momentum_pair_markets"][0]["strategy_ids"]) == {"momentum_15m", "pair_15m"}


@pytest.mark.asyncio
async def test_momentum_engine_blocks_low_confidence_before_publish() -> None:
    repository = FakeRepository()
    bus = FakeBus()
    context = SimpleNamespace(
        settings=SimpleNamespace(
            momentum_enabled=True,
            momentum_markets=["BTC"],
            momentum_trading_enabled=True,
            momentum_signal_confidence_threshold=0.95,
            momentum_min_history_points=6,
            momentum_cooldown_minutes=20,
            momentum_wait_for_next_market_start=False,
            live_trading=False,
            news_validation_enabled=False,
        ),
        risk_config=SimpleNamespace(
            min_edge=0.05,
            min_confidence=0.5,
            max_spread_bps=250,
            max_slippage_bps=150,
            max_order_price=0.9,
            min_market_volume_24h=1000.0,
        ),
        crypto_config=SimpleNamespace(major_assets=["ETH", "SOL"]),
        repository=repository,
        bus=bus,
    )
    engine = MomentumTradingEngine(context, FakeConnector())  # type: ignore[arg-type]
    engine.risk = FakeRisk()  # type: ignore[assignment]

    async def ensure_feed():
        return None

    engine._ensure_feed = ensure_feed  # type: ignore[method-assign,assignment]

    async def analyze_market(_market):
        return {
            "direction": "YES",
            "regime": "trend",
            "model_probability": 0.63,
            "market_probability": 0.54,
            "edge": 0.08,
            "confidence": 0.80,
            "expected_slippage_bps": 15.0,
            "expected_holding_minutes": 45,
            "features_summary": {"momentum_short": 0.03, "momentum_medium": 0.05, "orderbook_bias": 0.12},
        }

    engine._analyze_market = analyze_market  # type: ignore[method-assign,assignment]

    stats = await engine.tick()

    assert stats["persisted_signals"] == 0
    assert stats["pre_risk_blocked"] == 1
    assert stats["pre_risk_block_reasons"]["confidence_below_threshold"] == 1
    assert stats["risk_blocked"] == 0
    assert repository.signals == []


@pytest.mark.asyncio
async def test_momentum_engine_skips_low_liquidity_markets_before_orderbook_fetch() -> None:
    repository = FakeRepository()
    bus = FakeBus()
    connector = FakeConnector()
    connector.market["volume_24h"] = 5.0
    context = SimpleNamespace(
        settings=SimpleNamespace(
            momentum_enabled=True,
            momentum_markets=["BTC"],
            momentum_trading_enabled=True,
            momentum_signal_confidence_threshold=0.55,
            momentum_min_history_points=6,
            momentum_cooldown_minutes=20,
            momentum_wait_for_next_market_start=False,
            live_trading=False,
            news_validation_enabled=False,
            momentum_min_volume_24h=30.0,
        ),
        risk_config=SimpleNamespace(
            min_edge=0.05,
            min_confidence=0.5,
            max_spread_bps=250,
            max_slippage_bps=150,
            max_order_price=0.9,
            min_market_volume_24h=1000.0,
        ),
        crypto_config=SimpleNamespace(major_assets=["ETH", "SOL"]),
        repository=repository,
        bus=bus,
    )
    engine = MomentumTradingEngine(context, connector)  # type: ignore[arg-type]
    engine.risk = FakeRisk()  # type: ignore[assignment]

    async def ensure_feed(_cycles=None):
        return None

    async def analyze_market(_market):
        raise AssertionError("low-liquidity market should be filtered before analysis")

    engine._ensure_feed = ensure_feed  # type: ignore[method-assign,assignment]
    engine._analyze_market = analyze_market  # type: ignore[method-assign,assignment]

    stats = await engine.tick()

    assert stats["persisted_signals"] == 0
    assert stats["liquidity_prefilter_blocked"] == 1
    assert stats["pre_risk_blocked"] == 1
    assert connector.orderbook_calls == 0


@pytest.mark.asyncio
async def test_momentum_analysis_rejects_shallow_or_wide_book() -> None:
    repository = FakeRepository()
    context = SimpleNamespace(
        settings=SimpleNamespace(
            momentum_enabled=True,
            momentum_markets=["BTC"],
            momentum_trading_enabled=True,
            momentum_signal_confidence_threshold=0.55,
            momentum_min_history_points=6,
            momentum_cooldown_minutes=20,
            momentum_wait_for_next_market_start=False,
            live_trading=False,
            news_validation_enabled=False,
        ),
        risk_config=SimpleNamespace(
            min_edge=0.05,
            min_confidence=0.5,
            max_spread_bps=250,
            max_slippage_bps=150,
            max_order_price=0.9,
            min_market_volume_24h=1000.0,
        ),
        crypto_config=SimpleNamespace(major_assets=["ETH", "SOL"]),
        repository=repository,
        bus=FakeBus(),
    )
    connector = FakeConnector()
    connector.yes_summary = {"best_bid": 0.53, "best_ask": 0.54, "spread_bps": 290.0, "bid_depth": 8.0, "ask_depth": 8.0}
    engine = MomentumTradingEngine(context, connector)  # type: ignore[arg-type]

    decision = await engine._analyze_market(
        {
            "id": "market-btc-15m",
            "price_yes": 0.54,
            "price_no": 0.46,
            "orderbook_summary_yes": connector.yes_summary,
            "orderbook_summary_no": connector.no_summary,
        }
    )

    assert decision.decision is None
    assert decision.pre_risk_reason and "spread too wide" in decision.pre_risk_reason


@pytest.mark.asyncio
async def test_momentum_analysis_accepts_marginal_edge_after_quality_floor_relaxation() -> None:
    class QualityFloorConnector(FakeConnector):
        def __init__(self) -> None:
            super().__init__()
            self.yes_summary = {"best_bid": 0.51, "best_ask": 0.52, "spread_bps": 100.0, "bid_depth": 900.0, "ask_depth": 850.0}
            self.no_summary = {"best_bid": 0.47, "best_ask": 0.48, "spread_bps": 100.0, "bid_depth": 900.0, "ask_depth": 850.0}

    class QualityFloorRepository(FakeRepository):
        async def get_market_snapshots(self, market_id: str, limit: int = 12):
            return [
                {"price_yes": 0.45},
                {"price_yes": 0.47},
                {"price_yes": 0.48},
                {"price_yes": 0.49},
                {"price_yes": 0.50},
                {"price_yes": 0.51},
            ]

    repository = QualityFloorRepository()
    context = SimpleNamespace(
        settings=SimpleNamespace(
            momentum_enabled=True,
            momentum_markets=["BTC"],
            momentum_trading_enabled=True,
            momentum_signal_confidence_threshold=0.55,
            momentum_min_history_points=6,
            momentum_cooldown_minutes=20,
            momentum_wait_for_next_market_start=False,
            live_trading=False,
            news_validation_enabled=False,
            momentum_min_edge=0.10,
            momentum_min_volume_24h=1000.0,
        ),
        risk_config=SimpleNamespace(
            min_edge=0.05,
            min_confidence=0.5,
            max_spread_bps=250,
            max_slippage_bps=150,
            max_order_price=0.9,
            min_market_volume_24h=1000.0,
        ),
        crypto_config=SimpleNamespace(major_assets=["ETH", "SOL"]),
        repository=repository,
        bus=FakeBus(),
    )
    engine = MomentumTradingEngine(context, QualityFloorConnector())  # type: ignore[arg-type]

    decision = await engine._analyze_market(
        {
            "id": "market-btc-15m",
            "price_yes": 0.51,
            "price_no": 0.49,
            "orderbook_summary_yes": engine.connector.yes_summary,
            "orderbook_summary_no": engine.connector.no_summary,
        }
    )

    assert decision.decision is not None
    assert decision.decision["edge"] >= 0.08
    assert decision.decision["confidence"] >= 0.7


@pytest.mark.asyncio
async def test_momentum_engine_counts_prerisk_rejections_in_scan_telemetry() -> None:
    repository = FakeRepository()
    bus = FakeBus()
    context = SimpleNamespace(
        settings=SimpleNamespace(
            momentum_enabled=True,
            momentum_markets=["BTC"],
            momentum_trading_enabled=True,
            momentum_signal_confidence_threshold=0.55,
            momentum_min_history_points=6,
            momentum_cooldown_minutes=20,
            momentum_wait_for_next_market_start=False,
            live_trading=False,
            news_validation_enabled=False,
        ),
        risk_config=SimpleNamespace(
            min_edge=0.05,
            min_confidence=0.5,
            max_spread_bps=250,
            max_slippage_bps=150,
            max_order_price=0.9,
            min_market_volume_24h=1000.0,
        ),
        crypto_config=SimpleNamespace(major_assets=["ETH", "SOL"]),
        repository=repository,
        bus=bus,
    )
    connector = FakeConnector()
    connector.yes_summary = {"best_bid": 0.53, "best_ask": 0.61, "spread_bps": 240.0, "bid_depth": 10.0, "ask_depth": 10.0}
    engine = MomentumTradingEngine(context, connector)  # type: ignore[arg-type]
    engine.risk = FakeRisk()  # type: ignore[assignment]
    engine.risk.config = context.risk_config  # type: ignore[attr-defined]

    async def ensure_feed():
        return None

    engine._ensure_feed = ensure_feed  # type: ignore[method-assign,assignment]

    stats = await engine.tick()

    assert stats["pre_risk_blocked"] >= 1
    assert stats["pre_risk_block_reasons"]
    assert stats["risk_blocked"] == 0
