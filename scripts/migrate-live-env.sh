#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/polymarket-bot}"
APP_ENV_FILE="${APP_ENV_FILE:-.env}"
PAPER_ENV_FILE="${PAPER_ENV_FILE:-.env.paper}"
CURRENT_DB_NAME="${CURRENT_DB_NAME:-trading}"
PAPER_DB_NAME="${PAPER_DB_NAME:-trading_paper}"
PROD_DB_NAME="${PROD_DB_NAME:-trading_prod}"
POSTGRES_USER="${POSTGRES_USER:-trading}"
PROD_REDIS_DB="${PROD_REDIS_DB:-0}"
PAPER_REDIS_DB="${PAPER_REDIS_DB:-1}"
BACKUP_DIR="${BACKUP_DIR:-/opt/backups/polymarket-bot}"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
FUNDER_ADDRESS="${FUNDER_ADDRESS:-0x64d1C8A99308ca35f1B4F34e009B01F8165E1f96}"

cd "$APP_DIR"

if [[ ! -f "$APP_ENV_FILE" ]]; then
  echo "Missing env file: $APP_ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$APP_ENV_FILE"
set +a

POSTGRES_USER="${POSTGRES_USER:-trading}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"

if [[ -z "$POSTGRES_PASSWORD" ]]; then
  echo "POSTGRES_PASSWORD is required in $APP_ENV_FILE" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
COMPOSE=(docker compose -f docker-compose.prod.yml)
POSTGRES_CID="$(${COMPOSE[@]} ps -q postgres)"
REDIS_CID="$(${COMPOSE[@]} ps -q redis)"
API_CID="$(${COMPOSE[@]} ps -q api)"

if [[ -z "$POSTGRES_CID" || -z "$REDIS_CID" || -z "$API_CID" ]]; then
  echo "postgres/redis/api containers must be up before migration" >&2
  exit 1
fi

backup_file="$BACKUP_DIR/postgres_${CURRENT_DB_NAME}_before_live_migration_${STAMP}.sql"
overview_file="$BACKUP_DIR/overview_before_live_migration_${STAMP}.json"
performance_file="$BACKUP_DIR/performance_before_live_migration_${STAMP}.json"
positions_file="$BACKUP_DIR/positions_before_live_migration_${STAMP}.json"
agents_file="$BACKUP_DIR/agents_before_live_migration_${STAMP}.json"
env_backup_file="$BACKUP_DIR/$(basename "$APP_ENV_FILE").before_live_migration_${STAMP}"

upsert_env() {
  local key="$1"
  local value="$2"
  local file="$3"
  if grep -q "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >>"$file"
  fi
}

echo "[1/10] stopping agents to freeze the current paper runtime"
${COMPOSE[@]} stop agents

echo "[2/10] exporting runtime snapshots"
docker exec -i "$API_CID" python - <<'PY' >"$overview_file"
import urllib.request as u
print(u.urlopen('http://127.0.0.1:8000/metrics/overview', timeout=30).read().decode())
PY
docker exec -i "$API_CID" python - <<'PY' >"$performance_file"
import urllib.request as u
print(u.urlopen('http://127.0.0.1:8000/metrics/performance?hours=24', timeout=30).read().decode())
PY
docker exec -i "$API_CID" python - <<'PY' >"$positions_file"
import urllib.request as u
print(u.urlopen('http://127.0.0.1:8000/portfolio/positions', timeout=30).read().decode())
PY
docker exec -i "$API_CID" python - <<'PY' >"$agents_file"
import urllib.request as u
print(u.urlopen('http://127.0.0.1:8000/agents/status', timeout=30).read().decode())
PY

echo "[3/10] backing up current database ${CURRENT_DB_NAME}"
${COMPOSE[@]} exec -T postgres pg_dump -U "$POSTGRES_USER" "$CURRENT_DB_NAME" >"$backup_file"

echo "[4/10] cloning ${CURRENT_DB_NAME} into ${PAPER_DB_NAME}"
${COMPOSE[@]} exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS ${PAPER_DB_NAME} WITH (FORCE);"
${COMPOSE[@]} exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE ${PAPER_DB_NAME};"
cat "$backup_file" | ${COMPOSE[@]} exec -T postgres psql -U "$POSTGRES_USER" -d "$PAPER_DB_NAME" >/dev/null

echo "[5/10] creating clean production database ${PROD_DB_NAME}"
${COMPOSE[@]} exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS ${PROD_DB_NAME} WITH (FORCE);"
${COMPOSE[@]} exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE ${PROD_DB_NAME};"
docker exec -i \
  -e DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${PROD_DB_NAME}" \
  "$API_CID" python - <<'PY'
