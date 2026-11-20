"""Helpers shared by the scan, fix and test pipelines."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Callable

from autopatch.analysis.base import Structure, analyzer_for
from autopatch.core.files import looks_binary
from autopatch.core.workspace import Workspace
from autopatch.db import TestRunRow
from autopatch.testing import sandbox
from autopatch.testing.runner import RunOutcome

MAX_TEXT_FILE = 1_000_000


def load_text_files(ws: Workspace, version: str | None = None) -> dict[str, str]:
    files = {}
    for rel in ws.list_files(version):
        data = ws.read_bytes(rel, version)
        if len(data) > MAX_TEXT_FILE or looks_binary(data):
            continue
        files[rel] = data.decode("utf-8", "replace").replace("\r\n", "\n")
    return files


def python_structure(ws: Workspace) -> Structure:
    analyzer = analyzer_for("Python")
    return analyzer.structure(ws.root, [f for f in ws.list_files() if analyzer.handles(f)])


def backend_from_settings(settings: dict) -> sandbox.Sandbox:
    return sandbox.choose(settings["testing.sandbox"], settings["testing.allow_limited"])


def populate_from(ws: Workspace, version: str, overrides: dict[str, str] | None = None,
                  crlf: dict[str, bool] | None = None) -> Callable[[Path], None]:
    """Build the sandbox copy: the files of `version`, with some files replaced or added."""
    def populate(dest: Path) -> None:
        ws.export_to(dest, version)
        for rel, content in (overrides or {}).items():
            target = dest / PurePosixPath(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            newline = "\r\n" if (crlf or {}).get(rel) else "\n"
            with open(target, "w", encoding="utf-8", newline=newline) as fh:
                fh.write(content)
    return populate


def test_run_row(outcome: RunOutcome, project_id: int, kind: str, version: str,
                 patch_id: int | None = None, finding_id: int | None = None) -> TestRunRow:
    return TestRunRow(
        project_id=project_id, patch_id=patch_id, finding_id=finding_id, kind=kind, version=version,
        status=outcome.status, passed=outcome.passed, failed=outcome.failed, errors=outcome.errors,
        skipped=outcome.skipped, duration_s=outcome.duration_s,
        results=[t.__dict__ for t in outcome.tests], output=outcome.output, sandbox=outcome.sandbox,
    )


def outcome_line(outcome: RunOutcome) -> str:
    if outcome.status in ("timeout", "crashed", "no_tests", "error") and not outcome.tests:
        return {"timeout": "timed out", "crashed": "could not run", "no_tests": "no tests found",
                "error": "test collection failed"}[outcome.status]
    parts = [f"{outcome.passed} passed"]
    if outcome.failed:
        parts.append(f"{outcome.failed} failed")
    if outcome.errors:
        parts.append(f"{outcome.errors} errors")
    if outcome.skipped:
        parts.append(f"{outcome.skipped} skipped")
    return ", ".join(parts) + f" in {outcome.duration_s:.1f}s"
