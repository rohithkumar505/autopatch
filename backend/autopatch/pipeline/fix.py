"""Root-cause analysis and the fix pipeline.

Fix pipeline for one finding:
  1. root-cause analysis (if not done yet)
  2. AI writes tests from the bug report, before any fix exists
  3. run all tests on the ORIGINAL code in the sandbox: a failing reproduce test confirms the bug
  4. AI writes a patch; it is applied to a copy and every test runs again in the sandbox
  5. if something still fails, the failures go back to the AI and it tries again (bounded)
  6. the result is stored as a patch awaiting review; nothing touches the project until accepted

A patch is only called "validated" when a reproduce test failed before and passes after, every
generated test passes, and no test that passed before fails after.
"""

from __future__ import annotations

import ast
from pathlib import PurePosixPath

from sqlmodel import select

from autopatch.ai.engine import build_context
from autopatch.ai.providers import AIUsage
from autopatch.engines import get_engine
from autopatch.analysis.findings import impact_for, priority
from autopatch.analysis.python_analyzer import module_name
from autopatch.core import jobs, settings as settings_mod
from autopatch.core.workspace import Workspace
from autopatch.db import Finding, Patch, Project, now_iso, record_event, session
from autopatch.pipeline.common import (
    backend_from_settings, load_text_files, outcome_line, populate_from, python_structure, test_run_row,
)
from autopatch.pipeline.patching import PatchApplyError, apply_edits, changed_line_ranges, unified_diff
from autopatch.testing.runner import RunOutcome, run_pytest


class FixError(RuntimeError):
    pass


# ---------------------------------------------------------------- shared setup


class _Env:
    """Everything one AI job needs about a finding's project at its current version."""

    def __init__(self, finding_id: int):
        with session() as s:
            self.finding = s.get(Finding, finding_id)
            if self.finding is None:
                raise FixError(f"Finding {finding_id} does not exist")
            self.project = s.get(Project, self.finding.project_id)
        self.cfg = settings_mod.get_all()
        self.engine = get_engine(self.cfg)
        self.ws = Workspace(self.project.id)
        self.version = self.ws.head()
        self.files = load_text_files(self.ws)
        self.structure = python_structure(self.ws)
        self.context = build_context(self.project.name, self.files, self.structure, self.cfg["ai.max_context_kb"],
                                     root=self.ws.root)
        self.usage: list[AIUsage] = []

    def related_text(self) -> str:
        f = self.finding
        sym = self.structure.find_symbol(f.file, f.function) or self.structure.symbol_at(f.file, f.line_start)
        if not sym:
            return ""
        lines = [f"{sym.qualname} is defined in {sym.file} lines {sym.line_start}-{sym.line_end}."]
        if sym.called_by:
            lines.append("It is called by: " + ", ".join(sym.called_by))
        if sym.calls:
            lines.append("It calls: " + ", ".join(sym.calls))
        lines.append(f"Existing tests that call it: {', '.join(sym.tested_by) or 'none'}")
        return "\n".join(lines)


def _save_finding(finding: Finding) -> None:
    with session() as s:
        s.add(finding)
        s.commit()


# ---------------------------------------------------------------- root-cause analysis


def start_analyze(finding_id: int):
    settings_mod_cfg = settings_mod.get_all()
    get_engine(settings_mod_cfg)  # fail fast with a clear message when AI is not configured
    with session() as s:
        finding = s.get(Finding, finding_id)
        if finding is None:
            raise FixError(f"Finding {finding_id} does not exist")
        project_id = finding.project_id
    return jobs.submit("analyze", project_id, lambda ctx: run_analyze(ctx, finding_id))