import asyncio
import os

from core.database import Database


async def main() -> None:
    db = Database(os.environ["DATABASE_URL"])
    try:
        await db.connect()
        await db.init_schema()
    finally:
        await db.close()


asyncio.run(main())
PY

echo "[6/10] generating ${PAPER_ENV_FILE} from ${APP_ENV_FILE}"
cp "$APP_ENV_FILE" "$PAPER_ENV_FILE"
upsert_env "DATABASE_URL" "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${PAPER_DB_NAME}" "$PAPER_ENV_FILE"
upsert_env "REDIS_URL" "redis://redis:6379/${PAPER_REDIS_DB}" "$PAPER_ENV_FILE"
upsert_env "LIVE_TRADING" "false" "$PAPER_ENV_FILE"
upsert_env "SMOKE_TEST_MODE" "false" "$PAPER_ENV_FILE"

echo "[7/10] switching ${APP_ENV_FILE} to clean live production defaults"
cp "$APP_ENV_FILE" "$env_backup_file"
upsert_env "DATABASE_URL" "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${PROD_DB_NAME}" "$APP_ENV_FILE"
upsert_env "REDIS_URL" "redis://redis:6379/${PROD_REDIS_DB}" "$APP_ENV_FILE"
upsert_env "LIVE_TRADING" "true" "$APP_ENV_FILE"
upsert_env "SMOKE_TEST_MODE" "false" "$APP_ENV_FILE"
upsert_env "MAX_DAILY_SPEND_USD" "5.00" "$APP_ENV_FILE"
upsert_env "MAX_SINGLE_POSITION_USD" "2.00" "$APP_ENV_FILE"
upsert_env "PAPER_BANKROLL_USD" "10.00" "$APP_ENV_FILE"
upsert_env "COPYTRADE_MARKETS" "BTC" "$APP_ENV_FILE"
upsert_env "COPYTRADE_SHARES" "1" "$APP_ENV_FILE"
upsert_env "COPYTRADE_MAX_BUY_COUNTS_PER_SIDE" "1" "$APP_ENV_FILE"
upsert_env "MOMENTUM_ENABLED" "false" "$APP_ENV_FILE"
upsert_env "POLYMARKET_FUNDER" "$FUNDER_ADDRESS" "$APP_ENV_FILE"
upsert_env "POLYMARKET_SIGNATURE_TYPE" "0" "$APP_ENV_FILE"
upsert_env "POLYMARKET_CHAIN_ID" "137" "$APP_ENV_FILE"

echo "[8/10] clearing production redis db ${PROD_REDIS_DB} before first live boot"
docker exec "$REDIS_CID" redis-cli -n "$PROD_REDIS_DB" FLUSHDB >/dev/null

echo "[9/10] validating cloned paper counts and clean prod counts"
echo "---PAPER_COUNTS---"
${COMPOSE[@]} exec -T postgres psql -U "$POSTGRES_USER" -d "$PAPER_DB_NAME" -At -F '|' -c "
select 'signals', count(*) from signals
union all select 'agent_decisions', count(*) from agent_decisions
union all select 'paper_orders', count(*) from paper_orders
union all select 'positions', count(*) from positions
union all select 'equity_snapshots', count(*) from equity_snapshots
union all select 'risk_events', count(*) from risk_events;
"
echo "---PROD_COUNTS---"
${COMPOSE[@]} exec -T postgres psql -U "$POSTGRES_USER" -d "$PROD_DB_NAME" -At -F '|' -c "
select 'signals', count(*) from signals
union all select 'agent_decisions', count(*) from agent_decisions
union all select 'paper_orders', count(*) from paper_orders
union all select 'positions', count(*) from positions
union all select 'equity_snapshots', count(*) from equity_snapshots
union all select 'risk_events', count(*) from risk_events;
"

echo "[10/10] migration artifacts"
echo "DB_BACKUP=$backup_file"
echo "OVERVIEW_SNAPSHOT=$overview_file"
echo "PERFORMANCE_SNAPSHOT=$performance_file"
echo "POSITIONS_SNAPSHOT=$positions_file"
echo "AGENTS_SNAPSHOT=$agents_file"
echo "ENV_BACKUP=$env_backup_file"
echo "PAPER_ENV_FILE=$PAPER_ENV_FILE"
echo "NEXT_STEP=run APP_ENV_FILE=$APP_ENV_FILE bash scripts/deploy-vps.sh and validate /api/live/bootstrap-status?refresh=true"
