#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/polymarket-bot}"
BRANCH="${BRANCH:-main}"

cd "$APP_DIR"

if [[ ! -f .env ]]; then
  echo ".env is missing in $APP_DIR; aborting deploy to avoid booting with empty production config."
  exit 1
fi

git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

docker compose -f docker-compose.prod.yml build --pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
