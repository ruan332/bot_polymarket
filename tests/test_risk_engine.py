from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.config import load_crypto_config
from core.exceptions import RiskBlockedError
from core.risk_engine import RiskEngine
from core.schemas import PairLegPlan, PairReviewPayload, PairSignalPayload, PortfolioSummary, ReviewPayload, SignalPayload


def make_context(
    *,
    positions: list[dict[str, object]] | None = None,
    execution_state: dict[str, object] | None = None,
):
    crypto_config = load_crypto_config()
    open_positions = positions or []
    execution_state = execution_state or {
        "daily_spend_usd": 0.0,
        "realized_pnl_usd": 0.0,
        "consecutive_losses": 0,
        "last_loss_at": None,
    }

    class Repository:
        def __init__(self):
            self.summary = PortfolioSummary(
                available_balance=1000.0,
                total_exposure=sum(float(item.get("cost_basis_usd") or 0.0) for item in open_positions),
                current_market_value=0.0,
                total_equity=1000.0,
                total_pnl=0.0,
                open_positions=len(open_positions),
                realized_pnl=0.0,
                unrealized_pnl=0.0,
            )

        async def get_portfolio_summary(self) -> PortfolioSummary:
            return self.summary

        async def get_open_positions(self) -> list[dict[str, object]]:
            return open_positions

        async def get_execution_risk_state(self, hours: int = 24) -> dict[str, object]:
            return execution_state

    return SimpleNamespace(
        settings=SimpleNamespace(
            paper_bankroll_usd=1000.0,
            momentum_max_positions=2,
            momentum_min_edge=0.085,
            momentum_min_volume_24h=500.0,
        ),
        risk_config=SimpleNamespace(
            min_edge=0.19,
            min_confidence=0.55,
            max_kelly_fraction=0.25,
            max_single_exposure_fraction=0.10,
            max_asset_exposure_fraction=0.18,
            max_strategy_exposure_fraction=0.25,
            max_single_position_usd=100.0,
            max_total_exposure_usd=250.0,
            min_market_volume_24h=10000.0,
            max_daily_spend_usd=100.0,
            max_spread_bps=250,
            max_slippage_bps=150,
            max_order_price=0.90,
            max_open_positions=5,
            default_limit_buffer_bps=50,
            circuit_breaker_loss_threshold_usd=100.0,
            circuit_breaker_cooldown_seconds=300,
            daily_drawdown_limit_fraction=0.08,
            loss_streak_size_discount=0.15,
            min_risk_fraction_after_losses=0.35,
            exit_scale_out_fraction=0.5,
            synthetic_asset_exposure_fraction=0.10,
        ),
        crypto_config=crypto_config,
        repository=Repository(),
    )


def make_signal(
    *,
    symbol: str,
    tier: str,
    edge: float,
    confidence: float,
    price: float,
    volume_24h: float,
    market_kind: str = "direct_coin",
) -> SignalPayload:
    return SignalPayload(
        signal_id=f"signal-{symbol.lower()}",
        market_id=f"market-{symbol.lower()}",
        token_id=f"token-{symbol.lower()}",
        market_question=f"Will {symbol} move higher?",
        direction="YES",
        edge=edge,
        confidence=confidence,
        price=price,
        price_yes=price,
        price_no=round(1 - price, 4),
        volume_24h=volume_24h,
        asset_symbol=symbol,
        asset_name=symbol,
        crypto_tier=tier,  # type: ignore[arg-type]
        market_kind=market_kind,
        question_type="direction",
        strategy_id="trend_follow_bayes",
        strategy_version="v1",
        model_probability=round(price + edge, 4),
        market_probability=price,
        regime="trend",
        expected_slippage_bps=50.0,
        expected_holding_minutes=180,
        thesis_tags=[symbol.lower(), tier],
        thesis_hash=f"{symbol.lower()}-{tier}",
        reasoning="test",
        features_summary={"momentum_short": 0.04},
        liquidity_summary={"spread_bps": 100.0, "ask_depth": 500.0},
    )


def make_review(signal: SignalPayload) -> ReviewPayload:
    return ReviewPayload(
        signal_id=signal.signal_id,
        asset_symbol=signal.asset_symbol,
        crypto_tier=signal.crypto_tier,
        approved=True,
        kelly_size=0,
        risk_fraction=0.1,
        notes="test",
        original_signal=signal,
    )


