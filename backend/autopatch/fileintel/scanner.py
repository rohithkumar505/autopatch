"""Run file intelligence over a whole project, incrementally: unchanged files reuse their last report."""

from __future__ import annotations

from pathlib import Path

from autopatch.fileintel.identify import identify
from autopatch.fileintel.inspect import FIXABLE, PARTIAL, inspect

ORDER = ["critical", "high", "medium", "low", "info"]
INSPECTOR_VERSION = "3"  # bump when inspectors change so cached reports are recomputed


def analyze_project(root: Path, blobs: dict[str, str], previous: dict[str, dict], analysis_limit: int,
                    log=lambda msg: None) -> list[dict]:
    reports = []
    total = len(blobs)
    reused = 0
    for n, (rel, sha) in enumerate(sorted(blobs.items()), 1):
        # Reuse only if the content AND the inspectors are unchanged since the last scan.
        blob = f"{sha}:{INSPECTOR_VERSION}"
        prev = previous.get(rel)
        if prev and prev.get("blob") == blob:
            reports.append({**prev, "reused": True})
            reused += 1
            continue
        try:
            ident = identify(root, rel)
            result = inspect(root, ident, analysis_limit)
            reports.append({
                "path": rel, "blob": blob, "size": ident.size, "format": ident.format, "mime": ident.mime,
                "category": ident.category, "encoding": ident.encoding, "language": ident.language,
                "extension_mismatch": ident.extension_mismatch, "status": result.status, "method": result.method,
                "issues": [i.to_dict() for i in result.issues], "notes": result.notes, "reused": False,
            })
        except OSError as exc:
            reports.append({"path": rel, "blob": blob, "size": 0, "format": "unknown", "mime": "", "category": "binary",
                            "encoding": "", "language": "", "extension_mismatch": "", "status": "unreadable",
                            "method": "none", "issues": [], "notes": [f"Could not read the file: {exc}"], "reused": False})
        if n % 500 == 0:
            log(f"File intelligence: {n:,} of {total:,} files")
    for r in reports:
        if r["extension_mismatch"] and not any(i["code"] == "format.extension-mismatch" for i in r["issues"]):
            r["issues"].append({
                "code": "format.extension-mismatch", "title": "Extension does not match content", "severity": "low",
                "message": r["extension_mismatch"].capitalize() + ".", "repairability": "requires_human",
                "reason": "Renaming could break references to the file; the right name is a decision.",
                "options": ["Rename the file to match its real format"], "limitations": "", "confidence": 0.9,
                "requirements": "", "line": 0, "column": 0, "repair": ""})
    log(f"File intelligence: {total:,} files identified ({reused:,} unchanged since the last scan)")
    return reports


def worst(issues: list[dict]) -> str:
    return min((i["severity"] for i in issues), key=ORDER.index, default="")


def repairable(issue: dict) -> bool:
    return issue["repairability"] in (FIXABLE, PARTIAL) and bool(issue.get("repair"))
