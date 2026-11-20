"""Projects, code explorer and project structure."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session, func, select

from autopatch.analysis.base import LANGUAGE_BY_EXTENSION, analyzer_for
from autopatch.core.files import (
    ProjectImportError, Report, clone_git, common_root, copy_tree, extract_zip, looks_binary, safe_relpath,
)
from autopatch.core.workspace import Workspace
from autopatch.db import Finding, Patch, Project, Scan, get_session, now_iso
from autopatch.pipeline.projects import create_project, delete_project, latest_scan

router = APIRouter(prefix="/api/projects", tags=["projects"])

OPEN_STATUSES = ("open", "fixing", "patch_ready", "patched_unverified")
MONACO_LANG = {
    "Python": "python", "JavaScript": "javascript", "TypeScript": "typescript", "Java": "java", "Go": "go",
    "Rust": "rust", "Ruby": "ruby", "PHP": "php", "C#": "csharp", "C": "c", "C++": "cpp", "Kotlin": "kotlin",
    "Shell": "shell", "PowerShell": "powershell", "SQL": "sql", "HTML": "html", "CSS": "css", "JSON": "json",
    "YAML": "yaml", "TOML": "ini", "Markdown": "markdown", "Swift": "swift", "Scala": "scala",
}


def _summary(s: Session, project: Project) -> dict:
    scan = latest_scan(s, project.id)
    open_count = 0
    if scan:
        open_count = s.exec(select(func.count()).select_from(Finding).where(
            Finding.scan_id == scan.id, Finding.status.in_(OPEN_STATUSES))).one()  # type: ignore[attr-defined]
    fixed = s.exec(select(func.count()).select_from(Finding).where(
        Finding.project_id == project.id, Finding.status == "fixed")).one()
    pending = s.exec(select(func.count()).select_from(Patch).where(
        Patch.project_id == project.id, Patch.status == "ready")).one()
    return {
        **project.model_dump(),
        "last_scan": {"id": scan.id, "finished_at": scan.finished_at, "version": scan.version} if scan else None,
        "open_findings": open_count, "fixed_findings": fixed, "pending_patches": pending,
    }


@router.get("")
def list_projects(s: Session = Depends(get_session)):
    projects = s.exec(select(Project).order_by(Project.updated_at.desc())).all()  # type: ignore[attr-defined]
    return [_summary(s, p) for p in projects]


def _get(s: Session, project_id: int) -> Project:
    project = s.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


@router.get("/{project_id}")
def get_project(project_id: int, s: Session = Depends(get_session)):
    project = _get(s, project_id)
    ws = Workspace(project_id)
    data = _summary(s, project)
    data["head"] = ws.head()
    data["versions"] = len(ws.versions())
    return data


class Rename(BaseModel):
    name: str


@router.patch("/{project_id}")
def rename_project(project_id: int, body: Rename, s: Session = Depends(get_session)):
    project = _get(s, project_id)
    if not body.name.strip():
        raise HTTPException(400, "Name cannot be empty")
    project.name = body.name.strip()[:120]
    project.updated_at = now_iso()
    s.add(project)
    s.commit()
    return _summary(s, project)


@router.delete("/{project_id}")
def remove_project(project_id: int, s: Session = Depends(get_session)):
    _get(s, project_id)
    s.close()
    delete_project(project_id)
    return {"deleted": project_id}


# ---------------------------------------------------------------- import


@router.post("/upload")
async def upload_files(files: list[UploadFile] = File(...), paths: list[str] = Form(default=[]),
                       name: str = Form(default="")):
    """Files and folders. Each upload is streamed to disk in 1 MB chunks, never held in memory."""
    raw = Path(tempfile.mkdtemp(prefix="autopatch-upload-"))
    rels = []
    try:
        for i, upload in enumerate(files):
            rel = safe_relpath(paths[i] if i < len(paths) and paths[i] else (upload.filename or f"file{i}"))
            rels.append(rel)
            target = raw / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as out:
                shutil.copyfileobj(upload.file, out, 1024 * 1024)
        if not rels:
            raise ProjectImportError("No files were uploaded")
        top = common_root(rels)
        source = "folder" if any(len(r.parts) > 1 for r in rels) else "files"

        def populate(dest: Path):
            report = copy_tree(raw / top if top else raw, dest)
            return (top or (rels[0].stem if len(rels) == 1 else None)), report

        return create_project(name or None, source, f"{len(rels)} uploaded file(s)", populate)
    finally:
        shutil.rmtree(raw, ignore_errors=True)


@router.post("/upload-zip")
async def upload_zip(file: UploadFile = File(...), name: str = Form(default="")):
    with tempfile.TemporaryDirectory(prefix="autopatch-zip-") as tmp:
        zpath = Path(tmp) / "upload.zip"
        with open(zpath, "wb") as out:  # streamed to disk
            shutil.copyfileobj(file.file, out, 1024 * 1024)
        stem = PurePosixPath(file.filename or "project.zip").stem

        def populate(dest: Path):
            report = Report()
            _count, top = extract_zip(zpath, dest, report)
            return (top or stem), report

        return create_project(name or None, "zip", file.filename or "upload.zip", populate)


class ImportPath(BaseModel):
    path: str
    name: str = ""


@router.post("/import-path")
def import_path(body: ImportPath):
    src = Path(body.path.strip().strip('"')).expanduser()
    if not src.is_dir():
        raise HTTPException(400, f"Folder not found: {src}")

    def populate(dest: Path):
        return src.resolve().name, copy_tree(src, dest)

    return create_project(body.name or None, "path", str(src.resolve()), populate)


class CloneRepo(BaseModel):
    url: str
    name: str = ""


@router.post("/clone")
def clone_repo(body: CloneRepo):
    def populate(dest: Path):
        with tempfile.TemporaryDirectory(prefix="autopatch-clone-") as tmp:
            target = Path(tmp) / "repo"
            clone_git(body.url, target)
            report = copy_tree(target, dest)
        return PurePosixPath(body.url.rstrip("/")).stem, report

    return create_project(body.name or None, "git", body.url.strip(), populate)


# ---------------------------------------------------------------- explorer


def _language(path: str) -> str:
    return LANGUAGE_BY_EXTENSION.get(PurePosixPath(path).suffix.lower(), "")


def _finding_counts(s: Session, project_id: int) -> dict[str, int]:
    scan = latest_scan(s, project_id)
    if not scan:
        return {}
    rows = s.exec(select(Finding.file, func.count()).where(
        Finding.scan_id == scan.id, Finding.status.in_(OPEN_STATUSES)).group_by(Finding.file)).all()  # type: ignore[attr-defined]
    return {f: n for f, n in rows}


@router.get("/{project_id}/tree")
def tree(project_id: int, version: str | None = None, s: Session = Depends(get_session)):
    _get(s, project_id)
    ws = Workspace(project_id)
    files = ws.list_files(version)
    counts = _finding_counts(s, project_id) if not version else {}
    root: dict = {"name": "", "path": "", "type": "dir", "children": {}}
    for path in files:
        node = root
        parts = PurePosixPath(path).parts
        for i, part in enumerate(parts[:-1]):
            node = node["children"].setdefault(part, {"name": part, "path": "/".join(parts[: i + 1]),
                                                      "type": "dir", "children": {}})
        node["children"][parts[-1]] = {"name": parts[-1], "path": path, "type": "file",
                                       "language": _language(path), "findings": counts.get(path, 0)}

    def finish(node: dict) -> dict:
        if node["type"] == "file":
            return node
        kids = [finish(c) for c in node["children"].values()]
        kids.sort(key=lambda c: (c["type"] != "dir", c["name"].lower()))
        total = sum(c.get("findings", 0) for c in kids)
        return {**node, "children": kids, "findings": total}

    return {"version": ws.resolve(version), "files": len(files), "root": finish(root)}


@router.get("/{project_id}/file")
def read_file(project_id: int, path: str, version: str | None = None, s: Session = Depends(get_session)):
    _get(s, project_id)
    ws = Workspace(project_id)
    try:
        data = ws.read_bytes(path, version)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    lang = _language(path)
    result = {"path": path, "version": ws.resolve(version), "size": len(data), "language": lang,
              "monaco_language": MONACO_LANG.get(lang, "plaintext"), "binary": looks_binary(data)}
    result["content"] = "" if result["binary"] else data.decode("utf-8", "replace")
    findings = []
    scan = latest_scan(s, project_id)
    if scan and not version:
        findings = s.exec(select(Finding).where(Finding.scan_id == scan.id, Finding.file == path)
                          .order_by(Finding.line_start)).all()
    result["findings"] = [{"id": f.id, "title": f.title, "severity": f.severity, "line_start": f.line_start,
                           "line_end": f.line_end, "status": f.status, "verification": f.verification}
                          for f in findings]
    return result


@router.get("/{project_id}/structure")
def structure(project_id: int, s: Session = Depends(get_session)):
    project = _get(s, project_id)
    ws = Workspace(project_id)
    analyzer = analyzer_for("Python")
    st = analyzer.structure(ws.root, [f for f in ws.list_files() if analyzer.handles(f)])
    data = st.to_dict()
    data["languages"] = project.languages
    data["declared_dependencies"] = project.dependencies or st.declared_dependencies
    data["external_imports"] = sorted({e for m in st.modules.values() for e in m.external_imports})
    data["syntax_errors"] = [i.to_dict() for i in st.syntax_issues]
    return data


@router.get("/{project_id}/download")
def download(project_id: int, version: str | None = None, s: Session = Depends(get_session)):
    project = _get(s, project_id)
    ws = Workspace(project_id)
    sha = ws.resolve(version)
    filename = f"{project.name}-{sha[:8]}.zip".replace(" ", "_")
    return Response(ws.archive_zip(sha), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/{project_id}/scans")
def list_scans(project_id: int, s: Session = Depends(get_session)):
    _get(s, project_id)
    return s.exec(select(Scan).where(Scan.project_id == project_id).order_by(Scan.id.desc())).all()  # type: ignore[attr-defined]


__all__ = ["router", "ProjectImportError"]


# ---------------------------------------------------------------- universal file reports

REPAIR_ORDER = ["fixable", "partially_fixable", "requires_human", "not_fixable", "unsupported"]


@router.get("/{project_id}/file-reports")
def file_reports(project_id: int, scan_id: int | None = None, s: Session = Depends(get_session)):
    from autopatch.db import FileReport

    project = _get(s, project_id)
    scan = s.get(Scan, scan_id) if scan_id else latest_scan(s, project_id)
    if scan is None:
        return {"scan": None, "files": [], "summary": {}, "import_report": project.import_report}
    rows = s.exec(select(FileReport).where(FileReport.scan_id == scan.id).order_by(FileReport.path)).all()
    findings = s.exec(select(Finding).where(Finding.scan_id == scan.id)).all()
    by_file: dict[str, list[Finding]] = {}
    for f in findings:
        by_file.setdefault(f.file, []).append(f)
    files = []
    for r in rows:
        code = [f for f in by_file.get(r.path, []) if not any(src.startswith("fileintel:") for src in f.sources)]
        linked = {(f.facts or {}).get("code"): f.id for f in by_file.get(r.path, []) if (f.facts or {}).get("kind") == "format"}
        issues = [{**i, "finding_id": linked.get(i["code"])} for i in r.issues]
        statuses = [i["repairability"] for i in r.issues] + (["fixable" if any("core:" in "".join(f.sources) for f in code)
                                                              else "requires_human"] if code else [])
        files.append({**r.model_dump(), "issues": issues,
                      "code_findings": [{"id": f.id, "title": f.title, "severity": f.severity, "status": f.status,
                                         "verification": f.verification, "line_start": f.line_start} for f in code],
                      "repairability": min(statuses, key=REPAIR_ORDER.index) if statuses else ""})
    summary = {
        "files": len(files),
        "by_status": {k: sum(1 for f in files if f["status"] == k) for k in
                      ("analyzed", "partially_analyzed", "identified_only", "skipped", "unreadable")},
        "by_category": {},
        "with_issues": sum(1 for f in files if f["issues"] or f["code_findings"]),
        "by_repairability": {k: sum(1 for f in files for i in f["issues"] if i["repairability"] == k) for k in REPAIR_ORDER},
    }
    for f in files:
        summary["by_category"][f["category"]] = summary["by_category"].get(f["category"], 0) + 1
    return {"scan": scan, "files": files, "summary": summary, "import_report": project.import_report}