def make_pair_signal() -> PairSignalPayload:
    return PairSignalPayload(
        signal_id="pair-signal-1",
        trade_group_id="pair-group-1",
        cycle_slug="btc-updown-15m-123",
        cycle_start=datetime.now(UTC),
        market_id="pair-market-1",
        market_question="Will BTC be above current price in 15 minutes?",
        asset_symbol="BTC",
        asset_name="Bitcoin",
        crypto_tier="btc",
        predictor_direction="up",
        predictor_signal="BUY_UP",
        predictor_confidence=0.7,
        side_count_state={"yes": 0, "no": 0, "max_per_side": 2},
        primary_leg=PairLegPlan(
            market_id="pair-market-1",
            token_id="token-yes-1",
            direction="YES",
            leg_role="primary",
            size=2,
            target_price=0.45,
            reference_price=0.45,
            current_ask=0.45,
            current_bid=0.44,
        ),
        hedge_leg=PairLegPlan(
            market_id="pair-market-1",
            token_id="token-no-1",
            direction="NO",
            leg_role="hedge",
            size=2,
            target_price=0.40,
            reference_price=0.45,
            current_ask=0.42,
            current_bid=0.41,
        ),
        reasoning="pair signal",
    )


def make_pair_review(signal: PairSignalPayload) -> PairReviewPayload:
    return PairReviewPayload(
        signal_id=signal.signal_id,
        trade_group_id=signal.trade_group_id,
        asset_symbol=signal.asset_symbol,
        crypto_tier=signal.crypto_tier,
        approved=True,
        approved_primary_leg=signal.primary_leg,
        approved_hedge_leg=signal.hedge_leg,
        notes="pair ok",
        original_signal=signal,
    )


@pytest.mark.asyncio
async def test_validate_signal_respects_crypto_tier_thresholds() -> None:
    risk = RiskEngine(make_context())
    btc_signal = make_signal(symbol="BTC", tier="btc", edge=0.21, confidence=0.61, price=0.40, volume_24h=50000.0)
    small_cap_signal = make_signal(
        symbol="PEPE",
        tier="small_cap",
        edge=0.21,
        confidence=0.61,
        price=0.40,
        volume_24h=50000.0,
    )

    await risk.validate_signal(btc_signal)
    with pytest.raises(RiskBlockedError):
        await risk.validate_signal(small_cap_signal)


@pytest.mark.asyncio
async def test_build_execution_guard_blocks_small_caps_more_aggressively() -> None:
    risk = RiskEngine(make_context())
    btc_signal = make_signal(symbol="BTC", tier="btc", edge=0.40, confidence=0.80, price=0.40, volume_24h=100000.0)
    small_cap_signal = make_signal(
        symbol="PEPE",
        tier="small_cap",
        edge=0.40,
        confidence=0.80,
        price=0.40,
        volume_24h=100000.0,
    )

    guard = await risk.build_execution_guard(make_review(btc_signal))
    assert guard.size == 250
    assert guard.notional_usd == pytest.approx(100.0)
    small_cap_guard = await risk.build_execution_guard(make_review(small_cap_signal))
    assert small_cap_guard.notional_usd == pytest.approx(34.8)
    assert small_cap_guard.size < guard.size


@pytest.mark.asyncio
async def test_build_execution_guard_treats_zero_daily_spend_as_unlimited() -> None:
    risk = RiskEngine(
        make_context(
            execution_state={
                "daily_spend_usd": 9_999.0,
                "realized_pnl_usd": 0.0,
                "consecutive_losses": 0,
                "last_loss_at": None,
            }
        )
    )
    risk.config.max_daily_spend_usd = 0.0
    signal = make_signal(symbol="BTC", tier="btc", edge=0.40, confidence=0.80, price=0.40, volume_24h=100000.0)

    guard = await risk.build_execution_guard(make_review(signal))

    assert guard.notional_usd == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_validate_signal_requires_stronger_thresholds_for_indirect_crypto() -> None:
    risk = RiskEngine(make_context())
    indirect_signal = make_signal(
        symbol="BTC",
        tier="btc",
        edge=0.15,
        confidence=0.63,
        price=0.40,
        volume_24h=6000.0,
        market_kind="indirect_crypto",
    )

    with pytest.raises(RiskBlockedError, match="edge below minimum"):
        await risk.validate_signal(indirect_signal)


