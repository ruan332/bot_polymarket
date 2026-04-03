#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/polymarket-bot}"
BRANCH="${BRANCH:-main}"
APP_ENV_FILE="${APP_ENV_FILE:-.env}"

cd "$APP_DIR"

if [[ ! -f "$APP_ENV_FILE" ]]; then
  echo "$APP_ENV_FILE is missing in $APP_DIR; aborting deploy to avoid booting with empty production config."
  exit 1
fi

git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "Deploying with APP_ENV_FILE=$APP_ENV_FILE"
APP_ENV_FILE="$APP_ENV_FILE" docker compose -f docker-compose.prod.yml build --pull
APP_ENV_FILE="$APP_ENV_FILE" docker compose -f docker-compose.prod.yml up -d
APP_ENV_FILE="$APP_ENV_FILE" docker compose -f docker-compose.prod.yml ps
