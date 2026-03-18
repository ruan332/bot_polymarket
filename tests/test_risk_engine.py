from types import SimpleNamespace

from core.risk_engine import RiskEngine


def make_context():
    return SimpleNamespace(
        risk_config=SimpleNamespace(
            min_edge=0.19,
            min_confidence=0.55,
            max_kelly_fraction=0.25,
            max_single_exposure_fraction=0.10,
            max_single_position_usd=100.0,
            max_total_exposure_usd=250.0,
            max_spread_bps=250,
            max_slippage_bps=150,
            max_open_positions=5,
            default_limit_buffer_bps=50,
        )
    )


def test_kelly_size_returns_zero_below_edge() -> None:
    risk = RiskEngine(make_context())
    assert risk.kelly_size(edge=0.10, price=0.45, bankroll=1000) == 0


def test_kelly_size_respects_fractional_cap() -> None:
    risk = RiskEngine(make_context())
    size = risk.kelly_size(edge=0.25, price=0.4, bankroll=1000)
    assert size == 250