@pytest.mark.asyncio
async def test_validate_signal_uses_momentum_specific_edge_floor() -> None:
    risk = RiskEngine(make_context())
    signal = make_signal(symbol="ETH", tier="major", edge=0.09, confidence=0.70, price=0.40, volume_24h=2000.0)
    signal.strategy_id = "momentum_15m"

    await risk.validate_signal(signal)


@pytest.mark.asyncio
async def test_validate_signal_uses_momentum_specific_volume_floor() -> None:
    risk = RiskEngine(make_context())
    signal = make_signal(symbol="ETH", tier="major", edge=0.20, confidence=0.70, price=0.40, volume_24h=600.0)
    signal.strategy_id = "momentum_15m"

    await risk.validate_signal(signal)


@pytest.mark.asyncio
async def test_build_execution_guard_scales_down_indirect_crypto_positions() -> None:
    risk = RiskEngine(make_context())
    direct_signal = make_signal(symbol="BTC", tier="btc", edge=0.40, confidence=0.80, price=0.40, volume_24h=100000.0)
    indirect_signal = make_signal(
        symbol="BTC",
        tier="btc",
        edge=0.40,
        confidence=0.80,
        price=0.40,
        volume_24h=100000.0,
        market_kind="indirect_crypto",
    )

    direct_guard = await risk.build_execution_guard(make_review(direct_signal))
    indirect_guard = await risk.build_execution_guard(make_review(indirect_signal))

    assert indirect_guard.notional_usd < direct_guard.notional_usd
    assert indirect_guard.size < direct_guard.size


@pytest.mark.asyncio
async def test_build_execution_guard_allows_missing_news_validation() -> None:
    risk = RiskEngine(make_context())
    signal = make_signal(symbol="BTC", tier="btc", edge=0.40, confidence=0.80, price=0.40, volume_24h=100000.0)

    guard = await risk.build_execution_guard(make_review(signal))

    assert guard.size == 250
    assert guard.price_limit == pytest.approx(0.405)


@pytest.mark.asyncio
async def test_build_execution_guard_blocks_opposite_position_same_market() -> None:
    existing_signal = make_signal(
        symbol="BTC",
        tier="btc",
        edge=0.40,
        confidence=0.80,
        price=0.40,
        volume_24h=100000.0,
    )
    incoming_signal = make_signal(
        symbol="BTC",
        tier="btc",
        edge=0.40,
        confidence=0.80,
        price=0.60,
        volume_24h=100000.0,
    )
    incoming_signal.direction = "NO"
    incoming_signal.market_id = existing_signal.market_id

    risk = RiskEngine(
        make_context(
            positions=[
                {
                    "market_id": existing_signal.market_id,
                    "direction": "YES",
                    "asset_symbol": "BTC",
                    "strategy_id": existing_signal.strategy_id,
                    "cost_basis_usd": 40.0,
                }
            ]
        )
    )

    with pytest.raises(RiskBlockedError, match="opposite position already open for this market"):
        await risk.build_execution_guard(make_review(incoming_signal))


@pytest.mark.asyncio
async def test_build_execution_guard_blocks_when_pair_position_exists_for_same_market() -> None:
    incoming_signal = make_signal(
        symbol="BTC",
        tier="btc",
        edge=0.40,
        confidence=0.80,
        price=0.40,
        volume_24h=100000.0,
    )
    incoming_signal.strategy_id = "momentum_15m"
    risk = RiskEngine(
        make_context(
            positions=[
                {
                    "market_id": incoming_signal.market_id,
                    "direction": "YES",
                    "asset_symbol": "BTC",
                    "strategy_id": "pair_15m",
                    "cost_basis_usd": 20.0,
                }
            ]
        )
    )

    with pytest.raises(RiskBlockedError, match="pair position already open for this market"):
        await risk.build_execution_guard(make_review(incoming_signal))


