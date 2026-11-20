"""Review decisions and version history: accept, reject, revert, restore, run project tests."""

from __future__ import annotations

from sqlmodel import select

from autopatch.core import jobs, settings as settings_mod
from autopatch.core.workspace import GitError, Workspace
from autopatch.db import Finding, Patch, Project, TestRunRow, now_iso, record_event, session
from autopatch.pipeline.common import backend_from_settings, outcome_line, populate_from, test_run_row
from autopatch.testing.runner import run_pytest


class ReviewError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def is_stale(patch: Patch, head: str) -> bool:
    return patch.status == "ready" and bool(patch.base_version) and patch.base_version != head


def accept_patch(patch_id: int, include_tests: bool = True, force: bool = False, note: str = "") -> Patch:
    with session() as s:
        patch = s.get(Patch, patch_id)
        if patch is None:
            raise ReviewError("Patch not found", 404)
        if patch.status != "ready":
            raise ReviewError(f"Only patches waiting for review can be accepted (this one is {patch.status})")
        if patch.validation not in ("validated", "partially_validated") and not force:
            raise ReviewError("This patch did not pass validation. Review the test results; accepting it "
                              "requires confirming that you want to apply an unvalidated change.", 409)
        ws = Workspace(patch.project_id)
        if is_stale(patch, ws.head()):
            raise ReviewError("The project changed after this patch was validated. Re-validate it first.", 409)
        finding = s.get(Finding, patch.finding_id)
        if (patch.validation_detail or {}).get("after_b64"):  # format repair: write the exact bytes
            import base64

            ws.write_bytes(patch.file, base64.b64decode(patch.validation_detail["after_b64"]))
        else:
            ws.write_text(patch.file, patch.after_content)
        added_tests = include_tests and bool(patch.test_code)
        if added_tests:
            ws.write_text(patch.test_file, patch.test_code, crlf=False)
        message = (f"Fix: {finding.title}\n\nAutoPatch patch #{patch.id} for finding #{finding.id} "
                   f"({patch.validation.replace('_', ' ')}).")
        try:
            sha = ws.commit_all(message)
        except GitError:
            ws.discard_uncommitted()
            raise
        if sha is None:
            raise ReviewError("Applying the patch produced no change; it may already be applied")
        patch.status = "accepted"
        patch.applied_version = sha
        patch.tests_added = added_tests
        patch.decided_at = now_iso()
        patch.decision_note = note
        finding.status = "fixed" if patch.validation == "validated" else "patched_unverified"
        s.add(patch)
        s.add(finding)
        project = s.get(Project, patch.project_id)
        project.updated_at = now_iso()
        s.add(project)
        record_event(s, "patch_accepted",
                     f"Accepted patch #{patch.id} for '{finding.title}'" + (" with its tests" if added_tests else ""),
                     patch.project_id, patch_id=patch.id, finding_id=finding.id, version=sha,
                     validation=patch.validation, forced=force and patch.validation == "failed")
        s.commit()
        s.refresh(patch)
        return patch


def reject_patch(patch_id: int, note: str = "") -> Patch:
    with session() as s:
        patch = s.get(Patch, patch_id)
        if patch is None:
            raise ReviewError("Patch not found", 404)
        if patch.status not in ("ready", "failed"):
            raise ReviewError(f"This patch is {patch.status} and cannot be rejected")
        patch.status = "rejected"
        patch.decided_at = now_iso()
        patch.decision_note = note
        finding = s.get(Finding, patch.finding_id)
        if finding.status in ("patch_ready", "fixing"):
            finding.status = "open"
        s.add(patch)
        s.add(finding)
        record_event(s, "patch_rejected", f"Rejected patch #{patch.id} for '{finding.title}'"
                     + (f": {note}" if note else ""), patch.project_id, patch_id=patch.id, finding_id=finding.id)
        s.commit()
        s.refresh(patch)
        return patch


