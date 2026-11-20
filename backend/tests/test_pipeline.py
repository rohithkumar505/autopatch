"""End-to-end pipeline test: real sandbox, real git workspace, real database.

Only the AI provider is replaced, by a scripted test double defined here. The product never
uses it; it exists so the pipeline can be tested without network access or API cost.
"""

from __future__ import annotations

import shutil
import sys

import pytest
from sqlmodel import select

from autopatch.ai.engine import Engine
from autopatch.ai.providers import AIProvider, AIUsage
from autopatch.ai.schemas import (
    AIFinding, AIScanResult, CodeEdit, FixProposal, GeneratedCase, GeneratedTests, RootCauseAnalysis,
)
from autopatch.core import jobs, settings
from autopatch.core.workspace import Workspace
from autopatch.db import Finding, Patch, session
from autopatch.pipeline import fix, history, scan
from autopatch.pipeline.projects import create_project
from autopatch.testing import sandbox

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="uses the AppContainer sandbox")

GRADE_TESTS = '''import pytest
from grades import get_grade

def test_reproduce_negative_marks_rejected():
    with pytest.raises(ValueError):
        get_grade(-5)

def test_maximum_above_100_rejected():
    with pytest.raises(ValueError):
        get_grade(101)

def test_minimum_zero_is_f():
    assert get_grade(0) == "F"

def test_normal_grades():
    assert get_grade(95) == "A"
    assert get_grade(45) == "D"

def test_normal_boundary_100():
    assert get_grade(100) == "A"
'''

GOOD_EDIT = CodeEdit(
    original_code="def get_grade(marks):\n    if marks >= 90:",
    patched_code="def get_grade(marks):\n    if not 0 <= marks <= MAX_MARKS:\n"
                 "        raise ValueError(f\"marks must be between 0 and {MAX_MARKS}, got {marks}\")\n"
                 "    if marks >= 90:",
)
# Rejects 100 as well, so the boundary test fails: this patch must NOT validate.
BAD_EDIT = CodeEdit(
    original_code="def get_grade(marks):\n    if marks >= 90:",
    patched_code="def get_grade(marks):\n    if not 0 <= marks < MAX_MARKS:\n"
                 "        raise ValueError('out of range')\n    if marks >= 90:",
)


