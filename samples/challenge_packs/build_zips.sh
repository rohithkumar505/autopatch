#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
ZIP_DIR="$ROOT/zip"
SRC="$ROOT/projects"
rm -rf "$ZIP_DIR"
mkdir -p "$ZIP_DIR"
for dir in "$SRC"/*/; do
  name="$(basename "$dir")"
  (cd "$SRC" && zip -rq "$ZIP_DIR/${name}.zip" "$name")
done
echo "Wrote $(ls -1 "$ZIP_DIR" | wc -l | tr -d ' ') zip(s) to $ZIP_DIR"