from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable

import asyncpg

from core.config import AppSettings
from core.schemas import (
    AgentHeartbeat,
    EquitySnapshotPoint,
    FlowAnalysisPayload,
    MarketSnapshotPayload,
    NewsValidationPayload,
    PairCycleStatePayload,
    PendingPairOrderPayload,
    PortfolioSummary,
)

EXECUTED_ORDER_STATUSES = ("simulated", "live_submitted", "live_filled")
FILLED_ORDER_STATUSES = ("simulated", "live_filled")


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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    trade_group_id TEXT NOT NULL DEFAULT '',
    cycle_slug TEXT NOT NULL DEFAULT '',
    leg_role TEXT NOT NULL DEFAULT '',
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
ALTER TABLE positions ADD COLUMN IF NOT EXISTS trade_group_id TEXT NOT NULL DEFAULT '';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS cycle_slug TEXT NOT NULL DEFAULT '';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS leg_role TEXT NOT NULL DEFAULT '';
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
    trigger_source TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE equity_snapshots ADD COLUMN IF NOT EXISTS current_market_value DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE equity_snapshots ADD COLUMN IF NOT EXISTS total_equity DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE equity_snapshots ADD COLUMN IF NOT EXISTS total_pnl DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE equity_snapshots ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'system';
ALTER TABLE equity_snapshots ADD COLUMN IF NOT EXISTS trigger_source TEXT NOT NULL DEFAULT '';

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

