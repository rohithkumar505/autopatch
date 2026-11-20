"""IDE workspace file-system APIs. All paths are confined to the project's Workspace."""

from __future__ import annotations

import base64
import mimetypes
import shutil
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session, func, select

from autopatch.analysis.base import LANGUAGE_BY_EXTENSION
from autopatch.core.files import ProjectImportError, looks_binary, safe_relpath
from autopatch.core.workspace import Workspace
from autopatch.db import Finding, Project, get_session, now_iso, record_event
from autopatch.pipeline.projects import latest_scan

router = APIRouter(prefix="/api/projects", tags=["ide"])

OPEN_STATUSES = ("open", "fixing", "patch_ready", "patched_unverified")
TEXT_SUFFIXES = set(LANGUAGE_BY_EXTENSION) | {
    ".pyi", ".jsonc", ".txt", ".rst", ".ini", ".cfg", ".env", ".xml", ".svg",
    ".bash", ".zsh", ".toml", ".gitignore", ".editorconfig", ".dockerfile",
}
MAX_SEARCH_HITS = 200
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = 32 * 1024 * 1024

MONACO_LANG = {
    "Python": "python", "JavaScript": "javascript", "TypeScript": "typescript", "Java": "java",
    "Go": "go", "Rust": "rust", "Ruby": "ruby", "PHP": "php", "C#": "csharp", "C": "c", "C++": "cpp",
    "Kotlin": "kotlin", "Shell": "shell", "PowerShell": "powershell", "SQL": "sql", "HTML": "html",
    "CSS": "css", "JSON": "json", "YAML": "yaml", "TOML": "ini", "Markdown": "markdown", "Swift": "swift",
    "Scala": "scala",
}


def _get(s: Session, project_id: int) -> Project:
    project = s.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


def _touch(s: Session, project: Project) -> None:
    project.updated_at = now_iso()
    s.add(project)


def _ws(project_id: int) -> Workspace:
    ws = Workspace(project_id)
    if not ws.exists():
        raise HTTPException(404, "Project workspace not found")
    return ws


