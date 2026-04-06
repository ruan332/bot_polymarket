from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.weather_copytrade_service import WeatherCopytradeService
from core.utils import parse_json_object


class WeatherCopytradeAgent(BaseAgent):
    def __init__(self, context: AppContext):
        super().__init__("weather_copytrade", context)
        self.service = WeatherCopytradeService(context)

    async def close(self) -> None:
        await self.service.close()
        await super().close()

    async def tick(self) -> None:
        state = await self.context.repository.get_weather_copytrade_state(self.service.category)
        should_scan = state is None
        if state is not None:
            last_sync = self._parse_datetime(self._metadata_map(state.get("metadata")).get("last_run_at"))
            if last_sync is None:
                should_scan = True
            else:
                should_scan = datetime.now(UTC) - last_sync >= timedelta(minutes=int(self.service.settings.scan_interval_minutes))

        scan_result = None
        if should_scan and self.context.weather_copytrade_config.enabled:
            scan_result = await self.service.run_analysis()
            state = scan_result["state"]
            await self.context.repository.record_pipeline_telemetry(
                str(uuid4()),
                self.name,
                "weather_copytrade.scan",
                {
                    "run_id": scan_result["run"]["run_id"],
                    "candidate_count": len(scan_result["candidates"]),
                    "selected_proxy_wallet": scan_result["run"].get("selected_proxy_wallet", ""),
                    "selected_user_name": scan_result["run"].get("selected_user_name", ""),
                    "report": scan_result["report"],
                },
            )
            await self.context.repository.upsert_weather_copytrade_state(
                {
                    **state,
                    "metadata": {
                        **self._metadata_map(state.get("metadata")),
                        "last_run_at": datetime.now(UTC).isoformat(),
                    },
                    "updated_at": datetime.now(UTC),
                }
            )

        if state and bool(state.get("active")) and not bool(state.get("paused")):
            sync_result = await self.service.sync_mirror_trades()
            live_sync_result = await self.service.sync_live_order_statuses()
            await self.context.repository.record_pipeline_telemetry(
                str(uuid4()),
                self.name,
                "weather_copytrade.sync",
                {
                    "copied": sync_result["copied"],
                    "processed": sync_result["processed"],
                    "skipped": sync_result["skipped"],
                    "reasons": sync_result["reasons"],
                    "selected_proxy_wallet": str((state or {}).get("selected_proxy_wallet") or ""),
                },
            )
            await self.context.repository.record_pipeline_telemetry(
                str(uuid4()),
                self.name,
                "weather_copytrade.order_sync",
                {
                    "scanned": live_sync_result["scanned"],
                    "synced": live_sync_result["synced"],
                    "filled": live_sync_result["filled"],
                    "cancelled": live_sync_result["cancelled"],
                    "open": live_sync_result["open"],
                    "selected_proxy_wallet": str((state or {}).get("selected_proxy_wallet") or ""),
                },
            )
        elif scan_result is None and state is not None:
            await self.context.repository.record_pipeline_telemetry(
                str(uuid4()),
                self.name,
                "weather_copytrade.idle",
                {
                    "active": bool(state.get("active")),
                    "paused": bool(state.get("paused")),
                    "selected_proxy_wallet": str(state.get("selected_proxy_wallet") or ""),
                },
            )

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            return None

    @staticmethod
    def _metadata_map(value: object) -> dict[str, object]:
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
