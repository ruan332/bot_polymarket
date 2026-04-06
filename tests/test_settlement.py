from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.settlement import SettlementService


class FakeRepository:
    def __init__(self) -> None:
        self.settlement_events: list[dict[str, object]] = []
        self.paper_orders: list[dict[str, object]] = []

    async def get_open_positions(self) -> list[dict[str, object]]:
        return [
            {
                "market_id": "market-1",
                "position_key": "market-1:YES",
                "token_id": "token-1",
                "market_question": "Will BTC be above current price in 15 minutes?",
                "asset_symbol": "BTC",
                "strategy_id": "momentum_15m",
                "trade_group_id": "group-1",
                "cycle_slug": "btc-updown-15m-1",
                "leg_role": "entry",
                "direction": "YES",
                "size": 10,
                "average_price": 0.42,
                "cost_basis_usd": 4.2,
            }
        ]

    async def record_settlement_event(self, *args):
        self.settlement_events.append({"args": args})

    async def record_paper_order(self, *args):
        self.paper_orders.append({"args": args})


class FakeConnector:
    async def get_market_resolution(self, market_id: str) -> dict[str, object]:
        assert market_id == "market-1"
        return {"resolved": True, "winning_direction": "YES"}


class FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    async def publish_event(self, stream: str, payload: dict[str, object]) -> None:
        self.events.append((stream, payload))


@pytest.mark.asyncio
async def test_process_redeem_cycle_skips_simulated_orders_in_live_mode() -> None:
    repository = FakeRepository()
    context = SimpleNamespace(
        settings=SimpleNamespace(live_trading=True),
        repository=repository,
        bus=FakeBus(),
    )
    service = SettlementService(context, FakeConnector())  # type: ignore[arg-type]

    result = await service.process_redeem_cycle(limit=10)

    assert result["dry_run"] is True
    assert result["processed_count"] == 1
    assert result["settled_count"] == 0
    assert result["skipped_count"] == 1
    assert repository.paper_orders == []
    assert repository.settlement_events
    assert repository.settlement_events[0]["args"][3] == "skipped"
    assert context.bus.events == []
