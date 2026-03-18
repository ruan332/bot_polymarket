#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-.env}"

if [[ -f "$TARGET" ]]; then
  echo "$TARGET already exists; not overwriting."
  exit 0
fi

cp .env.production.example "$TARGET"
echo "Created $TARGET from .env.production.example"