def _analyze(env: _Env, ctx: jobs.JobContext) -> None:
    f = env.finding
    ctx.log(f"Tracing the root cause of '{f.title}' in {f.file}:{f.line_start}")
    analysis, usage = env.engine.analyze(env.context, f, env.related_text())
    env.usage.append(usage)
    ctx.log(f"Analysis done ({usage.describe()})")
    f.title = analysis.title[:200] or f.title
    f.description = analysis.description
    f.root_cause = analysis.root_cause
    f.trigger = analysis.trigger
    f.expected_behavior = analysis.expected_behavior
    f.category = analysis.category
    f.severity = analysis.severity
    f.confidence = round(max(0.0, min(analysis.confidence, 0.95 if f.verification != "confirmed" else 1.0)), 2)
    f.severity_reason = analysis.severity_reason
    factor, call_graph_impact, related = impact_for(env.structure, f.file, f.function, f.line_start)
    f.impact = analysis.impact + (f"\n\nCall graph: {call_graph_impact}" if call_graph_impact else "")
    f.related = analysis.related_code or related
    f.analyzed = True
    if "ai" not in f.sources:
        f.sources = sorted([*f.sources, "ai"])
    if not analysis.is_real_bug:
        f.confidence = min(f.confidence, 0.3)
        ctx.log("The analysis concluded this is probably not a real bug; confidence lowered", "warning")
    f.priority = priority(f.severity, f.confidence, factor)
    _save_finding(f)


def run_analyze(ctx: jobs.JobContext, finding_id: int) -> dict:
    env = _Env(finding_id)
    _analyze(env, ctx)
    with session() as s:
        record_event(s, "finding_analyzed", f"Root cause analysed for '{env.finding.title}'", env.project.id,
                     finding_id=finding_id)
        s.commit()
    return {"finding_id": finding_id}


# ---------------------------------------------------------------- fix


def start_fix(finding_id: int) -> tuple[Patch, object]:
    cfg = settings_mod.get_all()
    with session() as s:
        finding = s.get(Finding, finding_id)
        if finding is None:
            raise FixError(f"Finding {finding_id} does not exist")
    is_format = (finding.facts or {}).get("kind") == "format"
    if is_format:
        if not (finding.facts or {}).get("repair"):
            raise FixError(f"No automatic repair: this issue is classified as "
                           f"{finding.facts.get('repairability', 'unknown').replace('_', ' ')}. {finding.facts.get('reason', '')}")
    elif (finding.facts or {}).get("repairability") == "requires_human":
        raise FixError("Requires human intervention: " + (finding.facts.get("human_reason") or "no safe automatic repair.")
                       + " The report explains the problem and the options.")
    else:
        get_engine(cfg)                   # needs an engine
        backend_from_settings(cfg)        # and a sandbox to validate in: fail now, not halfway
    with session() as s:
        finding = s.get(Finding, finding_id)
        if finding.status == "fixing":
            raise FixError("A fix for this finding is already being generated")
        patch = Patch(project_id=finding.project_id, finding_id=finding_id, file=finding.file)
        s.add(patch)
        finding.status = "fixing"
        s.add(finding)
        s.commit()
        s.refresh(patch)
    if is_format:
        from autopatch.pipeline.formatfix import run_format_fix

        job = jobs.submit("fix", patch.project_id, lambda ctx: run_format_fix(ctx, patch.id))
    else:
        job = jobs.submit("fix", patch.project_id, lambda ctx: run_fix(ctx, patch.id))
    with session() as s:
        row = s.get(Patch, patch.id)
        row.job_id = job.id
        s.add(row)
        s.commit()
        s.refresh(row)
        return row, job


def _test_path(ws_files: list[str], finding_id: int, finding_file: str = "") -> str:
    """Put generated tests next to the buggy package so imports match the app layout."""
    parts = PurePosixPath(finding_file).parts if finding_file else ()
    if len(parts) >= 2 and parts[0] not in ("tests", "test", "src"):
        pkg = parts[0]
        if any(f.startswith(f"{pkg}/tests/") for f in ws_files):
            return f"{pkg}/tests/test_autopatch_bug_{finding_id}.py"
        if any(f.startswith(f"{pkg}/test/") for f in ws_files):
            return f"{pkg}/test/test_autopatch_bug_{finding_id}.py"
        return f"{pkg}/test_autopatch_bug_{finding_id}.py"
    has_tests_dir = any(PurePosixPath(f).parts[0] in ("tests", "test") for f in ws_files if "/" in f)
    folder = next(
        (PurePosixPath(f).parts[0] for f in ws_files if "/" in f and PurePosixPath(f).parts[0] in ("tests", "test")),
        "tests",
    )
    return f"{folder}/test_autopatch_bug_{finding_id}.py" if has_tests_dir else f"test_autopatch_bug_{finding_id}.py"