def _safe(ws: Workspace, path: str) -> tuple[str, Path]:
    try:
        return ws.resolve_contained(path)
    except (ProjectImportError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _language(path: str) -> str:
    name = PurePosixPath(path).name.lower()
    if name in ("dockerfile", "makefile", "cmakelists.txt"):
        return "Shell" if name == "dockerfile" else "plaintext"
    return LANGUAGE_BY_EXTENSION.get(PurePosixPath(path).suffix.lower(), "")


def _commit(ws: Workspace, message: str, commit: bool) -> str | None:
    if not commit:
        return None
    return ws.commit_all(message[:200])


# ---------------------------------------------------------------- models


class SaveFile(BaseModel):
    path: str
    content: str
    commit: bool = True
    message: str = ""


class CreateEntry(BaseModel):
    path: str
    kind: str = Field(description="'file' or 'dir'")
    content: str = ""
    commit: bool = True


class PathPair(BaseModel):
    path: str
    new_path: str
    commit: bool = True


class DeleteBody(BaseModel):
    path: str
    commit: bool = True


# ---------------------------------------------------------------- worktree listing


@router.get("/{project_id}/fs/tree")
def fs_tree(project_id: int, s: Session = Depends(get_session)):
    """Directory listing from the real worktree (includes empty folders)."""
    _get(s, project_id)
    ws = _ws(project_id)
    files, dirs = ws.iter_worktree()
    counts: dict[str, int] = {}
    scan = latest_scan(s, project_id)
    if scan:
        rows = s.exec(select(Finding.file, func.count()).where(
            Finding.scan_id == scan.id, Finding.status.in_(OPEN_STATUSES)  # type: ignore[attr-defined]
        ).group_by(Finding.file)).all()
        counts = {f: n for f, n in rows}

    root: dict = {"name": "", "path": "", "type": "dir", "children": {}}
    for d in dirs:
        node = root
        parts = PurePosixPath(d).parts
        for i, part in enumerate(parts):
            node = node["children"].setdefault(
                part, {"name": part, "path": "/".join(parts[: i + 1]), "type": "dir", "children": {}}
            )
    for path in files:
        node = root
        parts = PurePosixPath(path).parts
        for i, part in enumerate(parts[:-1]):
            node = node["children"].setdefault(
                part, {"name": part, "path": "/".join(parts[: i + 1]), "type": "dir", "children": {}}
            )
        node["children"][parts[-1]] = {
            "name": parts[-1], "path": path, "type": "file",
            "language": _language(path), "findings": counts.get(path, 0),
        }

    def finish(node: dict) -> dict:
        if node["type"] == "file":
            return node
        kids = [finish(c) for c in node["children"].values()]
        kids.sort(key=lambda c: (c["type"] != "dir", c["name"].lower()))
        total = sum(c.get("findings", 0) for c in kids)
        return {**node, "children": kids, "findings": total}

    return {
        "version": ws.head(),
        "files": len(files),
        "dirs": len(dirs),
        "root": finish(root),
        "source": "worktree",
    }


# ---------------------------------------------------------------- read / write / CRUD


@router.put("/{project_id}/file")
def save_file(project_id: int, body: SaveFile, s: Session = Depends(get_session)):
    project = _get(s, project_id)
    ws = _ws(project_id)
    rel, target = _safe(ws, body.path)
    if target.exists() and target.is_dir():
        raise HTTPException(400, f"{rel} is a directory")
    if len(body.content.encode("utf-8")) > MAX_FILE_BYTES:
        raise HTTPException(400, f"File exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB limit")
    try:
        ws.write_text(rel, body.content)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    sha = _commit(ws, body.message or f"IDE: update {rel}", body.commit)
    _touch(s, project)
    record_event(s, "ide_edit", f"Saved {rel}", project_id=project_id, path=rel, version=sha)
    s.commit()
    return {"path": rel, "version": sha or ws.head(), "committed": bool(sha)}


@router.post("/{project_id}/fs")
def create_entry(project_id: int, body: CreateEntry, s: Session = Depends(get_session)):
    project = _get(s, project_id)
    ws = _ws(project_id)
    rel, target = _safe(ws, body.path)
    if target.exists():
        raise HTTPException(400, f"{rel} already exists")
    try:
        if body.kind == "dir":
            ws.mkdir(rel)
            # Keep empty folders visible to git via .gitkeep
            keep = f"{rel}/.gitkeep"
            ws.write_text(keep, "")
            msg = f"IDE: create folder {rel}"
        elif body.kind == "file":
            ws.write_text(rel, body.content)
            msg = f"IDE: create {rel}"
        else:
            raise HTTPException(400, "kind must be 'file' or 'dir'")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    sha = _commit(ws, msg, body.commit)
    _touch(s, project)
    record_event(s, "ide_create", msg, project_id=project_id, path=rel, entry_kind=body.kind, version=sha)
    s.commit()
    return {"path": rel, "kind": body.kind, "version": sha or ws.head(), "committed": bool(sha)}


@router.post("/{project_id}/fs/rename")
def rename_entry(project_id: int, body: PathPair, s: Session = Depends(get_session)):
    """Rename or move a file/folder inside the workspace."""
    project = _get(s, project_id)
    ws = _ws(project_id)
    src, _ = _safe(ws, body.path)
    dest, _ = _safe(ws, body.new_path)
    try:
        ws.rename_path(src, dest)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    sha = _commit(ws, f"IDE: move {src} → {dest}", body.commit)
    _touch(s, project)
    record_event(s, "ide_rename", f"Moved {src} → {dest}", project_id=project_id, path=src, new_path=dest, version=sha)
    s.commit()
    return {"path": dest, "from": src, "version": sha or ws.head(), "committed": bool(sha)}


@router.post("/{project_id}/fs/copy")
def copy_entry(project_id: int, body: PathPair, s: Session = Depends(get_session)):
    project = _get(s, project_id)
    ws = _ws(project_id)
    src, _ = _safe(ws, body.path)
    dest, _ = _safe(ws, body.new_path)
    try:
        ws.copy_path(src, dest)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    sha = _commit(ws, f"IDE: copy {src} → {dest}", body.commit)
    _touch(s, project)
    record_event(s, "ide_copy", f"Copied {src} → {dest}", project_id=project_id, path=src, new_path=dest, version=sha)
    s.commit()
    return {"path": dest, "from": src, "version": sha or ws.head(), "committed": bool(sha)}


@router.post("/{project_id}/fs/move")
def move_entry(project_id: int, body: PathPair, s: Session = Depends(get_session)):
    """Alias of rename — explicit move for drag-and-drop clients."""
    return rename_entry(project_id, body, s)


@router.delete("/{project_id}/fs")
def delete_entry(
    project_id: int,
    path: str = Query(...),
    commit: bool = True,
    s: Session = Depends(get_session),
):
    project = _get(s, project_id)
    ws = _ws(project_id)
    rel, _ = _safe(ws, path)
    try:
        ws.delete_path(rel)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    sha = _commit(ws, f"IDE: delete {rel}", commit)
    _touch(s, project)
    record_event(s, "ide_delete", f"Deleted {rel}", project_id=project_id, path=rel, version=sha)
    s.commit()
    return {"deleted": rel, "version": sha or ws.head(), "committed": bool(sha)}


# ---------------------------------------------------------------- upload / download


@router.post("/{project_id}/fs/upload")
async def upload_into_workspace(
    project_id: int,
    files: list[UploadFile] = File(...),
    dest_dir: str = Form(default=""),
    paths: list[str] = Form(default=[]),
    s: Session = Depends(get_session),
):
    """Upload one or more files into a folder inside the project workspace."""
    project = _get(s, project_id)
    ws = _ws(project_id)
    base = ""
    if dest_dir.strip():
        base, base_path = _safe(ws, dest_dir.strip())
        if base_path.exists() and not base_path.is_dir():
            raise HTTPException(400, f"{base} is not a directory")
        base_path.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    try:
        for i, upload in enumerate(files):
            raw_name = paths[i] if i < len(paths) and paths[i] else (upload.filename or f"upload-{i}")
            # paths may include nested relative segments from folder drag-drop
            rel_upload = str(safe_relpath(raw_name.replace("\\", "/")))
            dest = f"{base}/{rel_upload}" if base else rel_upload
            rel, target = _safe(ws, dest)
            target.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with open(target, "wb") as out:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        out.close()
                        target.unlink(missing_ok=True)
                        raise HTTPException(400, f"{rel} exceeds upload size limit")
                    out.write(chunk)
            written.append(rel)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    sha = _commit(ws, f"IDE: upload {len(written)} file(s)", True)
    _touch(s, project)
    record_event(s, "ide_upload", f"Uploaded {len(written)} file(s)", project_id=project_id, files=written, version=sha)
    s.commit()
    return {"files": written, "version": sha or ws.head(), "committed": bool(sha)}


@router.get("/{project_id}/fs/download")
def download_path(project_id: int, path: str = Query(...), s: Session = Depends(get_session)):
    """Download a single file, or a folder as a zip, from the workspace."""
    _get(s, project_id)
    ws = _ws(project_id)
    rel, target = _safe(ws, path)
    if not target.exists():
        raise HTTPException(404, f"{rel} does not exist")
    if target.is_file():
        data = target.read_bytes()
        mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        filename = PurePosixPath(rel).name
        return Response(
            data,
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    # folder → zip
    import tempfile
    with tempfile.TemporaryDirectory(prefix="autopatch-dl-") as tmp:
        zip_base = Path(tmp) / "archive"
        shutil.make_archive(str(zip_base), "zip", root_dir=target)
        data = (Path(str(zip_base) + ".zip")).read_bytes()
    filename = f"{PurePosixPath(rel).name or 'folder'}.zip"
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project_id}/fs/stat")
def stat_path(project_id: int, path: str = Query(...), s: Session = Depends(get_session)):
    """Metadata + preview hint for a path (used by file preview)."""
    _get(s, project_id)
    ws = _ws(project_id)
    rel, target = _safe(ws, path)
    if not target.exists():
        raise HTTPException(404, f"{rel} does not exist")
    if target.is_dir():
        children = list(target.iterdir())
        return {
            "path": rel, "type": "dir", "size": 0,
            "children": len(children),
            "language": "", "monaco_language": "plaintext", "binary": False, "preview": "folder",
        }
    data = target.read_bytes()
    lang = _language(rel)
    binary = looks_binary(data)
    mime = mimetypes.guess_type(rel)[0] or ("application/octet-stream" if binary else "text/plain")
    preview = "text"
    b64 = ""
    if mime.startswith("image/") and len(data) <= MAX_FILE_BYTES:
        preview = "image"
        b64 = base64.b64encode(data).decode("ascii")
    elif binary:
        preview = "binary"
    return {
        "path": rel,
        "type": "file",
        "size": len(data),
        "language": lang,
        "monaco_language": MONACO_LANG.get(lang, "plaintext"),
        "mime": mime,
        "binary": binary,
        "preview": preview,
        "image_base64": b64,
        "content": "" if binary else data.decode("utf-8", "replace"),
    }


# ---------------------------------------------------------------- search / git / problems (preserved)


@router.get("/{project_id}/search")
def search_project(
    project_id: int,
    q: str = Query(..., min_length=1),
    max_hits: int = Query(MAX_SEARCH_HITS, ge=1, le=500),
    s: Session = Depends(get_session),
):
    _get(s, project_id)
    ws = _ws(project_id)
    needle_l = q.lower()
    hits: list[dict] = []
    files, _dirs = ws.iter_worktree()
    for path in files:
        suffix = PurePosixPath(path).suffix.lower()
        name = PurePosixPath(path).name
        if needle_l in name.lower():
            hits.append({"path": path, "line": 0, "col": 0, "preview": name, "kind": "filename"})
            if len(hits) >= max_hits:
                return {"query": q, "hits": hits, "truncated": True}
        if suffix and suffix not in TEXT_SUFFIXES and name.lower() not in ("dockerfile", "makefile", "license"):
            continue
        try:
            raw = (ws.root / path).read_bytes()
        except OSError:
            continue
        if len(raw) > MAX_FILE_BYTES or looks_binary(raw):
            continue
        text = raw.decode("utf-8", "replace")
        for i, line in enumerate(text.splitlines(), start=1):
            idx = line.lower().find(needle_l)
            if idx < 0:
                continue
            hits.append({
                "path": path, "line": i, "col": idx + 1, "preview": line.strip()[:240],
                "kind": "content", "language": _language(path),
            })
            if len(hits) >= max_hits:
                return {"query": q, "hits": hits, "truncated": True}
    return {"query": q, "hits": hits, "truncated": False}


@router.get("/{project_id}/git/status")
def git_status(project_id: int, s: Session = Depends(get_session)):
    _get(s, project_id)
    ws = _ws(project_id)
    versions = ws.versions()[:30]
    return {
        "head": ws.head(),
        "changes": ws.working_tree_status(),
        "versions": [
            {"sha": v.sha, "short": v.short, "message": v.message, "date": v.date, "parent": v.parent}
            for v in versions
        ],
    }


@router.get("/{project_id}/problems")
def problems(project_id: int, s: Session = Depends(get_session)):
    _get(s, project_id)
    scan = latest_scan(s, project_id)
    if scan is None:
        return {"scan": None, "problems": []}
    rows = s.exec(
        select(Finding).where(Finding.scan_id == scan.id, Finding.status.in_(OPEN_STATUSES))  # type: ignore[attr-defined]
        .order_by(Finding.priority.desc(), Finding.severity)
    ).all()
    return {
        "scan": {"id": scan.id, "finished_at": scan.finished_at, "version": scan.version},
        "problems": [
            {
                "id": f.id, "title": f.title, "file": f.file, "line": f.line_start, "end_line": f.line_end,
                "severity": f.severity, "category": f.category, "status": f.status,
                "verification": f.verification, "confidence": f.confidence,
            }
            for f in rows
        ],
    }
