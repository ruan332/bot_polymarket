from __future__ import annotations

from pathlib import Path
from typing import Any

from clima_bot.config.remote_env import bootstrap_env_file, update_env_file
from clima_bot.config.settings import DEFAULT_ENV_PATH, ClimaBotSettings
from clima_bot.services.analysis import AnalysisService
from clima_bot.services.mirror import MirrorService
from clima_bot.services.performance import PerformanceService
from clima_bot.services.polymarket import ClimaBotConnector
from clima_bot.storage.repository import ClimaBotRepository


class ClimaBotEngine:
    def __init__(self, settings: ClimaBotSettings | None = None, *, env_path: Path | None = None) -> None:
        self.env_path = env_path or DEFAULT_ENV_PATH
        self.settings = settings or ClimaBotSettings.load(self.env_path)
        self.repository = ClimaBotRepository(self.settings.storage_path)
        self.repository.init_schema()
        self.connector = ClimaBotConnector(self.settings)
        self.analysis = AnalysisService(self.connector, self.repository, category=self.settings.clima_bot_category)
        self.mirror = MirrorService(self.connector, self.repository, self.settings)
        self.performance = PerformanceService(self.repository)
        self.repository.update_engine_state(
            {
                "running": False,
                "mode": "live" if self.settings.live_trading else "paper",
                "status_text": "idle",
                "last_error": "",
                "metadata": {"env_path": str(self.env_path)},
            }
        )

    async def close(self) -> None:
        await self.connector.close()
        self.repository.close()

    async def run_analysis(self, limit: int | None = None) -> dict[str, Any]:
        return await self.analysis.run_analysis(limit)

    def approve_wallets(self, proxy_wallets: list[str]) -> list[dict[str, Any]]:
        allowed = proxy_wallets[: int(self.settings.clima_bot_max_wallets)]
        approved = self.analysis.approve_wallets(allowed, int(self.settings.clima_bot_max_wallets))
        if approved:
            self.repository.append_log("info", "wallets", f"Approved {len(approved)} wallet(s)")
        return approved

    def add_wallet(self, proxy_wallet: str, *, user_name: str | None = None) -> dict[str, Any]:
        if self.repository.count_wallets() >= int(self.settings.clima_bot_max_wallets):
            raise ValueError("wallet limit reached")
        wallet = self.repository.upsert_wallet(
            {
                "proxy_wallet": proxy_wallet.strip(),
                "user_name": user_name or proxy_wallet.strip()[:10],
                "score": 0,
                "approved": True,
                "active": True,
                "paused": False,
                "profile": {"proxy_wallet": proxy_wallet.strip()},
                "metrics": {},
                "selection": {},
            }
        )
        self.repository.append_log("info", "wallets", f"Wallet {proxy_wallet.strip()} added manually")
        return wallet

    def pause_wallet(self, proxy_wallet: str, paused: bool = True) -> dict[str, Any] | None:
        wallet = self.repository.set_wallet_paused(proxy_wallet, paused)
        if wallet:
            action = "paused" if paused else "resumed"
            self.repository.append_log("warning" if paused else "info", "wallets", f"Wallet {proxy_wallet} {action}")
        return wallet

    def remove_wallet(self, proxy_wallet: str) -> None:
        self.repository.remove_wallet(proxy_wallet)
        self.repository.append_log("warning", "wallets", f"Wallet {proxy_wallet} removed")

    def update_runtime_settings(self, updates: dict[str, str]) -> None:
        update_env_file(self.env_path, updates)
        self.settings = ClimaBotSettings.load(self.env_path)
        old_connector = self.connector
        self.connector = ClimaBotConnector(self.settings)
        self.analysis = AnalysisService(self.connector, self.repository, category=self.settings.clima_bot_category)
        self.mirror = MirrorService(self.connector, self.repository, self.settings)
        self.repository.append_log("info", "config", "Runtime settings updated", meta="env_file=.env.clima_bot")
        self.repository.update_engine_state(
            {
                "running": False,
                "mode": "live" if self.settings.live_trading else "paper",
                "status_text": "config_reloaded",
                "last_error": "",
            }
        )
        # Close the previous HTTP session after the new connector is ready.
        try:
            import asyncio

            asyncio.create_task(old_connector.close())
        except Exception:
            pass

    def bootstrap_env(self, *, overwrite: bool = False) -> dict[str, Any]:
        try:
            result = bootstrap_env_file(
                destination=self.env_path,
                overwrite=overwrite,
                remote_host=self.settings.clima_bot_remote_env_host,
                remote_path=self.settings.clima_bot_remote_env_path,
                fallback_env_path=Path(".env"),
            )
            self.repository.append_log("info", "env", f"Env bootstrap source={result.source}", meta=str(result.path))
            return {"ok": True, "source": result.source, "path": str(result.path)}
        except Exception as exc:
            self.repository.append_log("error", "env", "Env bootstrap failed", detail=str(exc))
            return {"ok": False, "error": str(exc)}

    async def sync_once(self) -> dict[str, Any]:
        self.repository.update_engine_state(
            {
                "running": True,
                "mode": "live" if self.settings.live_trading else "paper",
                "status_text": "syncing",
                "last_error": "",
            }
        )
        try:
            result = await self.mirror.sync_once()
            snapshots = self.performance.refresh_all()
            self.repository.update_engine_state(
                {
                    "running": True,
                    "mode": "live" if self.settings.live_trading else "paper",
                    "status_text": "ready",
                    "last_sync_at": self.repository.get_engine_state().get("updated_at"),
                    "last_error": "",
                    "metadata": {
                        "processed": result["processed"],
                        "copied": result["copied"],
                        "skipped": result["skipped"],
                        "wallets": list(snapshots.keys()),
                    },
                }
            )
            return {"sync": result, "snapshots": snapshots}
        except Exception as exc:
            self.repository.update_engine_state(
                {
                    "running": False,
                    "mode": "live" if self.settings.live_trading else "paper",
                    "status_text": "error",
                    "last_error": str(exc),
                }
            )
            self.repository.append_log("error", "engine", "Sync failed", detail=str(exc))
            raise

    async def dashboard_snapshot(self) -> dict[str, Any]:
        latest = self.repository.get_latest_analysis()
        wallets = self.repository.list_wallets()
        performance = self.performance.dashboard_summary()
        logs = self.repository.list_logs()
        collateral = await self.connector.get_collateral_snapshot(sync_allowance=False) if self.settings.polymarket_private_key else None
        bankroll = float((collateral or {}).get("balance") or self.settings.paper_bankroll_usd)
        return {
            "brand": "CLIMA BOT",
            "engine": self.repository.get_engine_state(),
            "analysis": latest,
            "wallets": wallets,
            "performance": performance,
            "logs": logs,
            "bankroll_base_usd": bankroll,
            "per_trade_limit_usd": round(bankroll * 0.02, 4),
            "mode": "live" if self.settings.live_trading else "paper",
            "copy_trade_fraction": self.settings.clima_bot_copy_trade_fraction,
            "min_notional_usd": self.settings.clima_bot_min_notional_usd,
            "max_notional_usd": self.settings.clima_bot_max_notional_usd,
        }
