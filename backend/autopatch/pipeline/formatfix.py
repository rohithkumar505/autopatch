"""Repairs for file-level issues found by file intelligence (JSON, YAML, encodings, PNG, JPEG ...).

The repair runs on a copy. It counts as validated only if the same inspector that found the issue
no longer reports it, it introduces no new issues, and the project's tests (when it has any and a
sandbox is ready) show no regressions.
"""

from __future__ import annotations

import base64
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from autopatch.core import jobs, settings as settings_mod
from autopatch.core.files import looks_binary
from autopatch.core.workspace import Workspace
from autopatch.db import Finding, Patch, record_event, session
from autopatch.fileintel.identify import identify
from autopatch.fileintel.inspect import PARTIAL, REPAIRS, inspect
from autopatch.pipeline.common import outcome_line, populate_from, test_run_row
from autopatch.pipeline.patching import unified_diff
from autopatch.testing import sandbox as sandbox_mod
from autopatch.testing.runner import run_pytest


def _inspect_bytes(rel: str, data: bytes, limit: int):
    with tempfile.TemporaryDirectory(prefix="autopatch-check-") as tmp:
        target = Path(tmp) / PurePosixPath(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        ident = identify(Path(tmp), rel)
        return ident, inspect(Path(tmp), ident, limit)


def _describe(data: bytes) -> str:
    if looks_binary(data):
        return f"<binary data, {len(data):,} bytes; first bytes {data[:16].hex(' ')}>"
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def run_format_fix(ctx: jobs.JobContext, patch_id: int) -> dict:
    cfg = settings_mod.get_all()
    limit = cfg["limits.analysis_max_mb"] * 1024 * 1024
    with session() as s:
        patch = s.get(Patch, patch_id)
        finding = s.get(Finding, patch.finding_id)
    facts = finding.facts or {}
    ws = Workspace(finding.project_id)
    version = ws.head()
    repair_id = facts.get("repair")
    try:
        if not repair_id or repair_id not in REPAIRS:
            raise RuntimeError(f"No automatic repair: classified as {facts.get('repairability', 'unknown').replace('_', ' ')}. "
                               f"{facts.get('reason', '')} Options: {'; '.join(facts.get('options', []))}")
        fn, what = REPAIRS[repair_id]
        original = ws.read_bytes(finding.file)
        ctx.log(f"Applying a {facts.get('format', 'format')}-aware repair to {finding.file}: {what}")
        repaired = fn(original)
        _, before = _inspect_bytes(finding.file, original, limit)
        _, after = _inspect_bytes(finding.file, repaired, limit)
        before_codes = {i.code for i in before.issues}
        after_codes = {i.code for i in after.issues}
        resolved = facts["code"] not in after_codes
        new = sorted(after_codes - before_codes)
        ctx.log(f"Re-inspected the repaired file with {after.method}: issue {'resolved' if resolved else 'still present'}"
                + (f"; new issues: {', '.join(new)}" if new else "; no new issues"))
        tests = None
        if any(PurePosixPath(f).name.startswith("test_") and f.endswith(".py") for f in ws.list_files()):
            try:
                backend = sandbox_mod.choose(cfg["testing.sandbox"], cfg["testing.allow_limited"])
            except sandbox_mod.SandboxUnavailable as exc:
                ctx.log(f"Project tests not run: {exc}", "warning")
            else:
                ctx.log("Running the project's tests before and after the repair")

                def with_bytes(data: bytes):
                    base = populate_from(ws, version)

                    def populate(dest: Path):
                        base(dest)
                        (dest / PurePosixPath(finding.file)).write_bytes(data)
                    return populate

                b = run_pytest(with_bytes(original), backend, timeout_s=cfg["testing.timeout_s"])
                a = run_pytest(with_bytes(repaired), backend, timeout_s=cfg["testing.timeout_s"])
                regressions = sorted(b.passing() - a.passing())
                tests = {"before": outcome_line(b), "after": outcome_line(a), "regressions": regressions}
                ctx.log(f"Tests: before {tests['before']}; after {tests['after']}")
                with session() as s:
                    s.add(test_run_row(b, finding.project_id, "original", version, patch_id, finding.id))
                    s.add(test_run_row(a, finding.project_id, "patched", version, patch_id, finding.id))
                    s.commit()
        clean = resolved and not new and not (tests and tests["regressions"])
        verdict = ("partially_validated" if facts.get("repairability") == PARTIAL else "validated") if clean else "failed"
        detail = {
            "verdict": verdict, "kind": "format", "checker": after.method, "resolved": resolved, "new_issues": new,
            "issues_before": sorted(before_codes), "issues_after": sorted(after_codes), "tests": tests,
            "limitations": facts.get("limitations", ""), "after_b64": base64.b64encode(repaired).decode(),
            "bytes_before": len(original), "bytes_after": len(repaired),
            "existing": None, "reproduced": [facts["code"]] if resolved else [], "regressions": (tests or {}).get("regressions", []),
            "generated_failing": [], "still_failing": [] if resolved else [facts["code"]],
        }
        before_text, after_text = _describe(original), _describe(repaired)
        with session() as s:
            row = s.get(Patch, patch_id)
            row.base_version = version
            row.status = "ready"
            row.before_content = before_text
            row.after_content = after_text
            row.diff = unified_diff(finding.file, before_text, after_text) if not looks_binary(original) else \
                f"binary repair: {len(original):,} -> {len(repaired):,} bytes"
            row.explanation = f"{what} {facts.get('reason', '')}".strip()
            row.behavior_change = facts.get("limitations") or "The file content means the same as before."
            row.attempts = 1
            row.validation = verdict
            row.validation_detail = detail
            s.add(row)
            f = s.get(Finding, finding.id)
            f.status = "patch_ready"
            s.add(f)
            record_event(s, "patch_generated", f"Format repair #{patch_id} for '{finding.title}': {verdict.replace('_', ' ')}",
                         finding.project_id, patch_id=patch_id, finding_id=finding.id, validation=verdict)
            s.commit()
        return {"patch_id": patch_id, "validation": verdict}
    except Exception as exc:
        with session() as s:
            row = s.get(Patch, patch_id)
            row.status = "failed"
            row.error = str(exc)
            f = s.get(Finding, finding.id)
            if f.status == "fixing":
                f.status = "open"
            s.add(row)
            s.add(f)
            s.commit()
        raise


__all__ = ["run_format_fix", "shutil"]
