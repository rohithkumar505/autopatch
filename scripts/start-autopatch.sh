#!/usr/bin/env bash
# Reliable launcher when the editable-install .pth is not on sys.path (Python 3.12+).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "${ROOT}/.venv/bin/python" -m autopatch "$@"