#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${DOMAIN:-}"
BASE_URL="${BASE_URL:-}"
APP_DIR="${APP_DIR:-/opt/polymarket-bot}"

if [[ -z "$BASE_URL" ]]; then
  if [[ -n "$DOMAIN" ]]; then
    BASE_URL="https://$DOMAIN"
  else
    echo "Use DOMAIN=bot.codifica.tec.br ./scripts/post-deploy-check.sh"
    echo "ou BASE_URL=https://bot.codifica.tec.br ./scripts/post-deploy-check.sh"
    exit 1
  fi
fi

cd "$APP_DIR"

echo "[1/5] Containers"
docker compose -f docker-compose.prod.yml ps

echo "[2/5] API health"
curl -fsS "$BASE_URL/api/healthz"
echo

echo "[3/5] Agent status"
curl -fsS "$BASE_URL/api/agents/status"
echo

echo "[4/5] Metrics overview"
curl -fsS "$BASE_URL/api/metrics/overview"
echo

echo "[5/5] Dashboard headers"
curl -IfsS "$BASE_URL/"
