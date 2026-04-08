from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.cost_tracker import CostTracker
from core.exceptions import BudgetExceededError, InvalidModelResponseError
from core.market_connector import MarketConnector
from core.model_provider import ModelProvider
from core.utils import clamp, parse_json_object, sanitize_text

if TYPE_CHECKING:
    from core.app_context import AppContext


@dataclass(slots=True)
class WeatherCopytradeCandidate:
    proxy_wallet: str
    user_name: str
    rank: int
    score: float
    profile: dict[str, Any]
    metrics: dict[str, Any]
    rationale: str
    verified_badge: bool = False
    selected: bool = False


class WeatherCopytradeService:
    def __init__(
        self,
        context: AppContext,
        *,
        connector: MarketConnector | None = None,
        provider: ModelProvider | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self.context = context
        self.connector = connector or MarketConnector(context)
        self.provider = provider or ModelProvider("weather_copytrade", context)
        self.cost_tracker = cost_tracker or CostTracker("weather_copytrade", context)
        self.settings = context.weather_copytrade_config
        self.category = str(self.settings.category or "WEATHER").upper()
        self.trade_lookback = timedelta(days=int(self.settings.trade_lookback_days or 30))

    async def close(self) -> None:
        await self.connector.close()

    async def run_analysis(self, *, limit: int | None = None) -> dict[str, Any]:
        leaderboard_limit = int(limit or self.settings.leaderboard_limit)
        profiles = await self.context.repository.list_weather_copytrade_profiles(category=self.category)
        active_wallets = {
            str(item.get("proxy_wallet") or "")
            for item in profiles
            if bool(item.get("approved")) and bool(item.get("active")) and not bool(item.get("paused"))
        }
        leaderboard = await self.connector.get_trader_leaderboard(
            category=self.category,
            time_period="ALL",
            order_by="PNL",
            limit=leaderboard_limit,
        )
        rejected_breakdown: Counter[str] = Counter()
        enriched: list[dict[str, Any]] = []
        for index, trader in enumerate(leaderboard, start=1):
            candidate = await self._evaluate_trader(index, trader)
            if candidate is None:
                rejected_breakdown["invalid_profile"] += 1
                continue
            enriched.append(candidate)
            if not candidate["passed"]:
                rejected_breakdown[candidate["reject_reason"]] += 1

        enriched.sort(
            key=lambda item: (
                -float(item["score"]),
                -float(item["metrics"].get("pnl_30d", 0.0)),
                float(item["metrics"].get("max_drawdown", 1.0)),
                -float(item["metrics"].get("trades_30d", 0.0)),
                str(item["proxy_wallet"]),
            )
        )
        shortlisted = enriched[: int(self.settings.shortlist_limit)]
        recommended = self._pick_candidate(shortlisted)
        shortlisted_response = [self._decorate_candidate(item, active_wallets=active_wallets) for item in shortlisted]

        report = await self._build_short_report(recommended, shortlisted)
        run_payload = {
            "run_id": str(uuid4()),
            "category": self.category,
            "leaderboard_limit": leaderboard_limit,
            "universe_count": len(leaderboard),
            "shortlisted_count": len(shortlisted),
            "selected_count": len(active_wallets),
            "selected_proxy_wallet": recommended["proxy_wallet"] if recommended else "",
            "selected_user_name": recommended["user_name"] if recommended else "",
            "candidate_count": len(enriched),
            "stage_counts": [
                {"label": "universe", "count": len(leaderboard)},
                {"label": "profiled", "count": len(enriched)},
                {"label": "shortlisted", "count": len(shortlisted)},
                {"label": "copying", "count": len(active_wallets)},
            ],
            "rejected_breakdown": dict(sorted(rejected_breakdown.items(), key=lambda pair: (-pair[1], pair[0]))),
            "model_summary": report,
            "selection_summary": recommended["metrics"] if recommended else {},
            "scan_stats": {
                "category": self.category,
                "leaderboard_limit": leaderboard_limit,
                "universe_count": len(leaderboard),
                "enriched_count": len(enriched),
                "shortlisted_count": len(shortlisted),
                "active_profiles_count": len(active_wallets),
            },
            "metadata": {
                "scan_interval_minutes": int(self.settings.scan_interval_minutes),
                "copy_trade_fraction": float(self.settings.copy_trade_fraction),
                "max_notional_usd": float(self.settings.max_notional_usd),
                "min_notional_usd": float(self.settings.min_notional_usd),
                "thresholds": self._thresholds_payload(),
            },
            "created_at": datetime.now(UTC),
        }
        await self.context.repository.record_weather_copytrade_run(run_payload)
        await self.context.repository.record_weather_copytrade_candidates(
            [
                {
                    "run_id": run_payload["run_id"],
                    "rank": item["rank"],
                    "proxy_wallet": item["proxy_wallet"],
                    "user_name": item["user_name"],
                    "verified_badge": item["verified_badge"],
                    "profile": item["profile"],
                    "metrics": item["metrics"],
                    "score": item["score"],
                    "rationale": item["rationale"],
                    "passed": bool(item.get("passed", False)),
                    "reject_reason": item.get("reject_reason", ""),
                    "selected": bool(item.get("proxy_wallet") in active_wallets),
                    "created_at": item["created_at"],
                }
                for item in shortlisted
            ]
        )
        legacy_state = await self._sync_legacy_state(
            profiles=profiles,
            run_payload=run_payload,
            report=report,
            recommended=recommended,
        )
        portfolio_constraints = await self._portfolio_constraints()
        return {
            "run": run_payload,
            "candidates": shortlisted_response,
            "selected": recommended,
            "profiles": await self.list_profiles(profiles=profiles),
            "report": report,
            "state": legacy_state,
            "portfolio_constraints": portfolio_constraints,
        }

    async def approve_selection(
        self,
        *,
        run_id: str | None = None,
        proxy_wallet: str | None = None,
        proxy_wallets: list[str] | None = None,
    ) -> dict[str, Any]:
        summary = await self.context.repository.get_latest_weather_copytrade_summary(limit=int(self.settings.shortlist_limit))
        run = summary.get("run") or {}
        candidates = summary.get("candidates") or []
        if not run:
            raise ValueError("no weather copytrade run available")
        report = run.get("model_summary") or {}
        target_wallets = [item for item in (proxy_wallets or []) if str(item).strip()]
        if proxy_wallet and proxy_wallet not in target_wallets:
            target_wallets.append(proxy_wallet)
        if not target_wallets:
            selected = self._pick_candidate(candidates, proxy_wallet=proxy_wallet)
            if selected is None:
                raise ValueError("no candidate available to approve")
            target_wallets = [str(selected["proxy_wallet"])]

        approved_profiles: list[dict[str, Any]] = []
        for wallet in target_wallets:
            selected = self._pick_candidate(candidates, proxy_wallet=wallet)
            if selected is None:
                raise ValueError(f"candidate {wallet} not available in latest run")
            existing = await self.context.repository.get_weather_copytrade_profile(self.category, wallet)
            profile_payload = {
                "category": self.category,
                "run_id": run.get("run_id"),
                "proxy_wallet": selected["proxy_wallet"],
                "user_name": selected["user_name"],
                "profile": selected.get("profile") or {},
                "selection_snapshot": {
                    "proxy_wallet": selected["proxy_wallet"],
                    "user_name": selected["user_name"],
                    "score": selected["score"],
                    "metrics": selected.get("metrics") or {},
                    "rationale": selected.get("rationale") or "",
                    "verified_badge": bool(selected.get("verified_badge", False)),
                },
                "approved": True,
                "active": True,
                "paused": False,
                "approved_at": (existing or {}).get("approved_at", datetime.now(UTC)),
                "activated_at": datetime.now(UTC),
                "last_trade_seen_at": (existing or {}).get("last_trade_seen_at"),
                "last_trade_seen_hash": str((existing or {}).get("last_trade_seen_hash") or ""),
                "processed_trade_hashes": self._json_list((existing or {}).get("processed_trade_hashes")),
                "metrics_snapshot": selected.get("metrics") or {},
                "performance_snapshot": await self._profile_performance_snapshot(selected["proxy_wallet"]),
                "metadata": {
                    **self._metadata_map((existing or {}).get("metadata")),
                    "approved_from_run_id": run.get("run_id"),
                    "approved_at": datetime.now(UTC).isoformat(),
                },
                "created_at": (existing or {}).get("created_at", datetime.now(UTC)),
                "updated_at": datetime.now(UTC),
            }
            approved_profiles.append(await self.context.repository.upsert_weather_copytrade_profile(profile_payload))
        legacy_state = await self._sync_legacy_state(
            profiles=await self.context.repository.list_weather_copytrade_profiles(category=self.category),
            run_payload=run,
            report=report,
        )
        return {
            "run": run,
            "profiles": approved_profiles,
            "state": legacy_state,
            "report": report,
            "approved_count": len(approved_profiles),
            "requested_run_id": run_id,
            "used_run_id": run.get("run_id"),
            "stale_run_id": bool(run_id and str(run.get("run_id")) != run_id),
        }

    async def pause(self, paused: bool = True) -> dict[str, Any]:
        profiles = await self.context.repository.list_weather_copytrade_profiles(category=self.category)
        updated_profiles: list[dict[str, Any]] = []
        for profile in profiles:
            updated_profiles.append(
                await self.context.repository.upsert_weather_copytrade_profile(
                    {
                        **profile,
                        "paused": paused,
                        "active": False if paused else bool(profile.get("approved")),
                        "updated_at": datetime.now(UTC),
                        "created_at": profile.get("created_at", datetime.now(UTC)),
                    }
                )
            )
        state = await self._sync_legacy_state(
            profiles=updated_profiles,
            run_payload=(await self.context.repository.get_latest_weather_copytrade_summary(limit=int(self.settings.shortlist_limit)) or {}).get("run"),
            report=((await self.context.repository.get_latest_weather_copytrade_summary(limit=int(self.settings.shortlist_limit)) or {}).get("report") or {}),
        )
        return {"state": state, "profiles": updated_profiles}

    async def pause_profile(self, *, proxy_wallet: str, paused: bool = True) -> dict[str, Any]:
        profile = await self.context.repository.get_weather_copytrade_profile(self.category, proxy_wallet)
        if profile is None:
            raise ValueError("profile not found")
        updated = await self.context.repository.upsert_weather_copytrade_profile(
            {
                **profile,
                "paused": paused,
                "active": False if paused else bool(profile.get("approved")),
                "updated_at": datetime.now(UTC),
                "created_at": profile.get("created_at", datetime.now(UTC)),
            }
        )
        profiles = await self.context.repository.list_weather_copytrade_profiles(category=self.category)
        summary = await self.context.repository.get_latest_weather_copytrade_summary(limit=int(self.settings.shortlist_limit)) or {}
        state = await self._sync_legacy_state(profiles=profiles, run_payload=summary.get("run"), report=summary.get("report") or {})
        return {"profile": updated, "state": state}

    async def remove_profile(self, *, proxy_wallet: str) -> dict[str, Any]:
        profile = await self.context.repository.get_weather_copytrade_profile(self.category, proxy_wallet)
        if profile is None:
            raise ValueError("profile not found")
        await self.context.repository.delete_weather_copytrade_profile(category=self.category, proxy_wallet=proxy_wallet)
        profiles = await self.context.repository.list_weather_copytrade_profiles(category=self.category)
        summary = await self.context.repository.get_latest_weather_copytrade_summary(limit=int(self.settings.shortlist_limit)) or {}
        state = await self._sync_legacy_state(profiles=profiles, run_payload=summary.get("run"), report=summary.get("report") or {})
        return {"removed_proxy_wallet": proxy_wallet, "state": state, "profiles": profiles}

    async def list_profiles(self, *, profiles: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        rows = profiles if profiles is not None else await self.context.repository.list_weather_copytrade_profiles(category=self.category)
        enriched: list[dict[str, Any]] = []
        for row in rows:
            selection = self._metadata_map(row.get("selection_snapshot"))
            performance = self._metadata_map(row.get("performance_snapshot"))
            if not performance:
                performance = await self._profile_performance_snapshot(str(row.get("proxy_wallet") or ""))
            enriched.append(
                {
                    **row,
                    "score": self._as_float(selection.get("score")),
                    "metrics_snapshot": self._metadata_map(row.get("metrics_snapshot")),
                    "performance_snapshot": performance,
                }
            )
        return enriched

    async def summary(self) -> dict[str, Any]:
        payload = await self.context.repository.get_latest_weather_copytrade_summary(limit=int(self.settings.shortlist_limit)) or {
            "run": None,
            "candidates": [],
            "state": await self.context.repository.get_weather_copytrade_state(self.category),
            "profiles": await self.context.repository.list_weather_copytrade_profiles(category=self.category),
        }
        profiles = await self.list_profiles(profiles=payload.get("profiles") or [])
        active_wallets = {str(item.get("proxy_wallet") or "") for item in profiles if bool(item.get("active")) and not bool(item.get("paused"))}
        payload["profiles"] = profiles
        payload["candidates"] = [self._decorate_candidate(item, active_wallets=active_wallets) for item in (payload.get("candidates") or [])]
        payload["portfolio_constraints"] = await self._portfolio_constraints()
        payload["state"] = await self._sync_legacy_state(
            profiles=payload.get("profiles") or [],
            run_payload=payload.get("run"),
            report=payload.get("report") or {},
            recommended=self._pick_candidate(payload.get("candidates") or []),
        )
        return payload

    async def sync_mirror_trades(self) -> dict[str, Any]:
        profiles = await self.context.repository.list_weather_copytrade_profiles(category=self.category)
        active_profiles = [item for item in profiles if bool(item.get("active")) and not bool(item.get("paused"))]
        if not active_profiles:
            state = await self._sync_legacy_state(profiles=profiles)
            return {"processed": 0, "copied": 0, "skipped": 0, "reasons": {}, "state": state, "profiles": profiles}
        processed = copied = skipped = 0
        reasons: Counter[str] = Counter()
        synced_profiles: list[dict[str, Any]] = []

        for profile in active_profiles:
            proxy_wallet = str(profile.get("proxy_wallet") or "")
            if not proxy_wallet:
                reasons["no_selected_wallet"] += 1
                continue
            trades = await self.connector.get_user_trades(proxy_wallet, limit=100, offset=0, taker_only=False)
            trades.sort(key=self._trade_timestamp)
            processed_hashes = set(self._json_list(profile.get("processed_trade_hashes")))
            raw_last_seen = profile.get("last_trade_seen_at")
            last_seen_at = self._parse_datetime(raw_last_seen) if raw_last_seen not in (None, "") else (datetime.now(UTC) - timedelta(minutes=30))
            latest_seen_at = self._parse_datetime(raw_last_seen) if raw_last_seen not in (None, "") else None
            latest_seen_hash = str(profile.get("last_trade_seen_hash") or "")

            for trade in trades:
                trade_hash = str(trade.get("transactionHash") or "")
                if not trade_hash or trade_hash in processed_hashes:
                    skipped += 1
                    reasons["duplicate"] += 1
                    continue
                if self._trade_timestamp(trade) <= last_seen_at:
                    continue
                processed += 1
                if not self._is_weather_trade(trade):
                    skipped += 1
                    reasons["non_weather_market"] += 1
                    processed_hashes.add(trade_hash)
                    continue
                result = await self._copy_trade(trade, profile)
                processed_hashes.add(trade_hash)
                latest_seen_at = max(latest_seen_at or self._trade_timestamp(trade), self._trade_timestamp(trade))
                latest_seen_hash = trade_hash
                if result["copied"]:
                    copied += 1
                else:
                    skipped += 1
                    reasons[result["reason"]] += 1

            processed_history = self._json_list(profile.get("processed_trade_hashes"))
            for trade_hash in processed_hashes:
                if trade_hash not in processed_history:
                    processed_history.append(trade_hash)
            synced_profiles.append(
                await self.context.repository.upsert_weather_copytrade_profile(
                    {
                        **profile,
                        "last_trade_seen_at": latest_seen_at,
                        "last_trade_seen_hash": latest_seen_hash,
                        "processed_trade_hashes": processed_history[-500:],
                        "performance_snapshot": await self._profile_performance_snapshot(proxy_wallet),
                        "metadata": {
                            **self._metadata_map(profile.get("metadata")),
                            "last_sync_at": datetime.now(UTC).isoformat(),
                        },
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
        state = await self._sync_legacy_state(profiles=await self.context.repository.list_weather_copytrade_profiles(category=self.category))
        return {
            "processed": processed,
            "copied": copied,
            "skipped": skipped,
            "reasons": dict(reasons),
            "state": state,
            "profiles": synced_profiles,
        }

    async def sync_live_order_statuses(self, *, limit: int = 100) -> dict[str, Any]:
        if not bool(getattr(self.context.settings, "live_trading", False)):
            return {"scanned": 0, "synced": 0, "filled": 0, "cancelled": 0, "open": 0, "orders": []}
        recent_orders = await self.context.repository.get_recent_orders(limit=limit, strategy="weather_copytrade")
        tracked_orders = [
            item
            for item in recent_orders
            if str(item.get("status") or "") in {"live_submitted", "live_filled"}
        ]
        scanned = synced = filled = cancelled = open_orders = 0
        order_events: list[dict[str, object]] = []
        for order in tracked_orders:
            exchange_order_id = str(order.get("exchange_order_id") or order.get("order_id") or "").strip()
            order_id = str(order.get("order_id") or "").strip()
            signal_id = str(order.get("signal_id") or "").strip()
            market_id = str(order.get("market_id") or "").strip()
            if not exchange_order_id:
                continue
            scanned += 1
            live_state = await self._live_order_state(exchange_order_id, order)
            if live_state is None or live_state["state"] == "open":
                open_orders += 1
                continue
            synced += 1
            order_events.append(
                {
                    "order_id": order_id,
                    "exchange_order_id": exchange_order_id,
                    "state": live_state["state"],
                    "exchange_status": live_state.get("exchange_status", ""),
                }
            )
            if not order_id or not signal_id or not market_id:
                continue
            if live_state["state"] == "filled":
                filled += 1
                await self.context.repository.record_paper_order(
                    order_id,
                    signal_id,
                    market_id,
                    "live_filled",
                    {
                        **order,
                        **live_state.get("payload", {}),
                        "status": "live_filled",
                        "exchange_order_id": exchange_order_id,
                        "realized_pnl_usd": float(order.get("realized_pnl_usd") or 0.0),
                    },
                )
            elif live_state["state"] == "cancelled":
                cancelled += 1
                await self.context.repository.record_paper_order(
                    order_id,
                    signal_id,
                    market_id,
                    "cancelled",
                    {
                        **order,
                        **live_state.get("payload", {}),
                        "status": "cancelled",
                        "exchange_order_id": exchange_order_id,
                        "reason": live_state.get("reason") or "exchange_cancelled",
                    },
                )
        return {
            "scanned": scanned,
            "synced": synced,
            "filled": filled,
            "cancelled": cancelled,
            "open": open_orders,
            "orders": order_events[:10],
        }

    async def _sync_legacy_state(
        self,
        *,
        profiles: list[dict[str, Any]] | None = None,
        run_payload: dict[str, Any] | None = None,
        report: dict[str, Any] | None = None,
        recommended: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rows = profiles if profiles is not None else await self.context.repository.list_weather_copytrade_profiles(category=self.category)
        active_profiles = [item for item in rows if bool(item.get("active")) and not bool(item.get("paused"))]
        lead = active_profiles[0] if active_profiles else (recommended or {})
        state_payload = {
            "category": self.category,
            "run_id": (run_payload or {}).get("run_id"),
            "selected_proxy_wallet": str(lead.get("proxy_wallet") or ""),
            "selected_user_name": str(lead.get("user_name") or ""),
            "selected_profile": lead.get("profile") or {},
            "selection": lead.get("selection_snapshot") or lead,
            "report": report or {},
            "approved": bool(active_profiles),
            "active": bool(active_profiles),
            "paused": False if active_profiles else True,
            "approved_at": active_profiles[0].get("approved_at") if active_profiles else None,
            "activated_at": active_profiles[0].get("activated_at") if active_profiles else None,
            "last_trade_seen_at": active_profiles[0].get("last_trade_seen_at") if active_profiles else None,
            "last_trade_seen_hash": str(active_profiles[0].get("last_trade_seen_hash") or "") if active_profiles else "",
            "processed_trade_hashes": self._json_list(active_profiles[0].get("processed_trade_hashes")) if active_profiles else [],
            "metadata": {
                "active_profiles_count": len(active_profiles),
                "proxy_wallets": [str(item.get("proxy_wallet") or "") for item in active_profiles],
            },
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        return await self.context.repository.upsert_weather_copytrade_state(state_payload)

    async def _portfolio_constraints(self) -> dict[str, Any]:
        portfolio = await self.context.repository.get_portfolio_summary()
        base = max(min(float(portfolio.available_balance), float(portfolio.total_equity)), 0.0)
        per_trade_limit = round(base * 0.02, 4)
        open_positions = await self.context.repository.get_open_positions()
        weather_open_positions = [
            item for item in open_positions if str(item.get("strategy_id") or "") == "weather_copytrade"
        ]
        profiles = await self.context.repository.list_weather_copytrade_profiles(category=self.category)
        active_profiles = [item for item in profiles if bool(item.get("active")) and not bool(item.get("paused"))]
        return {
            "bankroll_base_usd": round(base, 4),
            "per_trade_limit_usd": per_trade_limit,
            "risk_rule": "2%_of_current_bankroll",
            "max_notional_usd": float(self.settings.max_notional_usd),
            "min_notional_usd": float(self.settings.min_notional_usd),
            "copy_trade_fraction": float(self.settings.copy_trade_fraction),
            "active_profiles_count": len(active_profiles),
            "open_positions_count": len(weather_open_positions),
            "max_open_copied_positions": int(self.settings.max_open_copied_positions),
        }

    async def _profile_performance_snapshot(self, proxy_wallet: str) -> dict[str, Any]:
        if not proxy_wallet:
            return {}
        report = await self.context.repository.get_performance_report(
            hours=720,
            strategy="weather_copytrade",
            trade_group_id=proxy_wallet,
        )
        summary = report.get("summary") or {}
        return {
            "orders": int(summary.get("orders") or 0),
            "signals": int(summary.get("signals") or 0),
            "risk_events": int(summary.get("risk_events") or 0),
            "approval_rate": float(summary.get("approval_rate") or 0.0),
            "execution_rate": float(summary.get("execution_rate") or 0.0),
            "win_rate": float(summary.get("win_rate") or 0.0),
            "realized_pnl_window": float(summary.get("realized_pnl_window") or 0.0),
            "max_drawdown": float(summary.get("max_drawdown") or 0.0),
        }

    def _decorate_candidate(self, candidate: dict[str, Any], *, active_wallets: set[str]) -> dict[str, Any]:
        payload = dict(candidate)
        wallet = str(payload.get("proxy_wallet") or "")
        if wallet in active_wallets:
            status = "already_copying"
        elif bool(payload.get("passed")):
            status = "eligible"
        else:
            status = "rejected"
        payload["status"] = status
        payload["selected"] = wallet in active_wallets
        return payload

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
        rationale = self._build_rationale(metrics, passed, reject_reason)
        return {
            "rank": rank,
            "proxy_wallet": proxy_wallet,
            "user_name": user_name,
            "verified_badge": bool(trader.get("verifiedBadge") or trader.get("verified") or profile.get("verifiedBadge")),
            "profile": self._normalize_profile(profile, proxy_wallet, user_name),
            "metrics": metrics,
            "score": round(score, 4),
            "rationale": rationale,
            "passed": passed,
            "reject_reason": reject_reason,
            "created_at": datetime.now(UTC),
        }

    async def _build_short_report(self, selected: dict[str, Any] | None, shortlisted: list[dict[str, Any]]) -> dict[str, Any]:
        if selected is None:
            return {
                "summary": "Nenhum trader WEATHER atingiu os thresholds conservadores.",
                "why": "Sem candidato consistente o bastante para ativacao.",
                "risks": ["historico insuficiente", "drawdown elevado", "perfil nao selecionado"],
                "selected_proxy_wallet": "",
                "selected_user_name": "",
                "model": "deterministic",
                "provider": "deterministic",
                "fallback_used": False,
            }
        prompt = (
            "Escolha o melhor trader WEATHER para copytrade conservador.\n"
            f"Selecionado: {selected['user_name']} ({selected['proxy_wallet']})\n"
            f"Score: {selected['score']:.4f}\n"
            f"Metrics: {selected['metrics']}\n"
            f"Shortlist: {[{'user_name': item['user_name'], 'score': item['score'], 'wallet': item['proxy_wallet']} for item in shortlisted[:5]]}\n"
            "Responda APENAS com JSON contendo summary, why, risks e selection_reason."
        )
        response = None
        try:
            response = await self.provider.call(prompt=prompt, system="Voce resume de forma conservadora e curta.")
            await self.cost_tracker.record(response, prompt_type="weather_copytrade_report")
            payload = parse_json_object(response.content)
        except (BudgetExceededError, InvalidModelResponseError, Exception):
            payload = {}
        summary = sanitize_text(str(payload.get("summary") or self._deterministic_summary(selected)), int(self.settings.report_token_limit))
        why = sanitize_text(str(payload.get("why") or selected["rationale"]), int(self.settings.report_token_limit))
        raw_risks = payload.get("risks", [])
        if isinstance(raw_risks, str):
            raw_risks = [raw_risks]
        elif not isinstance(raw_risks, list):
            raw_risks = []
        risks = [sanitize_text(str(item), 90) for item in raw_risks if str(item).strip()]
        if not risks:
            risks = self._deterministic_risks(selected)
        return {
            "summary": summary,
            "why": why,
            "risks": risks[:5],
            "selection_reason": sanitize_text(str(payload.get("selection_reason") or selected["rationale"]), int(self.settings.report_token_limit)),
            "selected_proxy_wallet": selected["proxy_wallet"],
            "selected_user_name": selected["user_name"],
            "model": getattr(response, "model", self.settings.short_report_model) if response else "deterministic",
            "provider": getattr(response, "provider", self.settings.short_report_provider) if response else "deterministic",
            "fallback_used": bool(getattr(response, "fallback_used", False)) if response else False,
        }

    async def _merge_state_from_run(
        self,
        run_payload: dict[str, Any],
        selected: dict[str, Any] | None,
        report: dict[str, Any],
        *,
        existing_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        has_existing_lifecycle = bool(existing_state) and (
            bool(existing_state.get("approved")) or bool(existing_state.get("active")) or bool(existing_state.get("paused"))
        )
        preserve_selection = has_existing_lifecycle and bool(existing_state.get("approved") or existing_state.get("active"))
        approved = bool(existing_state.get("approved")) if has_existing_lifecycle else False
        active = bool(existing_state.get("active")) if has_existing_lifecycle else False
        paused = bool(existing_state.get("paused")) if has_existing_lifecycle else True
        approved_at = existing_state.get("approved_at") if has_existing_lifecycle else None
        activated_at = existing_state.get("activated_at") if has_existing_lifecycle else None
        last_trade_seen_at = existing_state.get("last_trade_seen_at") if has_existing_lifecycle else None
        last_trade_seen_hash = str(existing_state.get("last_trade_seen_hash") or "") if has_existing_lifecycle else ""
        processed_trade_hashes = self._json_list(existing_state.get("processed_trade_hashes")) if has_existing_lifecycle else []
        created_at = existing_state.get("created_at", datetime.now(UTC)) if has_existing_lifecycle else datetime.now(UTC)
        metadata = self._metadata_map(existing_state.get("metadata")) if has_existing_lifecycle else {}
        metadata.update(
            {
                "last_run_id": run_payload["run_id"],
                "selected_score": selected["score"] if selected else 0,
            }
        )
        return await self.context.repository.upsert_weather_copytrade_state(
            {
                "category": self.category,
                "run_id": run_payload["run_id"],
                "selected_proxy_wallet": existing_state.get("selected_proxy_wallet") if preserve_selection else (selected["proxy_wallet"] if selected else ""),
                "selected_user_name": existing_state.get("selected_user_name") if preserve_selection else (selected["user_name"] if selected else ""),
                "selected_profile": existing_state.get("selected_profile") if preserve_selection else (selected["profile"] if selected else {}),
                "selection": existing_state.get("selection") if preserve_selection else (selected or {}),
                "report": report,
                "approved": approved,
                "active": active,
                "paused": paused,
                "approved_at": approved_at,
                "activated_at": activated_at,
                "last_trade_seen_at": last_trade_seen_at,
                "last_trade_seen_hash": last_trade_seen_hash,
                "processed_trade_hashes": processed_trade_hashes,
                "metadata": metadata,
                "created_at": created_at,
                "updated_at": datetime.now(UTC),
            }
        )

    async def _copy_trade(self, trade: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
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
        token_ids = [str(token_id) for token_id in self._coerce_sequence(market.get("clobTokenIds"))]
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
        if spread_bps > float(self.settings.max_spread_bps):
            return {"copied": False, "reason": "spread_too_wide"}

        trade_price = self._as_float(trade.get("price"))
        trade_size = self._as_float(trade.get("size"))
        if trade_price <= 0 or trade_size <= 0:
            return {"copied": False, "reason": "bad_trade_size"}
        portfolio = await self.context.repository.get_portfolio_summary()
        bankroll_base = max(min(float(portfolio.available_balance), float(portfolio.total_equity)), 0.0)
        per_trade_limit = bankroll_base * 0.02
        if bankroll_base <= 0 or per_trade_limit <= 0:
            return {"copied": False, "reason": "insufficient_bankroll"}
        open_positions = await self.context.repository.get_open_positions()
        weather_open_positions = [
            item for item in open_positions if str(item.get("strategy_id") or "") == "weather_copytrade"
        ]
        if side == "BUY" and len(weather_open_positions) >= int(self.settings.max_open_copied_positions):
            return {"copied": False, "reason": "max_open_positions_reached"}
        notional = trade_price * trade_size
        max_notional = min(float(self.settings.max_notional_usd), per_trade_limit, float(portfolio.available_balance))
        if max_notional < float(self.settings.min_notional_usd):
            return {"copied": False, "reason": "insufficient_available_balance"}
        copy_notional = clamp(
            max(notional * float(self.settings.copy_trade_fraction), float(self.settings.min_notional_usd)),
            float(self.settings.min_notional_usd),
            max_notional,
        )
        if copy_notional <= 0 or copy_notional > float(portfolio.available_balance):
            return {"copied": False, "reason": "insufficient_available_balance"}
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
        action = "entry" if side == "BUY" else "close"
        payload = {
            "order_id": str(uuid4()),
            "signal_id": str(uuid4()),
            "market_id": str(market.get("id") or condition_id),
            "token_id": token_id,
            "market_question": str(market.get("question") or trade.get("title") or ""),
            "asset_symbol": self._guess_asset_symbol(trade, market),
            "crypto_tier": "small_cap",
            "action": action,
            "position_key": f"weather:{profile.get('proxy_wallet') or ''}:{condition_id}:{direction}",
            "strategy_id": "weather_copytrade",
            "regime": "weather_copytrade",
            "trade_group_id": str(profile.get("proxy_wallet") or ""),
            "cycle_slug": str(trade.get("slug") or ""),
            "leg_role": "primary",
            "direction": direction,
            "size": copy_size,
            "price_limit": price_limit,
            "notional_usd": round(copy_size * price_limit, 4),
            "entry_notional_target_usd": copy_notional if side == "BUY" else None,
            "entry_notional_actual_usd": round(copy_size * price_limit, 4),
            "take_profit_price": None,
            "stop_loss_price": None,
            "time_stop_minutes": None,
            "realized_pnl_usd": 0.0,
            "execution_mode": "deterministic",
            "status": str(order_status.get("status") or "simulated"),
            "reason": f"mirror_{side.lower()}_{trade_hash[:10]}",
            "news_validation": None,
            "metadata": {
                "copied_profile_wallet": str(profile.get("proxy_wallet") or ""),
                "bankroll_base_usd": round(bankroll_base, 4),
                "per_trade_limit_usd": round(per_trade_limit, 4),
            },
        }
        await self.context.repository.record_paper_order(
            payload["order_id"],
            payload["signal_id"],
            payload["market_id"],
            payload["status"],
            payload,
        )
        return {"copied": True, "reason": "mirrored", "trade_hash": trade_hash}

    async def _live_order_state(self, exchange_order_id: str, order: dict[str, object]) -> dict[str, object] | None:
        live_order = await self.connector.get_order(exchange_order_id)
        if not live_order:
            return None
        status = str(live_order.get("status") or live_order.get("order_status") or "").upper()
        matched_size = self._as_float(
            live_order.get("size_matched")
            or live_order.get("sizeMatched")
            or live_order.get("filled_size")
            or live_order.get("matched_size")
        )
        requested_size = max(
            self._as_float(live_order.get("size") or live_order.get("original_size")),
            self._as_float(order.get("size") or 0),
        )
        fill_price = self._as_float(
            live_order.get("avgPrice")
            or live_order.get("avg_price")
            or live_order.get("price")
            or order.get("price_limit")
        )
        if status in {"FILLED", "MATCHED"} or (requested_size > 0 and matched_size >= requested_size):
            return {
                "state": "filled",
                "exchange_status": status or "FILLED",
                "fill_price": fill_price or float(order.get("price_limit") or 0.0),
                "payload": {
                    "live_order": live_order,
                    "fill_price": fill_price or float(order.get("price_limit") or 0.0),
                    "fill_source": "exchange_status",
                },
            }
        if status in {"CANCELLED", "CANCELED", "REJECTED", "FAILED"}:
            return {
                "state": "cancelled",
                "exchange_status": status,
                "reason": status.lower(),
                "payload": {
                    "live_order": live_order,
                    "cancelled_at": datetime.now(UTC).isoformat(),
                },
            }
        return {
            "state": "open",
            "exchange_status": status or "OPEN",
            "payload": {
                "live_order": live_order,
            },
        }

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

    def _deterministic_summary(self, selected: dict[str, Any]) -> str:
        metrics = selected["metrics"]
        return (
            f"{selected['user_name']} em WEATHER com pnl_30d={metrics.get('pnl_30d', 0):.2f}, "
            f"profit_factor={metrics.get('profit_factor', 0):.2f} e drawdown={metrics.get('max_drawdown', 0):.2%}."
        )

    def _deterministic_risks(self, selected: dict[str, Any]) -> list[str]:
        metrics = selected["metrics"]
        risks: list[str] = []
        if metrics.get("max_drawdown", 0.0) > 0.10:
            risks.append("drawdown ainda relevante")
        if metrics.get("pnl_concentration", 0.0) > 0.30:
            risks.append("lucro concentrado em poucos mercados")
        if metrics.get("trades_7d", 0) < 10:
            risks.append("atividade recente limitada")
        if not risks:
            risks.append("copytrade conservador em capital baixo")
        return risks

    def _pick_candidate(self, candidates: list[dict[str, Any]], *, proxy_wallet: str | None = None) -> dict[str, Any] | None:
        if proxy_wallet:
            return next((item for item in candidates if str(item.get("proxy_wallet") or "") == proxy_wallet), None)
        return next((item for item in candidates if bool(item.get("passed"))), None)

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

    def _is_weather_trade(self, trade: dict[str, Any]) -> bool:
        slug = str(trade.get("slug") or trade.get("eventSlug") or trade.get("title") or "").lower()
        return "weather" in slug

    def _is_weather_market_from_market(self, market: dict[str, Any]) -> bool:
        text = " ".join(str(market.get(key) or "") for key in ("question", "slug", "description", "eventSlug", "title")).lower()
        return "weather" in text

    def _guess_asset_symbol(self, trade: dict[str, Any], market: dict[str, Any]) -> str:
        slug = str(market.get("slug") or trade.get("slug") or trade.get("eventSlug") or "").upper()
        for token in ("RAIN", "SNOW", "TEMP", "HURRICANE", "WEATHER", "COLD", "HOT"):
            if token in slug:
                return token
        return "WEATHER"

    @staticmethod
    def _trade_timestamp(trade: dict[str, Any]) -> datetime:
        return WeatherCopytradeService._parse_datetime(trade.get("timestamp") or trade.get("createdAt") or trade.get("created_at"))

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        if value in (None, ""):
            return datetime.now(UTC)
        try:
            raw = str(value).strip()
            if raw.isdigit():
                return datetime.fromtimestamp(float(raw), tz=UTC)
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            return datetime.now(UTC)

    @staticmethod
    def _as_float(value: Any) -> float:
        if value in (None, ""):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _as_int(value: Any) -> int:
        if value in (None, ""):
            return 0
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _json_list(value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                try:
                    parsed = parse_json_object(stripped)
                    if isinstance(parsed, list):
                        return [str(item) for item in parsed if str(item).strip()]
                except Exception:
                    pass
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return [str(value)]

    @staticmethod
    def _metadata_map(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return {}
            try:
                parsed = parse_json_object(stripped)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

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
                    parsed = parse_json_object(stripped)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    pass
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return [value]

    @staticmethod
    def _leaderboard_number(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> float:
        if not rows:
            return 0.0
        row = rows[0]
        for key in keys:
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _position_pnl(position: dict[str, Any]) -> float:
        for key in ("realizedPnl", "cashPnl", "pnl", "percentPnl"):
            value = position.get(key)
            if value not in (None, ""):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0

    @staticmethod
    def _current_value(position: dict[str, Any]) -> float:
        for key in ("currentValue", "value", "size"):
            value = position.get(key)
            if value not in (None, ""):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _max_drawdown(self, closed_positions: list[dict[str, Any]]) -> float:
        ordered = sorted(
            closed_positions,
            key=lambda item: self._parse_datetime(item.get("endDate") or item.get("closedAt") or item.get("timestamp") or item.get("createdAt")),
        )
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
            ts = self._parse_datetime(item.get("endDate") or item.get("closedAt") or item.get("timestamp") or item.get("createdAt"))
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
