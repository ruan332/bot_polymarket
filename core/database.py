from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import asyncpg

from core.schemas import AgentHeartbeat, EquitySnapshotPoint, MarketSnapshotPayload, PortfolioSummary


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
    token_id TEXT,
    market_question TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL,
    size INTEGER NOT NULL,
    average_price DOUBLE PRECISION NOT NULL,
    exposure_usd DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE positions ADD COLUMN IF NOT EXISTS token_id TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS market_question TEXT NOT NULL DEFAULT '';

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

CREATE INDEX IF NOT EXISTS idx_market_snapshots_market_created_at
ON market_snapshots (market_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_equity_snapshots_created_at
ON equity_snapshots (created_at DESC);
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
                token_id=str(payload.get("token_id", "")),
                market_question=str(payload.get("market_question", "")),
                direction=payload["direction"],
                size=payload["size"],
                average_price=payload["price_limit"],
                exposure_usd=payload["notional_usd"],
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
                direction,
                size,
                average_price,
                exposure_usd
            FROM positions
            """
        )
        if not rows:
            return PortfolioSummary(
                available_balance=self.initial_bankroll,
                total_equity=self.initial_bankroll,
            )

        latest_prices = await self._latest_market_prices([str(row["market_id"]) for row in rows])
        total_exposure = 0.0
        current_market_value = 0.0
        realized_pnl = 0.0
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
                COALESCE(item->'metadata', '{}'::jsonb),
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
            SELECT market_id, token_id, market_question, direction, size, average_price, exposure_usd, updated_at
            FROM positions
            ORDER BY updated_at DESC
            """
        )
        latest_prices = await self._latest_market_prices([str(row["market_id"]) for row in rows])
        positions: list[dict[str, Any]] = []
        for row in rows:
            latest = latest_prices.get(str(row["market_id"]))
            current_price = float(row["average_price"] or 0.0)
            if latest:
                current_price = float(latest["price_yes"] if row["direction"] == "YES" else latest["price_no"])
            size = int(row["size"] or 0)
            cost_basis = float(row["exposure_usd"] or 0.0)
            current_value = size * current_price
            positions.append(
                {
                    "market_id": row["market_id"],
                    "token_id": row["token_id"],
                    "market_question": row["market_question"],
                    "direction": row["direction"],
                    "size": size,
                    "average_price": float(row["average_price"] or 0.0),
                    "current_price": current_price,
                    "cost_basis_usd": cost_basis,
                    "current_value_usd": current_value,
                    "unrealized_pnl": current_value - cost_basis,
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

    async def _upsert_position(
        self,
        market_id: str,
        token_id: str,
        market_question: str,
        direction: str,
        size: int,
        average_price: float,
        exposure_usd: float,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO positions (market_id, token_id, market_question, direction, size, average_price, exposure_usd, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (market_id) DO UPDATE
            SET token_id = EXCLUDED.token_id,
                market_question = EXCLUDED.market_question,
                direction = EXCLUDED.direction,
                size = positions.size + EXCLUDED.size,
                average_price = CASE
                    WHEN positions.size + EXCLUDED.size = 0 THEN EXCLUDED.average_price
                    ELSE ((positions.average_price * positions.size) + (EXCLUDED.average_price * EXCLUDED.size))
                        / (positions.size + EXCLUDED.size)
                END,
                exposure_usd = positions.exposure_usd + EXCLUDED.exposure_usd,
                updated_at = EXCLUDED.updated_at
            """,
            market_id,
            token_id,
            market_question,
            direction,
            size,
            average_price,
            exposure_usd,
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
            }
            for row in rows
        }

    def _decode_record(self, row: asyncpg.Record) -> dict[str, Any]:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload["created_at"] = row["created_at"]
        return payload
