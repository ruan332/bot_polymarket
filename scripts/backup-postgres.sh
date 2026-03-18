#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/polymarket-bot}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"
cd "$APP_DIR"

docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-trading}" "${POSTGRES_DB:-trading}" \
  > "$BACKUP_DIR/postgres-$STAMP.sql"

echo "Backup written to $BACKUP_DIR/postgres-$STAMP.sql"
