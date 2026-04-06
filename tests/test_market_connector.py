from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.config import load_crypto_config
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
            polymarket_live_min_usdc_balance=5.0,
            polymarket_sync_balance_allowance_on_startup=False,
        )
        self.crypto_config = load_crypto_config()
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
async def test_place_order_returns_simulated_pending_when_open_position_disabled() -> None:
    connector = MarketConnector(FakeContext(live_trading=False))
    result = await connector.place_order(
        market_id="market-1",
        token_id="token-hedge-1",
        direction="NO",
        size=5,
        price_limit=0.38,
        open_position=False,
    )

    assert result["status"] == "simulated_pending"
    assert result["exchange_order_id"].startswith("paper-")
    assert result["response"]["orderID"] == result["exchange_order_id"]


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
    assert created["order_args"].side == "SELL"
    assert created["post"]["signed_order"]["signed"] is True


@pytest.mark.asyncio
async def test_place_order_rejects_live_orders_below_exchange_minimum() -> None:
    connector = MarketConnector(FakeContext(live_trading=True))

    with pytest.raises(ValueError, match="minimum size"):
        await connector.place_order(
            market_id="market-1",
            token_id="token-1",
            direction="YES",
            size=2,
            price_limit=0.41,
        )


@pytest.mark.asyncio
async def test_get_live_bootstrap_status_reports_paper_mode() -> None:
    connector = MarketConnector(FakeContext(live_trading=False))

    result = await connector.get_live_bootstrap_status()

    assert result["mode"] == "paper"
    assert result["ready"] is True
    assert result["checks"][0]["name"] == "live_trading_mode"


@pytest.mark.asyncio
async def test_get_live_bootstrap_status_validates_balance_and_allowance(monkeypatch) -> None:
    sync_calls = {"count": 0}

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_address(self):
            return "0xFunder"

        def create_or_derive_api_creds(self):
            return SimpleNamespace(api_key="key", api_secret="secret", api_passphrase="pass")

        def get_server_time(self):
            return {"epoch": 12345}

        def update_balance_allowance(self, params):
            sync_calls["count"] += 1

        def get_balance_allowance(self, params):
            return {"balance": "12.50", "allowance": "25.00"}

    monkeypatch.setattr("core.market_connector.ClobClient", FakeClient)

    connector = MarketConnector(FakeContext(live_trading=True))
    result = await connector.get_live_bootstrap_status(sync_allowance=True)

    assert result["mode"] == "live"
    assert result["ready"] is True
    assert result["api_creds_source"] == "derived"
    assert result["parsed_collateral"]["balance"] == pytest.approx(12.5)
    assert result["parsed_collateral"]["allowance"] == pytest.approx(25.0)
    assert sync_calls["count"] == 1
    assert any(check["name"] == "allowance_sync" for check in result["checks"])


