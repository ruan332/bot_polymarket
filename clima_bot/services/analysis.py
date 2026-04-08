from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from core.config import load_weather_copytrade_config
from core.utils import clamp

from clima_bot.storage.repository import ClimaBotRepository


class AnalysisService:
    def __init__(self, connector, repository: ClimaBotRepository, category: str = "WEATHER") -> None:
        self.connector = connector
        self.repository = repository
        self.settings = load_weather_copytrade_config()
        self.category = category
        self.trade_lookback = timedelta(days=int(self.settings.trade_lookback_days or 30))

    async def run_analysis(self, limit: int | None = None) -> dict[str, Any]:
        leaderboard_limit = int(limit or self.settings.leaderboard_limit)
        leaderboard = await self.connector.get_trader_leaderboard(
            category=self.category,
            time_period="ALL",
            order_by="PNL",
            limit=leaderboard_limit,
        )
        candidates: list[dict[str, Any]] = []
        for index, trader in enumerate(leaderboard, start=1):
            candidate = await self._evaluate_trader(index, trader)
            if candidate is not None:
                candidates.append(candidate)

        candidates.sort(
            key=lambda item: (
                -float(item["score"]),
                -float(item["metrics"].get("pnl_30d", 0.0)),
                float(item["metrics"].get("max_drawdown", 1.0)),
                -float(item["metrics"].get("trades_30d", 0.0)),
                str(item["proxy_wallet"]),
            )
        )
        shortlisted = candidates[: int(self.settings.shortlist_limit)]
        run = {
            "run_id": str(uuid4()),
            "category": self.category,
            "leaderboard_limit": leaderboard_limit,
            "shortlisted_count": len(shortlisted),
            "summary": self._run_summary(shortlisted),
            "metadata": {
                "candidate_count": len(candidates),
                "thresholds": self._thresholds_payload(),
            },
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.repository.save_analysis_run(run, shortlisted)
        self.repository.append_log(
            "info",
            "analysis",
            f"Analysis completed with {len(shortlisted)} shortlisted profiles",
            detail=run["summary"],
            meta=f"limit={leaderboard_limit}",
        )
        return {"run": run, "candidates": shortlisted}

    def approve_wallets(self, proxy_wallets: list[str], max_wallets: int) -> list[dict[str, Any]]:
        latest = self.repository.get_latest_analysis()
        candidates = latest["candidates"]
        tracked_count = self.repository.count_wallets()
        selected: list[dict[str, Any]] = []
        for wallet in proxy_wallets:
            if tracked_count + len(selected) >= max_wallets:
                break
            candidate = next((item for item in candidates if item["proxy_wallet"] == wallet), None)
            if candidate is None:
                continue
            payload = {
                "proxy_wallet": candidate["proxy_wallet"],
                "user_name": candidate["user_name"],
                "score": candidate["score"],
                "approved": True,
                "active": True,
                "paused": False,
                "profile": candidate["profile"],
                "metrics": candidate["metrics"],
                "selection": candidate,
            }
            selected.append(self.repository.upsert_wallet(payload))
        return selected

    async def _evaluate_trader(self, rank: int, trader: dict[str, Any]) -> dict[str, Any] | None:
        proxy_wallet = str(trader.get("proxyWallet") or trader.get("proxy_wallet") or "").strip()
        if not proxy_wallet:
            return None
        user_name = str(trader.get("userName") or trader.get("user_name") or "").strip() or proxy_wallet[:10]
        profile = await self.connector.get_public_profile(proxy_wallet) or {}
        if not self._profile_visible(profile, trader):
            return None

        week = await self.connector.get_trader_leaderboard(
            category=self.category,
            time_period="WEEK",
            order_by="PNL",
            limit=1,
            user=proxy_wallet,
        )
        month = await self.connector.get_trader_leaderboard(
            category=self.category,
            time_period="MONTH",
            order_by="PNL",
            limit=1,
            user=proxy_wallet,
        )
        all_time = await self.connector.get_trader_leaderboard(
            category=self.category,
            time_period="ALL",
            order_by="PNL",
            limit=1,
            user=proxy_wallet,
        )
        trades = await self.connector.get_user_trades(proxy_wallet, limit=200, offset=0, taker_only=False)
        positions = await self.connector.get_current_positions(proxy_wallet, limit=100, offset=0)
        closed_positions = await self.connector.get_closed_positions(proxy_wallet, limit=200, offset=0)

        metrics = self._build_metrics(trades, positions, closed_positions, week, month, all_time)
        passed, reject_reason = self._passes_thresholds(metrics)
        score = self._score_metrics(metrics, trader)
        return {
            "rank": rank,
            "proxy_wallet": proxy_wallet,
            "user_name": user_name,
            "verified_badge": bool(trader.get("verifiedBadge") or trader.get("verified") or profile.get("verifiedBadge")),
            "profile": self._normalize_profile(profile, proxy_wallet, user_name),
            "metrics": metrics,
            "score": round(score, 4),
            "rationale": self._build_rationale(metrics, passed, reject_reason),
            "passed": passed,
            "reject_reason": reject_reason,
            "created_at": datetime.now(UTC).isoformat(),
        }

    def _run_summary(self, shortlisted: list[dict[str, Any]]) -> str:
        if not shortlisted:
            return "Nenhum trader WEATHER atingiu os thresholds conservadores."
        lead = shortlisted[0]
        metrics = lead["metrics"]
        return (
            f"{lead['user_name']} lidera com score={lead['score']:.2f}, pnl30d={metrics.get('pnl_30d', 0):.2f}, "
            f"profit_factor={metrics.get('profit_factor', 0):.2f}, win_rate={metrics.get('win_rate', 0):.2%}."
        )

    def _passes_thresholds(self, metrics: dict[str, Any]) -> tuple[bool, str]:
        if metrics.get("pnl_all", 0.0) < float(self.settings.min_pnl_all):
            return False, "pnl_all_below_threshold"
        if metrics.get("pnl_30d", 0.0) < float(self.settings.min_pnl_30d):
            return False, "pnl_30d_below_threshold"
        if metrics.get("pnl_7d", 0.0) < float(self.settings.min_pnl_7d):
            return False, "pnl_7d_below_threshold"
        if metrics.get("trades_30d", 0) < int(self.settings.min_trades_30d):
            return False, "insufficient_30d_trades"
        if metrics.get("trades_7d", 0) < int(self.settings.min_trades_7d):
            return False, "insufficient_7d_trades"
        if metrics.get("closed_positions_30d", 0) < int(self.settings.min_closed_positions_30d):
            return False, "insufficient_closed_positions"
        if metrics.get("positive_weeks_4", 0) < int(self.settings.min_positive_weeks_4):
            return False, "insufficient_positive_weeks"
        if metrics.get("max_drawdown", 1.0) > float(self.settings.max_drawdown):
            return False, "drawdown_too_high"
        if metrics.get("profit_factor", 0.0) < float(self.settings.min_profit_factor):
            return False, "profit_factor_too_low"
        if metrics.get("win_rate", 0.0) < float(self.settings.min_win_rate):
            return False, "win_rate_too_low"
        if metrics.get("pnl_concentration", 1.0) > float(self.settings.max_pnl_concentration):
            return False, "pnl_too_concentrated"
        return True, "passed"

    def _score_metrics(self, metrics: dict[str, Any], trader: dict[str, Any]) -> float:
        pnl_scale = clamp((metrics.get("pnl_30d", 0.0) / 1000.0) + (metrics.get("pnl_all", 0.0) / 2000.0), 0.0, 1.5)
        trade_scale = clamp(metrics.get("trades_30d", 0) / max(float(self.settings.min_trades_30d), 1.0), 0.0, 1.5)
        consistency_scale = clamp(metrics.get("positive_weeks_4", 0) / max(float(self.settings.min_positive_weeks_4), 1.0), 0.0, 1.0)
        drawdown_penalty = 1.0 - clamp(metrics.get("max_drawdown", 0.0) / max(float(self.settings.max_drawdown), 1e-6), 0.0, 1.0)
        profit_factor = clamp(metrics.get("profit_factor", 0.0) / max(float(self.settings.min_profit_factor), 1e-6), 0.0, 1.5)
        concentration_penalty = 1.0 - clamp(metrics.get("pnl_concentration", 1.0), 0.0, 1.0)
        verified_bonus = 0.08 if bool(trader.get("verifiedBadge")) else 0.0
        return round(
            100
            * (
                0.24 * pnl_scale
                + 0.20 * trade_scale
                + 0.18 * consistency_scale
                + 0.18 * drawdown_penalty
                + 0.14 * profit_factor
                + 0.06 * concentration_penalty
                + verified_bonus
            ),
            4,
        )

    def _build_metrics(
        self,
        trades: list[dict[str, Any]],
        positions: list[dict[str, Any]],
        closed_positions: list[dict[str, Any]],
        week: list[dict[str, Any]],
        month: list[dict[str, Any]],
        all_time: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        trades_30d = [item for item in trades if self._trade_timestamp(item) >= now - self.trade_lookback]
        trades_7d = [item for item in trades if self._trade_timestamp(item) >= now - timedelta(days=7)]
        pnl_7d = self._leaderboard_number(week, ("pnl", "cashPnl", "percentPnl"))
        pnl_30d = self._leaderboard_number(month, ("pnl", "cashPnl", "percentPnl"))
        pnl_all = self._leaderboard_number(all_time, ("pnl", "cashPnl", "percentPnl"))
        closed_pnls = [self._position_pnl(item) for item in closed_positions]
        positive = [value for value in closed_pnls if value > 0]
        negative = [value for value in closed_pnls if value < 0]
        profit_factor = sum(positive) / abs(sum(negative)) if negative else (sum(positive) if positive else 0.0)
        win_rate = len(positive) / len(closed_pnls) if closed_pnls else 0.0
        drawdown = self._max_drawdown(closed_positions)
        positive_weeks = self._positive_weeks(closed_positions)
        concentration = self._pnl_concentration(closed_positions)
        open_value = sum(self._current_value(item) for item in positions)
        total_value = self._leaderboard_number(all_time, ("value", "currentValue", "totalValue", "vol"))
        total_value = total_value if total_value > 0 else max(open_value, 0.0)
        return {
            "trades_30d": len(trades_30d),
            "trades_7d": len(trades_7d),
            "closed_positions_30d": len(closed_positions),
            "open_positions": len(positions),
            "pnl_7d": round(pnl_7d, 4),
            "pnl_30d": round(pnl_30d, 4),
            "pnl_all": round(pnl_all, 4),
            "profit_factor": round(profit_factor, 4),
            "win_rate": round(win_rate, 4),
            "max_drawdown": round(drawdown, 4),
            "positive_weeks_4": positive_weeks,
            "pnl_concentration": round(concentration, 4),
            "position_value": round(open_value, 4),
            "total_value": round(total_value, 4),
        }

    def _build_rationale(self, metrics: dict[str, Any], passed: bool, reject_reason: str) -> str:
        if not passed:
            return f"reject:{reject_reason} | pnl30d={metrics.get('pnl_30d', 0):.2f} | dd={metrics.get('max_drawdown', 0):.2%}"
        return (
            f"consistent pnl={metrics.get('pnl_30d', 0):.2f} "
            f"pf={metrics.get('profit_factor', 0):.2f} "
            f"dd={metrics.get('max_drawdown', 0):.2%} "
            f"weeks={metrics.get('positive_weeks_4', 0)}/4"
        )

    def _profile_visible(self, profile: dict[str, Any], trader: dict[str, Any]) -> bool:
        if profile.get("displayUsernamePublic") is True:
            return True
        if profile.get("name") or profile.get("pseudonym") or profile.get("xUsername"):
            return True
        return bool(trader.get("userName"))

    def _normalize_profile(self, profile: dict[str, Any], proxy_wallet: str, fallback_name: str) -> dict[str, Any]:
        return {
            "created_at": profile.get("createdAt"),
            "proxy_wallet": profile.get("proxyWallet") or proxy_wallet,
            "profile_image": profile.get("profileImage"),
            "display_username_public": profile.get("displayUsernamePublic"),
            "bio": profile.get("bio"),
            "pseudonym": profile.get("pseudonym") or fallback_name,
            "name": profile.get("name") or fallback_name,
            "x_username": profile.get("xUsername"),
            "verified_badge": profile.get("verifiedBadge"),
        }

    def _thresholds_payload(self) -> dict[str, Any]:
        return {
            "min_trades_30d": self.settings.min_trades_30d,
            "min_trades_7d": self.settings.min_trades_7d,
            "min_closed_positions_30d": self.settings.min_closed_positions_30d,
            "min_positive_weeks_4": self.settings.min_positive_weeks_4,
            "max_drawdown": self.settings.max_drawdown,
            "min_profit_factor": self.settings.min_profit_factor,
            "min_win_rate": self.settings.min_win_rate,
            "max_pnl_concentration": self.settings.max_pnl_concentration,
            "max_spread_bps": self.settings.max_spread_bps,
            "min_notional_usd": self.settings.min_notional_usd,
            "max_notional_usd": self.settings.max_notional_usd,
            "copy_trade_fraction": self.settings.copy_trade_fraction,
        }

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
    def _leaderboard_number(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> float:
        if not rows:
            return 0.0
        row = rows[0]
        for key in keys:
            value = row.get(key)
            if value in (None, ""):
                continue
            return float(value)
        return 0.0

    @staticmethod
    def _position_pnl(position: dict[str, Any]) -> float:
        for key in ("realizedPnl", "cashPnl", "pnl", "percentPnl"):
            value = position.get(key)
            if value not in (None, ""):
                return float(value)
        return 0.0

    @staticmethod
    def _current_value(position: dict[str, Any]) -> float:
        for key in ("currentValue", "value", "size"):
            value = position.get(key)
            if value not in (None, ""):
                return float(value)
        return 0.0

    def _max_drawdown(self, closed_positions: list[dict[str, Any]]) -> float:
        ordered = sorted(closed_positions, key=lambda item: self._trade_timestamp(item))
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for item in ordered:
            equity += self._position_pnl(item)
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)
        return max_drawdown

    def _positive_weeks(self, closed_positions: list[dict[str, Any]]) -> int:
        weeks: dict[tuple[int, int], float] = defaultdict(float)
        for item in closed_positions:
            ts = self._trade_timestamp(item)
            iso = ts.isocalendar()
            weeks[(iso.year, iso.week)] += self._position_pnl(item)
        ordered = sorted(weeks.items(), key=lambda pair: pair[0], reverse=True)
        return sum(1 for _, pnl in ordered[:4] if pnl > 0)

    def _pnl_concentration(self, closed_positions: list[dict[str, Any]]) -> float:
        by_market: dict[str, float] = defaultdict(float)
        total_positive = 0.0
        for item in closed_positions:
            pnl = self._position_pnl(item)
            if pnl <= 0:
                continue
            key = str(item.get("slug") or item.get("title") or item.get("eventSlug") or item.get("conditionId") or "unknown")
            by_market[key] += pnl
            total_positive += pnl
        if total_positive <= 0:
            return 0.0
        top = max(by_market.values(), default=0.0)
        return top / total_positive