CREATE TABLE IF NOT EXISTS pair_cycles (
    asset_symbol TEXT PRIMARY KEY,
    asset_name TEXT NOT NULL DEFAULT '',
    crypto_tier TEXT NOT NULL DEFAULT '',
    cycle_slug TEXT NOT NULL,
    cycle_start TIMESTAMPTZ NOT NULL,
    market_id TEXT NOT NULL,
    market_question TEXT NOT NULL DEFAULT '',
    token_id_yes TEXT NOT NULL DEFAULT '',
    token_id_no TEXT NOT NULL DEFAULT '',
    price_yes DOUBLE PRECISION NOT NULL DEFAULT 0,
    price_no DOUBLE PRECISION NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    side_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    max_buy_counts_per_side INTEGER NOT NULL DEFAULT 1,
    last_signal_direction TEXT,
    last_signal_at TIMESTAMPTZ,
    last_quote_at TIMESTAMPTZ,
    predictor_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS flow_analyses (
    id UUID PRIMARY KEY,
    signal_id UUID,
    trade_group_id TEXT NOT NULL DEFAULT '',
    market_id TEXT NOT NULL,
    cycle_slug TEXT NOT NULL DEFAULT '',
    market_question TEXT NOT NULL DEFAULT '',
    asset_symbol TEXT NOT NULL DEFAULT '',
    asset_name TEXT NOT NULL DEFAULT '',
    crypto_tier TEXT NOT NULL DEFAULT '',
    window_minutes INTEGER NOT NULL DEFAULT 15,
    dominant_direction TEXT NOT NULL DEFAULT 'neutral',
    dominance_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    up_trade_count INTEGER NOT NULL DEFAULT 0,
    down_trade_count INTEGER NOT NULL DEFAULT 0,
    up_notional DOUBLE PRECISION NOT NULL DEFAULT 0,
    down_notional DOUBLE PRECISION NOT NULL DEFAULT 0,
    total_trades INTEGER NOT NULL DEFAULT 0,
    total_notional DOUBLE PRECISION NOT NULL DEFAULT 0,
    freshness_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    source_used TEXT NOT NULL DEFAULT 'ws',
    sample_count INTEGER NOT NULL DEFAULT 0,
    last_trade_at TIMESTAMPTZ,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pending_pair_orders (
    pending_order_id UUID PRIMARY KEY,
    trade_group_id TEXT NOT NULL,
    signal_id UUID NOT NULL,
    cycle_slug TEXT NOT NULL,
    market_id TEXT NOT NULL,
    market_question TEXT NOT NULL DEFAULT '',
    asset_symbol TEXT NOT NULL DEFAULT '',
    crypto_tier TEXT NOT NULL DEFAULT '',
    strategy_id TEXT NOT NULL DEFAULT 'pair_15m',
    position_key TEXT NOT NULL DEFAULT '',
    token_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    leg_role TEXT NOT NULL DEFAULT 'hedge',
    size INTEGER NOT NULL,
    target_price DOUBLE PRECISION NOT NULL,
    reference_price DOUBLE PRECISION NOT NULL,
    exchange_order_id TEXT NOT NULL DEFAULT '',
    submission_status TEXT NOT NULL DEFAULT '',
    submission_created_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS news_validations (
    id UUID PRIMARY KEY,
    signal_id UUID NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS analysis_cutoffs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cutoff_name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS market_discovery_runs (
    run_id UUID PRIMARY KEY,
    requested_limit INTEGER NOT NULL DEFAULT 0,
    universe_count INTEGER NOT NULL DEFAULT 0,
    crypto_classified_count INTEGER NOT NULL DEFAULT 0,
    deterministic_passed_count INTEGER NOT NULL DEFAULT 0,
    research_passed_count INTEGER NOT NULL DEFAULT 0,
    claude_passed_count INTEGER NOT NULL DEFAULT 0,
    operable_count INTEGER NOT NULL DEFAULT 0,
    stage_counts JSONB NOT NULL DEFAULT '[]'::jsonb,
    dropoff_counts JSONB NOT NULL DEFAULT '[]'::jsonb,
    rejected_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
    cost_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    scan_stats JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_discovery_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES market_discovery_runs (run_id) ON DELETE CASCADE,
    market_id TEXT NOT NULL,
    question TEXT NOT NULL DEFAULT '',
    asset_symbol TEXT NOT NULL DEFAULT '',
    asset_name TEXT NOT NULL DEFAULT '',
    crypto_tier TEXT NOT NULL DEFAULT '',
    market_kind TEXT NOT NULL DEFAULT '',
    volume_24h DOUBLE PRECISION NOT NULL DEFAULT 0,
    spread_bps DOUBLE PRECISION NOT NULL DEFAULT 0,
    edge DOUBLE PRECISION NOT NULL DEFAULT 0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    liquidity_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    time_to_expiry_hours DOUBLE PRECISION,
    strategy_id TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT '',
    deterministic_pass BOOLEAN NOT NULL DEFAULT FALSE,
    research_pass BOOLEAN NOT NULL DEFAULT FALSE,
    claude_pass BOOLEAN NOT NULL DEFAULT FALSE,
    verdict TEXT NOT NULL DEFAULT 'reject',
    score DOUBLE PRECISION NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    stage_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS weather_copytrade_runs (
    run_id UUID PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'WEATHER',
    leaderboard_limit INTEGER NOT NULL DEFAULT 0,
    universe_count INTEGER NOT NULL DEFAULT 0,
    shortlisted_count INTEGER NOT NULL DEFAULT 0,
    selected_count INTEGER NOT NULL DEFAULT 0,
    selected_proxy_wallet TEXT NOT NULL DEFAULT '',
    selected_user_name TEXT NOT NULL DEFAULT '',
    candidate_count INTEGER NOT NULL DEFAULT 0,
    stage_counts JSONB NOT NULL DEFAULT '[]'::jsonb,
    rejected_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    selection_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    scan_stats JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS weather_copytrade_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES weather_copytrade_runs (run_id) ON DELETE CASCADE,
    rank INTEGER NOT NULL DEFAULT 0,
    proxy_wallet TEXT NOT NULL DEFAULT '',
    user_name TEXT NOT NULL DEFAULT '',
    verified_badge BOOLEAN NOT NULL DEFAULT FALSE,
    profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    score DOUBLE PRECISION NOT NULL DEFAULT 0,
    rationale TEXT NOT NULL DEFAULT '',
    passed BOOLEAN NOT NULL DEFAULT FALSE,
    reject_reason TEXT NOT NULL DEFAULT '',
    selected BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS weather_copytrade_state (
    category TEXT PRIMARY KEY,
    run_id UUID,
    selected_proxy_wallet TEXT NOT NULL DEFAULT '',
    selected_user_name TEXT NOT NULL DEFAULT '',
    selected_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    selection JSONB NOT NULL DEFAULT '{}'::jsonb,
    report JSONB NOT NULL DEFAULT '{}'::jsonb,
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    paused BOOLEAN NOT NULL DEFAULT TRUE,
    approved_at TIMESTAMPTZ,
    activated_at TIMESTAMPTZ,
    last_trade_seen_at TIMESTAMPTZ,
    last_trade_seen_hash TEXT NOT NULL DEFAULT '',
    processed_trade_hashes JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS weather_copytrade_profiles (
    category TEXT NOT NULL DEFAULT 'WEATHER',
    run_id UUID,
    proxy_wallet TEXT NOT NULL DEFAULT '',
    user_name TEXT NOT NULL DEFAULT '',
    profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    selection_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    paused BOOLEAN NOT NULL DEFAULT TRUE,
    approved_at TIMESTAMPTZ,
    activated_at TIMESTAMPTZ,
    last_trade_seen_at TIMESTAMPTZ,
    last_trade_seen_hash TEXT NOT NULL DEFAULT '',
    processed_trade_hashes JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    performance_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (category, proxy_wallet)
);

CREATE TABLE IF NOT EXISTS settlement_events (
    id UUID PRIMARY KEY,
    position_key TEXT NOT NULL,
    market_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_market_created_at
ON market_snapshots (market_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pair_cycles_cycle_slug
ON pair_cycles (cycle_slug, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_flow_analyses_market_created_at
ON flow_analyses (market_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_flow_analyses_asset_created_at
ON flow_analyses (asset_symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pending_pair_orders_status
ON pending_pair_orders (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pending_pair_orders_trade_group
ON pending_pair_orders (trade_group_id, created_at DESC);

ALTER TABLE pending_pair_orders ADD COLUMN IF NOT EXISTS exchange_order_id TEXT NOT NULL DEFAULT '';
ALTER TABLE pending_pair_orders ADD COLUMN IF NOT EXISTS submission_status TEXT NOT NULL DEFAULT '';
ALTER TABLE pending_pair_orders ADD COLUMN IF NOT EXISTS submission_created_at TIMESTAMPTZ;

ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_equity_snapshots_created_at
ON equity_snapshots (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_news_validations_signal_id
ON news_validations (signal_id);

CREATE INDEX IF NOT EXISTS idx_analysis_cutoffs_created_at
ON analysis_cutoffs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_market_discovery_runs_created_at
ON market_discovery_runs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_market_discovery_candidates_run_id
ON market_discovery_candidates (run_id, score DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_market_discovery_candidates_verdict
ON market_discovery_candidates (verdict, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_weather_copytrade_runs_created_at
ON weather_copytrade_runs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_weather_copytrade_candidates_run_id
ON weather_copytrade_candidates (run_id, score DESC, rank ASC, created_at DESC);

ALTER TABLE IF EXISTS weather_copytrade_candidates
ADD COLUMN IF NOT EXISTS passed BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE IF EXISTS weather_copytrade_candidates
ADD COLUMN IF NOT EXISTS reject_reason TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_weather_copytrade_state_active
ON weather_copytrade_state (active, paused, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_weather_copytrade_profiles_active
ON weather_copytrade_profiles (category, active, paused, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_settlement_events_created_at
ON settlement_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_settlement_events_market
ON settlement_events (market_id, created_at DESC);

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
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return value
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
    def __init__(self, db: Database, initial_bankroll: float, settings: AppSettings | None = None):
        self.db = db
        self.initial_bankroll = initial_bankroll
        self.settings = settings
        self._live_balance_provider: Callable[[], Awaitable[dict[str, Any]]] | None = None

    def bind_live_balance_provider(self, provider: Callable[[], Awaitable[dict[str, Any]]]) -> None:
        self._live_balance_provider = provider

    def _is_live_mode(self) -> bool:
        return bool(getattr(self.settings, "live_trading", False))

    async def _get_live_bootstrap_status(self) -> dict[str, Any] | None:
        if not self._is_live_mode() or self._live_balance_provider is None:
            return None
        status = await self._live_balance_provider()
        return status if isinstance(status, dict) else None

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
        previous_status_row = await self.db.fetchrow(
            "SELECT status FROM paper_orders WHERE id = $1::uuid",
            order_id,
        )
        previous_status = str(previous_status_row["status"]) if previous_status_row is not None else ""
        await self.db.execute(
            """
            INSERT INTO paper_orders (id, signal_id, market_id, status, payload, created_at, updated_at)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb, NOW(), NOW())
            ON CONFLICT (id) DO UPDATE
            SET signal_id = EXCLUDED.signal_id,
                market_id = EXCLUDED.market_id,
                status = EXCLUDED.status,
                payload = EXCLUDED.payload,
                updated_at = EXCLUDED.updated_at
            """,
            order_id,
            signal_id,
            market_id,
            status,
            _as_json(payload),
        )
        should_apply_position_effect = status in {"simulated", "live_filled"} and previous_status not in FILLED_ORDER_STATUSES
        if should_apply_position_effect:
            action = str(payload.get("action") or "entry")
            if action in {"entry", "scale_in"}:
                await self._upsert_position(
                    market_id=market_id,
                    position_key=str(payload.get("position_key") or f"{market_id}:{payload['direction']}"),
                    token_id=str(payload.get("token_id", "")),
                    market_question=str(payload.get("market_question", "")),
                    asset_symbol=str(payload.get("asset_symbol", "")),
                    crypto_tier=str(payload.get("crypto_tier", "")),
                    strategy_id=str(payload.get("strategy_id", "")),
                    regime=str(payload.get("regime", "")),
                    trade_group_id=str(payload.get("trade_group_id", "")),
                    cycle_slug=str(payload.get("cycle_slug", "")),
                    leg_role=str(payload.get("leg_role", "")),
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

    async def record_settlement_event(
        self,
        settlement_id: str,
        position_key: str,
        market_id: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        await self.db.execute(
            "INSERT INTO settlement_events (id, position_key, market_id, status, payload) VALUES ($1::uuid, $2, $3, $4, $5::jsonb)",
            settlement_id,
            position_key,
            market_id,
            status,
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
        canonical_source = summary.balance_source or ("polymarket_live" if summary.mode == "live" else "paper_ledger")
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
                source,
                trigger_source
            )
            VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            summary.available_balance,
            summary.total_exposure,
            summary.current_market_value,
            summary.total_equity,
            summary.total_pnl,
            summary.realized_pnl,
            summary.unrealized_pnl,
            canonical_source,
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
            WHERE status = ANY($1::text[])
            """,
            list(FILLED_ORDER_STATUSES),
        )
        realized_pnl = float(realized_row["realized_pnl"] or 0.0) if realized_row else 0.0
        live_status = await self._get_live_bootstrap_status()
        live_balance = None
        live_allowance = None
        funder = ""
        mode = "live" if self._is_live_mode() else "paper"
        balance_source = "polymarket_live" if mode == "live" else "paper_ledger"
        if live_status is not None:
            live_balance, live_allowance = self._extract_live_collateral(live_status)
            funder = str(live_status.get("funder") or getattr(self.settings, "polymarket_funder", "") or "")

        total_exposure = 0.0
        current_market_value = 0.0
        if rows:
            latest_prices = await self._latest_market_prices([str(row["market_id"]) for row in rows])
            pair_cycles = await self._current_pair_cycles([str(row["asset_symbol"] or "") for row in rows])
            for row in rows:
                total_exposure += float(row["exposure_usd"] or 0.0)
                current_price, _ = self._mark_price_for_position(
                    row,
                    latest_prices.get(str(row["market_id"])),
                    pair_cycles.get(str(row["asset_symbol"] or "")),
                )
                current_market_value += int(row["size"] or 0) * current_price

        unrealized_pnl = current_market_value - total_exposure
        if mode == "live":
            available_balance = float(live_balance or 0.0)
        else:
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
            mode=mode,
            balance_source=balance_source,
            funder=funder,
            live_balance=live_balance,
            live_allowance=live_allowance,
        )

    async def get_recent_signals(
        self,
        limit: int = 20,
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        cutoff_name: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._recent_payloads(
            "signals",
            limit=limit,
            asset=asset,
            tier=tier,
            strategy=strategy,
            cutoff_name=cutoff_name,
        )

    async def record_flow_analysis(self, analysis: FlowAnalysisPayload) -> None:
        payload = analysis.model_dump(mode="json")
        await self.db.execute(
            """
            INSERT INTO flow_analyses (
                id,
                signal_id,
                trade_group_id,
                market_id,
                cycle_slug,
                market_question,
                asset_symbol,
                asset_name,
                crypto_tier,
                window_minutes,
                dominant_direction,
                dominance_score,
                confidence,
                up_trade_count,
                down_trade_count,
                up_notional,
                down_notional,
                total_trades,
                total_notional,
                freshness_seconds,
                source_used,
                sample_count,
                last_trade_at,
                payload,
                created_at
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9,
                $10,
                $11,
                $12,
                $13,
                $14,
                $15,
                $16,
                $17,
                $18,
                $19,
                $20,
                $21,
                $22,
                $23,
                $24::jsonb,
                $25
            )
            """,
            analysis.flow_id,
            analysis.signal_id,
            analysis.trade_group_id,
            analysis.market_id,
            analysis.cycle_slug,
            analysis.market_question,
            analysis.asset_symbol,
            analysis.asset_name,
            analysis.crypto_tier,
            analysis.window_minutes,
            analysis.dominant_direction,
            analysis.dominance_score,
            analysis.confidence,
            analysis.up_trade_count,
            analysis.down_trade_count,
            analysis.up_notional,
            analysis.down_notional,
            analysis.total_trades,
            analysis.total_notional,
            analysis.freshness_seconds,
            analysis.source_used,
            analysis.sample_count,
            analysis.last_trade_at,
            _as_json(payload),
            analysis.updated_at,
        )

    async def get_recent_flow_analyses(
        self,
        limit: int = 48,
        *,
        asset: str | None = None,
        market_id: str | None = None,
        strategy: str | None = None,
        cutoff_name: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if asset:
            args.append(asset)
            clauses.append(f"asset_symbol = ${len(args)}")
        if market_id:
            args.append(market_id)
            clauses.append(f"market_id = ${len(args)}")
        args.append(limit)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self.db.fetch(
            f"""
            SELECT *
            FROM flow_analyses
            {where}
            ORDER BY created_at DESC
            LIMIT ${len(args)}
            """,
            *args,
        )
        return [self._decode_flow_record(row) for row in rows]

    async def get_recent_decisions(
        self,
        limit: int = 20,
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        cutoff_name: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._recent_payloads(
            "agent_decisions",
            limit=limit,
            asset=asset,
            tier=tier,
            strategy=strategy,
            cutoff_name=cutoff_name,
        )

    async def get_recent_orders(
        self,
        limit: int = 20,
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        trade_group_id: str | None = None,
        cutoff_name: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._recent_payloads(
            "paper_orders",
            limit=limit,
            asset=asset,
            tier=tier,
            strategy=strategy,
            trade_group_id=trade_group_id,
            cutoff_name=cutoff_name,
        )

    async def get_execution_risk_state(self, hours: int = 24) -> dict[str, Any]:
        window_start = datetime.now(UTC) - timedelta(hours=max(hours, 1))
        rows = await self.db.fetch(
            """
            SELECT payload, created_at
            FROM paper_orders
            WHERE created_at >= $1 AND status = ANY($2::text[])
            ORDER BY created_at ASC
            """,
            window_start,
            list(EXECUTED_ORDER_STATUSES),
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

    async def create_analysis_cutoff(
        self,
        cutoff_name: str,
        *,
        created_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        created_at = created_at or datetime.now(UTC)
        metadata = metadata or {}
        row = await self.db.fetchrow(
            """
            INSERT INTO analysis_cutoffs (cutoff_name, created_at, metadata)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (cutoff_name) DO UPDATE
            SET created_at = EXCLUDED.created_at,
                metadata = EXCLUDED.metadata
            RETURNING cutoff_name, created_at, metadata
            """,
            cutoff_name,
            created_at,
            _as_json(metadata),
        )
        assert row is not None
        return {
            "cutoff_name": str(row["cutoff_name"]),
            "created_at": row["created_at"],
            "metadata": self._json_value(row["metadata"]),
        }

    async def get_analysis_cutoffs(self) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            "SELECT cutoff_name, created_at, metadata FROM analysis_cutoffs ORDER BY created_at DESC, cutoff_name ASC"
        )
        return [
            {
                "cutoff_name": str(row["cutoff_name"]),
                "created_at": row["created_at"],
                "metadata": self._json_value(row["metadata"]),
            }
            for row in rows
        ]

    async def get_analysis_cutoff(self, cutoff_name: str) -> dict[str, Any] | None:
        row = await self.db.fetchrow(
            "SELECT cutoff_name, created_at, metadata FROM analysis_cutoffs WHERE cutoff_name = $1",
            cutoff_name,
        )
        if row is None:
            return None
        return {
            "cutoff_name": str(row["cutoff_name"]),
            "created_at": row["created_at"],
            "metadata": self._json_value(row["metadata"]),
        }

    async def get_recent_risk_events(
        self,
        limit: int = 20,
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        cutoff_name: str | None = None,
    ) -> list[dict[str, Any]]:
        since = await self._cutoff_timestamp(cutoff_name)
        if since is None:
            rows = await self.db.fetch(
                "SELECT agent, reason, payload, created_at FROM risk_events ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        else:
            rows = await self.db.fetch(
                "SELECT agent, reason, payload, created_at FROM risk_events WHERE created_at >= $1 ORDER BY created_at DESC LIMIT $2",
                since,
                limit,
            )
        events = await self._normalize_risk_payloads([self._decode_record(row) for row in rows])
        return [
            item
            for item in events
            if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy, allow_missing=True)
        ]

    async def get_risk_breakdown_report(
        self,
        hours: int = 24,
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        cutoff_name: str | None = None,
    ) -> dict[str, Any]:
        window_start = datetime.now(UTC) - timedelta(hours=max(hours, 1))
        cutoff = await self.get_analysis_cutoff(cutoff_name) if cutoff_name else None
        if cutoff is not None and cutoff["created_at"] > window_start:
            window_start = cutoff["created_at"]
        risk_rows = await self.db.fetch(
            "SELECT agent, reason, payload, created_at FROM risk_events WHERE created_at >= $1 ORDER BY created_at ASC",
            window_start,
        )
        risk_payloads = await self._normalize_risk_payloads([self._decode_record(row) for row in risk_rows])
        risk_filtered = [
            item
            for item in risk_payloads
            if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy, allow_missing=True)
        ]
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "window_hours": hours,
            "analysis_cutoff": {
                "cutoff_name": cutoff["cutoff_name"],
                "created_at": cutoff["created_at"].isoformat(),
                "metadata": cutoff["metadata"],
            }
            if cutoff
            else None,
            "asset_filter": asset or "",
            "tier_filter": tier or "",
            "strategy_filter": strategy or "",
            "total_events": len(risk_filtered),
            "by_reason": self._count_by_key(risk_filtered, "reason", limit=12),
            "by_strategy": self._count_by_key(risk_filtered, "strategy_id", limit=8, missing_label="unknown"),
            "by_strategy_reason": self._group_count_by_keys(
                risk_filtered,
                group_key="strategy_id",
                item_key="reason",
                group_limit=8,
                item_limit=6,
                missing_group_label="unknown",
            ),
        }

    async def get_recent_pipeline_telemetry(
        self,
        limit: int = 30,
        *,
        cutoff_name: str | None = None,
    ) -> list[dict[str, Any]]:
        since = await self._cutoff_timestamp(cutoff_name)
        if since is None:
            rows = await self.db.fetch(
                """
                SELECT agent, event_type, payload, created_at
                FROM pipeline_telemetry
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT agent, event_type, payload, created_at
                FROM pipeline_telemetry
                WHERE created_at >= $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                since,
                limit,
            )
        return [self._decode_pipeline_record(row) for row in rows]

    async def record_discovery_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = await self.db.fetchrow(
            """
            INSERT INTO market_discovery_runs (
                run_id,
                requested_limit,
                universe_count,
                crypto_classified_count,
                deterministic_passed_count,
                research_passed_count,
                claude_passed_count,
                operable_count,
                stage_counts,
                dropoff_counts,
                rejected_breakdown,
                cost_summary,
                scan_stats,
                metadata,
                created_at
            )
            VALUES (
                $1::uuid,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9::jsonb,
                $10::jsonb,
                $11::jsonb,
                $12::jsonb,
                $13::jsonb,
                $14::jsonb,
                $15
            )
            RETURNING run_id, requested_limit, universe_count, crypto_classified_count,
                      deterministic_passed_count, research_passed_count, claude_passed_count,
                      operable_count, stage_counts, dropoff_counts, rejected_breakdown,
                      cost_summary, scan_stats, metadata, created_at
            """,
            payload["run_id"],
            int(payload.get("requested_limit") or 0),
            int(payload.get("universe_count") or 0),
            int(payload.get("crypto_classified_count") or 0),
            int(payload.get("deterministic_passed_count") or 0),
            int(payload.get("research_passed_count") or 0),
            int(payload.get("claude_passed_count") or 0),
            int(payload.get("operable_count") or 0),
            _as_json(payload.get("stage_counts") or []),
            _as_json(payload.get("dropoff_counts") or []),
            _as_json(payload.get("rejected_breakdown") or {}),
            _as_json(payload.get("cost_summary") or {}),
            _as_json(payload.get("scan_stats") or {}),
            _as_json(payload.get("metadata") or {}),
            payload.get("created_at") or datetime.now(UTC),
        )
        assert row is not None
        return {
            "run_id": str(row["run_id"]),
            "requested_limit": int(row["requested_limit"] or 0),
            "universe_count": int(row["universe_count"] or 0),
            "crypto_classified_count": int(row["crypto_classified_count"] or 0),
            "deterministic_passed_count": int(row["deterministic_passed_count"] or 0),
            "research_passed_count": int(row["research_passed_count"] or 0),
            "claude_passed_count": int(row["claude_passed_count"] or 0),
            "operable_count": int(row["operable_count"] or 0),
            "stage_counts": self._json_value(row["stage_counts"]),
            "dropoff_counts": self._json_value(row["dropoff_counts"]),
            "rejected_breakdown": self._json_value(row["rejected_breakdown"]),
            "cost_summary": self._json_value(row["cost_summary"]),
            "scan_stats": self._json_value(row["scan_stats"]),
            "metadata": self._json_value(row["metadata"]),
            "created_at": row["created_at"],
        }

    async def record_discovery_candidates(self, candidates: list[dict[str, Any]]) -> None:
        if not candidates:
            return
        await self.db.execute(
            """
            INSERT INTO market_discovery_candidates (
                run_id,
                market_id,
                question,
                asset_symbol,
                asset_name,
                crypto_tier,
                market_kind,
                volume_24h,
                spread_bps,
                edge,
                confidence,
                liquidity_score,
                time_to_expiry_hours,
                strategy_id,
                direction,
                deterministic_pass,
                research_pass,
                claude_pass,
                verdict,
                score,
                reason,
                stage_payload,
                created_at
            )
            SELECT
                (item->>'run_id')::uuid,
                item->>'market_id',
                item->>'question',
                item->>'asset_symbol',
                item->>'asset_name',
                item->>'crypto_tier',
                item->>'market_kind',
                COALESCE((item->>'volume_24h')::double precision, 0),
                COALESCE((item->>'spread_bps')::double precision, 0),
                COALESCE((item->>'edge')::double precision, 0),
                COALESCE((item->>'confidence')::double precision, 0),
                COALESCE((item->>'liquidity_score')::double precision, 0),
                NULLIF(item->>'time_to_expiry_hours', '')::double precision,
                item->>'strategy_id',
                item->>'direction',
                COALESCE((item->>'deterministic_pass')::boolean, false),
                COALESCE((item->>'research_pass')::boolean, false),
                COALESCE((item->>'claude_pass')::boolean, false),
                item->>'verdict',
                COALESCE((item->>'score')::double precision, 0),
                item->>'reason',
                item,
                COALESCE((item->>'created_at')::timestamptz, NOW())
            FROM jsonb_array_elements($1::jsonb) AS item
            """,
            _as_json([candidate for candidate in candidates]),
        )

    async def get_latest_discovery_funnel(self, *, limit: int = 12) -> dict[str, Any] | None:
        run_row = await self.db.fetchrow(
            """
            SELECT run_id, requested_limit, universe_count, crypto_classified_count,
                   deterministic_passed_count, research_passed_count, claude_passed_count,
                   operable_count, stage_counts, dropoff_counts, rejected_breakdown,
                   cost_summary, scan_stats, metadata, created_at
            FROM market_discovery_runs
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        if run_row is None:
            return None
        candidate_rows = await self.db.fetch(
            """
            SELECT run_id, market_id, question, asset_symbol, asset_name, crypto_tier, market_kind,
                   volume_24h, spread_bps, edge, confidence, liquidity_score, time_to_expiry_hours,
                   strategy_id, direction, deterministic_pass, research_pass, claude_pass,
                   verdict, score, reason, stage_payload, created_at
            FROM market_discovery_candidates
            WHERE run_id = $1::uuid
            ORDER BY score DESC, created_at DESC
            LIMIT $2
            """,
            run_row["run_id"],
            limit,
        )
        candidates: list[dict[str, Any]] = []
        for row in candidate_rows:
            candidates.append(
                {
                    "run_id": str(row["run_id"]),
                    "market_id": row["market_id"],
                    "question": row["question"],
                    "asset_symbol": row["asset_symbol"],
                    "asset_name": row["asset_name"],
                    "crypto_tier": row["crypto_tier"],
                    "market_kind": row["market_kind"],
                    "volume_24h": float(row["volume_24h"] or 0.0),
                    "spread_bps": float(row["spread_bps"] or 0.0),
                    "edge": float(row["edge"] or 0.0),
                    "confidence": float(row["confidence"] or 0.0),
                    "liquidity_score": float(row["liquidity_score"] or 0.0),
                    "time_to_expiry_hours": _as_optional_float(row["time_to_expiry_hours"]),
                    "strategy_id": row["strategy_id"],
                    "direction": row["direction"],
                    "deterministic_pass": bool(row["deterministic_pass"]),
                    "research_pass": bool(row["research_pass"]),
                    "claude_pass": bool(row["claude_pass"]),
                    "verdict": row["verdict"],
                    "score": float(row["score"] or 0.0),
                    "reason": row["reason"],
                    "stage_payload": self._json_value(row["stage_payload"]),
                    "created_at": row["created_at"],
                }
            )
        return {
            "run": {
                "run_id": str(run_row["run_id"]),
                "requested_limit": int(run_row["requested_limit"] or 0),
                "universe_count": int(run_row["universe_count"] or 0),
                "crypto_classified_count": int(run_row["crypto_classified_count"] or 0),
                "deterministic_passed_count": int(run_row["deterministic_passed_count"] or 0),
                "research_passed_count": int(run_row["research_passed_count"] or 0),
                "claude_passed_count": int(run_row["claude_passed_count"] or 0),
                "operable_count": int(run_row["operable_count"] or 0),
                "stage_counts": self._json_value(run_row["stage_counts"]),
                "dropoff_counts": self._json_value(run_row["dropoff_counts"]),
                "rejected_breakdown": self._json_value(run_row["rejected_breakdown"]),
                "cost_summary": self._json_value(run_row["cost_summary"]),
                "scan_stats": self._json_value(run_row["scan_stats"]),
                "metadata": self._json_value(run_row["metadata"]),
                "created_at": run_row["created_at"],
            },
            "candidates": candidates,
        }

    async def record_weather_copytrade_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = await self.db.fetchrow(
            """
            INSERT INTO weather_copytrade_runs (
                run_id,
                category,
                leaderboard_limit,
                universe_count,
                shortlisted_count,
                selected_count,
                selected_proxy_wallet,
                selected_user_name,
                candidate_count,
                stage_counts,
                rejected_breakdown,
                model_summary,
                selection_summary,
                scan_stats,
                metadata,
                created_at
            )
            VALUES (
                $1::uuid,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9,
                $10::jsonb,
                $11::jsonb,
                $12::jsonb,
                $13::jsonb,
                $14::jsonb,
                $15::jsonb,
                $16
            )
            RETURNING *
            """,
            payload["run_id"],
            payload.get("category", "WEATHER"),
            int(payload.get("leaderboard_limit", 0) or 0),
            int(payload.get("universe_count", 0) or 0),
            int(payload.get("shortlisted_count", 0) or 0),
            int(payload.get("selected_count", 0) or 0),
            str(payload.get("selected_proxy_wallet") or ""),
            str(payload.get("selected_user_name") or ""),
            int(payload.get("candidate_count", 0) or 0),
            _as_json(payload.get("stage_counts", [])),
            _as_json(payload.get("rejected_breakdown", {})),
            _as_json(payload.get("model_summary", {})),
            _as_json(payload.get("selection_summary", {})),
            _as_json(payload.get("scan_stats", {})),
            _as_json(payload.get("metadata", {})),
            payload.get("created_at", datetime.now(UTC)),
        )
        assert row is not None
        return self._normalize_weather_copytrade_payload(dict(row))

    async def record_weather_copytrade_candidates(self, candidates: list[dict[str, Any]]) -> None:
        if not candidates:
            return
        await self.db.execute(
            """
            INSERT INTO weather_copytrade_candidates (
                run_id,
                rank,
                proxy_wallet,
                user_name,
                verified_badge,
                profile,
                metrics,
                score,
                rationale,
                passed,
                reject_reason,
                selected,
                created_at
            )
            SELECT
                payload.run_id::uuid,
                payload.rank,
                payload.proxy_wallet,
                payload.user_name,
                payload.verified_badge,
                payload.profile::jsonb,
                payload.metrics::jsonb,
                payload.score,
                payload.rationale,
                payload.passed,
                payload.reject_reason,
                payload.selected,
                payload.created_at
            FROM jsonb_to_recordset($1::jsonb) AS payload(
                run_id TEXT,
                rank INTEGER,
                proxy_wallet TEXT,
                user_name TEXT,
                verified_badge BOOLEAN,
                profile TEXT,
                metrics TEXT,
                score DOUBLE PRECISION,
                rationale TEXT,
                passed BOOLEAN,
                reject_reason TEXT,
                selected BOOLEAN,
                created_at TIMESTAMPTZ
            )
            """,
            _as_json(candidates),
        )

    async def upsert_weather_copytrade_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = await self.db.fetchrow(
            """
            INSERT INTO weather_copytrade_state (
                category,
                run_id,
                selected_proxy_wallet,
                selected_user_name,
                selected_profile,
                selection,
                report,
                approved,
                active,
                paused,
                approved_at,
                activated_at,
                last_trade_seen_at,
                last_trade_seen_hash,
                processed_trade_hashes,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                $1,
                $2::uuid,
                $3,
                $4,
                $5::jsonb,
                $6::jsonb,
                $7::jsonb,
                $8,
                $9,
                $10,
                $11,
                $12,
                $13,
                $14,
                $15::jsonb,
                $16::jsonb,
                $17,
                $18
            )
            ON CONFLICT (category) DO UPDATE
            SET run_id = EXCLUDED.run_id,
                selected_proxy_wallet = EXCLUDED.selected_proxy_wallet,
                selected_user_name = EXCLUDED.selected_user_name,
                selected_profile = EXCLUDED.selected_profile,
                selection = EXCLUDED.selection,
                report = EXCLUDED.report,
                approved = EXCLUDED.approved,
                active = EXCLUDED.active,
                paused = EXCLUDED.paused,
                approved_at = EXCLUDED.approved_at,
                activated_at = EXCLUDED.activated_at,
                last_trade_seen_at = EXCLUDED.last_trade_seen_at,
                last_trade_seen_hash = EXCLUDED.last_trade_seen_hash,
                processed_trade_hashes = EXCLUDED.processed_trade_hashes,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            str(payload.get("category") or "WEATHER"),
            payload.get("run_id"),
            str(payload.get("selected_proxy_wallet") or ""),
            str(payload.get("selected_user_name") or ""),
            _as_json(payload.get("selected_profile", {})),
            _as_json(payload.get("selection", {})),
            _as_json(payload.get("report", {})),
            bool(payload.get("approved", False)),
            bool(payload.get("active", False)),
            bool(payload.get("paused", True)),
            payload.get("approved_at"),
            payload.get("activated_at"),
            payload.get("last_trade_seen_at"),
            str(payload.get("last_trade_seen_hash") or ""),
            _as_json(payload.get("processed_trade_hashes", [])),
            _as_json(payload.get("metadata", {})),
            payload.get("created_at", datetime.now(UTC)),
            payload.get("updated_at", datetime.now(UTC)),
        )
        assert row is not None
        return self._normalize_weather_copytrade_payload(dict(row))

    async def get_weather_copytrade_state(self, category: str = "WEATHER") -> dict[str, Any] | None:
        row = await self.db.fetchrow(
            """
            SELECT *
            FROM weather_copytrade_state
            WHERE category = $1
            """,
            category,
        )
        if row is None:
            return None
        return self._normalize_weather_copytrade_payload(dict(row))

    async def upsert_weather_copytrade_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = await self.db.fetchrow(
            """
            INSERT INTO weather_copytrade_profiles (
                category,
                run_id,
                proxy_wallet,
                user_name,
                profile,
                selection_snapshot,
                approved,
                active,
                paused,
                approved_at,
                activated_at,
                last_trade_seen_at,
                last_trade_seen_hash,
                processed_trade_hashes,
                metrics_snapshot,
                performance_snapshot,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                $1,
                $2::uuid,
                $3,
                $4,
                $5::jsonb,
                $6::jsonb,
                $7,
                $8,
                $9,
                $10,
                $11,
                $12,
                $13,
                $14::jsonb,
                $15::jsonb,
                $16::jsonb,
                $17::jsonb,
                $18,
                $19
            )
            ON CONFLICT (category, proxy_wallet) DO UPDATE
            SET run_id = EXCLUDED.run_id,
                user_name = EXCLUDED.user_name,
                profile = EXCLUDED.profile,
                selection_snapshot = EXCLUDED.selection_snapshot,
                approved = EXCLUDED.approved,
                active = EXCLUDED.active,
                paused = EXCLUDED.paused,
                approved_at = EXCLUDED.approved_at,
                activated_at = EXCLUDED.activated_at,
                last_trade_seen_at = EXCLUDED.last_trade_seen_at,
                last_trade_seen_hash = EXCLUDED.last_trade_seen_hash,
                processed_trade_hashes = EXCLUDED.processed_trade_hashes,
                metrics_snapshot = EXCLUDED.metrics_snapshot,
                performance_snapshot = EXCLUDED.performance_snapshot,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            str(payload.get("category") or "WEATHER"),
            payload.get("run_id"),
            str(payload.get("proxy_wallet") or ""),
            str(payload.get("user_name") or ""),
            _as_json(payload.get("profile", {})),
            _as_json(payload.get("selection_snapshot", {})),
            bool(payload.get("approved", False)),
            bool(payload.get("active", False)),
            bool(payload.get("paused", True)),
            payload.get("approved_at"),
            payload.get("activated_at"),
            payload.get("last_trade_seen_at"),
            str(payload.get("last_trade_seen_hash") or ""),
            _as_json(payload.get("processed_trade_hashes", [])),
            _as_json(payload.get("metrics_snapshot", {})),
            _as_json(payload.get("performance_snapshot", {})),
            _as_json(payload.get("metadata", {})),
            payload.get("created_at", datetime.now(UTC)),
            payload.get("updated_at", datetime.now(UTC)),
        )
        assert row is not None
        return self._normalize_weather_copytrade_payload(dict(row))

    async def get_weather_copytrade_profile(self, category: str, proxy_wallet: str) -> dict[str, Any] | None:
        row = await self.db.fetchrow(
            """
            SELECT *
            FROM weather_copytrade_profiles
            WHERE category = $1 AND proxy_wallet = $2
            """,
            category,
            proxy_wallet,
        )
        if row is None:
            return None
        return self._normalize_weather_copytrade_payload(dict(row))

    async def list_weather_copytrade_profiles(
        self,
        *,
        category: str = "WEATHER",
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        if active_only:
            rows = await self.db.fetch(
                """
                SELECT *
                FROM weather_copytrade_profiles
                WHERE category = $1 AND active = TRUE
                ORDER BY paused ASC, approved_at DESC NULLS LAST, updated_at DESC
                """,
                category,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT *
                FROM weather_copytrade_profiles
                WHERE category = $1
                ORDER BY active DESC, paused ASC, approved_at DESC NULLS LAST, updated_at DESC
                """,
                category,
            )
        return [self._normalize_weather_copytrade_payload(dict(row)) for row in rows]

    async def delete_weather_copytrade_profile(self, *, category: str, proxy_wallet: str) -> None:
        await self.db.execute(
            """
            DELETE FROM weather_copytrade_profiles
            WHERE category = $1 AND proxy_wallet = $2
            """,
            category,
            proxy_wallet,
        )

    async def get_latest_weather_copytrade_summary(self, *, limit: int = 12) -> dict[str, Any] | None:
        run_row = await self.db.fetchrow(
            """
            SELECT run_id, category, leaderboard_limit, universe_count, shortlisted_count, selected_count,
                   selected_proxy_wallet, selected_user_name, candidate_count, stage_counts,
                   rejected_breakdown, model_summary, selection_summary, scan_stats, metadata, created_at
            FROM weather_copytrade_runs
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        if run_row is None:
            state = await self.get_weather_copytrade_state()
            profiles = await self.list_weather_copytrade_profiles(category="WEATHER")
            return {
                "run": None,
                "candidates": [],
                "state": state,
                "profiles": profiles,
                "report": (state or {}).get("report") if state else None,
                "selection_summary": {},
                "scan_stats": {},
                "metadata": {},
                "portfolio_constraints": {},
            }
        candidate_rows = await self.db.fetch(
            """
            SELECT run_id, rank, proxy_wallet, user_name, verified_badge, profile, metrics, score, rationale, passed, reject_reason, selected, created_at
            FROM weather_copytrade_candidates
            WHERE run_id = $1::uuid
            ORDER BY score DESC, rank ASC, created_at DESC
            LIMIT $2
            """,
            run_row["run_id"],
            limit,
        )
        state = await self.get_weather_copytrade_state(str(run_row["category"] or "WEATHER"))
        profiles = await self.list_weather_copytrade_profiles(category=str(run_row["category"] or "WEATHER"))
        run = self._normalize_weather_copytrade_payload(dict(run_row))
        candidates = [self._normalize_weather_copytrade_payload(dict(row)) for row in candidate_rows]
        report = (state or {}).get("report") or run.get("model_summary") or {}
        return {
            "run": run,
            "candidates": candidates,
            "state": state,
            "profiles": profiles,
            "report": report,
            "selection_summary": run.get("selection_summary") or {},
            "scan_stats": run.get("scan_stats") or {},
            "metadata": run.get("metadata") or {},
            "portfolio_constraints": {},
        }

    async def get_recent_weather_copytrade_runs(self, *, limit: int = 12) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            """
            SELECT run_id, category, leaderboard_limit, universe_count, shortlisted_count, selected_count,
                   selected_proxy_wallet, selected_user_name, candidate_count, stage_counts,
                   rejected_breakdown, model_summary, selection_summary, scan_stats, metadata, created_at
            FROM weather_copytrade_runs
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [self._normalize_weather_copytrade_payload(dict(row)) for row in rows]

    async def get_recent_settlement_events(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            """
            SELECT payload, created_at
            FROM settlement_events
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
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
        return await self.metrics_overview_since(cutoff_name=None)

    async def metrics_overview_since(self, cutoff_name: str | None = None) -> dict[str, Any]:
        since = await self._cutoff_timestamp(cutoff_name)
        signals = await self._count_rows_since("signals", since)
        decisions = await self._count_rows_since("agent_decisions", since)
        orders = await self._count_rows_since("paper_orders", since)
        risk_events = await self._count_rows_since("risk_events", since)
        signal_rows = await self.db.fetch(
            "SELECT payload, created_at FROM signals" if since is None else "SELECT payload, created_at FROM signals WHERE created_at >= $1",
            *(tuple() if since is None else (since,)),
        )
        order_rows = await self.db.fetch(
            "SELECT payload, created_at FROM paper_orders" if since is None else "SELECT payload, created_at FROM paper_orders WHERE created_at >= $1",
            *(tuple() if since is None else (since,)),
        )
        pending_pair_orders = await self.db.fetchrow(
            "SELECT COUNT(*) AS count FROM pending_pair_orders WHERE status = 'pending'"
        )
        pipeline_since = max(filter(None, [since, datetime.now(UTC) - timedelta(minutes=15)]), default=None)
        pipeline_rows = await self.db.fetch(
            """
            SELECT agent, event_type, payload, created_at
            FROM pipeline_telemetry
            WHERE created_at >= $1
            ORDER BY created_at ASC
            """,
            pipeline_since or (datetime.now(UTC) - timedelta(minutes=15)),
        )
        pipeline_events = [self._decode_pipeline_record(row) for row in pipeline_rows]
        signal_payloads = [self._decode_record(row) for row in signal_rows]
        order_payloads = [self._decode_record(row) for row in order_rows]
        portfolio = await self.get_portfolio_summary()
        flow_summary = self._summarize_pipeline_events(pipeline_events, window_minutes=15)
        return {
            "analysis_cutoff": await self.get_analysis_cutoff(cutoff_name) if cutoff_name else None,
            "signals": int(signals["count"] or 0),
            "decisions": int(decisions["count"] or 0),
            "orders": int(orders["count"] or 0),
            "risk_events": int(risk_events["count"] or 0),
            "pending_pair_orders": int(pending_pair_orders["count"] or 0),
            "portfolio": portfolio.model_dump(),
            "flow_summary": flow_summary,
            "strategy_breakdown": self._strategy_breakdown(signal_payloads, order_payloads),
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
        trade_group_id: str | None = None,
        cutoff_name: str | None = None,
    ) -> dict[str, Any]:
        window_start = datetime.now(UTC) - timedelta(hours=max(hours, 1))
        cutoff = await self.get_analysis_cutoff(cutoff_name) if cutoff_name else None
        if cutoff is not None and cutoff["created_at"] > window_start:
            window_start = cutoff["created_at"]
        portfolio = await self.get_portfolio_summary()
        signal_rows = await self.db.fetch("SELECT payload, created_at FROM signals WHERE created_at >= $1 ORDER BY created_at ASC", window_start)
        decision_rows = await self.db.fetch(
            "SELECT payload, created_at FROM agent_decisions WHERE created_at >= $1 ORDER BY created_at ASC",
            window_start,
        )
        order_rows = await self.db.fetch(
            "SELECT payload, created_at, updated_at FROM paper_orders WHERE created_at >= $1 ORDER BY created_at ASC",
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
        risk_payloads = await self._normalize_risk_payloads([self._decode_record(row) for row in risk_event_rows])

        signals_filtered = [
            item
            for item in signals_payloads
            if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy, trade_group_id=trade_group_id)
        ]
        decisions_filtered = [
            item
            for item in decisions_payloads
            if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy, trade_group_id=trade_group_id)
        ]
        orders_filtered = [
            item
            for item in orders_payloads
            if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy, trade_group_id=trade_group_id)
        ]
        pending_pair_orders = [
            item
            for item in await self.list_pending_pair_orders()
            if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy, trade_group_id=trade_group_id)
        ]
        risk_filtered = [
            item
            for item in risk_payloads
            if self._matches_filters(
                item,
                asset=asset,
                tier=tier,
                strategy=strategy,
                trade_group_id=trade_group_id,
                allow_missing=True,
            )
        ]

        signals = len(signals_filtered)
        decisions = len(decisions_filtered)
        orders = len(orders_filtered)
        risk_events = len(risk_filtered)
        total_cost = sum(float(row["cost_usd"] or 0.0) for row in llm_cost_rows)
        open_positions = await self.get_open_positions()
        open_positions = [
            item
            for item in open_positions
            if self._matches_filters(item, asset=asset, tier=tier, strategy=strategy, trade_group_id=trade_group_id)
        ]
        positive_positions = sum(1 for position in open_positions if float(position["unrealized_pnl"]) > 0)

        signal_metrics = [self._signal_metrics(item) for item in signals_filtered]
        signal_edges = [item["edge"] for item in signal_metrics]
        signal_confidences = [item["confidence"] for item in signal_metrics]
        tracked_orders = [item for item in orders_filtered if str(item.get("status")) in EXECUTED_ORDER_STATUSES]
        filled_orders = [item for item in orders_filtered if str(item.get("status")) in FILLED_ORDER_STATUSES]
        live_orders = [item for item in tracked_orders if str(item.get("status")).startswith("live_")]
        live_submitted_orders = [item for item in tracked_orders if str(item.get("status")) == "live_submitted"]
        live_filled_orders = [item for item in tracked_orders if str(item.get("status")) == "live_filled"]
        paper_orders = [item for item in tracked_orders if str(item.get("status")).startswith("simulated")]
        entry_orders = [item for item in tracked_orders if str(item.get("action") or "entry") in {"entry", "scale_in"}]
        exit_orders = [item for item in filled_orders if str(item.get("action") or "") in {"scale_out", "close"}]
        total_notional = sum(float(item.get("notional_usd") or 0.0) for item in tracked_orders)
        avg_notional = total_notional / len(tracked_orders) if tracked_orders else 0.0
        realized_pnl = sum(float(item.get("realized_pnl_usd") or 0.0) for item in exit_orders)
        win_rate = (
            round(sum(1 for item in exit_orders if float(item.get("realized_pnl_usd") or 0.0) > 0) / len(exit_orders), 4)
            if exit_orders
            else 0.0
        )

        order_count_by_market: dict[str, int] = {}
        for item in tracked_orders:
            market_id = str(item.get("market_id") or "")
            if market_id:
                order_count_by_market[market_id] = order_count_by_market.get(market_id, 0) + 1

        top_markets = self._top_markets(signals_filtered, order_count_by_market)
        risk_breakdown = self._count_by_key(risk_filtered, "reason", limit=8)
        risk_breakdown_by_strategy = self._group_count_by_keys(
            risk_filtered,
            group_key="strategy_id",
            item_key="reason",
            group_limit=6,
            item_limit=5,
            missing_group_label="unknown",
        )
        asset_breakdown = self._count_by_key(signals_filtered, "asset_symbol", limit=8)
        tier_breakdown = self._count_by_key(signals_filtered, "crypto_tier", limit=3)
        strategy_breakdown = self._strategy_breakdown(signals_filtered, tracked_orders)
        regime_breakdown = self._count_by_key(signals_filtered, "regime", limit=6)
        exit_reason_breakdown = self._count_by_key(exit_orders, "exit_reason", limit=6)
        news_breakdown = self._news_breakdown(signals_filtered)
        news_provider_breakdown = self._news_provider_breakdown(signals_filtered)
        news_fallback_breakdown = self._news_fallback_breakdown(signals_filtered)
        last_news_provider = self._last_news_provider(signals_filtered)
        pair_trade_summary = self._pair_trade_summary(
            tracked_orders,
            pending_count=len(pending_pair_orders),
        )
        order_lifecycle_summary = self._order_lifecycle_summary(orders_filtered, pending_pair_orders)
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
            "analysis_cutoff": {
                "cutoff_name": cutoff["cutoff_name"],
                "created_at": cutoff["created_at"].isoformat(),
                "metadata": cutoff["metadata"],
            }
            if cutoff
            else None,
            "asset_filter": asset or "",
            "tier_filter": tier or "",
            "strategy_filter": strategy or "",
            "summary": {
                "signals": signals,
                "decisions": decisions,
                "orders": orders,
                "paper_orders": len(paper_orders),
                "live_orders": len(live_orders),
                "live_submitted_orders": len(live_submitted_orders),
                "live_filled_orders": len(live_filled_orders),
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
            "risk_breakdown_by_strategy": risk_breakdown_by_strategy,
            "asset_breakdown": asset_breakdown,
            "tier_breakdown": tier_breakdown,
            "strategy_breakdown": strategy_breakdown,
            "regime_breakdown": regime_breakdown,
            "exit_reason_breakdown": exit_reason_breakdown,
            "news_breakdown": news_breakdown,
            "news_provider_breakdown": news_provider_breakdown,
            "news_fallback_breakdown": news_fallback_breakdown,
            "last_news_provider": last_news_provider,
            "pair_trade_summary": pair_trade_summary,
            "order_lifecycle_summary": order_lifecycle_summary,
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

    async def upsert_pair_cycle(self, state: PairCycleStatePayload) -> None:
        payload = state.model_dump(mode="json")
        await self.db.execute(
            """
            INSERT INTO pair_cycles (
                asset_symbol, asset_name, crypto_tier, cycle_slug, cycle_start, market_id, market_question,
                token_id_yes, token_id_no, price_yes, price_no, status, side_counts, max_buy_counts_per_side,
                last_signal_direction, last_signal_at, last_quote_at, predictor_state, metadata, created_at, updated_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12, $13::jsonb, $14,
                $15, $16, $17, $18::jsonb, $19::jsonb, $20, $21
            )
            ON CONFLICT (asset_symbol) DO UPDATE
            SET asset_name = EXCLUDED.asset_name,
                crypto_tier = EXCLUDED.crypto_tier,
                cycle_slug = EXCLUDED.cycle_slug,
                cycle_start = EXCLUDED.cycle_start,
                market_id = EXCLUDED.market_id,
                market_question = EXCLUDED.market_question,
                token_id_yes = EXCLUDED.token_id_yes,
                token_id_no = EXCLUDED.token_id_no,
                price_yes = EXCLUDED.price_yes,
                price_no = EXCLUDED.price_no,
                status = EXCLUDED.status,
                side_counts = EXCLUDED.side_counts,
                max_buy_counts_per_side = EXCLUDED.max_buy_counts_per_side,
                last_signal_direction = EXCLUDED.last_signal_direction,
                last_signal_at = EXCLUDED.last_signal_at,
                last_quote_at = EXCLUDED.last_quote_at,
                predictor_state = EXCLUDED.predictor_state,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            """,
            state.asset_symbol,
            state.asset_name,
            state.crypto_tier,
            state.cycle_slug,
            state.cycle_start,
            state.market_id,
            state.market_question,
            state.token_id_yes,
            state.token_id_no,
            state.price_yes,
            state.price_no,
            state.status,
            _as_json(payload["side_counts"]),
            state.max_buy_counts_per_side,
            state.last_signal_direction,
            state.last_signal_at,
            state.last_quote_at,
            _as_json(payload["predictor_state"]),
            _as_json(payload["metadata"]),
            state.updated_at,
            state.updated_at,
        )

    async def get_pair_cycle(self, asset_symbol: str) -> dict[str, Any] | None:
        row = await self.db.fetchrow(
            """
            SELECT asset_symbol, asset_name, crypto_tier, cycle_slug, cycle_start, market_id, market_question,
                   token_id_yes, token_id_no, price_yes, price_no, status, side_counts, max_buy_counts_per_side,
                   last_signal_direction, last_signal_at, last_quote_at, predictor_state, metadata, updated_at
            FROM pair_cycles
            WHERE asset_symbol = $1
            """,
            asset_symbol,
        )
        if row is None:
            return None
        return {
            "asset_symbol": row["asset_symbol"],
            "asset_name": row["asset_name"],
            "crypto_tier": row["crypto_tier"],
            "cycle_slug": row["cycle_slug"],
            "cycle_start": row["cycle_start"],
            "market_id": row["market_id"],
            "market_question": row["market_question"],
            "token_id_yes": row["token_id_yes"],
            "token_id_no": row["token_id_no"],
            "price_yes": float(row["price_yes"] or 0.0),
            "price_no": float(row["price_no"] or 0.0),
            "status": row["status"],
            "side_counts": self._json_value(row["side_counts"]),
            "max_buy_counts_per_side": int(row["max_buy_counts_per_side"] or 0),
            "last_signal_direction": row["last_signal_direction"],
            "last_signal_at": row["last_signal_at"],
            "last_quote_at": row["last_quote_at"],
            "predictor_state": self._json_value(row["predictor_state"]),
            "metadata": self._json_value(row["metadata"]),
            "updated_at": row["updated_at"],
        }

    async def upsert_pending_pair_order(self, pending: PendingPairOrderPayload) -> None:
        await self.db.execute(
            """
            INSERT INTO pending_pair_orders (
                pending_order_id, trade_group_id, signal_id, cycle_slug, market_id, market_question, asset_symbol,
                crypto_tier, strategy_id, position_key, token_id, direction, leg_role, size, target_price,
                reference_price, exchange_order_id, submission_status, submission_created_at, status, reason, payload,
                created_at, updated_at
            )
            VALUES (
                $1::uuid, $2, $3::uuid, $4, $5, $6, $7,
                $8, $9, $10, $11, $12, $13, $14, $15,
                $16, $17, $18, $19, $20, $21, $22::jsonb, $23, $24
            )
            ON CONFLICT (pending_order_id) DO UPDATE
            SET trade_group_id = EXCLUDED.trade_group_id,
                signal_id = EXCLUDED.signal_id,
                cycle_slug = EXCLUDED.cycle_slug,
                market_id = EXCLUDED.market_id,
                market_question = EXCLUDED.market_question,
                asset_symbol = EXCLUDED.asset_symbol,
                crypto_tier = EXCLUDED.crypto_tier,
                strategy_id = EXCLUDED.strategy_id,
                position_key = EXCLUDED.position_key,
                token_id = EXCLUDED.token_id,
                direction = EXCLUDED.direction,
                leg_role = EXCLUDED.leg_role,
                size = EXCLUDED.size,
                target_price = EXCLUDED.target_price,
                reference_price = EXCLUDED.reference_price,
                exchange_order_id = EXCLUDED.exchange_order_id,
                submission_status = EXCLUDED.submission_status,
                submission_created_at = EXCLUDED.submission_created_at,
                status = EXCLUDED.status,
                reason = EXCLUDED.reason,
                payload = EXCLUDED.payload,
                updated_at = EXCLUDED.updated_at
            """,
            pending.pending_order_id,
            pending.trade_group_id,
            pending.signal_id,
            pending.cycle_slug,
            pending.market_id,
            pending.market_question,
            pending.asset_symbol,
            pending.crypto_tier,
            pending.strategy_id,
            pending.position_key,
            pending.token_id,
            pending.direction,
            pending.leg_role,
            pending.size,
            pending.target_price,
            pending.reference_price,
            pending.exchange_order_id,
            pending.submission_status,
            pending.submission_created_at,
            pending.status,
            pending.reason,
            _as_json(pending.model_dump(mode="json")),
            pending.created_at,
            pending.updated_at,
        )

    async def list_pending_pair_orders(self, status: str = "pending") -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            """
            SELECT pending_order_id, trade_group_id, signal_id, cycle_slug, market_id, market_question, asset_symbol,
                   crypto_tier, strategy_id, position_key, token_id, direction, leg_role, size, target_price,
                   reference_price, exchange_order_id, submission_status, submission_created_at, status, reason, payload,
                   created_at, updated_at
            FROM pending_pair_orders
            WHERE status = $1
            ORDER BY created_at ASC
            """,
            status,
        )
        pending_orders: list[dict[str, Any]] = []
        for row in rows:
            payload = self._json_value(row["payload"])
            pending_orders.append(
                {
                    "pending_order_id": str(row["pending_order_id"]),
                    "trade_group_id": row["trade_group_id"],
                    "signal_id": str(row["signal_id"]),
                    "cycle_slug": row["cycle_slug"],
                    "market_id": row["market_id"],
                    "market_question": row["market_question"],
                    "asset_symbol": row["asset_symbol"],
                    "crypto_tier": row["crypto_tier"],
                    "strategy_id": row["strategy_id"],
                    "position_key": row["position_key"],
                    "token_id": row["token_id"],
                    "direction": row["direction"],
                    "leg_role": row["leg_role"],
                    "size": int(row["size"] or 0),
                    "target_price": float(row["target_price"] or 0.0),
                    "reference_price": float(row["reference_price"] or 0.0),
                    "exchange_order_id": row["exchange_order_id"],
                    "submission_status": row["submission_status"],
                    "submission_created_at": row["submission_created_at"],
                    "status": row["status"],
                    "reason": row["reason"],
                    "payload": payload,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return pending_orders

    async def resolve_pending_pair_order(
        self,
        pending_order_id: str,
        *,
        status: str,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        existing = await self.db.fetchrow(
            "SELECT payload FROM pending_pair_orders WHERE pending_order_id = $1::uuid",
            pending_order_id,
        )
        merged_payload = self._json_value(existing["payload"]) if existing is not None else {}
        if payload:
            merged_payload.update(payload)
        await self.db.execute(
            """
            UPDATE pending_pair_orders
            SET status = $2,
                reason = $3,
                payload = $4::jsonb,
                updated_at = $5
            WHERE pending_order_id = $1::uuid
            """,
            pending_order_id,
            status,
            reason,
            _as_json(merged_payload),
            datetime.now(UTC),
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
                trigger_source,
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
                trigger_source=str(row["trigger_source"] or ""),
                source=str(row["source"] or "system"),
            ).model_dump(mode="json")
            for row in reversed(rows)
        ]

    async def get_open_positions(self) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            """
            SELECT market_id, position_key, token_id, market_question, direction, size, average_price, exposure_usd, updated_at
                 , asset_symbol, crypto_tier, strategy_id, regime, trade_group_id, cycle_slug, leg_role
                 , take_profit_price, stop_loss_price, time_stop_minutes
                 , opened_at, scaled_out_count
            FROM positions
            WHERE size > 0
            ORDER BY updated_at DESC
            """
        )
        latest_prices = await self._latest_market_prices([str(row["market_id"]) for row in rows])
        pair_cycles = await self._current_pair_cycles([str(row["asset_symbol"] or "") for row in rows])
        positions: list[dict[str, Any]] = []
        for row in rows:
            current_price, latest_spread_bps = self._mark_price_for_position(
                row,
                latest_prices.get(str(row["market_id"])),
                pair_cycles.get(str(row["asset_symbol"] or "")),
            )
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
                    "trade_group_id": row["trade_group_id"],
                    "cycle_slug": row["cycle_slug"],
                    "leg_role": row["leg_role"],
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
        trade_group_id: str | None = None,
        cutoff_name: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        since = await self._cutoff_timestamp(cutoff_name)
        if since is not None:
            args.append(since)
            clauses.append(f"created_at >= ${len(args)}")
        if asset:
            args.append(asset.upper())
            clauses.append(f"payload->>'asset_symbol' = ${len(args)}")
        if tier:
            args.append(tier)
            clauses.append(f"payload->>'crypto_tier' = ${len(args)}")
        if strategy:
            args.append(strategy)
            clauses.append(f"payload->>'strategy_id' = ${len(args)}")
        if trade_group_id:
            args.append(trade_group_id)
            clauses.append(f"payload->>'trade_group_id' = ${len(args)}")
        args.append(limit)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT payload, created_at FROM {table} {where} ORDER BY created_at DESC LIMIT ${len(args)}"
        rows = await self.db.fetch(query, *args)
        return [self._decode_record(row) for row in rows]

    async def _cutoff_timestamp(self, cutoff_name: str | None) -> datetime | None:
        if not cutoff_name:
            return None
        cutoff = await self.get_analysis_cutoff(cutoff_name)
        if cutoff is None:
            return None
        return cutoff["created_at"]

    async def _count_rows_since(self, table: str, since: datetime | None) -> asyncpg.Record | None:
        if since is None:
            return await self.db.fetchrow(f"SELECT COUNT(*) AS count FROM {table}")
        return await self.db.fetchrow(f"SELECT COUNT(*) AS count FROM {table} WHERE created_at >= $1", since)

    @staticmethod
    def _matches_filters(
        payload: dict[str, Any],
        *,
        asset: str | None = None,
        tier: str | None = None,
        strategy: str | None = None,
        trade_group_id: str | None = None,
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
        if trade_group_id:
            trade_group_value = str(payload.get("trade_group_id") or "").strip()
            if not trade_group_value:
                if not allow_missing:
                    return False
            elif trade_group_value != trade_group_id:
                return False
        return True

    @staticmethod
    def _count_by_key(
        items: list[dict[str, Any]],
        key: str,
        *,
        limit: int,
        missing_label: str = "unknown",
    ) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for item in items:
            value = str(item.get(key) or "").strip()
            if not value:
                value = missing_label
            counts[value] = counts.get(value, 0) + 1
        ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        return [{"label": label, "count": count} for label, count in ordered[:limit]]

    @classmethod
    def _group_count_by_keys(
        cls,
        items: list[dict[str, Any]],
        *,
        group_key: str,
        item_key: str,
        group_limit: int,
        item_limit: int,
        missing_group_label: str = "unknown",
        missing_item_label: str = "unknown",
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            group_label = str(item.get(group_key) or "").strip() or missing_group_label
            grouped.setdefault(group_label, []).append(item)
        ordered_groups = sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0]))[:group_limit]
        return [
            {
                "label": label,
                "count": len(group_items),
                "reasons": cls._count_by_key(
                    group_items,
                    item_key,
                    limit=item_limit,
                    missing_label=missing_item_label,
                ),
            }
            for label, group_items in ordered_groups
        ]

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
    def _order_timestamp(item: dict[str, Any], key: str) -> datetime | None:
        value = item.get(key)
        if isinstance(value, datetime):
            return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        if value in (None, ""):
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            return None

    @classmethod
    def _order_lifecycle_summary(
        cls,
        orders: list[dict[str, Any]],
        pending_pair_orders: list[dict[str, Any]],
    ) -> dict[str, Any]:
        live_submitted = [item for item in orders if str(item.get("status")) == "live_submitted"]
        live_filled = [item for item in orders if str(item.get("status")) == "live_filled"]
        blocked = [item for item in orders if str(item.get("status")) == "blocked"]
        cancelled_like = [item for item in orders if str(item.get("status")) in {"cancelled", "expired"}]
        pending_cancelled = [
            item for item in pending_pair_orders if str(item.get("status") or "") in {"cancelled", "expired"}
        ]

        fill_latencies: list[float] = []
        open_durations: list[float] = []
        now = datetime.now(UTC)
        for item in live_filled:
            created_at = cls._order_timestamp(item, "created_at")
            updated_at = cls._order_timestamp(item, "updated_at") or created_at
            if created_at and updated_at:
                fill_latencies.append(max((updated_at - created_at).total_seconds(), 0.0))
        for item in live_submitted:
            created_at = cls._order_timestamp(item, "created_at")
            updated_at = cls._order_timestamp(item, "updated_at") or now
            if created_at and updated_at:
                open_durations.append(max((updated_at - created_at).total_seconds(), 0.0))

        cancellation_items = [*blocked, *cancelled_like, *pending_cancelled]
        lifecycle_orders = len(live_submitted) + len(live_filled) + len(blocked) + len(cancelled_like) + len(pending_cancelled)
        cancel_count = len(blocked) + len(cancelled_like) + len(pending_cancelled)
        return {
            "tracked_orders": len(orders),
            "live_submitted_orders": len(live_submitted),
            "live_filled_orders": len(live_filled),
            "blocked_orders": len(blocked),
            "cancelled_orders": len(cancelled_like) + len(pending_cancelled),
            "pending_cancelled_orders": len(pending_cancelled),
            "fill_rate": round(len(live_filled) / len(live_submitted), 4) if live_submitted else 0.0,
            "cancel_rate": round(cancel_count / lifecycle_orders, 4) if lifecycle_orders else 0.0,
            "avg_fill_latency_seconds": round(sum(fill_latencies) / len(fill_latencies), 4) if fill_latencies else 0.0,
            "avg_open_duration_seconds": round(sum(open_durations) / len(open_durations), 4) if open_durations else 0.0,
            "cancel_reason_breakdown": cls._count_by_key(
                cancellation_items,
                "reason",
                limit=8,
                missing_label="unknown",
            ),
        }

    @staticmethod
    def _signal_metrics(item: dict[str, Any]) -> dict[str, float]:
        strategy_id = str(item.get("strategy_id") or "").strip()
        edge = float(item.get("edge") or 0.0)
        confidence = float(item.get("confidence") or 0.0)
        if strategy_id == "pair_15m":
            confidence = float(item.get("predictor_confidence") or confidence or 0.0)
            primary_leg = item.get("primary_leg") or {}
            try:
                target_price = float(primary_leg.get("target_price") or 0.0)
                reference_price = float(primary_leg.get("reference_price") or 0.0)
                edge = abs(target_price - reference_price)
            except (TypeError, ValueError, AttributeError):
                edge = 0.0
        return {"edge": edge, "confidence": confidence}

    @staticmethod
    def _pair_trade_summary(orders: list[dict[str, Any]], *, pending_count: int) -> dict[str, Any]:
        groups: dict[str, dict[str, Any]] = {}
        for item in orders:
            if str(item.get("strategy_id") or "") != "pair_15m":
                continue
            trade_group_id = str(item.get("trade_group_id") or "").strip()
            if not trade_group_id:
                continue
            bucket = groups.setdefault(
                trade_group_id,
                {
                    "legs": set(),
                    "primary_notional": 0.0,
                    "hedge_notional": 0.0,
                },
            )
            leg_role = str(item.get("leg_role") or "").strip().lower()
            if leg_role:
                bucket["legs"].add(leg_role)
            notional = float(item.get("notional_usd") or 0.0)
            if leg_role == "primary":
                bucket["primary_notional"] += notional
            elif leg_role == "hedge":
                bucket["hedge_notional"] += notional
        fully_hedged = sum(1 for bucket in groups.values() if {"primary", "hedge"}.issubset(bucket["legs"]))
        primary_only = sum(1 for bucket in groups.values() if "primary" in bucket["legs"] and "hedge" not in bucket["legs"])
        orphan_hedges = sum(1 for bucket in groups.values() if "hedge" in bucket["legs"] and "primary" not in bucket["legs"])
        return {
            "groups": len(groups),
            "fully_hedged_groups": fully_hedged,
            "primary_only_groups": primary_only,
            "orphan_hedge_groups": orphan_hedges,
            "pending_hedges": pending_count,
            "primary_notional": round(sum(float(bucket["primary_notional"]) for bucket in groups.values()), 4),
            "hedge_notional": round(sum(float(bucket["hedge_notional"]) for bucket in groups.values()), 4),
        }

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
            metrics = TradingRepository._signal_metrics(item)
            bucket["edge_total"] += metrics["edge"]
            bucket["confidence_total"] += metrics["confidence"]
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
        position_key: str,
        token_id: str,
        market_question: str,
        asset_symbol: str,
        crypto_tier: str,
        strategy_id: str,
        regime: str,
        trade_group_id: str,
        cycle_slug: str,
        leg_role: str,
        direction: str,
        size: int,
        average_price: float,
        exposure_usd: float,
        take_profit_price: float | None,
        stop_loss_price: float | None,
        time_stop_minutes: int | None,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO positions (
                market_id, position_key, token_id, market_question, asset_symbol, crypto_tier, strategy_id, regime,
                trade_group_id, cycle_slug, leg_role, direction, size, average_price, exposure_usd,
                take_profit_price, stop_loss_price, time_stop_minutes, opened_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
            ON CONFLICT (position_key) DO UPDATE
            SET token_id = EXCLUDED.token_id,
                market_question = EXCLUDED.market_question,
                asset_symbol = EXCLUDED.asset_symbol,
                crypto_tier = EXCLUDED.crypto_tier,
                strategy_id = COALESCE(NULLIF(EXCLUDED.strategy_id, ''), positions.strategy_id),
                regime = COALESCE(NULLIF(EXCLUDED.regime, ''), positions.regime),
                trade_group_id = COALESCE(NULLIF(EXCLUDED.trade_group_id, ''), positions.trade_group_id),
                cycle_slug = COALESCE(NULLIF(EXCLUDED.cycle_slug, ''), positions.cycle_slug),
                leg_role = COALESCE(NULLIF(EXCLUDED.leg_role, ''), positions.leg_role),
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
            trade_group_id,
            cycle_slug,
            leg_role,
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

    @staticmethod
    def _mark_price_for_position(
        row: asyncpg.Record | dict[str, Any],
        latest: dict[str, Any] | None,
        pair_cycle: dict[str, Any] | None = None,
    ) -> tuple[float, float]:
        if isinstance(row, dict):
            average_price = float(row.get("average_price") or 0.0)
            direction = str(row.get("direction") or "")
            cycle_slug = str(row.get("cycle_slug") or "")
        else:
            average_price = float(row["average_price"] or 0.0)
            direction = str(row["direction"] or "")
            cycle_slug = str(row.get("cycle_slug") or "")
        if pair_cycle and str(pair_cycle.get("cycle_slug") or "") == cycle_slug:
            price_yes = float(pair_cycle.get("price_yes") or 0.0)
            price_no = float(pair_cycle.get("price_no") or 0.0)
            pair_sum = price_yes + price_no
            if price_yes > 0 and price_no > 0 and pair_sum <= 1.1:
                return (price_yes if direction == "YES" else price_no), 0.0
        if not latest:
            return average_price, 0.0
        payload = latest.get("payload") or {}
        summary_key = "orderbook_summary_yes" if direction == "YES" else "orderbook_summary_no"
        summary = payload.get(summary_key) or {}
        best_bid = float(summary.get("best_bid") or 0.0)
        best_ask = float(summary.get("best_ask") or 0.0)
        spread_bps = float(summary.get("spread_bps") or 0.0)
        if best_bid > 0:
            return best_bid, spread_bps
        if best_ask > 0:
            return best_ask, spread_bps
        price_yes = float(latest.get("price_yes") or 0.0)
        price_no = float(latest.get("price_no") or 0.0)
        pair_sum = price_yes + price_no
        if price_yes > 0 and price_no > 0 and pair_sum <= 1.1:
            return (price_yes if direction == "YES" else price_no), spread_bps
        if price_yes > 0 and price_no > 0 and pair_sum > 1.1:
            return average_price, spread_bps
        if direction == "YES" and price_yes > 0 and price_yes <= 0.99:
            return price_yes, spread_bps
        if direction == "NO" and price_no > 0 and price_no <= 0.99:
            return price_no, spread_bps
        return average_price, spread_bps

    async def _current_pair_cycles(self, asset_symbols: list[str]) -> dict[str, dict[str, Any]]:
        unique_assets = [asset for asset in dict.fromkeys(asset_symbols) if asset]
        if not unique_assets:
            return {}
        rows = await self.db.fetch(
            """
            SELECT asset_symbol, cycle_slug, price_yes, price_no, updated_at
            FROM pair_cycles
            WHERE asset_symbol = ANY($1::text[])
            """,
            unique_assets,
        )
        return {
            str(row["asset_symbol"]): {
                "cycle_slug": str(row["cycle_slug"] or ""),
                "price_yes": float(row["price_yes"] or 0.0),
                "price_no": float(row["price_no"] or 0.0),
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def _decode_record(self, row: asyncpg.Record) -> dict[str, Any]:
        payload = row["payload"]
        payload = self._json_value(payload)
        if "agent" in row:
            agent = row["agent"]
            if agent not in (None, ""):
                payload.setdefault("agent", agent)
        if "reason" in row:
            reason = row["reason"]
            if reason not in (None, ""):
                payload.setdefault("reason", reason)
        payload["created_at"] = row["created_at"]
        if "updated_at" in row and row["updated_at"] not in (None, ""):
            payload["updated_at"] = row["updated_at"]
        return payload

    @classmethod
    def _decode_flow_record(cls, row: asyncpg.Record) -> dict[str, Any]:
        payload = cls._json_value(row["payload"])
        return {
            "flow_id": row["id"],
            "signal_id": row["signal_id"],
            "trade_group_id": row["trade_group_id"],
            "market_id": row["market_id"],
            "cycle_slug": row["cycle_slug"],
            "market_question": row["market_question"],
            "asset_symbol": row["asset_symbol"],
            "asset_name": row["asset_name"],
            "crypto_tier": row["crypto_tier"],
            "window_minutes": row["window_minutes"],
            "dominant_direction": row["dominant_direction"],
            "dominance_score": float(row["dominance_score"] or 0.0),
            "confidence": float(row["confidence"] or 0.0),
            "up_trade_count": int(row["up_trade_count"] or 0),
            "down_trade_count": int(row["down_trade_count"] or 0),
            "up_notional": float(row["up_notional"] or 0.0),
            "down_notional": float(row["down_notional"] or 0.0),
            "total_trades": int(row["total_trades"] or 0),
            "total_notional": float(row["total_notional"] or 0.0),
            "freshness_seconds": float(row["freshness_seconds"] or 0.0),
            "source_used": row["source_used"],
            "sample_count": int(row["sample_count"] or 0),
            "last_trade_at": row["last_trade_at"],
            "payload": payload,
            "created_at": row["created_at"],
        }

    @classmethod
    def _decode_pipeline_record(cls, row: asyncpg.Record) -> dict[str, Any]:
        payload = cls._json_value(row["payload"])
        return {
            "agent": row["agent"],
            "event_type": row["event_type"],
            "created_at": row["created_at"],
            **payload,
        }

    async def _normalize_risk_payloads(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        missing_signal_ids = sorted(
            {
                str(item.get("signal_id") or "").strip()
                for item in items
                if not str(item.get("strategy_id") or "").strip() and str(item.get("signal_id") or "").strip()
            }
        )
        strategy_by_signal_id: dict[str, str] = {}
        if missing_signal_ids:
            rows = await self.db.fetch(
                "SELECT id, payload FROM signals WHERE id = ANY($1::uuid[])",
                missing_signal_ids,
            )
            for row in rows:
                payload = self._json_value(row["payload"])
                strategy_id = str(payload.get("strategy_id") or "").strip()
                if strategy_id:
                    strategy_by_signal_id[str(row["id"])] = strategy_id

        normalized: list[dict[str, Any]] = []
        for item in items:
            normalized_item = dict(item)
            if not str(normalized_item.get("strategy_id") or "").strip():
                signal_id = str(normalized_item.get("signal_id") or "").strip()
                inferred = strategy_by_signal_id.get(signal_id) or self._infer_risk_strategy_id(normalized_item)
                if inferred:
                    normalized_item["strategy_id"] = inferred
            normalized.append(normalized_item)
        return normalized

    @staticmethod
    def _infer_risk_strategy_id(payload: dict[str, Any]) -> str:
        reason = str(payload.get("reason") or "").strip().lower()
        error = str(payload.get("error") or "").strip().lower()
        agent = str(payload.get("agent") or "").strip().lower()
        if "momentumtradingengine" in error or "momentum_15m" in error or (
            agent == "claude" and ("momentum" in error or "momentum" in reason)
        ):
            return "momentum_15m"
        if "get_market_snapshots" in error:
            return "momentum_15m"
        if not reason:
            if "agent_error" in error:
                return "momentum_15m" if "momentum" in error else ""
            return ""
        if "non-pair position already open" in reason or "daily spend would exceed max_daily_spend_usd" in reason:
            return "momentum_15m"
        if "pair trade would exceed" in reason or "pair position already open" in reason:
            return "pair_15m"
        if "momentum max positions reached" in reason:
            return "momentum_15m"
        if "pair cycle" in reason or "hedge" in reason:
            return "pair_15m"
        return ""

    @staticmethod
    def _json_value(value: Any) -> Any:
        if value in (None, ""):
            return {}
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            decoded = json.loads(value)
            if isinstance(decoded, (dict, list)):
                return decoded
            if isinstance(decoded, str):
                return TradingRepository._json_value(decoded)
            return decoded
        try:
            return dict(value)
        except Exception:
            return value

    @classmethod
    def _normalize_weather_copytrade_payload(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cls._normalize_weather_copytrade_payload(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._normalize_weather_copytrade_payload(item) for item in value]
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    return cls._normalize_weather_copytrade_payload(json.loads(stripped))
                except Exception:
                    return value
        return value

    @staticmethod
    def _extract_live_collateral(status: dict[str, Any] | None) -> tuple[float | None, float | None]:
        if not status:
            return None, None
        parsed = status.get("parsed_collateral")
        if not isinstance(parsed, dict):
            return None, None
        return TradingRepository._safe_float(parsed.get("balance")), TradingRepository._safe_float(parsed.get("allowance"))

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

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
            "pre_risk_blocked": cls._sum_pipeline_field(events, "scanner.scan_cycle", "pre_risk_blocked"),
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
