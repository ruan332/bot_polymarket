#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${DOMAIN:-}"
APP_DIR="${APP_DIR:-/opt/polymarket-bot}"

if [[ -z "$DOMAIN" ]]; then
  echo "Use DOMAIN=bot.seudominio.com ./scripts/post-deploy-check.sh"
  exit 1
fi

cd "$APP_DIR"

echo "[1/5] Containers"
docker compose -f docker-compose.prod.yml ps

echo "[2/5] API health"
curl -fsS "https://$DOMAIN/api/healthz"
echo

echo "[3/5] Agent status"
curl -fsS "https://$DOMAIN/api/agents/status"
echo

echo "[4/5] Metrics overview"
curl -fsS "https://$DOMAIN/api/metrics/overview"
echo

echo "[5/5] Dashboard headers"
curl -IfsS "https://$DOMAIN/"
