from __future__ import annotations

from types import SimpleNamespace
import pytest

from core.market_connector import MarketConnector


class FakeRepository:
    pass


class FakeContext:
    def __init__(self, *, live_trading: bool):
        self.settings = SimpleNamespace(
            live_trading=live_trading,
            polymarket_clob_url="https://clob.polymarket.com",
            polymarket_gamma_url="https://gamma-api.polymarket.com",
            polymarket_market_ws="wss://ws-subscriptions-clob.polymarket.com/ws/market",
            polymarket_private_key="0xabc",
            polymarket_api_key="",
            polymarket_api_secret="",
            polymarket_api_passphrase="",
            polymarket_funder="",
            polymarket_signature_type=0,
            polymarket_chain_id=137,
        )
        self.repository = FakeRepository()
        self.bus = None


@pytest.mark.asyncio
async def test_place_order_returns_simulated_when_live_disabled() -> None:
    connector = MarketConnector(FakeContext(live_trading=False))
    result = await connector.place_order(
        market_id="market-1",
        token_id="token-1",
        direction="YES",
        size=10,
        price_limit=0.41,
    )
    assert result["status"] == "simulated"
    assert result["order"]["token_id"] == "token-1"


@pytest.mark.asyncio
async def test_place_order_uses_py_clob_client_when_live_enabled(monkeypatch) -> None:
    created = {}

    class FakeClient:
        def __init__(self, host, chain_id=None, key=None, creds=None, signature_type=None, funder=None, **kwargs):
            created.setdefault("instances", []).append(
                {
                    "host": host,
                    "chain_id": chain_id,
                    "key": key,
                    "creds": creds,
                    "signature_type": signature_type,
                    "funder": funder,
                }
            )

        def get_address(self):
            return "0xFunder"

        def create_or_derive_api_creds(self):
            return SimpleNamespace(api_key="key", api_secret="secret", api_passphrase="pass")

        def create_order(self, order_args):
            created["order_args"] = order_args
            return {"signed": True, "token_id": order_args.token_id}

        def post_order(self, signed_order, order_type):
            created["post"] = {"signed_order": signed_order, "order_type": order_type}
            return {"success": True, "orderID": "123"}

    monkeypatch.setattr("core.market_connector.ClobClient", FakeClient)

    connector = MarketConnector(FakeContext(live_trading=True))
    result = await connector.place_order(
        market_id="market-1",
        token_id="token-1",
        direction="NO",
        size=10,
        price_limit=0.41,
    )

    assert result["status"] == "live_submitted"
    assert created["order_args"].token_id == "token-1"
    assert created["order_args"].side == "BUY"
    assert created["post"]["signed_order"]["signed"] is True


@pytest.mark.asyncio
async def test_get_active_markets_parses_json_encoded_arrays(monkeypatch) -> None:
    payload = [
        {
            "id": "market-1",
            "question": "Will BTC be above 100k?",
            "description": "test",
            "outcomePrices": "[\"0.41\", \"0.59\"]",
            "clobTokenIds": "[\"token-yes-1\", \"token-no-1\"]",
            "volume24hr": "12345.67",
        }
    ]

    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self):
            return None

        async def json(self):
            return payload

    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResponse()

        @property
        def closed(self):
            return False

    connector = MarketConnector(FakeContext(live_trading=False))

    async def fake_client():
        return FakeSession()

    monkeypatch.setattr(connector, "_client", fake_client)

    markets = await connector.get_active_markets(limit=1)

    assert markets[0]["price_yes"] == 0.41
    assert markets[0]["price_no"] == 0.59
    assert markets[0]["token_id_yes"] == "token-yes-1"
    assert markets[0]["token_id_no"] == "token-no-1"
    assert markets[0]["volume_24h"] == 12345.67
