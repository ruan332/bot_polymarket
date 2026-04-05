from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from core.schemas import PortfolioSummary


class _FakeRepository:
    def __init__(self, available_balance: float = 10.0) -> None:
        self.available_balance = available_balance

    async def get_portfolio_summary(self) -> PortfolioSummary:
        return PortfolioSummary(
            available_balance=self.available_balance,
            total_exposure=0.0,
            current_market_value=0.0,
            total_equity=self.available_balance,
            total_pnl=0.0,
            open_positions=0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            mode="live",
            balance_source="paper_ledger",
        )


class _FakeConnector:
    def __init__(self, snapshot: dict[str, object] | None) -> None:
        self.snapshot = snapshot

    async def get_collateral_snapshot(self, *, sync_allowance: bool = False) -> dict[str, object] | None:  # noqa: ARG002
        return self.snapshot

    async def close(self) -> None:
        return None


class _FakeContext:
    def __init__(self, snapshot: dict[str, object] | None) -> None:
        self.settings = SimpleNamespace()
        self.repository = _FakeRepository(available_balance=10.0)
        self.market_connector = _FakeConnector(snapshot)

    async def close(self) -> None:
        return None


def test_portfolio_summary_overrides_with_live_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_context = _FakeContext(
        {
            "balance": 123.45,
            "allowance": 200.0,
            "funder": "0xabc",
            "signature_type": 1,
            "raw": {"balance": "123.45"},
        }
    )

    async def fake_create():
        return fake_context

    monkeypatch.setattr(api_main.AppContext, "create", fake_create)

    with TestClient(api_main.app) as client:
        summary = client.get("/portfolio/summary").json()

    assert summary["available_balance"] == pytest.approx(123.45)
    assert summary["live_balance"] == pytest.approx(123.45)
    assert summary["live_allowance"] == pytest.approx(200.0)
    assert summary["balance_source"] == "polymarket_live"
    assert summary["funder"] == "0xabc"


def test_portfolio_summary_keeps_fallback_without_live_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_context = _FakeContext(snapshot=None)

    async def fake_create():
        return fake_context

    monkeypatch.setattr(api_main.AppContext, "create", fake_create)

    with TestClient(api_main.app) as client:
        summary = client.get("/portfolio/summary").json()

    assert summary["available_balance"] == pytest.approx(10.0)
    assert summary["balance_source"] == "paper_ledger"


def test_portfolio_summary_accepts_partial_live_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_context = _FakeContext(
        {
            "balance": 77.7,
            "allowance": None,
            "funder": "0xdef",
            "signature_type": 1,
            "raw": {"balance": "77.7"},
        }
    )

    async def fake_create():
        return fake_context

    monkeypatch.setattr(api_main.AppContext, "create", fake_create)

    with TestClient(api_main.app) as client:
        summary = client.get("/portfolio/summary").json()

    assert summary["available_balance"] == pytest.approx(77.7)
    assert summary["live_balance"] == pytest.approx(77.7)
    assert summary["live_allowance"] is None
    assert summary["balance_source"] == "polymarket_live"
