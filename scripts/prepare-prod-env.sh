#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-.env}"
SOURCE_TEMPLATE="${SOURCE_TEMPLATE:-.env.production.example}"

if [[ -f "$TARGET" ]]; then
  echo "$TARGET already exists; not overwriting."
  exit 0
fi

cp "$SOURCE_TEMPLATE" "$TARGET"
echo "Created $TARGET from $SOURCE_TEMPLATE"
