from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from core.schemas import AgentHeartbeat, EquitySnapshotPoint, MarketSnapshotPayload, NewsValidationPayload, PortfolioSummary


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id UUID PRIMARY KEY,
    signal_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paper_orders (
    id UUID PRIMARY KEY,
    signal_id UUID NOT NULL,
    market_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS positions (
    market_id TEXT NOT NULL,
    position_key TEXT,
    token_id TEXT,
    market_question TEXT NOT NULL DEFAULT '',
    asset_symbol TEXT NOT NULL DEFAULT '',
    crypto_tier TEXT NOT NULL DEFAULT '',
    strategy_id TEXT NOT NULL DEFAULT '',
    regime TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL,
    size INTEGER NOT NULL,
    average_price DOUBLE PRECISION NOT NULL,
    exposure_usd DOUBLE PRECISION NOT NULL,
    take_profit_price DOUBLE PRECISION,
    stop_loss_price DOUBLE PRECISION,
    time_stop_minutes INTEGER,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    scaled_out_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE positions ADD COLUMN IF NOT EXISTS token_id TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS market_question TEXT NOT NULL DEFAULT '';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS position_key TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS asset_symbol TEXT NOT NULL DEFAULT '';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS crypto_tier TEXT NOT NULL DEFAULT '';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS strategy_id TEXT NOT NULL DEFAULT '';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS regime TEXT NOT NULL DEFAULT '';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS take_profit_price DOUBLE PRECISION;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS stop_loss_price DOUBLE PRECISION;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS time_stop_minutes INTEGER;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE positions ADD COLUMN IF NOT EXISTS scaled_out_count INTEGER NOT NULL DEFAULT 0;
UPDATE positions SET position_key = COALESCE(NULLIF(position_key, ''), market_id || ':' || direction);
ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_position_key ON positions (position_key);

CREATE TABLE IF NOT EXISTS llm_calls (
    id UUID PRIMARY KEY,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    cost_usd DOUBLE PRECISION NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
    prompt_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS risk_events (
    id UUID PRIMARY KEY,
    agent TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pipeline_telemetry (
    id UUID PRIMARY KEY,
    agent TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id UUID PRIMARY KEY,
    available_balance DOUBLE PRECISION NOT NULL,
    total_exposure DOUBLE PRECISION NOT NULL,
    current_market_value DOUBLE PRECISION NOT NULL DEFAULT 0,
    total_equity DOUBLE PRECISION NOT NULL DEFAULT 0,
    total_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
    realized_pnl DOUBLE PRECISION NOT NULL,
    unrealized_pnl DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE equity_snapshots ADD COLUMN IF NOT EXISTS current_market_value DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE equity_snapshots ADD COLUMN IF NOT EXISTS total_equity DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE equity_snapshots ADD COLUMN IF NOT EXISTS total_pnl DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE equity_snapshots ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'system';

CREATE TABLE IF NOT EXISTS agent_heartbeats (
    agent TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    running BOOLEAN NOT NULL,
    config_version INTEGER NOT NULL,
    meta JSONB NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    market_id TEXT NOT NULL,
    question TEXT NOT NULL,
    token_id_yes TEXT NOT NULL,
    token_id_no TEXT NOT NULL,
    price_yes DOUBLE PRECISION NOT NULL,
    price_no DOUBLE PRECISION NOT NULL,
    volume_24h DOUBLE PRECISION NOT NULL DEFAULT 0,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS news_validations (
    id UUID PRIMARY KEY,
    signal_id UUID NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_market_created_at
ON market_snapshots (market_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_equity_snapshots_created_at
ON equity_snapshots (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_news_validations_signal_id
ON news_validations (signal_id);

CREATE INDEX IF NOT EXISTS idx_pipeline_telemetry_created_at
ON pipeline_telemetry (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_telemetry_agent_event
ON pipeline_telemetry (agent, event_type, created_at DESC);
"""


class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def init_schema(self) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)

    async def execute(self, query: str, *args: Any) -> str:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)


def _as_json(value: Any) -> str:
    return json.dumps(value, default=str)


def _as_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _as_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


class TradingRepository:
    def __init__(self, db: Database, initial_bankroll: float):
        self.db = db
        self.initial_bankroll = initial_bankroll

    async def record_signal(self, signal_id: str, event_type: str, payload: dict[str, Any]) -> None:
        await self.db.execute(
            "INSERT INTO signals (id, event_type, payload) VALUES ($1::uuid, $2, $3::jsonb)",
            signal_id,
            event_type,
            _as_json(payload),
        )

    async def has_recent_signal_duplicate(
        self,
        *,
        market_id: str,
        direction: str,
        thesis_hash: str,
        cooldown_minutes: int,
    ) -> bool:
        if cooldown_minutes <= 0:
            return False
        rows = await self.db.fetchrow(
            """
            SELECT 1
            FROM signals
            WHERE created_at >= $1
              AND payload->>'market_id' = $2
              AND payload->>'direction' = $3
              AND payload->>'thesis_hash' = $4
            LIMIT 1
            """,
            datetime.now(UTC) - timedelta(minutes=cooldown_minutes),
            market_id,
            direction,
            thesis_hash,
        )
        return rows is not None

    async def record_decision(self, decision_id: str, signal_id: str, event_type: str, payload: dict[str, Any]) -> None:
        await self.db.execute(
            "INSERT INTO agent_decisions (id, signal_id, event_type, payload) VALUES ($1::uuid, $2::uuid, $3, $4::jsonb)",
            decision_id,
            signal_id,
            event_type,
            _as_json(payload),
        )

    async def record_news_validation(self, validation: NewsValidationPayload) -> None:
        await self.db.execute(
            "INSERT INTO news_validations (id, signal_id, event_type, payload) VALUES ($1::uuid, $2::uuid, $3, $4::jsonb)",
            validation.validation_id,
            validation.signal_id,
            validation.event_type,
            _as_json(validation.model_dump(mode="json")),
        )

    async def attach_news_validation(self, signal_id: str, payload: dict[str, Any]) -> None:
        await self.db.execute(
            """
            UPDATE signals
            SET payload = jsonb_set(payload, '{news_validation}', $2::jsonb, true)
            WHERE id = $1::uuid
            """,
            signal_id,
            _as_json(payload),
        )

    async def record_paper_order(self, order_id: str, signal_id: str, market_id: str, status: str, payload: dict[str, Any]) -> None:
        await self.db.execute(
            "INSERT INTO paper_orders (id, signal_id, market_id, status, payload) VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb)",
            order_id,
            signal_id,
            market_id,
            status,
            _as_json(payload),
        )
        if status == "simulated":
            action = str(payload.get("action") or "entry")
            if action in {"entry", "scale_in"}:
                await self._upsert_position(
                    market_id=market_id,
                    token_id=str(payload.get("token_id", "")),
                    market_question=str(payload.get("market_question", "")),
                    asset_symbol=str(payload.get("asset_symbol", "")),
                    crypto_tier=str(payload.get("crypto_tier", "")),
                    strategy_id=str(payload.get("strategy_id", "")),
                    regime=str(payload.get("regime", "")),
                    direction=str(payload["direction"]),
                    size=int(payload["size"]),
                    average_price=float(payload["price_limit"]),
                    exposure_usd=float(payload["notional_usd"]),
                    take_profit_price=_as_optional_float(payload.get("take_profit_price")),
                    stop_loss_price=_as_optional_float(payload.get("stop_loss_price")),
                    time_stop_minutes=_as_optional_int(payload.get("time_stop_minutes")),
                )
            else:
                await self._reduce_position(
                    position_key=str(payload.get("position_key") or f"{market_id}:{payload['direction']}"),
                    size=int(payload["size"]),
                    exit_action=action,
                )
            await self.record_equity_snapshot(source="paper_order")

    async def record_llm_call(
        self,
        call_id: str,
        agent: str,
        model: str,
        provider: str,
        cost_usd: float,
        input_tokens: int,
        output_tokens: int,
        fallback_used: bool,
        prompt_type: str,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO llm_calls (
                id, agent, model, provider, cost_usd, input_tokens, output_tokens, fallback_used, prompt_type
            ) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            call_id,
            agent,
            model,
            provider,
            cost_usd,
            input_tokens,
            output_tokens,
            fallback_used,
            prompt_type,
        )

    async def record_risk_event(self, event_id: str, agent: str, reason: str, payload: dict[str, Any]) -> None:
        await self.db.execute(
            "INSERT INTO risk_events (id, agent, reason, payload) VALUES ($1::uuid, $2, $3, $4::jsonb)",
            event_id,
            agent,
            reason,
            _as_json(payload),
        )

    async def record_pipeline_telemetry(
        self,
        event_id: str,
        agent: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        await self.db.execute(
            "INSERT INTO pipeline_telemetry (id, agent, event_type, payload) VALUES ($1::uuid, $2, $3, $4::jsonb)",
            event_id,
            agent,
            event_type,
            _as_json(payload),
        )

    async def upsert_heartbeat(self, heartbeat: AgentHeartbeat) -> None:
        await self.db.execute(
            """
            INSERT INTO agent_heartbeats (agent, model, running, config_version, meta, last_seen)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            ON CONFLICT (agent) DO UPDATE
            SET model = EXCLUDED.model,
                running = EXCLUDED.running,
                config_version = EXCLUDED.config_version,
                meta = EXCLUDED.meta,
                last_seen = EXCLUDED.last_seen
            """,
            heartbeat.agent,
            heartbeat.model,
            heartbeat.running,
            heartbeat.config_version,
            _as_json(heartbeat.meta),
            heartbeat.last_seen,
        )

    async def record_equity_snapshot(self, source: str = "system") -> None:
        summary = await self.get_portfolio_summary()
        await self.db.execute(
            """
            INSERT INTO equity_snapshots (
                id,
                available_balance,
                total_exposure,
                current_market_value,
                total_equity,
                total_pnl,
                realized_pnl,
                unrealized_pnl,
                source
            )
            VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8)
            """,
            summary.available_balance,
            summary.total_exposure,
            summary.current_market_value,
            summary.total_equity,
            summary.total_pnl,
            summary.realized_pnl,
            summary.unrealized_pnl,
            source,
        )

    async def get_portfolio_summary(self) -> PortfolioSummary:
        rows = await self.db.fetch(
            """
            SELECT
                market_id,
                token_id,
                market_question,
                asset_symbol,
                crypto_tier,
                strategy_id,
                regime,
                direction,
                size,
                average_price,
                exposure_usd
            FROM positions
            WHERE size > 0
            """
        )
        realized_row = await self.db.fetchrow(
            """
            SELECT COALESCE(SUM((payload->>'realized_pnl_usd')::double precision), 0) AS realized_pnl
            FROM paper_orders
            WHERE status = 'simulated'
            """
        )
        realized_pnl = float(realized_row["realized_pnl"] or 0.0) if realized_row else 0.0
        if not rows:
            return PortfolioSummary(
                available_balance=self.initial_bankroll + realized_pnl,
                total_equity=self.initial_bankroll + realized_pnl,
                realized_pnl=realized_pnl,
                total_pnl=realized_pnl,
            )

        latest_prices = await self._latest_market_prices([str(row["market_id"]) for row in rows])
        total_exposure = 0.0
        current_market_value = 0.0
        for row in rows:
            total_exposure += float(row["exposure_usd"] or 0.0)
            current_price = float(row["average_price"] or 0.0)
            latest = latest_prices.get(str(row["market_id"]))
            if latest:
                current_price = float(latest["price_yes"] if row["direction"] == "YES" else latest["price_no"])
            current_market_value += int(row["size"] or 0) * current_price

        unrealized_pnl = current_market_value - total_exposure
        available_balance = max(self.initial_bankroll - total_exposure + realized_pnl, 0.0)
        total_equity = available_balance + current_market_value
        total_pnl = realized_pnl + unrealized_pnl
        return PortfolioSummary(
            available_balance=available_balance,
            total_exposure=total_exposure,
            current_market_value=current_market_value,
            total_equity=total_equity,
            total_pnl=total_pnl,
            open_positions=len(rows),
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
        )

    async def get_recent_signals(
        self,
        limit: int = 20,
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._recent_payloads("signals", limit=limit, asset=asset, tier=tier, strategy=strategy)

    async def get_recent_decisions(
        self,
        limit: int = 20,
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._recent_payloads("agent_decisions", limit=limit, asset=asset, tier=tier, strategy=strategy)

    async def get_recent_orders(
        self,
        limit: int = 20,
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._recent_payloads("paper_orders", limit=limit, asset=asset, tier=tier, strategy=strategy)

    async def get_execution_risk_state(self, hours: int = 24) -> dict[str, Any]:
        window_start = datetime.now(UTC) - timedelta(hours=max(hours, 1))
        rows = await self.db.fetch(
            """
            SELECT payload, created_at
            FROM paper_orders
            WHERE created_at >= $1 AND status = 'simulated'
            ORDER BY created_at ASC
            """,
            window_start,
        )
        payloads = [self._decode_record(row) for row in rows]
        daily_spend_usd = sum(
            float(item.get("notional_usd") or 0.0)
            for item in payloads
            if str(item.get("action") or "entry") in {"entry", "scale_in"}
        )
        realized_pnl_usd = sum(float(item.get("realized_pnl_usd") or 0.0) for item in payloads)
        consecutive_losses = 0
        last_loss_at = None
        for item in reversed(payloads):
            pnl = float(item.get("realized_pnl_usd") or 0.0)
            if pnl < 0:
                consecutive_losses += 1
                if last_loss_at is None:
                    last_loss_at = item.get("created_at")
            elif pnl > 0:
                break
        return {
            "daily_spend_usd": round(daily_spend_usd, 4),
            "realized_pnl_usd": round(realized_pnl_usd, 4),
            "consecutive_losses": consecutive_losses,
            "last_loss_at": last_loss_at,
        }

    async def get_latest_market_snapshot(self, market_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchrow(
            """
            SELECT market_id, question, token_id_yes, token_id_no, price_yes, price_no, volume_24h, payload, created_at
            FROM market_snapshots
            WHERE market_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            market_id,
        )
        if row is None:
            return None
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return {
            "market_id": row["market_id"],
            "question": row["question"],
            "token_id_yes": row["token_id_yes"],
            "token_id_no": row["token_id_no"],
            "price_yes": float(row["price_yes"] or 0.0),
            "price_no": float(row["price_no"] or 0.0),
            "volume_24h": float(row["volume_24h"] or 0.0),
            "metadata": payload,
            "created_at": row["created_at"],
        }

    async def get_recent_risk_events(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.db.fetch("SELECT payload, created_at FROM risk_events ORDER BY created_at DESC LIMIT $1", limit)
        return [self._decode_record(row) for row in rows]

    async def get_recent_pipeline_telemetry(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            """
            SELECT agent, event_type, payload, created_at
            FROM pipeline_telemetry
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [self._decode_pipeline_record(row) for row in rows]

    async def get_agent_status(self) -> list[dict[str, Any]]:
        rows = await self.db.fetch("SELECT agent, model, running, config_version, last_seen, meta FROM agent_heartbeats ORDER BY agent")
        return [
            {
                "agent": row["agent"],
                "model": row["model"],
                "running": row["running"],
                "config_version": row["config_version"],
                "last_seen": row["last_seen"],
                "meta": row["meta"],
            }
            for row in rows
        ]

    async def metrics_overview(self) -> dict[str, Any]:
        signals = await self.db.fetchrow("SELECT COUNT(*) AS count FROM signals")
        decisions = await self.db.fetchrow("SELECT COUNT(*) AS count FROM agent_decisions")
        orders = await self.db.fetchrow("SELECT COUNT(*) AS count FROM paper_orders")
        risk_events = await self.db.fetchrow("SELECT COUNT(*) AS count FROM risk_events")
        pipeline_rows = await self.db.fetch(
            """
            SELECT agent, event_type, payload, created_at
            FROM pipeline_telemetry
            WHERE created_at >= $1
            ORDER BY created_at ASC
            """,
            datetime.now(UTC) - timedelta(minutes=15),
        )
        pipeline_events = [self._decode_pipeline_record(row) for row in pipeline_rows]
        portfolio = await self.get_portfolio_summary()
        flow_summary = self._summarize_pipeline_events(pipeline_events, window_minutes=15)
        return {
            "signals": int(signals["count"] or 0),
            "decisions": int(decisions["count"] or 0),
            "orders": int(orders["count"] or 0),
            "risk_events": int(risk_events["count"] or 0),
            "portfolio": portfolio.model_dump(),
            "flow_summary": flow_summary,
            "latest_scan_telemetry": self._latest_pipeline_event(pipeline_events, "scanner.scan_cycle"),
            "latest_review_telemetry": self._latest_pipeline_event(pipeline_events, "reviewer.review_cycle"),
            "latest_execution_telemetry": self._latest_pipeline_event(pipeline_events, "executor.execute_cycle"),
        }

    async def get_performance_report(
        self,
        hours: int = 24,
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
    ) -> dict[str, Any]:
        window_start = datetime.now(UTC) - timedelta(hours=max(hours, 1))
        portfolio = await self.get_portfolio_summary()
        signal_rows = await self.db.fetch("SELECT payload, created_at FROM signals WHERE created_at >= $1 ORDER BY created_at ASC", window_start)
        decision_rows = await self.db.fetch(
            "SELECT payload, created_at FROM agent_decisions WHERE created_at >= $1 ORDER BY created_at ASC",
            window_start,
        )
        order_rows = await self.db.fetch(
            "SELECT payload, created_at FROM paper_orders WHERE created_at >= $1 ORDER BY created_at ASC",
            window_start,
        )
        risk_event_rows = await self.db.fetch(
            "SELECT payload, created_at FROM risk_events WHERE created_at >= $1 ORDER BY created_at ASC",
            window_start,
        )
        llm_cost_rows = await self.db.fetch(
            """
            SELECT agent, SUM(cost_usd) AS cost_usd, COUNT(*) AS calls
            FROM llm_calls
            WHERE created_at >= $1
            GROUP BY agent
            ORDER BY agent
            """,
            window_start,
        )
        equity_rows = await self.db.fetch(
            """
            SELECT created_at, total_equity, total_pnl, unrealized_pnl, available_balance
            FROM equity_snapshots
            WHERE created_at >= $1
            ORDER BY created_at ASC
            LIMIT 240
            """,
            window_start,
        )

        signals_payloads = [self._decode_record(row) for row in signal_rows]
        decisions_payloads = [self._decode_record(row) for row in decision_rows]
        orders_payloads = [self._decode_record(row) for row in order_rows]
        risk_payloads = [self._decode_record(row) for row in risk_event_rows]

        signals_filtered = [
            item for item in signals_payloads if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy)
        ]
        decisions_filtered = [
            item for item in decisions_payloads if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy)
        ]
        orders_filtered = [
            item for item in orders_payloads if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy)
        ]
        risk_filtered = [
            item
            for item in risk_payloads
            if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy, allow_missing=True)
        ]

        signals = len(signals_filtered)
        decisions = len(decisions_filtered)
        orders = len(orders_filtered)
        risk_events = len(risk_filtered)
        total_cost = sum(float(row["cost_usd"] or 0.0) for row in llm_cost_rows)
        open_positions = await self.get_open_positions()
        open_positions = [item for item in open_positions if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy)]
        positive_positions = sum(1 for position in open_positions if float(position["unrealized_pnl"]) > 0)

        signal_edges = [float(item.get("edge") or 0.0) for item in signals_filtered]
        signal_confidences = [float(item.get("confidence") or 0.0) for item in signals_filtered]
        simulated_orders = [item for item in orders_filtered if str(item.get("status")) == "simulated"]
        entry_orders = [item for item in simulated_orders if str(item.get("action") or "entry") in {"entry", "scale_in"}]
        exit_orders = [item for item in simulated_orders if str(item.get("action") or "") in {"scale_out", "close"}]
        total_notional = sum(float(item.get("notional_usd") or 0.0) for item in simulated_orders)
        avg_notional = total_notional / len(simulated_orders) if simulated_orders else 0.0
        realized_pnl = sum(float(item.get("realized_pnl_usd") or 0.0) for item in exit_orders)
        win_rate = (
            round(sum(1 for item in exit_orders if float(item.get("realized_pnl_usd") or 0.0) > 0) / len(exit_orders), 4)
            if exit_orders
            else 0.0
        )

        order_count_by_market: dict[str, int] = {}
        for item in simulated_orders:
            market_id = str(item.get("market_id") or "")
            if market_id:
                order_count_by_market[market_id] = order_count_by_market.get(market_id, 0) + 1

        top_markets = self._top_markets(signals_filtered, order_count_by_market)
        risk_breakdown = self._count_by_key(risk_filtered, "reason", limit=8)
        asset_breakdown = self._count_by_key(signals_filtered, "asset_symbol", limit=8)
        tier_breakdown = self._count_by_key(signals_filtered, "crypto_tier", limit=3)
        strategy_breakdown = self._strategy_breakdown(signals_filtered, simulated_orders)
        regime_breakdown = self._count_by_key(signals_filtered, "regime", limit=6)
        exit_reason_breakdown = self._count_by_key(exit_orders, "exit_reason", limit=6)
        news_breakdown = self._news_breakdown(signals_filtered)
        news_provider_breakdown = self._news_provider_breakdown(signals_filtered)
        news_fallback_breakdown = self._news_fallback_breakdown(signals_filtered)
        last_news_provider = self._last_news_provider(signals_filtered)
        time_series = self._build_pipeline_time_series(
            signals_filtered,
            decisions_filtered,
            orders_filtered,
            risk_filtered,
        )
        sharpe_ratio = self._sharpe_from_equity_rows(equity_rows)
        max_drawdown = self._max_drawdown_from_equity_rows(equity_rows)
        mae_mfe = self._estimate_mae_mfe(exit_orders, open_positions)

        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "window_hours": hours,
            "asset_filter": asset or "",
            "tier_filter": tier or "",
            "strategy_filter": strategy or "",
            "summary": {
                "signals": signals,
                "decisions": decisions,
                "orders": orders,
                "risk_events": risk_events,
                "approval_rate": round(decisions / signals, 4) if signals else 0.0,
                "execution_rate": round(orders / decisions, 4) if decisions else 0.0,
                "positive_position_rate": round(positive_positions / len(open_positions), 4) if open_positions else 0.0,
                "win_rate": win_rate,
                "avg_edge": round(sum(signal_edges) / len(signal_edges), 4) if signal_edges else 0.0,
                "avg_confidence": round(sum(signal_confidences) / len(signal_confidences), 4) if signal_confidences else 0.0,
                "total_order_notional": round(total_notional, 4),
                "avg_order_notional": round(avg_notional, 4),
                "daily_spend_usd": round(sum(float(item.get("notional_usd") or 0.0) for item in entry_orders), 4),
                "realized_pnl_window": round(realized_pnl, 4),
                "sharpe_ratio": sharpe_ratio,
                "max_drawdown": max_drawdown,
                "llm_cost_usd": round(total_cost, 4),
                **portfolio.model_dump(),
            },
            "cost_by_agent": [
                {
                    "agent": row["agent"],
                    "cost_usd": round(float(row["cost_usd"] or 0.0), 4),
                    "calls": int(row["calls"] or 0),
                }
                for row in llm_cost_rows
            ],
            "risk_breakdown": risk_breakdown,
            "asset_breakdown": asset_breakdown,
            "tier_breakdown": tier_breakdown,
            "strategy_breakdown": strategy_breakdown,
            "regime_breakdown": regime_breakdown,
            "exit_reason_breakdown": exit_reason_breakdown,
            "news_breakdown": news_breakdown,
            "news_provider_breakdown": news_provider_breakdown,
            "news_fallback_breakdown": news_fallback_breakdown,
            "last_news_provider": last_news_provider,
            "top_markets": top_markets,
            "open_positions": open_positions[:8],
            "mae_mfe": mae_mfe,
            "time_series": {
                "pipeline": time_series,
                "equity": [
                    {
                        "created_at": row["created_at"].isoformat(),
                        "total_equity": round(float(row["total_equity"] or 0.0), 4),
                        "total_pnl": round(float(row["total_pnl"] or 0.0), 4),
                        "unrealized_pnl": round(float(row["unrealized_pnl"] or 0.0), 4),
                        "available_balance": round(float(row["available_balance"] or 0.0), 4),
                    }
                    for row in equity_rows
                ],
            },
        }

    async def record_market_snapshots(self, snapshots: list[MarketSnapshotPayload]) -> None:
        if not snapshots:
            return
        await self.db.execute(
            """
            INSERT INTO market_snapshots (
                market_id, question, token_id_yes, token_id_no, price_yes, price_no, volume_24h, payload, created_at
            )
            SELECT
                item->>'market_id',
                item->>'question',
                item->>'token_id_yes',
                item->>'token_id_no',
                (item->>'price_yes')::double precision,
                (item->>'price_no')::double precision,
                (item->>'volume_24h')::double precision,
                item,
                (item->>'created_at')::timestamptz
            FROM jsonb_array_elements($1::jsonb) AS item
            """,
            _as_json([snapshot.model_dump(mode="json") for snapshot in snapshots]),
        )

    async def get_equity_history(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            """
            SELECT
                available_balance,
                total_exposure,
                current_market_value,
                total_equity,
                total_pnl,
                realized_pnl,
                unrealized_pnl,
                source,
                created_at
            FROM equity_snapshots
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [
            EquitySnapshotPoint(
                created_at=row["created_at"],
                available_balance=float(row["available_balance"] or 0.0),
                total_exposure=float(row["total_exposure"] or 0.0),
                current_market_value=float(row["current_market_value"] or 0.0),
                total_equity=float(row["total_equity"] or 0.0),
                total_pnl=float(row["total_pnl"] or 0.0),
                realized_pnl=float(row["realized_pnl"] or 0.0),
                unrealized_pnl=float(row["unrealized_pnl"] or 0.0),
                source=str(row["source"] or "system"),
            ).model_dump(mode="json")
            for row in reversed(rows)
        ]

    async def get_open_positions(self) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            """
            SELECT market_id, position_key, token_id, market_question, direction, size, average_price, exposure_usd, updated_at
                 , asset_symbol, crypto_tier, strategy_id, regime, take_profit_price, stop_loss_price, time_stop_minutes
                 , opened_at, scaled_out_count
            FROM positions
            WHERE size > 0
            ORDER BY updated_at DESC
            """
        )
        latest_prices = await self._latest_market_prices([str(row["market_id"]) for row in rows])
        positions: list[dict[str, Any]] = []
        for row in rows:
            latest = latest_prices.get(str(row["market_id"]))
            current_price = float(row["average_price"] or 0.0)
            latest_spread_bps = 0.0
            if latest:
                current_price = float(latest["price_yes"] if row["direction"] == "YES" else latest["price_no"])
                payload = latest.get("payload") or {}
                summary_key = "orderbook_summary_yes" if row["direction"] == "YES" else "orderbook_summary_no"
                latest_spread_bps = float(((payload.get(summary_key) or {}).get("spread_bps")) or 0.0)
            size = int(row["size"] or 0)
            cost_basis = float(row["exposure_usd"] or 0.0)
            current_value = size * current_price
            positions.append(
                {
                    "market_id": row["market_id"],
                    "position_key": row["position_key"],
                    "token_id": row["token_id"],
                    "market_question": row["market_question"],
                    "asset_symbol": row["asset_symbol"],
                    "crypto_tier": row["crypto_tier"],
                    "strategy_id": row["strategy_id"],
                    "regime": row["regime"],
                    "direction": row["direction"],
                    "size": size,
                    "average_price": float(row["average_price"] or 0.0),
                    "current_price": current_price,
                    "cost_basis_usd": cost_basis,
                    "current_value_usd": current_value,
                    "unrealized_pnl": current_value - cost_basis,
                    "take_profit_price": _as_optional_float(row["take_profit_price"]),
                    "stop_loss_price": _as_optional_float(row["stop_loss_price"]),
                    "time_stop_minutes": _as_optional_int(row["time_stop_minutes"]),
                    "opened_at": row["opened_at"],
                    "scaled_out_count": int(row["scaled_out_count"] or 0),
                    "latest_spread_bps": latest_spread_bps,
                    "updated_at": row["updated_at"],
                }
            )
        return positions

    async def get_market_snapshots(
        self,
        *,
        market_id: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if market_id:
            args.append(market_id)
            clauses.append(f"market_id = ${len(args)}")
        if start_at:
            args.append(start_at)
            clauses.append(f"created_at >= ${len(args)}")
        if end_at:
            args.append(end_at)
            clauses.append(f"created_at <= ${len(args)}")
        args.append(limit)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT market_id, question, token_id_yes, token_id_no, price_yes, price_no, volume_24h, payload, created_at
            FROM market_snapshots
            {where}
            ORDER BY created_at ASC
            LIMIT ${len(args)}
        """
        rows = await self.db.fetch(query, *args)
        snapshots: list[dict[str, Any]] = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            snapshots.append(
                {
                    "market_id": row["market_id"],
                    "question": row["question"],
                    "token_id_yes": row["token_id_yes"],
                    "token_id_no": row["token_id_no"],
                    "price_yes": float(row["price_yes"] or 0.0),
                    "price_no": float(row["price_no"] or 0.0),
                    "volume_24h": float(row["volume_24h"] or 0.0),
                    "metadata": payload,
                    "created_at": row["created_at"],
                }
            )
        return snapshots

    async def get_replay_orders(
        self,
        *,
        market_id: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if market_id:
            args.append(market_id)
            clauses.append(f"market_id = ${len(args)}")
        if start_at:
            args.append(start_at)
            clauses.append(f"created_at >= ${len(args)}")
        if end_at:
            args.append(end_at)
            clauses.append(f"created_at <= ${len(args)}")
        args.append(limit)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT payload, created_at
            FROM paper_orders
            {where}
            ORDER BY created_at ASC
            LIMIT ${len(args)}
        """
        rows = await self.db.fetch(query, *args)
        return [self._decode_record(row) for row in rows]

    async def _recent_payloads(
        self,
        table: str,
        *,
        limit: int,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if asset:
            args.append(asset.upper())
            clauses.append(f"payload->>'asset_symbol' = ${len(args)}")
        if tier:
            args.append(tier)
            clauses.append(f"payload->>'crypto_tier' = ${len(args)}")
        if strategy:
            args.append(strategy)
            clauses.append(f"payload->>'strategy_id' = ${len(args)}")
        args.append(limit)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT payload, created_at FROM {table} {where} ORDER BY created_at DESC LIMIT ${len(args)}"
        rows = await self.db.fetch(query, *args)
        return [self._decode_record(row) for row in rows]

    @staticmethod
    def _matches_filters(
        payload: dict[str, Any],
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        allow_missing: bool = False,
    ) -> bool:
        if asset:
            asset_value = str(payload.get("asset_symbol") or "").upper()
            if not asset_value:
                if not allow_missing:
                    return False
            elif asset_value != asset.upper():
                return False
        if tier:
            tier_value = str(payload.get("crypto_tier") or "").lower()
            if not tier_value:
                if not allow_missing:
                    return False
            elif tier_value != tier.lower():
                return False
        if strategy:
            strategy_value = str(payload.get("strategy_id") or "").strip()
            if not strategy_value:
                if not allow_missing:
                    return False
            elif strategy_value != strategy:
                return False
        return True

    @staticmethod
    def _count_by_key(items: list[dict[str, Any]], key: str, *, limit: int) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for item in items:
            value = str(item.get(key) or "").strip()
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
        ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        return [{"label": label, "count": count} for label, count in ordered[:limit]]

    @staticmethod
    def _strategy_breakdown(signals: list[dict[str, Any]], orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        signal_counts: dict[str, int] = {}
        order_counts: dict[str, int] = {}
        realized: dict[str, float] = {}
        for item in signals:
            key = str(item.get("strategy_id") or "").strip()
            if key:
                signal_counts[key] = signal_counts.get(key, 0) + 1
        for item in orders:
            key = str(item.get("strategy_id") or "").strip()
            if not key:
                continue
            order_counts[key] = order_counts.get(key, 0) + 1
            realized[key] = realized.get(key, 0.0) + float(item.get("realized_pnl_usd") or 0.0)
        keys = sorted(set(signal_counts) | set(order_counts))
        return [
            {
                "label": key,
                "signals": signal_counts.get(key, 0),
                "orders": order_counts.get(key, 0),
                "realized_pnl_usd": round(realized.get(key, 0.0), 4),
            }
            for key in keys
        ]

    @staticmethod
    def _news_breakdown(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        summary = {"validated": 0, "rejected": 0, "pending": 0}
        for item in items:
            news_validation = item.get("news_validation") or {}
            if not news_validation:
                summary["pending"] += 1
            elif news_validation.get("validated"):
                summary["validated"] += 1
            else:
                summary["rejected"] += 1
        return [{"label": key, "count": value} for key, value in summary.items()]

    @staticmethod
    def _news_provider_breakdown(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for item in items:
            news_validation = item.get("news_validation") or {}
            provider_used = str(news_validation.get("provider_used") or "").strip().lower()
            if not provider_used:
                continue
            counts[provider_used] = counts.get(provider_used, 0) + 1
        ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        return [{"label": label, "count": count} for label, count in ordered]

    @staticmethod
    def _news_fallback_breakdown(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        summary = {"primary_only": 0, "fallback_used": 0, "unknown": 0}
        for item in items:
            news_validation = item.get("news_validation") or {}
            if not news_validation:
                summary["unknown"] += 1
            elif news_validation.get("fallback_used"):
                summary["fallback_used"] += 1
            else:
                summary["primary_only"] += 1
        return [{"label": key, "count": value} for key, value in summary.items()]

    @staticmethod
    def _last_news_provider(items: list[dict[str, Any]]) -> dict[str, Any] | None:
        for item in reversed(items):
            news_validation = item.get("news_validation") or {}
            provider_used = str(news_validation.get("provider_used") or "").strip()
            if not provider_used:
                continue
            return {
                "provider_used": provider_used,
                "fallback_used": bool(news_validation.get("fallback_used")),
                "signal_id": str(item.get("signal_id") or ""),
                "asset_symbol": str(item.get("asset_symbol") or ""),
                "crypto_tier": str(item.get("crypto_tier") or ""),
                "created_at": (
                    item.get("created_at").isoformat()
                    if isinstance(item.get("created_at"), datetime)
                    else item.get("created_at")
                ),
            }
        return None

    @staticmethod
    def _top_markets(signals: list[dict[str, Any]], order_count_by_market: dict[str, int]) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for item in signals:
            market_id = str(item.get("market_id") or "")
            if not market_id:
                continue
            bucket = buckets.setdefault(
                market_id,
                {
                    "market_id": market_id,
                    "market_question": item.get("market_question", ""),
                    "asset_symbol": item.get("asset_symbol", ""),
                    "crypto_tier": item.get("crypto_tier", ""),
                    "signal_count": 0,
                    "edge_total": 0.0,
                    "confidence_total": 0.0,
                },
            )
            bucket["signal_count"] += 1
            bucket["edge_total"] += float(item.get("edge") or 0.0)
            bucket["confidence_total"] += float(item.get("confidence") or 0.0)
        ordered = sorted(
            buckets.values(),
            key=lambda item: (-int(item["signal_count"]), -float(item["edge_total"]), str(item["market_id"])),
        )
        results: list[dict[str, Any]] = []
        for item in ordered[:6]:
            signal_count = int(item["signal_count"])
            results.append(
                {
                    "market_id": item["market_id"],
                    "market_question": item["market_question"],
                    "asset_symbol": item["asset_symbol"],
                    "crypto_tier": item["crypto_tier"],
                    "signal_count": signal_count,
                    "order_count": order_count_by_market.get(str(item["market_id"]), 0),
                    "avg_edge": round(float(item["edge_total"]) / signal_count, 4) if signal_count else 0.0,
                    "avg_confidence": round(float(item["confidence_total"]) / signal_count, 4) if signal_count else 0.0,
                }
            )
        return results

    @staticmethod
    def _build_pipeline_time_series(
        signals: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        orders: list[dict[str, Any]],
        risk_events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}

        def touch(items: list[dict[str, Any]], key: str) -> None:
            for item in items:
                created_at = item.get("created_at")
                if created_at is None:
                    continue
                dt = created_at.astimezone(UTC) if isinstance(created_at, datetime) else datetime.fromisoformat(
                    str(created_at).replace("Z", "+00:00")
                ).astimezone(UTC)
                bucket = dt.replace(minute=0, second=0, microsecond=0).isoformat()
                entry = buckets.setdefault(bucket, {"bucket": bucket, "signals": 0, "decisions": 0, "orders": 0, "risk_events": 0})
                entry[key] += 1

        touch(signals, "signals")
        touch(decisions, "decisions")
        touch(orders, "orders")
        touch(risk_events, "risk_events")
        return [buckets[key] for key in sorted(buckets)]

    @staticmethod
    def _sharpe_from_equity_rows(rows: list[asyncpg.Record]) -> float:
        if len(rows) < 2:
            return 0.0
        returns: list[float] = []
        previous = float(rows[0]["total_equity"] or 0.0)
        for row in rows[1:]:
            current = float(row["total_equity"] or 0.0)
            if previous > 0 and current > 0:
                returns.append(math.log(current / previous))
            previous = current
        if len(returns) < 2:
            return 0.0
        mean_return = sum(returns) / len(returns)
        variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
        std = math.sqrt(variance)
        return round(mean_return / std, 4) if std > 0 else 0.0

    @staticmethod
    def _max_drawdown_from_equity_rows(rows: list[asyncpg.Record]) -> float:
        peak = 0.0
        max_drawdown = 0.0
        for row in rows:
            equity = float(row["total_equity"] or 0.0)
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)
        return round(max_drawdown, 6)

    @staticmethod
    def _estimate_mae_mfe(exit_orders: list[dict[str, Any]], open_positions: list[dict[str, Any]]) -> dict[str, float]:
        per_share_pnl: list[float] = []
        for item in exit_orders:
            size = int(item.get("size") or 0)
            if size <= 0:
                continue
            per_share_pnl.append(float(item.get("realized_pnl_usd") or 0.0) / size)
        for item in open_positions:
            size = int(item.get("size") or 0)
            if size <= 0:
                continue
            per_share_pnl.append((float(item.get("current_price") or 0.0) - float(item.get("average_price") or 0.0)))
        if not per_share_pnl:
            return {"avg_mae": 0.0, "avg_mfe": 0.0}
        negatives = [value for value in per_share_pnl if value < 0]
        positives = [value for value in per_share_pnl if value > 0]
        return {
            "avg_mae": round(sum(negatives) / len(negatives), 4) if negatives else 0.0,
            "avg_mfe": round(sum(positives) / len(positives), 4) if positives else 0.0,
        }

    async def _upsert_position(
        self,
        market_id: str,
        token_id: str,
        market_question: str,
        asset_symbol: str,
        crypto_tier: str,
        strategy_id: str,
        regime: str,
        direction: str,
        size: int,
        average_price: float,
        exposure_usd: float,
        take_profit_price: float | None,
        stop_loss_price: float | None,
        time_stop_minutes: int | None,
    ) -> None:
        position_key = f"{market_id}:{direction}"
        await self.db.execute(
            """
            INSERT INTO positions (
                market_id, position_key, token_id, market_question, asset_symbol, crypto_tier, strategy_id, regime,
                direction, size, average_price, exposure_usd, take_profit_price, stop_loss_price, time_stop_minutes, opened_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
            ON CONFLICT (position_key) DO UPDATE
            SET token_id = EXCLUDED.token_id,
                market_question = EXCLUDED.market_question,
                asset_symbol = EXCLUDED.asset_symbol,
                crypto_tier = EXCLUDED.crypto_tier,
                strategy_id = COALESCE(NULLIF(EXCLUDED.strategy_id, ''), positions.strategy_id),
                regime = COALESCE(NULLIF(EXCLUDED.regime, ''), positions.regime),
                direction = EXCLUDED.direction,
                size = positions.size + EXCLUDED.size,
                average_price = CASE
                    WHEN positions.size + EXCLUDED.size = 0 THEN EXCLUDED.average_price
                    ELSE ((positions.average_price * positions.size) + (EXCLUDED.average_price * EXCLUDED.size))
                        / (positions.size + EXCLUDED.size)
                END,
                exposure_usd = positions.exposure_usd + EXCLUDED.exposure_usd,
                take_profit_price = COALESCE(EXCLUDED.take_profit_price, positions.take_profit_price),
                stop_loss_price = COALESCE(EXCLUDED.stop_loss_price, positions.stop_loss_price),
                time_stop_minutes = COALESCE(EXCLUDED.time_stop_minutes, positions.time_stop_minutes),
                updated_at = EXCLUDED.updated_at
            """,
            market_id,
            position_key,
            token_id,
            market_question,
            asset_symbol,
            crypto_tier,
            strategy_id,
            regime,
            direction,
            size,
            average_price,
            exposure_usd,
            take_profit_price,
            stop_loss_price,
            time_stop_minutes,
            datetime.now(UTC),
            datetime.now(UTC),
        )

    async def _reduce_position(self, position_key: str, size: int, exit_action: str) -> None:
        row = await self.db.fetchrow(
            """
            SELECT size, scaled_out_count
            FROM positions
            WHERE position_key = $1
            """,
            position_key,
        )
        if row is None:
            return
        remaining_size = max(int(row["size"] or 0) - size, 0)
        if remaining_size == 0:
            await self.db.execute("DELETE FROM positions WHERE position_key = $1", position_key)
            return
        scale_out_increment = 1 if exit_action == "scale_out" else 0
        await self.db.execute(
            """
            UPDATE positions
            SET size = $2::integer,
                exposure_usd = average_price * ($2::double precision),
                scaled_out_count = scaled_out_count + $3::integer,
                updated_at = $4
            WHERE position_key = $1
            """,
            position_key,
            remaining_size,
            scale_out_increment,
            datetime.now(UTC),
        )

    async def _latest_market_prices(self, market_ids: list[str]) -> dict[str, dict[str, float]]:
        unique_ids = [market_id for market_id in dict.fromkeys(market_ids) if market_id]
        if not unique_ids:
            return {}
        rows = await self.db.fetch(
            """
            SELECT DISTINCT ON (market_id)
                market_id,
                price_yes,
                price_no,
                payload,
                created_at
            FROM market_snapshots
            WHERE market_id = ANY($1::text[])
            ORDER BY market_id, created_at DESC
            """,
            unique_ids,
        )
        return {
            str(row["market_id"]): {
                "price_yes": float(row["price_yes"] or 0.0),
                "price_no": float(row["price_no"] or 0.0),
                "payload": json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
            }
            for row in rows
        }

    def _decode_record(self, row: asyncpg.Record) -> dict[str, Any]:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload["created_at"] = row["created_at"]
        return payload

    @staticmethod
    def _decode_pipeline_record(row: asyncpg.Record) -> dict[str, Any]:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return {
            "agent": row["agent"],
            "event_type": row["event_type"],
            "created_at": row["created_at"],
            **payload,
        }

    @staticmethod
    def _latest_pipeline_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
        for event in reversed(events):
            if str(event.get("event_type") or "") == event_type:
                return event
        return None

    @staticmethod
    def _sum_pipeline_field(events: list[dict[str, Any]], event_type: str, field: str) -> int:
        return sum(
            int(event.get(field) or 0)
            for event in events
            if str(event.get("event_type") or "") == event_type
        )

    @classmethod
    def _summarize_pipeline_events(cls, events: list[dict[str, Any]], *, window_minutes: int) -> dict[str, Any]:
        return {
            "window_minutes": window_minutes,
            "gamma_markets_fetched": cls._sum_pipeline_field(events, "scanner.scan_cycle", "gamma_markets_fetched"),
            "crypto_classified": cls._sum_pipeline_field(events, "scanner.scan_cycle", "crypto_classified"),
            "selected_for_scan": cls._sum_pipeline_field(events, "scanner.scan_cycle", "selected_for_scan"),
            "strategy_candidates": cls._sum_pipeline_field(events, "scanner.scan_cycle", "strategy_candidates"),
            "reached_risk_engine": cls._sum_pipeline_field(events, "scanner.scan_cycle", "reached_risk_engine"),
            "risk_passed": cls._sum_pipeline_field(events, "scanner.scan_cycle", "risk_passed"),
            "risk_blocked": cls._sum_pipeline_field(events, "scanner.scan_cycle", "risk_blocked"),
            "duplicates_blocked": cls._sum_pipeline_field(events, "scanner.scan_cycle", "duplicates_blocked"),
            "persisted_signals": cls._sum_pipeline_field(events, "scanner.scan_cycle", "persisted_signals"),
            "reviewer_inbox": cls._sum_pipeline_field(events, "reviewer.review_cycle", "inbox_count"),
            "reviewer_approved": cls._sum_pipeline_field(events, "reviewer.review_cycle", "approved_count"),
            "reviewer_rejected": cls._sum_pipeline_field(events, "reviewer.review_cycle", "rejected_count"),
            "executor_inbox": cls._sum_pipeline_field(events, "executor.execute_cycle", "inbox_count"),
            "executor_executed": cls._sum_pipeline_field(events, "executor.execute_cycle", "executed_count"),
            "executor_blocked": cls._sum_pipeline_field(events, "executor.execute_cycle", "blocked_count"),
            "exit_orders_count": cls._sum_pipeline_field(events, "executor.execute_cycle", "exit_orders_count"),
        }