@pytest.mark.asyncio
async def test_get_live_bootstrap_status_normalizes_base_unit_balance_and_uses_allowances_map(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_address(self):
            return "0xFunder"

        def create_or_derive_api_creds(self):
            return SimpleNamespace(api_key="key", api_secret="secret", api_passphrase="pass")

        def get_server_time(self):
            return {"epoch": 12345}

        def update_balance_allowance(self, params):
            return None

        def get_balance_allowance(self, params):
            return {
                "balance": "5994819",
                "allowances": {
                    "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E": "115792089237316195423570985008687907853269984665640564039457584007913129639935"
                },
            }

    monkeypatch.setattr("core.market_connector.ClobClient", FakeClient)

    connector = MarketConnector(FakeContext(live_trading=True))
    result = await connector.get_live_bootstrap_status(sync_allowance=False)

    assert result["mode"] == "live"
    assert result["ready"] is True
    assert result["parsed_collateral"]["balance"] == pytest.approx(5.994819)
    assert result["parsed_collateral"]["allowance"] is None
    allowance_check = next(check for check in result["checks"] if check["name"] == "collateral_allowance")
    assert allowance_check["ok"] is True


@pytest.mark.asyncio
async def test_get_collateral_snapshot_normalizes_base_units(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_address(self):
            return "0xFunder"

        def create_or_derive_api_creds(self):
            return SimpleNamespace(api_key="key", api_secret="secret", api_passphrase="pass")

        def get_balance_allowance(self, params):
            return {"balance": "12345678", "allowance": "25000000"}

    monkeypatch.setattr("core.market_connector.ClobClient", FakeClient)

    connector = MarketConnector(FakeContext(live_trading=True))
    snapshot = await connector.get_collateral_snapshot(sync_allowance=False)

    assert snapshot is not None
    assert snapshot["balance"] == pytest.approx(12.345678)
    assert snapshot["allowance"] == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_get_live_bootstrap_status_requires_private_key_in_live_mode() -> None:
    context = FakeContext(live_trading=True)
    context.settings.polymarket_private_key = ""
    connector = MarketConnector(context)

    result = await connector.get_live_bootstrap_status()

    assert result["mode"] == "live"
    assert result["ready"] is False
    assert result["checks"][0]["name"] == "private_key"


@pytest.mark.asyncio
async def test_stream_market_enables_best_bid_ask_and_handles_ping(monkeypatch) -> None:
    sent_messages: list[str] = []

    class FakeWebSocket:
        def __init__(self):
            self.messages = iter(
                [
                    "PING",
                    json.dumps({"event_type": "subscription_success"}),
                    json.dumps(
                        {
                            "event_type": "best_bid_ask",
                            "asset_id": "token-1",
                            "best_bid": "0.40",
                            "best_ask": "0.41",
                        }
                    ),
                ]
            )

        async def send(self, message: str) -> None:
            sent_messages.append(message)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.messages)
            except StopIteration as exc:  # pragma: no cover - async iterator protocol
                raise StopAsyncIteration from exc

    class FakeConnect:
        def __init__(self):
            self.websocket = FakeWebSocket()

        async def __aenter__(self):
            return self.websocket

        async def __aexit__(self, exc_type, exc, tb):
            return None

    connector = MarketConnector(FakeContext(live_trading=False))
    monkeypatch.setattr("core.market_connector.websockets.connect", lambda *args, **kwargs: FakeConnect())

    messages = []
    async for payload in connector.stream_market(["token-1"]):
        messages.append(payload)

    assert len(messages) == 1
    assert messages[0]["event_type"] == "best_bid_ask"
    subscription_message = json.loads(sent_messages[0])
    assert subscription_message["assets_ids"] == ["token-1"]
    assert subscription_message["custom_feature_enabled"] is True
    assert "PONG" in sent_messages


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

    async def fake_orderbook_summary(token_id: str):
        return {"best_bid": 0.4, "best_ask": 0.41, "spread_bps": 243.9, "bid_depth": 100.0, "ask_depth": 90.0}

    monkeypatch.setattr(connector, "_client", fake_client)
    monkeypatch.setattr(connector, "get_orderbook_summary", fake_orderbook_summary)

    markets = await connector.get_active_markets(limit=1)

    assert markets[0]["price_yes"] == 0.41
    assert markets[0]["price_no"] == 0.59
    assert markets[0]["token_id_yes"] == "token-yes-1"
    assert markets[0]["token_id_no"] == "token-no-1"
    assert markets[0]["volume_24h"] == 12345.67
    assert markets[0]["asset_symbol"] == "BTC"
    assert markets[0]["crypto_tier"] == "btc"
    assert markets[0]["question_type"] == "upside_target"
    assert markets[0]["thesis_hash"]
    assert markets[0]["orderbook_summary_yes"]["best_bid"] == 0.4


@pytest.mark.asyncio
async def test_get_market_resolution_derives_winning_direction_from_outcome_prices(monkeypatch) -> None:
    payload = {
        "id": "market-1",
        "question": "Will BTC close higher?",
        "outcomePrices": ["0.99", "0.01"],
        "closed": True,
    }

    async def fake_get_market_by_id(self, market_id: str):
        assert market_id == "market-1"
        return payload

    monkeypatch.setattr(MarketConnector, "get_market_by_id", fake_get_market_by_id)

    connector = MarketConnector(FakeContext(live_trading=False))
    result = await connector.get_market_resolution("market-1")

    assert result["found"] is True
    assert result["resolved"] is True
    assert result["winning_direction"] == "YES"
    assert result["payout_yes"] == pytest.approx(0.99)
    assert result["payout_no"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_get_active_markets_includes_indirect_crypto_markets(monkeypatch) -> None:
    payload = [
        {
            "id": "event-1",
            "markets": [
                {
                    "id": "market-1",
                    "question": "Will BTC be above 100k this month?",
                    "description": "test",
                    "outcomePrices": "[\"0.41\", \"0.59\"]",
                    "clobTokenIds": "[\"token-yes-1\", \"token-no-1\"]",
                    "volume24hr": "12345.67",
                },
                {
                    "id": "market-2",
                    "question": "Will a BTC ETF be approved?",
                    "description": "regulation",
                    "outcomePrices": "[\"0.20\", \"0.80\"]",
                    "clobTokenIds": "[\"token-yes-2\", \"token-no-2\"]",
                    "volume24hr": "50000.00",
                },
            ],
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

    async def fake_orderbook_summary(token_id: str):
        return {"best_bid": 0.4, "best_ask": 0.41, "spread_bps": 243.9, "bid_depth": 100.0, "ask_depth": 90.0}

    monkeypatch.setattr(connector, "_client", fake_client)
    monkeypatch.setattr(connector, "get_orderbook_summary", fake_orderbook_summary)

    markets = await connector.get_active_markets(limit=2, crypto_only=True)

    assert len(markets) == 2
    assert markets[0]["id"] == "market-1"
    assert markets[0]["market_kind"] == "direct_coin"
    assert markets[1]["id"] == "market-2"
    assert markets[1]["asset_symbol"] == "BTC"
    assert markets[1]["market_kind"] == "indirect_crypto"
    assert connector.last_scan_stats["discovery_source"] == "events"


@pytest.mark.asyncio
async def test_get_active_markets_expands_upstream_fetch_for_crypto_only(monkeypatch) -> None:
    payload = [
        {
            "id": "event-1",
            "markets": [
                {
                    "id": f"noise-{idx}",
                    "question": f"Will random topic {idx} happen?",
                    "description": "non-crypto market",
                    "outcomePrices": "[\"0.20\", \"0.80\"]",
                    "clobTokenIds": f"[\"noise-yes-{idx}\", \"noise-no-{idx}\"]",
                    "volume24hr": "100000.00",
                }
                for idx in range(60)
            ]
            + [
                {
                    "id": "market-btc",
                    "question": "Will BTC be above 100k this month?",
                    "description": "test",
                    "outcomePrices": "[\"0.41\", \"0.59\"]",
                    "clobTokenIds": "[\"token-yes-btc\", \"token-no-btc\"]",
                    "volume24hr": "12345.67",
                },
                {
                    "id": "market-eth",
                    "question": "Will ETH be above 5k this month?",
                    "description": "test",
                    "outcomePrices": "[\"0.31\", \"0.69\"]",
                    "clobTokenIds": "[\"token-yes-eth\", \"token-no-eth\"]",
                    "volume24hr": "22345.67",
                },
            ],
        }
    ]
    captured: dict[str, object] = {}

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
        def get(self, url, *, params=None):
            captured["url"] = url
            captured["params"] = params or {}
            return FakeResponse()

        @property
        def closed(self):
            return False

    connector = MarketConnector(FakeContext(live_trading=False))

    async def fake_client():
        return FakeSession()

    async def fake_orderbook_summary(token_id: str):
        return {"best_bid": 0.4, "best_ask": 0.41, "spread_bps": 25.0, "bid_depth": 1000.0, "ask_depth": 1000.0}

    monkeypatch.setattr(connector, "_client", fake_client)
    monkeypatch.setattr(connector, "get_orderbook_summary", fake_orderbook_summary)

    markets = await connector.get_active_markets(limit=2, crypto_only=True)

    assert captured["url"].endswith("/events")
    assert captured["params"]["limit"] == 100
    assert captured["params"]["order"] == "volume24hr"
    assert [item["id"] for item in markets] == ["market-btc", "market-eth"]


@pytest.mark.asyncio
async def test_get_active_markets_assigns_synthetic_crypto_asset_and_keeps_directs_first(monkeypatch) -> None:
    payload = [
        {
            "id": "event-1",
            "markets": [
                {
                    "id": "market-indirect",
                    "question": "Will crypto regulation tighten this quarter?",
                    "description": "digital assets regulation market",
                    "outcomePrices": "[\"0.20\", \"0.80\"]",
                    "clobTokenIds": "[\"token-yes-indirect\", \"token-no-indirect\"]",
                    "volume24hr": "50000.00",
                },
                {
                    "id": "market-direct",
                    "question": "Will ETH be above 5k this month?",
                    "description": "test",
                    "outcomePrices": "[\"0.31\", \"0.69\"]",
                    "clobTokenIds": "[\"token-yes-eth\", \"token-no-eth\"]",
                    "volume24hr": "50000.00",
                },
            ],
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

    async def fake_orderbook_summary(token_id: str):
        return {"best_bid": 0.4, "best_ask": 0.41, "spread_bps": 25.0, "bid_depth": 1000.0, "ask_depth": 1000.0}

    monkeypatch.setattr(connector, "_client", fake_client)
    monkeypatch.setattr(connector, "get_orderbook_summary", fake_orderbook_summary)

    markets = await connector.get_active_markets(limit=2, crypto_only=True)

    assert [item["id"] for item in markets] == ["market-direct", "market-indirect"]
    assert markets[1]["asset_symbol"] == "CRYPTO"
    assert markets[1]["market_kind"] == "indirect_crypto"


@pytest.mark.asyncio
async def test_get_active_markets_uses_markets_endpoint_when_crypto_filter_disabled(monkeypatch) -> None:
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
    captured: dict[str, object] = {}

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
        def get(self, url, *, params=None):
            captured["url"] = url
            captured["params"] = params or {}
            return FakeResponse()

        @property
        def closed(self):
            return False

    connector = MarketConnector(FakeContext(live_trading=False))

    async def fake_client():
        return FakeSession()

    async def fake_orderbook_summary(token_id: str):
        return {"best_bid": 0.4, "best_ask": 0.41, "spread_bps": 243.9, "bid_depth": 100.0, "ask_depth": 90.0}

    monkeypatch.setattr(connector, "_client", fake_client)
    monkeypatch.setattr(connector, "get_orderbook_summary", fake_orderbook_summary)

    markets = await connector.get_active_markets(limit=1, crypto_only=False)

    assert len(markets) == 1
    assert captured["url"].endswith("/markets")
    assert captured["params"]["limit"] == 1
    assert connector.last_scan_stats["discovery_source"] == "markets"
