from __future__ import annotations

import asyncio
import contextlib
import json
from asyncio import to_thread
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, AsyncGenerator
from uuid import uuid4

import aiohttp
import websockets
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, AssetType, BalanceAllowanceParams, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

if TYPE_CHECKING:
    from core.app_context import AppContext

from core.crypto import ASSET_NAMES, classify_crypto_market_with_reason


class MarketConnector:
    def __init__(self, context: AppContext):
        self.context = context
        self.session: aiohttp.ClientSession | None = None
        self.clob_client: ClobClient | None = None
        self.last_scan_stats: dict[str, Any] = {}

    async def close(self) -> None:
        session = self.session
        self.session = None
        if session and not session.closed:
            await session.close()

    async def _client(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        return self.session

    async def get_market_by_slug(self, slug: str) -> dict[str, Any] | None:
        session = await self._client()
        try:
            async with session.get(
                f"{self.context.settings.polymarket_gamma_url}/markets",
                params={"slug": slug, "limit": 10},
            ) as response:
                response.raise_for_status()
                payload = await response.json()
        except Exception:
            return None
        items = payload if isinstance(payload, list) else payload.get("data", [])
        for item in items:
            if not isinstance(item, dict):
                continue
            item_slug = str(item.get("slug") or "").strip().lower()
            if item_slug == slug.strip().lower():
                return item
        return None

    async def get_market_by_id(self, market_id: str) -> dict[str, Any] | None:
        if not market_id:
            return None
        session = await self._client()
        lookup_variants = [
            (f"{self.context.settings.polymarket_gamma_url}/markets/{market_id}", None),
            (f"{self.context.settings.polymarket_gamma_url}/markets", {"id": market_id, "limit": 20}),
            (f"{self.context.settings.polymarket_gamma_url}/markets", {"conditionId": market_id, "limit": 20}),
            (f"{self.context.settings.polymarket_gamma_url}/markets", {"condition_id": market_id, "limit": 20}),
        ]
        for url, params in lookup_variants:
            try:
                async with session.get(url, params=params) as response:
                    if response.status >= 400:
                        continue
                    payload = await response.json()
            except Exception:
                continue
            item = self._match_market_id_payload(payload, market_id)
            if item is not None:
                return item
        return None

    async def resolve_copytrade_market(self, asset_symbol: str, cycle_start: datetime) -> dict[str, Any] | None:
        slug = f"{asset_symbol.lower()}-updown-15m-{int(cycle_start.astimezone(UTC).timestamp())}"
        market = await self.get_market_by_slug(slug)
        if market is None:
            session = await self._client()
            try:
                async with session.get(
                    f"{self.context.settings.polymarket_gamma_url}/markets",
                    params={"active": "true", "closed": "false", "limit": 200},
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
            except Exception:
                return None
            items = payload if isinstance(payload, list) else payload.get("data", [])
            market = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict) and str(item.get("slug") or "").strip().lower() == slug
                ),
                None,
            )
        if market is None:
            return None
        clob_token_ids = [str(token_id) for token_id in self._coerce_sequence(market.get("clobTokenIds"))]
        outcome_prices = [self._coerce_float(price, 0.0) for price in self._coerce_sequence(market.get("outcomePrices"))]
        if len(outcome_prices) == 1:
            outcome_prices.append(round(1 - outcome_prices[0], 4))
        return {
            "id": str(market.get("id") or market.get("conditionId") or ""),
            "slug": str(market.get("slug") or slug),
            "question": str(market.get("question") or ""),
            "asset_name": ASSET_NAMES.get(asset_symbol.upper(), asset_symbol.upper()),
            "token_id_yes": str(clob_token_ids[0]) if len(clob_token_ids) > 0 else "",
            "token_id_no": str(clob_token_ids[1]) if len(clob_token_ids) > 1 else "",
            "price_yes": self._coerce_float(outcome_prices[0] if outcome_prices else 0.0, 0.0),
            "price_no": self._coerce_float(outcome_prices[1] if len(outcome_prices) > 1 else 0.0, 0.0),
            "volume_24h": self._coerce_float(market.get("volume24hr") or market.get("volume24hrClob"), 0.0),
            "end_date": (
                market.get("endDate")
                or market.get("end_date")
                or market.get("closeTime")
                or ""
            ),
        }

    async def get_active_markets(self, limit: int = 20, crypto_only: bool = False) -> list[dict[str, Any]]:
        if crypto_only:
            markets, fetch_stats = await self._fetch_active_crypto_markets(limit)
        else:
            markets, fetch_stats = await self._fetch_active_markets(limit)
        normalized: list[dict[str, Any]] = []
        rejection_breakdown: dict[str, int] = {}
        crypto_classified = 0
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
            question = str(market.get("question", "Unknown market"))
            description = str(market.get("description", ""))
            classification = classify_crypto_market_with_reason(question, description, self.context.crypto_config)
            candidate = classification.candidate
            if candidate is not None:
                crypto_classified += 1
            elif crypto_only:
                reason = classification.rejection_reason or "unclassified"
                rejection_breakdown[reason] = rejection_breakdown.get(reason, 0) + 1
            if crypto_only and candidate is None:
                continue
            token_id_yes = str(clob_token_ids[0]) if len(clob_token_ids) > 0 else str(market_id)
            token_id_no = str(clob_token_ids[1]) if len(clob_token_ids) > 1 else str(market_id)
            normalized.append(
                {
                    "id": str(market.get("id") or market.get("conditionId") or market_id),
                    "question": question,
                    "description": description,
                    "price_yes": float(prices[0]),
                    "price_no": float(prices[1]) if len(prices) > 1 else round(1 - float(prices[0]), 4),
                    "volume_24h": self._coerce_float(market.get("volume24hr") or market.get("volume24hrClob"), 0.0),
                    "clob_token_ids": clob_token_ids,
                    "token_id_yes": token_id_yes,
                    "token_id_no": token_id_no,
                    "asset_symbol": candidate.asset_symbol if candidate else "",
                    "asset_name": candidate.asset_name if candidate else "",
                    "crypto_tier": candidate.crypto_tier if candidate else "",
                    "market_kind": candidate.market_kind if candidate else "",
                    "question_type": candidate.question_type if candidate else "",
                    "thesis_tags": candidate.thesis_tags if candidate else [],
                    "thesis_hash": candidate.thesis_hash if candidate else "",
                    "end_date": (
                        market.get("endDate")
                        or market.get("end_date")
                        or market.get("closeTime")
                        or market.get("end_date_iso")
                        or ""
                    ),
                    "orderbook_summary_yes": {},
                    "orderbook_summary_no": {},
                }
            )
        if crypto_only:
            tier_priority = {tier: index for index, tier in enumerate(self.context.crypto_config.scan_priority)}
            normalized.sort(
                key=lambda item: (
                    tier_priority.get(item.get("crypto_tier", ""), 99),
                    self.context.crypto_config.market_kind_rank(str(item.get("market_kind", ""))),
                    -(item.get("volume_24h", 0.0)),
                )
            )
        selected = normalized[:limit]
        for market in selected:
            if not market.get("asset_symbol"):
                continue
            market["orderbook_summary_yes"] = await self.get_orderbook_summary(str(market["token_id_yes"]))
            market["orderbook_summary_no"] = await self.get_orderbook_summary(str(market["token_id_no"]))
        self.last_scan_stats = {
            **fetch_stats,
            "requested_limit": limit,
            "crypto_classified": crypto_classified,
            "rejection_breakdown": rejection_breakdown,
            "selected_for_scan": len(selected),
            "selected_markets": [
                {
                    "market_id": str(item.get("id") or ""),
                    "asset_symbol": str(item.get("asset_symbol") or ""),
                    "crypto_tier": str(item.get("crypto_tier") or ""),
                    "market_kind": str(item.get("market_kind") or ""),
                    "volume_24h": float(item.get("volume_24h") or 0.0),
                    "question": str(item.get("question") or ""),
                }
                for item in selected[:6]
            ],
        }
        return selected

    async def _fetch_active_markets(self, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        session = await self._client()
        async with session.get(
            f"{self.context.settings.polymarket_gamma_url}/markets",
            params={"active": "true", "closed": "false", "limit": limit},
        ) as response:
            response.raise_for_status()
            data = await response.json()
        markets = data if isinstance(data, list) else data.get("data", [])
        return markets, {
            "discovery_source": "markets",
            "upstream_limit": limit,
            "gamma_events_fetched": 0,
            "gamma_markets_fetched": len(markets),
            "gamma_pages_fetched": 1,
        }

    async def _fetch_active_crypto_markets(self, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        session = await self._client()
        page_size = 100
        max_pages = 10
        raw_markets_target = max(limit * 20, 1000)
        markets: list[dict[str, Any]] = []
        seen_market_ids: set[str] = set()
        events_fetched = 0
        pages_fetched = 0

        try:
            for page in range(max_pages):
                offset = page * page_size
                async with session.get(
                    f"{self.context.settings.polymarket_gamma_url}/events",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": page_size,
                        "offset": offset,
                        "order": "volume24hr",
                        "ascending": "false",
                    },
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                events = data if isinstance(data, list) else data.get("data", [])
                if not events:
                    break
                pages_fetched += 1
                events_fetched += len(events)
                for event in events:
                    event_markets = event.get("markets") or []
                    if not isinstance(event_markets, list):
                        continue
                    for market in event_markets:
                        if not isinstance(market, dict):
                            continue
                        market_id = str(
                            market.get("id")
                            or market.get("conditionId")
                            or market.get("market_id")
                            or ""
                        )
                        if not market_id or market_id in seen_market_ids:
                            continue
                        seen_market_ids.add(market_id)
                        markets.append(market)
                if len(markets) >= raw_markets_target:
                    break
        except Exception:
            fallback_markets, fallback_stats = await self._fetch_active_markets(min(max(limit * 8, 100), 250))
            fallback_stats["discovery_source"] = "markets_fallback"
            return fallback_markets, fallback_stats

        return markets, {
            "discovery_source": "events",
            "upstream_limit": page_size,
            "gamma_events_fetched": events_fetched,
            "gamma_markets_fetched": len(markets),
            "gamma_pages_fetched": pages_fetched,
        }

    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        session = await self._client()
        async with session.get(
            f"{self.context.settings.polymarket_clob_url}/book",
            params={"token_id": token_id},
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def get_orderbook_summary(self, token_id: str) -> dict[str, Any]:
        try:
            book = await self.get_orderbook(token_id)
        except Exception:
            return {}
        if not isinstance(book, dict):
            return {}
        bids = self._normalize_levels(book.get("bids") or book.get("buy") or [], reverse=True)
        asks = self._normalize_levels(book.get("asks") or book.get("sell") or [], reverse=False)
        best_bid = bids[0]["price"] if bids else 0.0
        best_ask = asks[0]["price"] if asks else 0.0
        spread_bps = 0.0
        if best_bid > 0 and best_ask > 0:
            mid = (best_bid + best_ask) / 2
            if mid > 0:
                spread_bps = abs(best_ask - best_bid) / mid * 10000
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_bps": round(spread_bps, 2),
            "bid_depth": round(sum(level["price"] * level["size"] for level in bids[:5]), 4),
            "ask_depth": round(sum(level["price"] * level["size"] for level in asks[:5]), 4),
        }

    async def place_order(
        self,
        *,
        market_id: str,
        token_id: str,
        direction: str,
        size: int,
        price_limit: float,
        open_position: bool = True,
    ) -> dict[str, Any]:
        order = {
            "market_id": market_id,
            "token_id": token_id,
            "direction": direction,
            "size": size,
            "price_limit": price_limit,
            "open_position": open_position,
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
            response_payload = self._mapping_from_object(response)
            exchange_order_id = str(
                response_payload.get("orderID")
                or response_payload.get("orderId")
                or response_payload.get("id")
                or ""
            )
            return {
                "status": "live_submitted",
                "order": order,
                "response": response_payload,
                "exchange_order_id": exchange_order_id,
            }
        paper_order_id = f"paper-{uuid4().hex[:12]}"
        return {
            "status": "simulated" if open_position else "simulated_pending",
            "order": order,
            "response": {"orderID": paper_order_id},
            "exchange_order_id": paper_order_id,
        }

    async def stream_market(self, asset_ids: list[str]) -> AsyncGenerator[dict[str, Any], None]:
        async with websockets.connect(self.context.settings.polymarket_market_ws, ping_interval=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "assets_ids": asset_ids,
                        "type": "market",
                        "custom_feature_enabled": True,
                    }
                )
            )

            async def ping_loop() -> None:
                while True:
                    await asyncio.sleep(10)
                    await websocket.send("PING")

            ping_task = asyncio.create_task(ping_loop())
            try:
                async for message in websocket:
                    if message == "PING":
                        await websocket.send("PONG")
                        continue
                    if message == "PONG":
                        continue
                    payload = json.loads(message)
                    if isinstance(payload, dict) and str(payload.get("event_type") or payload.get("type") or "") in {
                        "subscription_success",
                    }:
                        continue
                    yield payload
            finally:
                ping_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ping_task

    async def get_order(self, order_id: str) -> dict[str, Any] | None:
        if not self.context.settings.live_trading or not order_id:
            return None
        client = await self._get_live_client()
        getter = getattr(client, "get_order", None)
        if not callable(getter):
            return None
        result = await to_thread(getter, order_id)
        return self._mapping_from_object(result)

    async def cancel_order(self, order_id: str) -> dict[str, Any] | None:
        if not self.context.settings.live_trading or not order_id:
            return None
        client = await self._get_live_client()
        cancel = getattr(client, "cancel_order", None)
        if callable(cancel):
            result = await to_thread(cancel, order_id)
            return self._mapping_from_object(result)
        cancel_many = getattr(client, "cancel_orders", None)
        if callable(cancel_many):
            result = await to_thread(cancel_many, [order_id])
            return self._mapping_from_object(result)
        return None

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

    async def get_live_bootstrap_status(self, *, sync_allowance: bool = False) -> dict[str, Any]:
        settings = self.context.settings
        if not settings.live_trading:
            return {
                "mode": "paper",
                "ready": True,
                "reason": "live trading disabled",
                "checks": [
                    {
                        "name": "live_trading_mode",
                        "ok": True,
                        "details": "paper mode active",
                    }
                ],
            }
        if not settings.polymarket_private_key:
            return {
                "mode": "live",
                "ready": False,
                "reason": "POLYMARKET_PRIVATE_KEY is required for live trading",
                "checks": [
                    {
                        "name": "private_key",
                        "ok": False,
                        "details": "POLYMARKET_PRIVATE_KEY is missing",
                    }
                ],
            }

        checks: list[dict[str, Any]] = [
            {
                "name": "private_key",
                "ok": True,
                "details": "configured",
            }
        ]
        try:
            client = await self._get_live_client()
            creds_source = (
                "configured"
                if settings.polymarket_api_key and settings.polymarket_api_secret and settings.polymarket_api_passphrase
                else "derived"
            )
            checks.append(
                {
                    "name": "clob_credentials",
                    "ok": True,
                    "details": f"{creds_source} credentials ready",
                }
            )
            server_time = await to_thread(client.get_server_time)
            checks.append(
                {
                    "name": "clob_server_time",
                    "ok": True,
                    "details": self._mapping_from_object(server_time),
                }
            )
            collateral_status, parsed_collateral = await self._fetch_collateral_snapshot(
                client,
                sync_allowance=sync_allowance,
            )
            if sync_allowance:
                checks.append(
                    {
                        "name": "allowance_sync",
                        "ok": True,
                        "details": "update_balance_allowance executed",
                    }
                )
            min_balance = float(settings.polymarket_live_min_usdc_balance or 0.0)
            balance_ok = parsed_collateral["balance"] is not None and parsed_collateral["balance"] >= min_balance
            allowance_ok = self._has_positive_collateral_allowance(collateral_status, parsed_collateral["allowance"])
            checks.extend(
                [
                    {
                        "name": "collateral_balance",
                        "ok": balance_ok,
                        "details": {
                            "balance": parsed_collateral["balance"],
                            "minimum_required": min_balance,
                        },
                    },
                    {
                        "name": "collateral_allowance",
                        "ok": allowance_ok,
                        "details": {
                            "allowance": parsed_collateral["allowance"],
                        },
                    },
                ]
            )
            ready = balance_ok and allowance_ok
            return {
                "mode": "live",
                "ready": ready,
                "reason": "live bootstrap ready" if ready else "insufficient collateral balance/allowance for live trading",
                "server_time": server_time,
                "funder": settings.polymarket_funder or client.get_address() or "",
                "api_key_configured": bool(settings.polymarket_api_key),
                "api_creds_source": creds_source,
                "signature_type": settings.polymarket_signature_type,
                "min_required_usdc_balance": min_balance,
                "collateral_status": self._mapping_from_object(collateral_status),
                "parsed_collateral": parsed_collateral,
                "synced_allowance": sync_allowance,
                "checks": checks,
            }
        except Exception as exc:
            return {
                "mode": "live",
                "ready": False,
                "reason": f"live bootstrap failed: {exc}",
                "checks": checks
                + [
                    {
                        "name": "bootstrap_error",
                        "ok": False,
                        "details": str(exc),
                    }
                ],
            }

    async def get_collateral_snapshot(self, *, sync_allowance: bool = False) -> dict[str, Any] | None:
        """
        Returns live collateral balance/allowance when credentials are present.
        This is read-only telemetry and can run independently from LIVE_TRADING mode.
        """
        settings = self.context.settings
        if not settings.polymarket_private_key:
            return None
        try:
            client = await self._get_live_client()
            collateral_status, parsed_collateral = await self._fetch_collateral_snapshot(
                client,
                sync_allowance=sync_allowance,
            )
            return {
                "balance": parsed_collateral["balance"],
                "allowance": parsed_collateral["allowance"],
                "raw": self._mapping_from_object(collateral_status),
                "funder": settings.polymarket_funder or client.get_address() or "",
                "signature_type": settings.polymarket_signature_type,
            }
        except Exception:
            return None

    async def get_market_resolution(self, market_id: str) -> dict[str, Any]:
        market = await self.get_market_by_id(market_id)
        if market is None:
            return {
                "market_id": market_id,
                "found": False,
                "resolved": False,
                "closed": False,
                "winning_direction": "",
                "payout_yes": None,
                "payout_no": None,
                "reason": "market not found",
            }
        prices = [self._coerce_float(price, 0.0) for price in self._coerce_sequence(market.get("outcomePrices"))]
        if len(prices) == 1:
            prices.append(round(1 - prices[0], 4))
        payout_yes = prices[0] if prices else None
        payout_no = prices[1] if len(prices) > 1 else None
        winning_direction = ""
        if payout_yes is not None and payout_no is not None:
            if payout_yes >= 0.99 and payout_no <= 0.01:
                winning_direction = "YES"
            elif payout_no >= 0.99 and payout_yes <= 0.01:
                winning_direction = "NO"
        explicit_winner = str(
            market.get("winner")
            or market.get("winningOutcome")
            or market.get("winning_outcome")
            or market.get("resolution")
            or ""
        ).upper()
        if explicit_winner in {"YES", "NO"}:
            winning_direction = explicit_winner
        closed = bool(market.get("closed") or market.get("isClosed") or market.get("acceptingOrders") is False)
        resolved = bool(
            market.get("resolved")
            or market.get("isResolved")
            or market.get("redeemable")
            or winning_direction
        )
        return {
            "market_id": str(market.get("id") or market_id),
            "found": True,
            "resolved": resolved,
            "closed": closed,
            "winning_direction": winning_direction,
            "payout_yes": payout_yes,
            "payout_no": payout_no,
            "end_date": (
                market.get("endDate")
                or market.get("end_date")
                or market.get("closeTime")
                or ""
            ),
            "market": {
                "question": str(market.get("question") or ""),
                "slug": str(market.get("slug") or ""),
            },
        }

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

    @staticmethod
    def _mapping_from_object(value: Any) -> dict[str, Any]:
        if value in (None, ""):
            return {}
        if isinstance(value, dict):
            return value
        if hasattr(value, "__dict__"):
            return {key: item for key, item in vars(value).items() if not key.startswith("_")}
        try:
            return dict(value)
        except Exception:
            return {"value": value}

    @staticmethod
    def _match_market_id_payload(payload: Any, market_id: str) -> dict[str, Any] | None:
        items: list[dict[str, Any]]
        if isinstance(payload, dict) and any(key in payload for key in {"id", "conditionId", "question", "slug"}):
            items = [payload]
        elif isinstance(payload, list):
            items = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            nested = payload.get("data", [])
            items = [item for item in nested if isinstance(item, dict)]
        else:
            items = []
        for item in items:
            if str(item.get("id") or "") == market_id or str(item.get("conditionId") or "") == market_id:
                return item
        return None

    @classmethod
    def _extract_balance_allowance_numbers(cls, payload: Any) -> dict[str, float | None]:
        mapping = cls._mapping_from_object(payload)
        raw_balance = cls._find_value_for_keys(
            mapping,
            ("balance", "available", "availableBalance", "free", "usdcBalance", "balanceAvailable"),
        )
        raw_allowance = cls._find_value_for_keys(
            mapping,
            ("allowance", "availableAllowance", "maxAllowance", "usdcAllowance"),
        )
        balance = cls._normalize_collateral_amount(raw_balance)
        allowance = cls._normalize_collateral_amount(raw_allowance)
        return {"balance": balance, "allowance": allowance}

    async def _fetch_collateral_snapshot(
        self,
        client: ClobClient,
        *,
        sync_allowance: bool,
    ) -> tuple[Any, dict[str, float | None]]:
        collateral_params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=self.context.settings.polymarket_signature_type,
        )
        if sync_allowance:
            await to_thread(client.update_balance_allowance, collateral_params)
        collateral_status = await to_thread(client.get_balance_allowance, collateral_params)
        parsed_collateral = self._extract_balance_allowance_numbers(collateral_status)
        return collateral_status, parsed_collateral

    @classmethod
    def _find_value_for_keys(cls, value: Any, preferred_keys: tuple[str, ...]) -> Any:
        if isinstance(value, dict):
            for key in preferred_keys:
                if key in value:
                    return value.get(key)
            for nested in value.values():
                if isinstance(nested, (dict, list, tuple)):
                    parsed = cls._find_value_for_keys(nested, preferred_keys)
                    if parsed is not None:
                        return parsed
            return None
        if isinstance(value, list):
            for item in value:
                parsed = cls._find_value_for_keys(item, preferred_keys)
                if parsed is not None:
                    return parsed
            return None
        return None

    @classmethod
    def _normalize_collateral_amount(cls, value: Any) -> float | None:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                amount = float(stripped)
            except (TypeError, ValueError):
                return None
            if "." in stripped or "e" in stripped.lower():
                return amount
            return amount / 1_000_000
        if isinstance(value, int):
            return float(value) / 1_000_000
        if isinstance(value, float):
            return value
        parsed = cls._try_float(value)
        return parsed

    @classmethod
    def _has_positive_collateral_allowance(cls, payload: Any, parsed_allowance: float | None) -> bool:
        if parsed_allowance is not None and parsed_allowance > 0:
            return True
        mapping = cls._mapping_from_object(payload)
        allowances = mapping.get("allowances") if isinstance(mapping, dict) else None
        if isinstance(allowances, dict):
            return any((cls._try_float(item) or 0.0) > 0 for item in allowances.values())
        return False

    @staticmethod
    def _try_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _normalize_levels(cls, levels: Any, *, reverse: bool) -> list[dict[str, float]]:
        normalized: list[dict[str, float]] = []
        if not isinstance(levels, list):
            return normalized
        for level in levels:
            if isinstance(level, dict):
                price = cls._coerce_float(level.get("price") or level.get("p"), 0.0)
                size = cls._coerce_float(level.get("size") or level.get("quantity") or level.get("q"), 0.0)
            elif isinstance(level, (list, tuple)) and len(level) >= 2:
                price = cls._coerce_float(level[0], 0.0)
                size = cls._coerce_float(level[1], 0.0)
            else:
                continue
            if price > 0 and size > 0:
                normalized.append({"price": price, "size": size})
        normalized.sort(key=lambda item: item["price"], reverse=reverse)
        return normalized
