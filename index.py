"""Vercel entrypoint — exports the FastAPI app for serverless hosting."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("AUTOPATCH_DATA_DIR", "/tmp/autopatch")
os.environ.setdefault("AUTOPATCH_SERVERLESS", "1")
os.environ.setdefault("VERCEL", "1")

from autopatch.main import app  # noqa: E402