class ScriptedProvider(AIProvider):
    name = "scripted"
    model = "scripted-test-double"

    def __init__(self, fixes: list[FixProposal]):
        self.fixes = list(fixes)
        self.calls: list[tuple[str, str]] = []

    def generate(self, *, system, context, task, schema, effort, max_tokens=64000):
        self.calls.append((schema.__name__, task))
        usage = AIUsage(self.name, self.model, input_tokens=len(context) // 4, output_tokens=100)
        if schema is AIScanResult:
            return AIScanResult(project_summary="Marks calculator.", findings=[AIFinding(
                title="get_grade accepts marks outside 0-100", file="grades.py", function="get_grade",
                line_start=16, line_end=26, category="incorrect_output", severity="high", confidence=0.85,
                description="Negative marks return 'F' and marks above 100 return 'A'.",
                root_cause="No range check before the grade thresholds.", trigger="get_grade(-5)",
                expected_behavior="Reject marks outside 0..100 with ValueError.",
                severity_reason="Wrong result on plausible user input.",
            )]), usage
        if schema is RootCauseAnalysis:
            return RootCauseAnalysis(
                is_real_bug=True, title="get_grade accepts marks outside 0-100",
                description="Out-of-range marks get a grade.", root_cause="Thresholds assume 0..100.",
                trigger="get_grade(-5), get_grade(150)", expected_behavior="Raise ValueError.",
                related_code=["report_card passes averages into get_grade"], impact="Report cards.",
                category="incorrect_output", severity="high", confidence=0.9, severity_reason="Wrong grades.",
            ), usage
        if schema is GeneratedTests:
            return GeneratedTests(test_code=GRADE_TESTS, cases=[
                GeneratedCase(name="test_reproduce_negative_marks_rejected", kind="reproduce", description="-5"),
                GeneratedCase(name="test_maximum_above_100_rejected", kind="maximum", description="101"),
                GeneratedCase(name="test_minimum_zero_is_f", kind="minimum", description="0"),
                GeneratedCase(name="test_normal_grades", kind="normal", description="typical"),
                GeneratedCase(name="test_normal_boundary_100", kind="normal", description="100"),
            ]), usage
        if schema is FixProposal:
            return self.fixes.pop(0), usage
        raise AssertionError(schema)

    def test_connection(self):
        return {"ok": True, "message": "scripted"}


@pytest.fixture()
def project(data_dir, sample_copy, monkeypatch):
    sandbox.get_backend("appcontainer").prepare()
    settings.update({"testing.sandbox": "appcontainer", "testing.max_fix_attempts": 2})
    p = create_project(None, "path", str(sample_copy),
                       lambda dest: (shutil.copytree(sample_copy, dest, dirs_exist_ok=True), "marks_calculator")[1])
    return p


def _use(monkeypatch, provider):
    monkeypatch.setattr(scan, "get_engine", lambda cfg: Engine(provider))
    monkeypatch.setattr(fix, "get_engine", lambda cfg: Engine(provider))


def test_full_workflow_scan_fix_validate_accept_revert(project, monkeypatch):
    provider = ScriptedProvider([FixProposal(file="grades.py", edits=[GOOD_EDIT], explanation="Range check.",
                                             behavior_change="Out-of-range marks raise ValueError.")])
    _use(monkeypatch, provider)

    s_row, job = scan.start_scan(project.id, {"ai": True})
    assert jobs.wait(job.id, 300).status == "succeeded", jobs.get(job.id).error
    with session() as s:
        findings = s.exec(select(Finding).where(Finding.scan_id == s_row.id)).all()
    by_func = {f.function: f for f in findings}
    assert {"parse_marks", "add_marks", "get_grade"} <= set(by_func)
    assert by_func["get_grade"].sources == ["ai"]
    assert set(by_func["parse_marks"].sources) == {"pylint:W0123", "ruff:S307"}

    patch, job = fix.start_fix(by_func["get_grade"].id)
    done = jobs.wait(job.id, 600)
    assert done.status == "succeeded", (done.error, done.progress)
    with session() as s:
        patch = s.get(Patch, patch.id)
        finding = s.get(Finding, patch.finding_id)
    assert patch.validation == "validated", patch.validation_detail
    detail = patch.validation_detail
    assert detail["reproduced"] and not detail["regressions"] and not detail["generated_failing"]
    assert detail["existing"]["before"] == {"passed": 7, "total": 7} == detail["existing"]["after"]
    kinds = {c["kind"]: (c["before"], c["after"]) for c in patch.test_cases}
    assert kinds["reproduce"] == ("failed", "passed")
    assert finding.verification == "confirmed" and finding.status == "patch_ready"
    assert "+    if not 0 <= marks <= MAX_MARKS:" in patch.diff
    assert detail["scope"]["outside_function"] == []

    ws = Workspace(project.id)
    original = ws.head()
    assert "raise ValueError" not in ws.read_text("grades.py")  # nothing applied before approval

    accepted = history.accept_patch(patch.id, include_tests=True)
    assert accepted.status == "accepted" and ws.head() != original
    assert "raise ValueError" in ws.read_text("grades.py")
    assert patch.test_file in ws.list_files()
    with session() as s:
        assert s.get(Finding, finding.id).status == "fixed"

    history.revert_patch(patch.id)
    assert "raise ValueError" not in ws.read_text("grades.py")
    with session() as s:
        assert s.get(Patch, patch.id).status == "reverted"
        assert s.get(Finding, finding.id).status == "open"

    history.restore_version(project.id, accepted.applied_version)  # bring the accepted change back
    assert "raise ValueError" in ws.read_text("grades.py")
    with session() as s:
        assert s.get(Patch, patch.id).status == "accepted"


def test_failing_patch_is_retried_with_feedback_then_reported_honestly(project, monkeypatch):
    bad = FixProposal(file="grades.py", edits=[BAD_EDIT], explanation="x", behavior_change="x")
    provider = ScriptedProvider([bad, bad])
    _use(monkeypatch, provider)
    s_row, job = scan.start_scan(project.id, {"ai": True, "pylint": False})
    jobs.wait(job.id, 300)
    with session() as s:
        target = s.exec(select(Finding).where(Finding.scan_id == s_row.id, Finding.function == "get_grade")).one()
    patch, job = fix.start_fix(target.id)
    assert jobs.wait(job.id, 600).status == "succeeded"
    with session() as s:
        patch = s.get(Patch, patch.id)
    assert patch.validation == "failed"
    assert patch.attempts == 2
    fix_tasks = [task for name, task in provider.calls if name == "FixProposal"]
    assert "not good enough" in fix_tasks[1] and "test_normal_boundary_100" in fix_tasks[1]
    with pytest.raises(history.ReviewError) as err:
        history.accept_patch(patch.id)  # unvalidated patches need explicit confirmation
    assert err.value.status == 409
    rejected = history.reject_patch(patch.id, "rejects 100")
    assert rejected.status == "rejected"


def test_stale_patch_requires_revalidation(project, monkeypatch):
    good = FixProposal(file="grades.py", edits=[GOOD_EDIT], explanation="x", behavior_change="x")
    _use(monkeypatch, ScriptedProvider([good]))
    s_row, job = scan.start_scan(project.id, {"ai": True, "pylint": False})
    jobs.wait(job.id, 300)
    with session() as s:
        target = s.exec(select(Finding).where(Finding.scan_id == s_row.id, Finding.function == "get_grade")).one()
    patch, job = fix.start_fix(target.id)
    jobs.wait(job.id, 600)
    ws = Workspace(project.id)
    ws.write_text("README.md", "changed\n")
    ws.commit_all("Unrelated change")
    with pytest.raises(history.ReviewError) as err:
        history.accept_patch(patch.id)
    assert err.value.status == 409
    job = fix.start_revalidate(patch.id)
    assert jobs.wait(job.id, 300).status == "succeeded"
    assert history.accept_patch(patch.id).status == "accepted"
