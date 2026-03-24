from __future__ import annotations

import asyncio
import os
import sys

import asyncpg


PAIR_REASON_SQL = """
    UPDATE risk_events
    SET payload = jsonb_set(payload, '{strategy_id}', to_jsonb('pair_15m'::text), true)
    WHERE coalesce(payload->>'strategy_id', '') = ''
      AND (
        payload::text ILIKE '%pair trade would exceed%'
        OR payload::text ILIKE '%pair position already open%'
        OR payload::text ILIKE '%pair cycle%'
        OR payload::text ILIKE '%hedge%'
      )
"""


MOMENTUM_REASON_SQL = """
    UPDATE risk_events
    SET payload = jsonb_set(payload, '{strategy_id}', to_jsonb('momentum_15m'::text), true)
    WHERE coalesce(payload->>'strategy_id', '') = ''
      AND (
        payload::text ILIKE '%non-pair position already open%'
        OR payload::text ILIKE '%daily spend would exceed max_daily_spend_usd%'
        OR payload::text ILIKE '%momentum max positions reached%'
      )
"""


MOMENTUM_ERROR_SQL = """
    UPDATE risk_events
    SET payload = jsonb_set(payload, '{strategy_id}', to_jsonb('momentum_15m'::text), true)
    WHERE coalesce(payload->>'strategy_id', '') = ''
      AND (
        payload::text ILIKE '%MomentumTradingEngine%'
        OR payload::text ILIKE '%get_market_snapshots%'
        OR payload::text ILIKE '%momentum_15m%'
        OR payload::text ILIKE '%momentum%'
      )
"""


async def main() -> int:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(dsn)
    try:
        before = await conn.fetchval("SELECT COUNT(*) FROM risk_events WHERE coalesce(payload->>'strategy_id', '') = ''")
        signal_backfill = await conn.execute(
            """
            UPDATE risk_events re
            SET payload = jsonb_set(re.payload, '{strategy_id}', to_jsonb(sig_strategy.strategy_id), true)
            FROM (
                SELECT id::text AS signal_id, payload->>'strategy_id' AS strategy_id
                FROM signals
                WHERE coalesce(payload->>'strategy_id', '') <> ''
            ) AS sig_strategy
            WHERE coalesce(re.payload->>'strategy_id', '') = ''
              AND re.payload->>'signal_id' = sig_strategy.signal_id
            """
        )
        pair_backfill = await conn.execute(PAIR_REASON_SQL)
        momentum_backfill = await conn.execute(MOMENTUM_REASON_SQL)
        momentum_error_backfill = await conn.execute(MOMENTUM_ERROR_SQL)
        after = await conn.fetchval("SELECT COUNT(*) FROM risk_events WHERE coalesce(payload->>'strategy_id', '') = ''")

        print(
            {
                "before": int(before or 0),
                "signal_backfill": signal_backfill,
                "pair_backfill": pair_backfill,
                "momentum_backfill": momentum_backfill,
                "momentum_error_backfill": momentum_error_backfill,
                "after": int(after or 0),
            }
        )
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
