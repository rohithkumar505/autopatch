"""Editor support APIs: format document and symbol/definition lookup from real project structure."""

from __future__ import annotations

import json
import subprocess
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from autopatch.analysis.base import analyzer_for
from autopatch.api.ide import MONACO_LANG, _get, _language, _safe, _ws
from autopatch.core.files import looks_binary
from autopatch.db import get_session, now_iso, record_event

router = APIRouter(prefix="/api/projects", tags=["editor"])


class FormatBody(BaseModel):
    path: str
    content: str | None = None  # if omitted, format the file on disk
    commit: bool = False


class DefinitionQuery(BaseModel):
    path: str
    line: int
    column: int = 1
    word: str = ""


@router.post("/{project_id}/format")
def format_document(project_id: int, body: FormatBody, s: Session = Depends(get_session)):
    """Format a workspace file. Python uses Ruff; JSON/JSONC pretty-print; others return normalized newlines."""
    project = _get(s, project_id)
    ws = _ws(project_id)
    rel, target = _safe(ws, body.path)
    if body.content is not None:
        text = body.content
    else:
        if not target.exists() or not target.is_file():
            raise HTTPException(404, f"{rel} not found")
        raw = target.read_bytes()
        if looks_binary(raw):
            raise HTTPException(400, "Cannot format a binary file")
        text = raw.decode("utf-8", "replace")

    suffix = PurePosixPath(rel).suffix.lower()
    formatted = text
    tool = "normalize"

    if suffix == ".py":
        try:
            proc = subprocess.run(
                ["ruff", "format", "-", "--stdin-filename", rel],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout:
                formatted = proc.stdout.decode("utf-8")
                tool = "ruff"
            else:
                # Fall back to light cleanup if ruff unavailable/fails.
                formatted = text.replace("\r\n", "\n")
                tool = "normalize"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            formatted = text.replace("\r\n", "\n")
            tool = "normalize"
    elif suffix in (".json", ".jsonc"):
        try:
            formatted = json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"
            tool = "json"
        except json.JSONDecodeError:
            formatted = text.replace("\r\n", "\n")
            tool = "normalize"
    else:
        formatted = text.replace("\r\n", "\n")
        if not formatted.endswith("\n") and formatted:
            formatted += "\n"
        tool = "normalize"

    if body.commit or body.content is None:
        ws.write_text(rel, formatted)
        if body.commit:
            ws.commit_all(f"IDE: format {rel}")
            project.updated_at = now_iso()
            s.add(project)
            record_event(s, "ide_format", f"Formatted {rel} with {tool}", project_id=project_id, path=rel, tool=tool)
            s.commit()

    return {
        "path": rel,
        "content": formatted,
        "changed": formatted != text,
        "tool": tool,
        "language": _language(rel),
        "monaco_language": MONACO_LANG.get(_language(rel), "plaintext"),
    }


@router.post("/{project_id}/definition")
def goto_definition(project_id: int, body: DefinitionQuery, s: Session = Depends(get_session)):
    """Resolve a symbol under the cursor using AutoPatch's Python structure graph (LSP-lite)."""
    _get(s, project_id)
    ws = _ws(project_id)
    rel, _ = _safe(ws, body.path)
    analyzer = analyzer_for("Python")
    files = [f for f in ws.list_files() if analyzer.handles(f)]
    st = analyzer.structure(ws.root, files)
    word = (body.word or "").strip()
    if not word:
        # Read the line and extract a rough identifier at column.
        try:
            lines = ws.read_text(rel).splitlines()
            if 1 <= body.line <= len(lines):
                line = lines[body.line - 1]
                col = max(0, body.column - 1)
                i = col
                while i > 0 and (line[i - 1].isalnum() or line[i - 1] == "_"):
                    i -= 1
                j = col
                while j < len(line) and (line[j].isalnum() or line[j] == "_"):
                    j += 1
                word = line[i:j]
        except Exception:
            word = ""
    if not word:
        return {"word": "", "definitions": []}

    defs = []
    for sym in st.symbols.values():
        if sym.name == word or sym.qualname.endswith("." + word) or sym.qualname == word:
            defs.append({
                "name": sym.name,
                "qualname": sym.qualname,
                "kind": sym.kind,
                "file": sym.file,
                "line": sym.line_start,
                "end_line": sym.line_end,
            })
    # Prefer definitions in the same file, then others.
    defs.sort(key=lambda d: (0 if d["file"] == rel else 1, d["file"], d["line"]))
    return {"word": word, "definitions": defs}


@router.get("/{project_id}/completions")
def completions(project_id: int, path: str = "", s: Session = Depends(get_session)):
    """Symbol names from the project for Monaco completion (Python-focused, real structure)."""
    _get(s, project_id)
    ws = _ws(project_id)
    analyzer = analyzer_for("Python")
    files = [f for f in ws.list_files() if analyzer.handles(f)]
    st = analyzer.structure(ws.root, files)
    items = []
    for sym in st.symbols.values():
        if path and sym.file != path and not path.endswith(".py"):
            continue
        items.append({
            "label": sym.name,
            "detail": sym.qualname,
            "kind": sym.kind,
            "file": sym.file,
            "line": sym.line_start,
            "insertText": sym.name,
            "documentation": (sym.doc or "")[:400],
        })
    # Deduplicate by label+file
    seen = set()
    unique = []
    for it in items:
        key = (it["label"], it["file"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)
    return {"items": unique[:500], "path": path}
