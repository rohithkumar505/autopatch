from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "samples" / "marks_calculator"

# Share one sandbox runtime across test sessions; it is slow to build.
os.environ.setdefault("AUTOPATCH_RUNTIME_DIR", str(REPO / "data" / "runtime"))


@pytest.fixture()
def data_dir(tmp_path: Path):
    from autopatch.config import set_data_dir

    p = set_data_dir(tmp_path / "data", Path(os.environ["AUTOPATCH_RUNTIME_DIR"]))
    yield p


@pytest.fixture()
def sample_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "marks_calculator"
    shutil.copytree(SAMPLE, dest)
    return dest
