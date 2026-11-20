"""Create, update and delete projects."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable

from sqlmodel import delete, select

from autopatch.analysis.base import analyzer_for, detect_languages
from autopatch.config import paths
from autopatch.core.files import ProjectImportError, rmtree
from autopatch.core.workspace import Workspace
from autopatch.db import (
    Event, FileReport, Finding, Job, Patch, Project, Scan, TestRunRow, now_iso, record_event, session,
)


def create_project(name: str | None, source_type: str, source_ref: str,
                   populate: Callable[[Path], object]) -> Project:
    """populate(staging_dir) writes the files and returns a suggested name, or (name, import report)."""
    staging = paths().uploads / uuid.uuid4().hex
    staging.mkdir(parents=True)
    project_id = None
    try:
        result = populate(staging)
        suggested, import_report = result if isinstance(result, tuple) else (result, {})
        if hasattr(import_report, "to_dict"):
            import_report = import_report.to_dict()
        final_name = (name or suggested or source_ref or "project").strip()[:120] or "project"
        with session() as s:
            project = Project(name=final_name, source_type=source_type, source_ref=source_ref[:500])
            s.add(project)
            s.commit()
            s.refresh(project)
            project_id = project.id
        ws = Workspace.create(project_id, staging)
        files = ws.list_files()
        languages = detect_languages(files)
        deps: list[str] = []
        if "Python" in languages:
            deps = analyzer_for("Python").structure(ws.root, [f for f in files if f.endswith(".py") or
                                                              f.endswith((".txt", ".toml"))]).declared_dependencies
        with session() as s:
            project = s.get(Project, project_id)
            project.original_version = ws.head()
            project.file_count = len(files)
            project.languages = languages
            project.dependencies = deps
            project.import_report = import_report or {"imported": len(files), "skipped": []}
            project.updated_at = now_iso()
            s.add(project)
            skipped = sum(x.get("files", 1) for x in project.import_report.get("skipped", []))
            record_event(s, "project_imported", f"Imported {final_name}: {len(files)} files"
                         + (f", {skipped:,} not imported (see the import report)" if skipped else ""), project_id,
                         source=source_type, version=project.original_version)
            s.commit()
            s.refresh(project)
            return project
    except Exception:
        if project_id is not None:
            delete_project(project_id)
        raise
    finally:
        rmtree(staging)


def delete_project(project_id: int) -> None:
    with session() as s:
        for model in (Finding, Patch, Scan, TestRunRow, Event, Job, FileReport):
            s.exec(delete(model).where(model.project_id == project_id))  # type: ignore[attr-defined]
        project = s.get(Project, project_id)
        if project:
            s.delete(project)
        s.commit()
    Workspace(project_id).delete()


def get_project(s, project_id: int) -> Project:
    project = s.get(Project, project_id)
    if project is None:
        raise ProjectImportError(f"Project {project_id} does not exist")
    return project


def latest_scan(s, project_id: int) -> Scan | None:
    return s.exec(
        select(Scan).where(Scan.project_id == project_id, Scan.status == "completed").order_by(Scan.id.desc())  # type: ignore[attr-defined]
    ).first()
