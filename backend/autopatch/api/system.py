"""Dashboard, history, jobs, settings, sandbox and language support."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from autopatch import __version__
from autopatch.ai.providers import AINotConfigured, ai_status, get_provider
from autopatch.analysis.base import analyzers
from autopatch.core import jobs, settings
from autopatch.core.workspace import Workspace
from autopatch.db import Event, Finding, Job, Patch, Project, TestRunRow, get_session
from autopatch.pipeline import history
from autopatch.pipeline.projects import latest_scan
from autopatch.testing import sandbox

router = APIRouter(prefix="/api", tags=["system"])

OPEN = ("open", "fixing", "patch_ready", "patched_unverified")


@router.get("/health")
def health():
    return {"ok": True, "version": __version__}


@router.get("/status")
def status():
    from autopatch.storage.serverless import persistence_status

    cfg = settings.get_all()
    try:
        active = sandbox.choose(cfg["testing.sandbox"], cfg["testing.allow_limited"])
        sb = {"ready": True, "name": active.name, "label": active.label, "isolation": active.isolation}
    except sandbox.SandboxUnavailable as exc:
        sb = {"ready": False, "message": str(exc)}
    return {"version": __version__, "ai": ai_status(cfg), "sandbox": sb,
            "active_jobs": len(jobs.active_jobs()), "persistence": persistence_status()}


# ---------------------------------------------------------------- dashboard


@router.get("/dashboard")
def dashboard(project_id: int | None = None, s: Session = Depends(get_session)):
    projects = s.exec(select(Project)).all()
    scope = [p for p in projects if project_id is None or p.id == project_id]
    current: list[Finding] = []
    per_project = []
    for p in scope:
        scan = latest_scan(s, p.id)
        rows = s.exec(select(Finding).where(Finding.scan_id == scan.id)).all() if scan else []
        current += rows
        per_project.append({
            "id": p.id, "name": p.name, "languages": p.languages,
            "open": sum(f.status in OPEN for f in rows),
            "critical": sum(f.severity == "critical" and f.status in OPEN for f in rows),
            "last_scan": scan.finished_at if scan else None,
        })
    ids = [p.id for p in scope]

    def count(model, *conds):
        if not ids:
            return 0
        return s.exec(select(func.count()).select_from(model).where(model.project_id.in_(ids), *conds)).one()  # type: ignore[attr-defined]

    open_findings = [f for f in current if f.status in OPEN]
    runs = s.exec(select(TestRunRow).where(TestRunRow.project_id.in_(ids))  # type: ignore[attr-defined]
                  .order_by(TestRunRow.id.desc())).all() if ids else []
    events = s.exec(select(Event).where(Event.project_id.in_(ids))  # type: ignore[attr-defined]
                    .order_by(Event.id.desc()).limit(12)).all() if ids else []
    top = sorted(open_findings, key=lambda f: -f.priority)[:6]
    return {
        "projects": len(projects),
        "findings": {
            "detected": len(current),
            "open": len(open_findings),
            "confirmed": sum(f.verification == "confirmed" for f in current),
            "possible": sum(f.verification == "possible" for f in current),
            "dismissed": sum(f.status == "dismissed" for f in current),
            "by_severity": {sev: sum(f.severity == sev for f in open_findings)
                            for sev in ("critical", "high", "medium", "low")},
            "by_category": {c: sum(f.category == c for f in open_findings) for c in
                            sorted({f.category for f in open_findings})},
        },
        "resolved": count(Finding, Finding.status == "fixed"),
        "patches": {
            "pending": count(Patch, Patch.status == "ready"),
            "accepted": count(Patch, Patch.status == "accepted"),
            "rejected": count(Patch, Patch.status == "rejected"),
            "reverted": count(Patch, Patch.status == "reverted"),
            "validated": count(Patch, Patch.validation == "validated"),
            "failed_validation": count(Patch, Patch.validation == "failed"),
        },
        "tests": {
            "runs": len(runs),
            "passed_runs": sum(r.status == "passed" for r in runs),
            "tests_executed": sum(r.passed + r.failed + r.errors + r.skipped for r in runs),
            "latest": runs[0].model_dump(exclude={"output", "results"}) if runs else None,
        },
        "top_findings": [{"id": f.id, "project_id": f.project_id, "title": f.title, "severity": f.severity,
                          "confidence": f.confidence, "file": f.file, "line_start": f.line_start,
                          "verification": f.verification, "status": f.status} for f in top],
        "per_project": per_project,
        "recent_events": events,
    }


# ---------------------------------------------------------------- history


@router.get("/projects/{project_id}/versions")
def versions(project_id: int, s: Session = Depends(get_session)):
    if s.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    ws = Workspace(project_id)
    patches = {p.applied_version: p for p in s.exec(select(Patch).where(Patch.project_id == project_id)).all()
               if p.applied_version}
    head = ws.head()
    out = []
    for v in ws.versions():
        p = patches.get(v.sha)
        out.append({
            "sha": v.sha, "short": v.short, "message": v.message, "date": v.date, "parent": v.parent,
            "is_head": v.sha == head, "files_changed": ws.changed_files(v.sha) if v.parent else [],
            "patch": {"id": p.id, "status": p.status, "finding_id": p.finding_id, "validation": p.validation}
            if p else None,
        })
    return out


@router.get("/projects/{project_id}/versions/{sha}/diff")
def version_diff(project_id: int, sha: str):
    ws = Workspace(project_id)
    return {"sha": ws.resolve(sha), "diff": ws.commit_diff(sha)}


class RestoreBody(BaseModel):
    version: str
    note: str = ""


@router.post("/projects/{project_id}/restore")
def restore(project_id: int, body: RestoreBody):
    sha = history.restore_version(project_id, body.version, body.note)
    return {"version": sha, "changed": sha is not None}


@router.get("/projects/{project_id}/events")
def events(project_id: int, limit: int = 200, s: Session = Depends(get_session)):
    return s.exec(select(Event).where(Event.project_id == project_id).order_by(Event.id.desc()).limit(limit)).all()  # type: ignore[attr-defined]


# ---------------------------------------------------------------- jobs


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs")
def list_jobs(project_id: int | None = None, active: bool = False, s: Session = Depends(get_session)):
    if active:
        return jobs.active_jobs(project_id)
    q = select(Job).order_by(Job.created_at.desc()).limit(50)  # type: ignore[attr-defined]
    if project_id is not None:
        q = q.where(Job.project_id == project_id)
    return s.exec(q).all()


# ---------------------------------------------------------------- settings


@router.get("/settings")
def get_settings():
    return settings.public()


@router.put("/settings")
def put_settings(changes: dict):
    if changes.get("ai.anthropic.api_key") is None:
        changes.pop("ai.anthropic.api_key", None)  # unchanged secret
    return settings.update(changes)


@router.post("/settings/test-ai")
def test_ai():
    if settings.get("ai.provider") == "builtin":
        return {"ok": True, "message": "AutoPatch Core is built in: no key, no connection, nothing leaves this computer."}
    try:
        provider = get_provider(settings.get_all())
    except AINotConfigured as exc:
        return {"ok": False, "message": str(exc)}
    return provider.test_connection()


@router.get("/sandbox")
def sandbox_status():
    cfg = settings.get_all()
    try:
        active = sandbox.choose(cfg["testing.sandbox"], cfg["testing.allow_limited"]).name
        error = ""
    except sandbox.SandboxUnavailable as exc:
        active, error = None, str(exc)
    return {"active": active, "error": error, "backends": [b.info().to_dict() for b in sandbox.backends()]}


@router.post("/sandbox/{name}/prepare")
def prepare_sandbox(name: str):
    backend = sandbox.get_backend(name)
    job = jobs.submit("sandbox_setup", None, lambda ctx: backend.prepare(ctx.log).to_dict())
    return {"job_id": job.id}


@router.post("/sandbox/{name}/self-test")
def sandbox_self_test(name: str):
    backend = sandbox.get_backend(name)
    info = backend.info()
    if not (info.available and info.ready):
        raise HTTPException(400, f"{info.label} is not set up: {info.detail}")
    return sandbox.self_test(backend)


@router.get("/languages")
def languages():
    return [{"language": a.language, "extensions": list(a.extensions), "test_framework": a.test_framework,
             "tools": [t.__dict__ for t in a.tools()]} for a in analyzers().values()]
