"""AutoPatch Core end to end: no API key, real sandbox. Both sample projects, every planted bug."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from sqlmodel import select

from autopatch.core import jobs, settings
from autopatch.db import Finding, Patch, session
from autopatch.pipeline import fix, scan
from autopatch.pipeline.projects import create_project
from autopatch.testing import sandbox

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="uses the AppContainer sandbox")
SAMPLES = Path(__file__).resolve().parents[2] / "samples"


@pytest.mark.parametrize("name, unfixable", [("marks_calculator", set()), ("shop_inventory", {"find_product"})])
def test_core_finds_proves_and_fixes_planted_bugs(data_dir, name, unfixable):
    sandbox.get_backend("appcontainer").prepare()
    settings.update({"testing.sandbox": "appcontainer", "ai.provider": "builtin"})
    src = SAMPLES / name
    project = create_project(None, "path", str(src), lambda d: (shutil.copytree(src, d, dirs_exist_ok=True), name)[1])
    s_row, job = scan.start_scan(project.id, {"pylint": False})
    assert jobs.wait(job.id, 300).status == "succeeded"
    with session() as s:
        code = [f for f in s.exec(select(Finding).where(Finding.scan_id == s_row.id)).all() if f.function != "<file>"]
    truth = json.loads((SAMPLES / "ground_truth" / f"{name}.json").read_text())
    planted = {b["function"] for b in truth["bugs"]}
    found = {f.function for f in code}
    assert planted <= found, planted - found
    assert not ({b["function"] for b in truth.get("not_bugs", [])} & found)  # no false alarm on correct code
    assert all(f.verification == "confirmed" for f in code if f.function not in unfixable)

    for f in code:
        if f.function not in planted:
            continue
        if f.function in unfixable:
            with pytest.raises(Exception):
                patch, job = fix.start_fix(f.id)
                if jobs.wait(job.id, 600).status != "succeeded":
                    raise RuntimeError("no automatic fix")
            continue
        patch, job = fix.start_fix(f.id)
        assert jobs.wait(job.id, 600).status == "succeeded", f.function
        with session() as s:
            p = s.get(Patch, patch.id)
        assert p.validation == "validated", (f.function, p.validation_detail)
        assert not p.validation_detail["regressions"]
