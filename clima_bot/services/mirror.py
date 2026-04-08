from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from core.utils import clamp

from clima_bot.config.settings import ClimaBotSettings
from clima_bot.storage.repository import ClimaBotRepository


class MirrorService:
    def __init__(self, connector, repository: ClimaBotRepository, settings: ClimaBotSettings) -> None:
        self.connector = connector
        self.repository = repository
        self.settings = settings

    async def sync_once(self) -> dict[str, Any]:
        processed = copied = skipped = 0
        reasons: dict[str, int] = {}
        wallets = [item for item in self.repository.list_wallets() if item.get("active") and not item.get("paused")]
        for wallet in wallets:
            trades = await self.connector.get_user_trades(wallet["proxy_wallet"], limit=100, offset=0, taker_only=False)
            trades = sorted(trades, key=self._trade_timestamp)
            for trade in trades:
                trade_hash = str(trade.get("transactionHash") or "")
                if not trade_hash:
                    continue
                if self.repository.has_trade_hash(trade_hash):
                    skipped += 1
                    reasons["duplicate"] = reasons.get("duplicate", 0) + 1
                    continue
                processed += 1
                if not self._is_weather_trade(trade):
                    skipped += 1
                    reasons["non_weather_market"] = reasons.get("non_weather_market", 0) + 1
                    continue
                result = await self._copy_trade(trade, wallet)
                if result["copied"]:
                    copied += 1
                else:
                    skipped += 1
                    reason = str(result["reason"])
                    reasons[reason] = reasons.get(reason, 0) + 1
        return {"processed": processed, "copied": copied, "skipped": skipped, "reasons": reasons}

    async def _copy_trade(self, trade: dict[str, Any], wallet: dict[str, Any]) -> dict[str, Any]:
        side = str(trade.get("side") or "BUY").upper()
        trade_hash = str(trade.get("transactionHash") or "")
        condition_id = str(trade.get("conditionId") or "")
        if not trade_hash or not condition_id:
            return {"copied": False, "reason": "missing_trade_identity"}
        market = await self.connector.get_market_by_id(condition_id)
        if market is None:
            return {"copied": False, "reason": "market_not_found"}
        if not self._is_weather_market_from_market(market):
            return {"copied": False, "reason": "non_weather_market"}
        token_ids = self._token_ids(market)
        if len(token_ids) != 2:
            return {"copied": False, "reason": "non_binary_market"}
        outcome_index = self._as_int(trade.get("outcomeIndex") or trade.get("outcome_index"))
        direction = "YES" if outcome_index in (0, None) else "NO"
        token_id = token_ids[0] if direction == "YES" else token_ids[1]
        book = await self.connector.get_orderbook_summary(token_id)
        if not book:
            return {"copied": False, "reason": "no_orderbook"}
        best_bid = float(book.get("best_bid") or 0.0)
        best_ask = float(book.get("best_ask") or 0.0)
        spread_bps = float(book.get("spread_bps") or 0.0)
        if best_bid <= 0 or best_ask <= 0:
            return {"copied": False, "reason": "bad_book"}
        if spread_bps > 150.0:
            return {"copied": False, "reason": "spread_too_wide"}

        trade_price = self._as_float(trade.get("price"))
        trade_size = self._as_float(trade.get("size"))
        if trade_price <= 0 or trade_size <= 0:
            return {"copied": False, "reason": "bad_trade_size"}

        bankroll_base = await self._bankroll_base()
        per_trade_limit = bankroll_base * 0.02
        max_notional = min(float(self.settings.clima_bot_max_notional_usd), per_trade_limit, bankroll_base)
        if max_notional < float(self.settings.clima_bot_min_notional_usd):
            return {"copied": False, "reason": "insufficient_available_balance"}
        notional = trade_price * trade_size
        copy_notional = clamp(
            max(notional * float(self.settings.clima_bot_copy_trade_fraction), float(self.settings.clima_bot_min_notional_usd)),
            float(self.settings.clima_bot_min_notional_usd),
            max_notional,
        )
        reference_price = best_ask if side == "BUY" else best_bid
        max_affordable_size = int(math.floor(max_notional / max(reference_price, 1e-6)))
        if max_affordable_size < 1:
            return {"copied": False, "reason": "insufficient_available_balance"}
        target_size = int(math.floor(copy_notional / max(reference_price, 1e-6)))
        copy_size = min(max_affordable_size, max(1, target_size))
        price_limit = best_ask + 0.01 if side == "BUY" else max(best_bid - 0.01, 0.01)
        order_status = await self.connector.place_order(
            market_id=str(market.get("id") or condition_id),
            token_id=token_id,
            direction=direction,
            size=copy_size,
            price_limit=price_limit,
            open_position=(side == "BUY"),
            side=side,
        )
        payload = {
            "order_id": str(uuid4()),
            "trade_hash": trade_hash,
            "proxy_wallet": str(wallet["proxy_wallet"]),
            "market_id": str(market.get("id") or condition_id),
            "position_key": f"{wallet['proxy_wallet']}:{condition_id}:{direction}",
            "asset_symbol": self._guess_asset_symbol(trade, market),
            "direction": direction,
            "action": "entry" if side == "BUY" else "close",
            "status": str(order_status.get("status") or "simulated"),
            "exchange_order_id": str(order_status.get("exchange_order_id") or ""),
            "price_limit": price_limit,
            "size": copy_size,
            "notional_usd": round(copy_size * price_limit, 4),
            "realized_pnl_usd": 0.0,
            "is_open_position": side == "BUY",
            "metadata": {
                "copied_profile_wallet": str(wallet["proxy_wallet"]),
                "source_trade_price": trade_price,
                "source_trade_size": trade_size,
                "bankroll_base_usd": round(bankroll_base, 4),
                "copy_notional_usd": round(copy_notional, 4),
            },
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.repository.record_order(payload)
        self.repository.append_log(
            "info",
            "mirror",
            f"{wallet['user_name']} {payload['action']} {payload['asset_symbol']} {direction}",
            detail=f"notional={payload['notional_usd']:.2f} status={payload['status']}",
            meta=f"wallet={wallet['proxy_wallet']}",
        )
        return {"copied": True, "reason": "mirrored"}

    async def _bankroll_base(self) -> float:
        if self.settings.live_trading and self.settings.polymarket_private_key:
            snapshot = await self.connector.get_collateral_snapshot(sync_allowance=False)
            balance = float((snapshot or {}).get("balance") or 0.0)
            if balance > 0:
                return balance
        return float(self.settings.paper_bankroll_usd)

    @staticmethod
    def _as_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _trade_timestamp(trade: dict[str, Any]) -> datetime:
        raw = trade.get("timestamp") or trade.get("createdAt") or trade.get("created_at") or datetime.now(UTC).isoformat()
        if isinstance(raw, datetime):
            return raw.astimezone(UTC) if raw.tzinfo else raw.replace(tzinfo=UTC)
        text = str(raw).strip()
        if text.isdigit():
            return datetime.fromtimestamp(float(text), tz=UTC)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)

    @staticmethod
    def _is_weather_trade(trade: dict[str, Any]) -> bool:
        slug = str(trade.get("slug") or trade.get("eventSlug") or trade.get("title") or "").lower()
        return "weather" in slug

    @staticmethod
    def _is_weather_market_from_market(market: dict[str, Any]) -> bool:
        text = " ".join(str(market.get(key) or "") for key in ("question", "slug", "description", "eventSlug", "title")).lower()
        return "weather" in text

    @staticmethod
    def _token_ids(market: dict[str, Any]) -> list[str]:
        raw = market.get("clobTokenIds") or []
        if isinstance(raw, list):
            return [str(item) for item in raw]
        if isinstance(raw, str):
            if raw.startswith("[") and raw.endswith("]"):
                raw = raw.strip("[]")
            return [item.strip().strip("'\"") for item in raw.split(",") if item.strip()]
        return []

    @staticmethod
    def _guess_asset_symbol(trade: dict[str, Any], market: dict[str, Any]) -> str:
        slug = str(market.get("slug") or trade.get("slug") or trade.get("eventSlug") or "").upper()
        for token in ("RAIN", "SNOW", "TEMP", "HURRICANE", "WEATHER", "COLD", "HOT"):
            if token in slug:
                return token
        return "WEATHER"
