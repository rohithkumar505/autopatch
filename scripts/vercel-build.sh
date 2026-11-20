#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"
npm run build
rm -rf "$ROOT/public"
mkdir -p "$ROOT/public"
cp -r dist/* "$ROOT/public/"
echo "Built frontend -> public/ ($(ls -1 "$ROOT/public" | wc -l | tr -d ' ') top-level entries)"