def _import_hint(env: _Env) -> str:
    f = env.finding
    parts = PurePosixPath(f.file).parts
    # Flat app folders (library_app/library.py) are imported as `from library import …`.
    if len(parts) >= 2 and parts[0] not in ("tests", "test", "src"):
        mod = PurePosixPath(f.file).stem
    else:
        mod = module_name(f.file)
    sym = env.structure.find_symbol(f.file, f.function)
    if sym:
        name = sym.qualname.split(".")[-1] if sym.kind == "function" else sym.name
        if f.function and f.function != "<module>":
            name = f.function
        return f"`from {mod} import {name}`"
    return f"`import {mod}`"


def _pytest_targets(finding_file: str, test_file: str = "") -> list[str]:
    """Scope Autofix pytest to the finding's package (+ generated tests), not sibling apps."""
    targets: list[str] = []
    if test_file:
        targets.append(test_file)
    parts = PurePosixPath(finding_file).parts
    if len(parts) >= 2 and parts[0] not in ("tests", "test"):
        pkg = parts[0]
        if pkg not in targets:
            targets.append(pkg)
    return targets


def _is_generated(test_id: str, test_file: str) -> bool:
    mod = test_file[:-3].replace("/", ".")
    stem = PurePosixPath(test_file).stem
    return (
        test_id.startswith(mod + "::") or test_id.startswith(mod + ".")
        or test_id.startswith(stem + "::") or f".{stem}::" in test_id or f"/{stem}.py::" in test_id
    )


def evaluate(before: RunOutcome, after: RunOutcome, test_file: str | None) -> dict:
    b, a = before.by_id(), after.by_id()
    gen = (lambda tid: _is_generated(tid, test_file)) if test_file else (lambda tid: False)
    reproduce = [t for t in before.tests if gen(t.id) and t.kind == "reproduce"]
    reproduced = [t.id for t in reproduce if t.status == "failed"]
    fixed = [tid for tid in reproduced if tid in a and a[tid].status == "passed"]
    still_failing = [tid for tid in reproduced if tid not in fixed]
    regressions = sorted(tid for tid, t in b.items() if t.status == "passed" and (tid not in a or a[tid].status != "passed"))
    generated_failing = sorted(t.id for t in after.tests if gen(t.id) and t.status != "passed")
    existing_b = [t for t in before.tests if not gen(t.id)]
    existing_a = [t for t in after.tests if not gen(t.id)]
    run_ok = after.status in ("passed", "failed") and before.status in ("passed", "failed")
    clean = run_ok and not regressions and not generated_failing and not still_failing
    if clean and reproduced:
        verdict = "validated"
    elif clean:
        verdict = "partially_validated"
    else:
        verdict = "failed"
    return {
        "verdict": verdict,
        "reproduced": reproduced,
        "reproduce_tests": [t.id for t in reproduce],
        "fixed": fixed,
        "still_failing": still_failing,
        "regressions": regressions,
        "generated_failing": generated_failing,
        "existing": {
            "before": {"passed": sum(t.status == "passed" for t in existing_b), "total": len(existing_b)},
            "after": {"passed": sum(t.status == "passed" for t in existing_a), "total": len(existing_a)},
            "failing_before": sorted(t.id for t in existing_b if t.status != "passed"),
        },
        "before_status": before.status,
        "after_status": after.status,
    }


def _score(ev: dict) -> int:
    return 3 * len(ev["regressions"]) + 2 * len(ev["still_failing"]) + len(ev["generated_failing"])


def _failure_feedback(ev: dict, after: RunOutcome) -> str:
    by_id = after.by_id()
    lines = []
    for label, ids in (("Tests that passed before and now fail (regressions)", ev["regressions"]),
                       ("Reproduce tests that still fail", ev["still_failing"]),
                       ("Generated tests failing after the patch", ev["generated_failing"])):
        if ids:
            lines.append(label + ":")
            for tid in ids[:10]:
                msg = by_id[tid].message[-800:] if tid in by_id else "(test did not run)"
                lines.append(f"- {tid}\n{msg}")
    if after.status not in ("passed", "failed"):
        lines.append(f"The test run itself ended with status '{after.status}':\n{after.output[-1500:]}")
    return "\n".join(lines) or "Unknown failure"


