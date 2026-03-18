from __future__ import annotations

import json
from asyncio import to_thread
from typing import TYPE_CHECKING, Any, AsyncGenerator

import aiohttp
import websockets
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

if TYPE_CHECKING:
    from core.app_context import AppContext


class MarketConnector:
    def __init__(self, context: AppContext):
        self.context = context
        self.session: aiohttp.ClientSession | None = None
        self.clob_client: ClobClient | None = None

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _client(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def get_active_markets(self, limit: int = 20) -> list[dict[str, Any]]:
        session = await self._client()
        async with session.get(
            f"{self.context.settings.polymarket_gamma_url}/markets",
            params={"active": "true", "closed": "false", "limit": limit},
        ) as response:
            response.raise_for_status()
            data = await response.json()

        markets = data if isinstance(data, list) else data.get("data", [])
        normalized: list[dict[str, Any]] = []
        for market in markets:
            prices = self._coerce_sequence(market.get("outcomePrices"))
            if not prices:
                last_price = self._coerce_float(market.get("price"), 0.5)
                prices = [last_price, round(1 - last_price, 4)]
            prices = [self._coerce_float(price, 0.0) for price in prices]
            if len(prices) == 1:
                prices.append(round(1 - prices[0], 4))
            clob_token_ids = [str(token_id) for token_id in self._coerce_sequence(market.get("clobTokenIds"))]
            market_id = str(market.get("id") or market.get("conditionId") or (clob_token_ids[0] if clob_token_ids else ""))
            normalized.append(
                {
                    "id": str(market.get("id") or market.get("conditionId") or market_id),
                    "question": market.get("question", "Unknown market"),
                    "description": market.get("description", ""),
                    "price_yes": float(prices[0]),
                    "price_no": float(prices[1]) if len(prices) > 1 else round(1 - float(prices[0]), 4),
                    "volume_24h": self._coerce_float(market.get("volume24hr") or market.get("volume24hrClob"), 0.0),
                    "clob_token_ids": clob_token_ids,
                    "token_id_yes": str(clob_token_ids[0]) if len(clob_token_ids) > 0 else str(market_id),
                    "token_id_no": str(clob_token_ids[1]) if len(clob_token_ids) > 1 else str(market_id),
                }
            )
        return normalized

    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        session = await self._client()
        async with session.get(
            f"{self.context.settings.polymarket_clob_url}/book",
            params={"token_id": token_id},
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def place_order(
        self,
        *,
        market_id: str,
        token_id: str,
        direction: str,
        size: int,
        price_limit: float,
    ) -> dict[str, Any]:
        order = {
            "market_id": market_id,
            "token_id": token_id,
            "direction": direction,
            "size": size,
            "price_limit": price_limit,
            "live_trading": self.context.settings.live_trading,
        }
        if self.context.settings.live_trading:
            client = await self._get_live_client()
            side = BUY if direction in {"YES", "BUY"} else BUY
            signed_order = await to_thread(
                client.create_order,
                OrderArgs(
                    token_id=token_id,
                    price=price_limit,
                    size=size,
                    side=side,
                ),
            )
            response = await to_thread(client.post_order, signed_order, OrderType.GTC)
            return {"status": "live_submitted", "order": order, "response": response}
        return {"status": "simulated", "order": order}

    async def stream_market(self, asset_ids: list[str]) -> AsyncGenerator[dict[str, Any], None]:
        async with websockets.connect(self.context.settings.polymarket_market_ws) as websocket:
            await websocket.send(json.dumps({"assets_ids": asset_ids, "type": "market"}))
            async for message in websocket:
                yield json.loads(message)

    async def _get_live_client(self) -> ClobClient:
        if self.clob_client is not None:
            return self.clob_client

        settings = self.context.settings
        if not settings.polymarket_private_key:
            raise ValueError("POLYMARKET_PRIVATE_KEY is required for live trading")

        funder = settings.polymarket_funder or None
        base_client = ClobClient(
            host=settings.polymarket_clob_url,
            chain_id=settings.polymarket_chain_id,
            key=settings.polymarket_private_key,
            signature_type=settings.polymarket_signature_type,
            funder=funder,
        )
        creds = self._load_or_derive_creds(base_client)
        if not funder:
            funder = base_client.get_address()

        self.clob_client = ClobClient(
            host=settings.polymarket_clob_url,
            chain_id=settings.polymarket_chain_id,
            key=settings.polymarket_private_key,
            creds=creds,
            signature_type=settings.polymarket_signature_type,
            funder=funder,
        )
        return self.clob_client

    def _load_or_derive_creds(self, base_client: ClobClient) -> ApiCreds:
        settings = self.context.settings
        if settings.polymarket_api_key and settings.polymarket_api_secret and settings.polymarket_api_passphrase:
            return ApiCreds(
                api_key=settings.polymarket_api_key,
                api_secret=settings.polymarket_api_secret,
                api_passphrase=settings.polymarket_api_passphrase,
            )
        return base_client.create_or_derive_api_creds()

    @staticmethod
    def _coerce_sequence(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return [value]

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list) and parsed:
                        return MarketConnector._coerce_float(parsed[0], default)
                except json.JSONDecodeError:
                    return default
            return float(stripped)
        return float(value)
