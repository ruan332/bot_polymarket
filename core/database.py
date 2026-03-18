from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg

from core.schemas import AgentHeartbeat, PortfolioSummary


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
    market_id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    size INTEGER NOT NULL,
    average_price DOUBLE PRECISION NOT NULL,
    exposure_usd DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id UUID PRIMARY KEY,
    available_balance DOUBLE PRECISION NOT NULL,
    total_exposure DOUBLE PRECISION NOT NULL,
    realized_pnl DOUBLE PRECISION NOT NULL,
    unrealized_pnl DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_heartbeats (
    agent TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    running BOOLEAN NOT NULL,
    config_version INTEGER NOT NULL,
    meta JSONB NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
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

    async def record_decision(self, decision_id: str, signal_id: str, event_type: str, payload: dict[str, Any]) -> None:
        await self.db.execute(
            "INSERT INTO agent_decisions (id, signal_id, event_type, payload) VALUES ($1::uuid, $2::uuid, $3, $4::jsonb)",
            decision_id,
            signal_id,
            event_type,
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
            await self._upsert_position(
                market_id=market_id,
                direction=payload["direction"],
                size=payload["size"],
                average_price=payload["price_limit"],
                exposure_usd=payload["notional_usd"],
            )
            await self.record_equity_snapshot()

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

    async def record_equity_snapshot(self) -> None:
        summary = await self.get_portfolio_summary()
        await self.db.execute(
            """
            INSERT INTO equity_snapshots (id, available_balance, total_exposure, realized_pnl, unrealized_pnl)
            VALUES (gen_random_uuid(), $1, $2, $3, $4)
            """,
            summary.available_balance,
            summary.total_exposure,
            summary.realized_pnl,
            summary.unrealized_pnl,
        )

    async def get_portfolio_summary(self) -> PortfolioSummary:
        row = await self.db.fetchrow(
            """
            SELECT
                COALESCE(SUM(exposure_usd), 0) AS total_exposure,
                COUNT(*) AS open_positions
            FROM positions
            """
        )
        total_exposure = float(row["total_exposure"] or 0.0) if row else 0.0
        open_positions = int(row["open_positions"] or 0) if row else 0
        available_balance = max(self.initial_bankroll - total_exposure, 0.0)
        return PortfolioSummary(
            available_balance=available_balance,
            total_exposure=total_exposure,
            open_positions=open_positions,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )

    async def get_recent_signals(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.db.fetch("SELECT payload, created_at FROM signals ORDER BY created_at DESC LIMIT $1", limit)
        return [self._decode_record(row) for row in rows]

    async def get_recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.db.fetch("SELECT payload, created_at FROM agent_decisions ORDER BY created_at DESC LIMIT $1", limit)
        return [self._decode_record(row) for row in rows]

    async def get_recent_orders(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.db.fetch("SELECT payload, created_at FROM paper_orders ORDER BY created_at DESC LIMIT $1", limit)
        return [self._decode_record(row) for row in rows]

    async def get_recent_risk_events(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.db.fetch("SELECT payload, created_at FROM risk_events ORDER BY created_at DESC LIMIT $1", limit)
        return [self._decode_record(row) for row in rows]

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
        portfolio = await self.get_portfolio_summary()
        return {
            "signals": int(signals["count"] or 0),
            "decisions": int(decisions["count"] or 0),
            "orders": int(orders["count"] or 0),
            "risk_events": int(risk_events["count"] or 0),
            "portfolio": portfolio.model_dump(),
        }

    async def _upsert_position(
        self,
        market_id: str,
        direction: str,
        size: int,
        average_price: float,
        exposure_usd: float,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO positions (market_id, direction, size, average_price, exposure_usd, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (market_id) DO UPDATE
            SET direction = EXCLUDED.direction,
                size = positions.size + EXCLUDED.size,
                average_price = EXCLUDED.average_price,
                exposure_usd = positions.exposure_usd + EXCLUDED.exposure_usd,
                updated_at = EXCLUDED.updated_at
            """,
            market_id,
            direction,
            size,
            average_price,
            exposure_usd,
            datetime.utcnow(),
        )

    def _decode_record(self, row: asyncpg.Record) -> dict[str, Any]:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload["created_at"] = row["created_at"]
        return payload
