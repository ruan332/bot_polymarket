from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config import load_crypto_config
from core.exceptions import RiskBlockedError
from core.risk_engine import RiskEngine
from core.schemas import PortfolioSummary, ReviewPayload, SignalPayload


def make_context():
    crypto_config = load_crypto_config()

    class Repository:
        def __init__(self):
            self.summary = PortfolioSummary(
                available_balance=1000.0,
                total_exposure=0.0,
                current_market_value=0.0,
                total_equity=1000.0,
                total_pnl=0.0,
                open_positions=0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
            )

        async def get_portfolio_summary(self) -> PortfolioSummary:
            return self.summary

    return SimpleNamespace(
        risk_config=SimpleNamespace(
            min_edge=0.19,
            min_confidence=0.55,
            max_kelly_fraction=0.25,
            max_single_exposure_fraction=0.10,
            max_single_position_usd=100.0,
            max_total_exposure_usd=250.0,
            min_market_volume_24h=10000.0,
            max_spread_bps=250,
            max_slippage_bps=150,
            max_order_price=0.90,
            max_open_positions=5,
            default_limit_buffer_bps=50,
        ),
        crypto_config=crypto_config,
        repository=Repository(),
    )


def make_signal(*, symbol: str, tier: str, edge: float, confidence: float, price: float, volume_24h: float) -> SignalPayload:
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
        market_kind="direct_coin",
        question_type="direction",
        thesis_tags=[symbol.lower(), tier],
        thesis_hash=f"{symbol.lower()}-{tier}",
        reasoning="test",
        liquidity_summary={"spread_bps": 100.0, "ask_depth": 500.0},
    )


def make_review(signal: SignalPayload) -> ReviewPayload:
    return ReviewPayload(
        signal_id=signal.signal_id,
        asset_symbol=signal.asset_symbol,
        crypto_tier=signal.crypto_tier,
        approved=True,
        kelly_size=0,
        notes="test",
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
    with pytest.raises(RiskBlockedError):
        await risk.build_execution_guard(make_review(small_cap_signal))


@pytest.mark.asyncio
async def test_build_execution_guard_allows_missing_news_validation() -> None:
    risk = RiskEngine(make_context())
    signal = make_signal(symbol="BTC", tier="btc", edge=0.40, confidence=0.80, price=0.40, volume_24h=100000.0)

    guard = await risk.build_execution_guard(make_review(signal))

    assert guard.size == 250
    assert guard.price_limit == pytest.approx(0.405)
