"""Scans, findings, patches and test runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from autopatch.core.workspace import Workspace
from autopatch.db import Finding, Patch, Project, Scan, TestRunRow, get_session, record_event
from autopatch.pipeline import fix, history, scan
from autopatch.pipeline.projects import latest_scan

router = APIRouter(prefix="/api", tags=["bugs"])

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class ScanOptions(BaseModel):
    ruff: bool | None = None
    pylint: bool | None = None
    ai: bool | None = None


@router.post("/projects/{project_id}/scans")
def start_scan(project_id: int, body: ScanOptions):
    options = {k: v for k, v in body.model_dump().items() if v is not None}
    row, job = scan.start_scan(project_id, options)
    return {"scan": row, "job_id": job.id}


@router.get("/scans/{scan_id}")
def get_scan(scan_id: int, s: Session = Depends(get_session)):
    row = s.get(Scan, scan_id)
    if row is None:
        raise HTTPException(404, "Scan not found")
    return row


def _finding_summary(f: Finding, latest_patch: Patch | None) -> dict:
    data = f.model_dump(exclude={"code_snippet"})
    data["latest_patch"] = ({"id": latest_patch.id, "status": latest_patch.status,
                             "validation": latest_patch.validation} if latest_patch else None)
    return data


@router.get("/projects/{project_id}/findings")
def list_findings(project_id: int, scan_id: int | None = None, s: Session = Depends(get_session)):
    if s.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    target = s.get(Scan, scan_id) if scan_id else latest_scan(s, project_id)
    if target is None:
        return {"scan": None, "findings": []}
    findings = s.exec(select(Finding).where(Finding.scan_id == target.id)).all()
    patches = s.exec(select(Patch).where(Patch.project_id == project_id).order_by(Patch.id)).all()
    latest = {p.finding_id: p for p in patches}
    findings = sorted(findings, key=lambda f: (-f.priority, SEVERITY_ORDER.get(f.severity, 9), f.file, f.line_start))
    return {"scan": target, "findings": [_finding_summary(f, latest.get(f.id)) for f in findings]}


@router.get("/findings/{finding_id}")
def get_finding(finding_id: int, s: Session = Depends(get_session)):
    f = s.get(Finding, finding_id)
    if f is None:
        raise HTTPException(404, "Finding not found")
    patches = s.exec(select(Patch).where(Patch.finding_id == finding_id).order_by(Patch.id.desc())).all()  # type: ignore[attr-defined]
    return {**f.model_dump(), "patches": [
        {"id": p.id, "status": p.status, "validation": p.validation, "created_at": p.created_at,
         "attempts": p.attempts, "error": p.error, "job_id": p.job_id} for p in patches]}


@router.post("/findings/{finding_id}/analyze")
def analyze(finding_id: int):
    job = fix.start_analyze(finding_id)
    return {"job_id": job.id}


@router.post("/findings/{finding_id}/fix")
def generate_fix(finding_id: int):
    patch, job = fix.start_fix(finding_id)
    return {"patch_id": patch.id, "job_id": job.id}


class Note(BaseModel):
    note: str = ""


@router.post("/findings/{finding_id}/dismiss")
def dismiss(finding_id: int, body: Note, s: Session = Depends(get_session)):
    f = s.get(Finding, finding_id)
    if f is None:
        raise HTTPException(404, "Finding not found")
    if f.status in ("fixing",):
        raise HTTPException(409, "A fix is being generated for this finding")
    f.status = "dismissed"
    s.add(f)
    record_event(s, "finding_dismissed", f"Dismissed '{f.title}'" + (f": {body.note}" if body.note else ""),
                 f.project_id, finding_id=f.id)
    s.commit()
    return f


@router.post("/findings/{finding_id}/reopen")
def reopen(finding_id: int, s: Session = Depends(get_session)):
    f = s.get(Finding, finding_id)
    if f is None:
        raise HTTPException(404, "Finding not found")
    if f.status != "dismissed":
        raise HTTPException(409, "Only dismissed findings can be reopened")
    f.status = "open"
    s.add(f)
    record_event(s, "finding_reopened", f"Reopened '{f.title}'", f.project_id, finding_id=f.id)
    s.commit()
    return f


# ---------------------------------------------------------------- patches


@router.get("/projects/{project_id}/patches")
def list_patches(project_id: int, s: Session = Depends(get_session)):
    head = Workspace(project_id).head()
    rows = s.exec(select(Patch, Finding).join(Finding, Finding.id == Patch.finding_id)
                  .where(Patch.project_id == project_id).order_by(Patch.id.desc())).all()  # type: ignore[attr-defined]
    return [{
        "id": p.id, "finding_id": p.finding_id, "finding_title": f.title, "severity": f.severity,
        "file": p.file, "status": p.status, "validation": p.validation, "attempts": p.attempts,
        "created_at": p.created_at, "decided_at": p.decided_at, "decision_note": p.decision_note,
        "applied_version": p.applied_version, "stale": history.is_stale(p, head), "error": p.error,
        "job_id": p.job_id,
    } for p, f in rows]


@router.get("/patches/{patch_id}")
def get_patch(patch_id: int, s: Session = Depends(get_session)):
    p = s.get(Patch, patch_id)
    if p is None:
        raise HTTPException(404, "Patch not found")
    finding = s.get(Finding, p.finding_id)
    runs = s.exec(select(TestRunRow).where(TestRunRow.patch_id == patch_id).order_by(TestRunRow.id)).all()  # type: ignore[attr-defined]
    head = Workspace(p.project_id).head()
    return {**p.model_dump(), "stale": history.is_stale(p, head), "finding": finding,
            "test_runs": [r.model_dump(exclude={"output"}) for r in runs]}


class AcceptBody(BaseModel):
    include_tests: bool = True
    force: bool = False
    note: str = ""


@router.post("/patches/{patch_id}/accept")
def accept(patch_id: int, body: AcceptBody):
    return history.accept_patch(patch_id, body.include_tests, body.force, body.note)


@router.post("/patches/{patch_id}/reject")
def reject(patch_id: int, body: Note):
    return history.reject_patch(patch_id, body.note)


@router.post("/patches/{patch_id}/revalidate")
def revalidate(patch_id: int):
    return {"job_id": fix.start_revalidate(patch_id).id}


@router.post("/patches/{patch_id}/revert")
def revert(patch_id: int, body: Note):
    return {"version": history.revert_patch(patch_id, body.note)}


# ---------------------------------------------------------------- tests


class TestRunBody(BaseModel):
    __test__ = False
    version: str | None = None


@router.post("/projects/{project_id}/test-runs")
def run_tests(project_id: int, body: TestRunBody):
    return {"job_id": history.start_project_tests(project_id, body.version).id}


@router.get("/projects/{project_id}/test-runs")
def list_test_runs(project_id: int):
    return [r.model_dump(exclude={"output", "results"}) | {"total": r.passed + r.failed + r.errors + r.skipped}
            for r in history.test_runs(project_id)]


@router.get("/test-runs/{run_id}")
def get_test_run(run_id: int, s: Session = Depends(get_session)):
    r = s.get(TestRunRow, run_id)
    if r is None:
        raise HTTPException(404, "Test run not found")
    return r