def _results_text(outcome: RunOutcome, test_file: str) -> str:
    rows = []
    for t in outcome.tests:
        if _is_generated(t.id, test_file):
            msg = f" -- {t.message.splitlines()[-1][:200]}" if t.message and t.status != "passed" else ""
            rows.append(f"- {t.name}: {t.status}{msg}")
    return "\n".join(rows) or "(no generated tests ran)"


def run_fix(ctx: jobs.JobContext, patch_id: int) -> dict:
    with session() as s:
        patch = s.get(Patch, patch_id)
        finding_id = patch.finding_id
    try:
        return _run_fix(ctx, patch_id, finding_id)
    except Exception as exc:
        with session() as s:
            patch = s.get(Patch, patch_id)
            patch.status = "failed"
            patch.error = str(exc)
            finding = s.get(Finding, finding_id)
            if finding.status == "fixing":
                finding.status = "open"
            s.add(patch)
            s.add(finding)
            record_event(s, "patch_failed", f"Could not generate a fix for '{finding.title}': {exc}",
                         patch.project_id, patch_id=patch_id, finding_id=finding_id)
            s.commit()
        raise


def _is_python_source(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in {".py", ".pyi"}


def _run_polyglot_fix(ctx: jobs.JobContext, env: _Env, patch_id: int, original: str) -> dict:
    """Pattern-based Autofix for non-Python findings (no pytest / Core AST)."""
    from autopatch.pipeline import polyglot_fix as poly

    f = env.finding
    ctx.log(f"Non-Python Autofix for {f.file} (pattern repair; sandbox tests are Python-only)")
    if not f.analyzed:
        try:
            _analyze(env, ctx)
        except Exception as exc:  # analysis is best-effort for polyglot
            ctx.log(f"Root-cause analysis skipped: {exc}", "warning")
            f.analyzed = True
            _save_finding(f)
    try:
        result = poly.repair(f, original)
    except ValueError as exc:
        raise FixError(str(exc)) from exc
    check = poly.validate_repair(env.ws.root, f.file, original, result.patched, result.concept)
    verdict = "validated" if check.get("resolved") and not check.get("new") else (
        "partially_validated" if result.patched != original else "failed"
    )
    if not check.get("resolved"):
        ctx.log("Pattern still present after repair; marking partially validated", "warning")
        verdict = "partially_validated"
    ranges = changed_line_ranges(original, result.patched)
    diff = unified_diff(f.file, original, result.patched)
    detail = {
        "verdict": verdict, "kind": "polyglot", "concept": result.concept,
        "pattern_check": check, "reproduced": [result.concept] if result.concept else [],
        "fixed": [result.concept] if check.get("resolved") else [],
        "still_failing": [] if check.get("resolved") else [result.concept],
        "regressions": [], "generated_failing": [], "existing": None,
        "attempts": [{"attempt": 1, "result": verdict, "detail": "pattern repair"}],
        "scope": {"changed_ranges": ranges, "lines_changed": sum(b - a + 1 for a, b in ranges),
                  "function": f.function, "function_lines": None, "outside_function": []},
        "sandbox": "n/a", "ai_usage": [u.to_dict() for u in env.usage],
    }
    with session() as s:
        patch = s.get(Patch, patch_id)
        patch.base_version = env.version
        patch.status = "ready"
        patch.file = f.file
        patch.before_content = original
        patch.after_content = result.patched
        patch.diff = diff
        patch.edits = [{"original_code": original, "patched_code": result.patched}]
        patch.explanation = result.explanation
        patch.behavior_change = result.behavior_change
        patch.test_file = ""
        patch.test_code = ""
        patch.test_cases = []
        patch.attempts = 1
        patch.validation = verdict
        patch.validation_detail = detail
        patch.error = ""
        s.add(patch)
        finding = s.get(Finding, f.id)
        finding.status = "patch_ready"
        finding.analyzed = True
        s.add(finding)
        record_event(s, "patch_generated",
                     f"Patch #{patch_id} for '{finding.title}': {verdict.replace('_', ' ')} (polyglot)",
                     env.project.id, patch_id=patch_id, finding_id=f.id, validation=verdict)
        s.commit()
    ctx.log(f"Patch #{patch_id} is ready for review ({verdict.replace('_', ' ')})")
    return {"patch_id": patch_id, "validation": verdict}


def _run_fix(ctx: jobs.JobContext, patch_id: int, finding_id: int) -> dict:
    env = _Env(finding_id)
    f = env.finding
    cfg = env.cfg
    if f.file not in env.files:
        raise FixError(f"{f.file} is not a text file in the current version")
    original = env.files[f.file]
    if not _is_python_source(f.file):
        return _run_polyglot_fix(ctx, env, patch_id, original)

    backend = backend_from_settings(cfg)
    timeout, memory = cfg["testing.timeout_s"], cfg["testing.memory_mb"]
    crlf = {f.file: env.ws.uses_crlf(f.file)}
    ctx.log(f"Fixing '{f.title}' ({f.file}:{f.line_start}); tests run in the {backend.label}")

    if not f.analyzed:
        _analyze(env, ctx)

    # ---- 1. tests written from the bug report, before a fix exists
    test_file, test_code, cases = "", "", []
    before: RunOutcome
    if cfg["testing.generate_tests"]:
        test_file = _test_path(list(env.files), f.id, f.file)
        feedback = ""
        for attempt in range(1, 4):
            ctx.log("Writing tests that reproduce the bug" + (f" (attempt {attempt})" if attempt > 1 else ""))
            suite, usage = env.engine.write_tests(env.context, f, test_file, _import_hint(env), feedback)
            env.usage.append(usage)
            try:
                ast.parse(suite.test_code)
            except SyntaxError as exc:
                feedback = f"The test module has a syntax error on line {exc.lineno}: {exc.msg}"
                continue
            test_code, cases = suite.test_code, [c.model_dump() for c in suite.cases]
            ctx.log(f"Running {len(cases)} generated test(s) and the existing tests on the original code")
            before = run_pytest(populate_from(env.ws, env.version, {test_file: test_code}), backend,
                                timeout_s=timeout, memory_mb=memory,
                                targets=_pytest_targets(f.file, test_file))
            gen_results = [t for t in before.tests if _is_generated(t.id, test_file)]
            ctx.log(f"Original code: {outcome_line(before)}")
            if not gen_results or all(t.status == "error" for t in gen_results):
                feedback = ("The test module could not be collected or every test errored during setup. "
                            f"pytest output:\n{before.output[-2000:]}")
                ctx.log("Generated tests did not run; asking for a corrected test module", "warning")
                continue
            repro = [t for t in gen_results if t.kind == "reproduce"]
            if repro and not any(t.status == "failed" for t in repro) and attempt == 1:
                feedback = ("On the CURRENT, unfixed code these reproduce tests PASSED, so they do not show the bug: "
                            + ", ".join(t.name for t in repro)
                            + ". Rewrite them to use an input that actually triggers the bug described, and assert "
                              "the correct behavior so they fail now and pass after a fix.")
                ctx.log("Reproduce tests passed on the original code; asking for tests that show the bug", "warning")
                continue
            break
        else:
            raise FixError("Could not produce a runnable test module for this bug after 3 attempts: "
                           + (feedback[:500] or "unknown error"))
        reproduced = [t.name for t in before.tests if _is_generated(t.id, test_file)
                      and t.kind == "reproduce" and t.status == "failed"]
        if reproduced:
            ctx.log(f"Bug confirmed: {len(reproduced)} reproduce test(s) fail on the original code")
            f.verification = "confirmed"
            f.confidence = max(f.confidence, 0.95)
        else:
            ctx.log("The bug could not be reproduced by a test; any fix can only be partially validated", "warning")
            f.verification = "not_reproduced"
        _save_finding(f)
    else:
        ctx.log("Test generation is off in Settings; validating with the existing tests only")
        before = run_pytest(populate_from(env.ws, env.version), backend, timeout_s=timeout, memory_mb=memory,
                            targets=_pytest_targets(f.file) or None)
        ctx.log(f"Original code: {outcome_line(before)}")

    with session() as s:
        s.add(test_run_row(before, env.project.id, "original", env.version, patch_id, f.id))
        s.commit()

    # ---- 2. patch, validate, retry
    best = None
    feedback = ""
    attempts_log = []
    max_attempts = cfg["testing.max_fix_attempts"]
    for attempt in range(1, max_attempts + 1):
        ctx.log(f"Generating a patch (attempt {attempt} of {max_attempts})")
        proposal, usage = env.engine.write_fix(env.context, f, test_code,
                                               _results_text(before, test_file) if test_code else "", feedback)
        env.usage.append(usage)
        if proposal.file.replace("\\", "/") != f.file:
            feedback = f"The patch must change {f.file}, not {proposal.file}."
            attempts_log.append({"attempt": attempt, "result": "rejected", "detail": feedback})
            continue
        edits = [e.model_dump() for e in proposal.edits]
        try:
            patched = apply_edits(original, edits, f.file)
        except PatchApplyError as exc:
            feedback = f"The patch could not be applied: {exc}. Copy original_code exactly from the listing."
            ctx.log(f"Patch did not apply: {exc}", "warning")
            attempts_log.append({"attempt": attempt, "result": "did not apply", "detail": str(exc)})
            continue
        if patched == original:
            feedback = "The patch made no change to the file."
            attempts_log.append({"attempt": attempt, "result": "no change", "detail": feedback})
            continue
        ctx.log("Testing the patched code in the sandbox")
        overrides = {f.file: patched, **({test_file: test_code} if test_code else {})}
        after = run_pytest(populate_from(env.ws, env.version, overrides, crlf), backend,
                           timeout_s=timeout, memory_mb=memory,
                           targets=_pytest_targets(f.file, test_file or ""))
        ev = evaluate(before, after, test_file or None)
        ctx.log(f"Patched code: {outcome_line(after)} -> {ev['verdict'].replace('_', ' ')}")
        attempts_log.append({"attempt": attempt, "result": ev["verdict"], "detail": outcome_line(after)})
        if best is None or _score(ev) < _score(best[2]) or ev["verdict"] != "failed":
            best = (proposal, patched, ev, after)
        if ev["verdict"] != "failed":
            break
        feedback = _failure_feedback(ev, after)

    if best is None:
        raise FixError("No usable patch was produced: " + "; ".join(a["detail"] for a in attempts_log))

    proposal, patched, ev, after = best
    lines_changed = changed_line_ranges(original, patched)
    sym = env.structure.find_symbol(f.file, f.function) or env.structure.symbol_at(f.file, f.line_start)
    outside = [r for r in lines_changed if sym and not (sym.line_start - 3 <= r[0] <= sym.line_end + 3)
               and r[0] > 15]
    by_after = after.by_id()
    by_before = before.by_id()
    case_rows = []
    descriptions = {c["name"]: c for c in cases}
    for t in before.tests:
        if test_file and _is_generated(t.id, test_file):
            t_after = by_after.get(t.id)
            case_rows.append({
                "id": t.id, "name": t.name, "kind": t.kind or descriptions.get(t.name, {}).get("kind", ""),
                "description": descriptions.get(t.name, {}).get("description", ""),
                "before": t.status, "after": t_after.status if t_after else "not run",
                "message_before": by_before[t.id].message[-1500:],
                "message_after": t_after.message[-1500:] if t_after else "",
            })
    detail = {
        **ev,
        "attempts": attempts_log,
        "scope": {
            "changed_ranges": lines_changed,
            "lines_changed": sum(b - a + 1 for a, b in lines_changed),
            "function": sym.qualname if sym else None,
            "function_lines": [sym.line_start, sym.line_end] if sym else None,
            "outside_function": outside,
        },
        "sandbox": backend.name,
        "ai_usage": [u.to_dict() for u in env.usage],
    }
    with session() as s:
        patch = s.get(Patch, patch_id)
        patch.base_version = env.version
        patch.status = "ready"
        patch.file = f.file
        patch.before_content = original
        patch.after_content = patched
        patch.diff = unified_diff(f.file, original, patched)
        patch.edits = [e.model_dump() for e in proposal.edits]
        patch.explanation = proposal.explanation
        patch.behavior_change = proposal.behavior_change
        patch.test_file = test_file
        patch.test_code = test_code
        patch.test_cases = case_rows
        patch.attempts = len(attempts_log)
        patch.validation = ev["verdict"]
        patch.validation_detail = detail
        s.add(patch)
        s.add(test_run_row(after, env.project.id, "patched", env.version, patch_id, f.id))
        finding = s.get(Finding, f.id)
        finding.status = "patch_ready"
        s.add(finding)
        record_event(s, "patch_generated",
                     f"Patch #{patch_id} for '{finding.title}': {ev['verdict'].replace('_', ' ')}",
                     env.project.id, patch_id=patch_id, finding_id=f.id, validation=ev["verdict"])
        s.commit()
    ctx.log(f"Patch #{patch_id} is ready for review ({ev['verdict'].replace('_', ' ')})")
    return {"patch_id": patch_id, "validation": ev["verdict"]}


# ---------------------------------------------------------------- revalidate a stale patch


def start_revalidate(patch_id: int):
    cfg = settings_mod.get_all()
    backend_from_settings(cfg)
    with session() as s:
        patch = s.get(Patch, patch_id)
        if patch is None or patch.status != "ready":
            raise FixError("Only patches waiting for review can be re-validated")
        project_id = patch.project_id
    return jobs.submit("revalidate", project_id, lambda ctx: run_revalidate(ctx, patch_id))


def run_revalidate(ctx: jobs.JobContext, patch_id: int) -> dict:
    """Re-apply a patch to the current version and re-run the same tests. No AI involved."""
    cfg = settings_mod.get_all()
    backend = backend_from_settings(cfg)
    with session() as s:
        patch = s.get(Patch, patch_id)
    ws = Workspace(patch.project_id)
    version = ws.head()
    current = ws.read_text(patch.file)
    ctx.log(f"Re-applying patch #{patch_id} to version {version[:8]}")
    try:
        patched = apply_edits(current, patch.edits, patch.file)
    except PatchApplyError as exc:
        with session() as s:
            row = s.get(Patch, patch_id)
            row.error = f"The patch no longer applies to the current code: {exc}. Generate a new fix."
            row.validation = "failed"
            s.add(row)
            s.commit()
        raise FixError(f"The patch no longer applies: {exc}") from exc
    extra = {patch.test_file: patch.test_code} if patch.test_code else {}
    timeout, memory = cfg["testing.timeout_s"], cfg["testing.memory_mb"]
    targets = _pytest_targets(patch.file, patch.test_file or "")
    before = run_pytest(populate_from(ws, version, extra), backend, timeout_s=timeout, memory_mb=memory,
                        targets=targets or None)
    ctx.log(f"Current code: {outcome_line(before)}")
    after = run_pytest(populate_from(ws, version, {patch.file: patched, **extra}, {patch.file: ws.uses_crlf(patch.file)}),
                       backend, timeout_s=timeout, memory_mb=memory, targets=targets or None)
    ev = evaluate(before, after, patch.test_file or None)
    ctx.log(f"Patched code: {outcome_line(after)} -> {ev['verdict'].replace('_', ' ')}")
    with session() as s:
        row = s.get(Patch, patch_id)
        row.base_version = version
        row.before_content = current
        row.after_content = patched
        row.diff = unified_diff(row.file, current, patched)
        row.validation = ev["verdict"]
        row.validation_detail = {**row.validation_detail, **ev, "revalidated_at": now_iso()}
        row.error = ""
        s.add(row)
        s.add(test_run_row(before, row.project_id, "original", version, patch_id, row.finding_id))
        s.add(test_run_row(after, row.project_id, "patched", version, patch_id, row.finding_id))
        record_event(s, "patch_revalidated", f"Patch #{patch_id} re-validated: {ev['verdict'].replace('_', ' ')}",
                     row.project_id, patch_id=patch_id)
        s.commit()
    return {"patch_id": patch_id, "validation": ev["verdict"]}


def open_fix_jobs(finding_id: int) -> list[Patch]:
    with session() as s:
        return list(s.exec(select(Patch).where(Patch.finding_id == finding_id, Patch.status == "generating")))