@pytest.mark.asyncio
async def test_build_execution_guard_enforces_momentum_max_positions() -> None:
    signal = make_signal(
        symbol="ETH",
        tier="major",
        edge=0.40,
        confidence=0.80,
        price=0.40,
        volume_24h=100000.0,
    )
    signal.strategy_id = "momentum_15m"
    risk = RiskEngine(
        make_context(
            positions=[
                {
                    "market_id": "market-btc",
                    "direction": "YES",
                    "asset_symbol": "BTC",
                    "strategy_id": "momentum_15m",
                    "cost_basis_usd": 20.0,
                },
                {
                    "market_id": "market-sol",
                    "direction": "NO",
                    "asset_symbol": "SOL",
                    "strategy_id": "momentum_15m",
                    "cost_basis_usd": 20.0,
                },
            ]
        )
    )

    with pytest.raises(RiskBlockedError, match="momentum max positions reached"):
        await risk.build_execution_guard(make_review(signal))


@pytest.mark.asyncio
async def test_build_execution_guard_uses_tighter_exposure_cap_for_synthetic_crypto_asset() -> None:
    risk = RiskEngine(
        make_context(
            positions=[
                {
                    "market_id": "market-crypto-existing",
                    "direction": "YES",
                    "asset_symbol": "CRYPTO",
                    "strategy_id": "trend_follow_bayes",
                    "cost_basis_usd": 90.0,
                }
            ]
        )
    )
    incoming_signal = make_signal(
        symbol="CRYPTO",
        tier="small_cap",
        edge=0.40,
        confidence=0.85,
        price=0.40,
        volume_24h=100000.0,
        market_kind="indirect_crypto",
    )

    with pytest.raises(RiskBlockedError, match="asset exposure exceeds max_asset_exposure_fraction"):
        await risk.build_execution_guard(make_review(incoming_signal))


@pytest.mark.asyncio
async def test_validate_pair_signal_accepts_opposite_legs_inside_cycle() -> None:
    risk = RiskEngine(make_context())

    await risk.validate_pair_signal(make_pair_signal())


@pytest.mark.asyncio
async def test_build_pair_execution_guard_blocks_non_pair_position_same_market() -> None:
    signal = make_pair_signal()
    risk = RiskEngine(
        make_context(
            positions=[
                {
                    "market_id": signal.market_id,
                    "direction": "YES",
                    "asset_symbol": "BTC",
                    "strategy_id": "trend_follow_bayes",
                    "cost_basis_usd": 30.0,
                }
            ]
        )
    )

    with pytest.raises(RiskBlockedError, match="non-pair position already open for this market"):
        await risk.build_pair_execution_guard(make_pair_review(signal))


@pytest.mark.asyncio
async def test_build_pair_execution_guard_returns_trade_group_position_keys() -> None:
    signal = make_pair_signal()
    risk = RiskEngine(make_context())

    guard = await risk.build_pair_execution_guard(make_pair_review(signal))

    assert guard.primary_position_key == "pair-group-1:YES"
    assert guard.hedge_position_key == "pair-group-1:NO"
    assert guard.total_notional_usd == pytest.approx(1.7)


@pytest.mark.asyncio
async def test_build_pair_execution_guard_honors_daily_spend_cap() -> None:
    signal = make_pair_signal()
    risk = RiskEngine(
        make_context(
            execution_state={
                "daily_spend_usd": 4.6,
                "realized_pnl_usd": 0.0,
                "consecutive_losses": 0,
                "last_loss_at": None,
            }
        )
    )
    risk.config.max_daily_spend_usd = 5.0

    with pytest.raises(RiskBlockedError, match="pair trade would exceed max_daily_spend_usd"):
        await risk.build_pair_execution_guard(make_pair_review(signal))


@pytest.mark.asyncio
async def test_build_pair_execution_guard_treats_zero_daily_spend_as_unlimited() -> None:
    signal = make_pair_signal()
    risk = RiskEngine(
        make_context(
            execution_state={
                "daily_spend_usd": 9_999.0,
                "realized_pnl_usd": 0.0,
                "consecutive_losses": 0,
                "last_loss_at": None,
            }
        )
    )
    risk.config.max_daily_spend_usd = 0.0

    guard = await risk.build_pair_execution_guard(make_pair_review(signal))

    assert guard.total_notional_usd == pytest.approx(1.7)