def sync_patch_states(project_id: int) -> None:
    """After history changes, mark accepted patches as reverted (or back in effect) by content."""
    ws = Workspace(project_id)
    with session() as s:
        patches = s.exec(select(Patch).where(
            Patch.project_id == project_id, Patch.status.in_(["accepted", "reverted"])  # type: ignore[attr-defined]
        )).all()
        for patch in patches:
            try:
                head_text = ws.read_text(patch.file)
            except FileNotFoundError:
                head_text = ""
            if (patch.validation_detail or {}).get("after_b64"):
                import base64

                try:
                    in_effect = ws.read_bytes(patch.file) == base64.b64decode(patch.validation_detail["after_b64"])
                except FileNotFoundError:
                    in_effect = False
            else:
                in_effect = None
            in_effect = in_effect if in_effect is not None else head_text == patch.after_content or all(
                e["patched_code"].strip() in head_text and (
                    e["original_code"].strip() not in head_text or e["original_code"].strip() in e["patched_code"])
                for e in patch.edits if e["patched_code"].strip()
            )
            finding = s.get(Finding, patch.finding_id)
            if patch.status == "accepted" and not in_effect:
                patch.status = "reverted"
                finding.status = "open"
            elif patch.status == "reverted" and in_effect:
                patch.status = "accepted"
                finding.status = "fixed" if patch.validation == "validated" else "patched_unverified"
            s.add(patch)
            s.add(finding)
        s.commit()


def revert_patch(patch_id: int, note: str = "") -> str:
    with session() as s:
        patch = s.get(Patch, patch_id)
        if patch is None or patch.status != "accepted":
            raise ReviewError("Only accepted patches can be reverted")
        finding = s.get(Finding, patch.finding_id)
        ws = Workspace(patch.project_id)
        try:
            sha = ws.revert(patch.applied_version, f"Revert: {finding.title}\n\nUndo AutoPatch patch #{patch.id}.")
        except GitError as exc:
            raise ReviewError(str(exc), 409) from exc
        record_event(s, "patch_reverted", f"Reverted patch #{patch.id} for '{finding.title}'"
                     + (f": {note}" if note else ""), patch.project_id, patch_id=patch.id, version=sha)
        s.commit()
    sync_patch_states(patch.project_id)
    return sha


def restore_version(project_id: int, version: str, note: str = "") -> str | None:
    ws = Workspace(project_id)
    try:
        target = ws.resolve(version)
    except GitError as exc:
        raise ReviewError(str(exc), 404) from exc
    short = target[:8]
    label = next((v.message for v in ws.versions() if v.sha == target), short)
    sha = ws.restore(target, f"Restore version {short}\n\nRestored to: {label}")
    with session() as s:
        if sha:
            record_event(s, "version_restored", f"Restored the project to version {short} ({label})"
                         + (f": {note}" if note else ""), project_id, version=sha, restored=target)
        s.commit()
    sync_patch_states(project_id)
    return sha


def start_project_tests(project_id: int, version: str | None = None):
    cfg = settings_mod.get_all()
    backend_from_settings(cfg)
    ws = Workspace(project_id)
    target = ws.resolve(version)

    def work(ctx: jobs.JobContext) -> dict:
        backend = backend_from_settings(cfg)
        ctx.log(f"Running the project's tests at version {target[:8]} in the {backend.label}")
        outcome = run_pytest(populate_from(ws, target), backend, timeout_s=cfg["testing.timeout_s"],
                             memory_mb=cfg["testing.memory_mb"])
        ctx.log(f"Result: {outcome_line(outcome)}")
        with session() as s:
            row = test_run_row(outcome, project_id, "project", target)
            s.add(row)
            record_event(s, "tests_run", f"Project tests at {target[:8]}: {outcome_line(outcome)}", project_id,
                         status=outcome.status)
            s.commit()
            s.refresh(row)
            return {"test_run_id": row.id, "status": outcome.status}

    return jobs.submit("tests", project_id, work)


def test_runs(project_id: int, limit: int = 100) -> list[TestRunRow]:
    with session() as s:
        return list(s.exec(select(TestRunRow).where(TestRunRow.project_id == project_id)
                           .order_by(TestRunRow.id.desc()).limit(limit)))  # type: ignore[attr-defined]
