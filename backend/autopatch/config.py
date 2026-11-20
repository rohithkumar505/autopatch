"""Filesystem locations. Everything AutoPatch stores lives under one data folder."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


class Paths:
    def __init__(self, data_dir: Path, runtime_dir: Path | None = None):
        self.data = data_dir
        self.projects = data_dir / "projects"      # one git workspace per project
        self.sandbox = data_dir / "sandbox"        # throwaway folders for test runs
        self.uploads = data_dir / "uploads"        # temporary upload staging
        self.runtime = runtime_dir or data_dir / "runtime"  # private Python used inside the sandbox
        self.db = data_dir / "autopatch.db"

    def ensure(self) -> "Paths":
        for p in (self.data, self.projects, self.sandbox, self.uploads):
            p.mkdir(parents=True, exist_ok=True)
        return self


_paths: Paths | None = None


def paths() -> Paths:
    global _paths
    if _paths is None:
        data = Path(os.environ.get("AUTOPATCH_DATA_DIR", REPO_ROOT / "data")).resolve()
        runtime = os.environ.get("AUTOPATCH_RUNTIME_DIR")
        _paths = Paths(data, Path(runtime).resolve() if runtime else None).ensure()
    return _paths


def set_data_dir(data_dir: Path, runtime_dir: Path | None = None) -> Paths:
    """Point AutoPatch at another data folder (used by tests)."""
    global _paths
    _paths = Paths(Path(data_dir).resolve(), Path(runtime_dir).resolve() if runtime_dir else None).ensure()
    from autopatch import db

    db.reset_engine()
    return _paths
