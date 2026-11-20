"""Scan pipeline: structure -> static analysis -> AI review -> merged, ranked findings."""

from __future__ import annotations

from autopatch.ai.engine import build_context
from autopatch.ai.providers import AINotConfigured
from autopatch.engines import engine_label, get_engine
from autopatch.analysis.base import analyzer_for, analyzers, detect_languages
from autopatch.analysis.findings import SEVERITIES, issues_to_drafts, merge_ai_findings, rank, snippet_for
from autopatch.core import jobs, settings as settings_mod
from autopatch.core.workspace import Workspace
from autopatch.db import Finding, Project, Scan, now_iso, record_event, session
from autopatch.pipeline.common import load_text_files


def start_scan(project_id: int, options: dict) -> tuple[Scan, object]:
    with session() as s:
        if s.get(Project, project_id) is None:
            raise ValueError(f"Project {project_id} does not exist")
        scan = Scan(project_id=project_id, options=options)
        s.add(scan)
        s.commit()
        s.refresh(scan)
    job = jobs.submit("scan", project_id, lambda ctx: run_scan(ctx, scan.id))
    with session() as s:
        row = s.get(Scan, scan.id)
        row.job_id = job.id
        s.add(row)
        s.commit()
        s.refresh(row)
        return row, job


def _update_scan(scan_id: int, **fields) -> None:
    with session() as s:
        scan = s.get(Scan, scan_id)
        for k, v in fields.items():
            setattr(scan, k, v)
        s.add(scan)
        s.commit()


def run_scan(ctx: jobs.JobContext, scan_id: int) -> dict:
    with session() as s:
        scan = s.get(Scan, scan_id)
        project = s.get(Project, scan.project_id)
        options = dict(scan.options)
    cfg = settings_mod.get_all()
    use_ruff = options.get("ruff", cfg["analysis.ruff"])
    use_pylint = options.get("pylint", cfg["analysis.pylint"])
    use_ai = options.get("ai", cfg["analysis.ai_scan"])
    _update_scan(scan_id, status="running")
    try:
        ws = Workspace(project.id)
        version = ws.head()
        _update_scan(scan_id, version=version)
        all_files = ws.list_files()
        languages = detect_languages(all_files)
        ctx.log(f"Scanning {project.name} at version {version[:8]}: {len(all_files)} files "
                f"({', '.join(f'{k} {v}' for k, v in languages.items()) or 'no recognised languages'})")
        available = analyzers()
        unsupported = [lang for lang in languages if lang not in available and lang not in
                       ("JSON", "YAML", "TOML", "Markdown", "HTML", "SQL", "PowerShell")]
        if unsupported:
            ctx.log(f"Recognised but no deep analyzer yet: {', '.join(unsupported)}", "warning")

        stats: dict = {"files": len(all_files), "languages": languages, "unsupported": unsupported,
                       "analyzed_files": 0, "tools": {}, "by_language": {}}

        # Universal file intelligence: every file, whatever its type
        file_findings = _file_intelligence(ctx, ws, project.id, scan_id, cfg, stats)

        from autopatch.analysis.base import Structure
        structure = Structure(language="+".join(languages) if languages else "")
        issues: list = []
        analyzed_files = 0
        tools = {t for t, on in (("ruff", use_ruff), ("pylint", use_pylint)) if on}

        for lang, analyzer in available.items():
            lang_files = [f for f in all_files if analyzer.handles(f)]
            if not lang_files:
                continue
            analyzed_files += len(lang_files)
            ctx.log(f"Analyzing {len(lang_files)} {lang} file(s)")
            part = analyzer.structure(ws.root, lang_files)
            structure.modules.update(part.modules)
            structure.symbols.update(part.symbols)
            structure.syntax_issues.extend(part.syntax_issues)
            structure.declared_dependencies.extend(part.declared_dependencies)
            # Python gets Ruff/Pylint; other languages use their pattern tool set.
            lang_tools = tools if lang == "Python" else {"patterns"}
            found, tool_stats = analyzer.static_issues(ws.root, lang_files, lang_tools)
            issues += found
            stats["by_language"][lang] = {"files": len(lang_files), "issues": len(found),
                                          "symbols": len(part.symbols), "tools": tool_stats}
            for tool, st in tool_stats.items():
                stats["tools"][f"{lang}:{tool}"] = st
                ctx.log(f"{lang}/{tool}: {st.get('issues', 0)} issue(s)" if st.get("status") == "ok"
                        else f"{lang}/{tool} failed: {st.get('error', '')}",
                        "info" if st.get("status") == "ok" else "warning")

        stats["analyzed_files"] = analyzed_files
        stats["symbols"] = len(structure.symbols)
        if structure.syntax_issues:
            ctx.log(f"{len(structure.syntax_issues)} file(s) have syntax errors", "warning")

        if analyzed_files == 0:
            ctx.log("No analyzable source files; file intelligence covered every file")
            return _finish(ctx, project, scan_id, [], file_findings, stats,
                           f"{len(all_files)} files identified and inspected. No source languages to analyze deeply.")

        n_funcs = sum(1 for s in structure.symbols.values() if s.kind != "class")
        ctx.log(f"Parsed {analyzed_files} source files across {len(stats['by_language'])} language(s): "
                f"{n_funcs} functions/methods, {sum(1 for s in structure.symbols.values() if s.kind == 'class')} classes")

        files = load_text_files(ws)
        drafts = issues_to_drafts(issues, structure, lambda f: files.get(f, ""))
        summary = ""
        stats["ai"] = {"used": False}
        if use_ai:
            try:
                engine = get_engine(cfg)
            except AINotConfigured as exc:
                ctx.log(f"AI review skipped: {exc}", "warning")
                stats["ai"] = {"used": False, "reason": str(exc)}
            else:
                context = build_context(project.name, files, structure, cfg["ai.max_context_kb"], root=ws.root)
                label = engine_label(engine)
                builtin = label == "AutoPatch Core"
                if context.omitted and not builtin:
                    ctx.log(f"{len(context.omitted)} file(s) left out of the AI review to stay under the size limit",
                            "warning")
                ctx.log(f"{label}: running detectors and confirming findings in the sandbox" if builtin else
                        f"AI review with {label}: sending {len(context.included)} files "
                        f"and {len(drafts)} static finding(s) for confirmation")
                ai_drafts, summary, usage = engine.scan(context, drafts)
                checked = []
                for d in ai_drafts:  # never trust a location the model gives without checking it
                    if d.file not in files:
                        ctx.log(f"Ignored an AI finding that points to a file not in the project: {d.file}", "warning")
                        continue
                    n_lines = files[d.file].count("\n") + 1
                    d.line_start = min(d.line_start, n_lines)
                    d.line_end = min(max(d.line_end, d.line_start), n_lines)
                    d.code_snippet, d.snippet_start = snippet_for(files[d.file], structure, d.file,
                                                                  d.line_start, d.line_end)
                    checked.append(d)
                ai_drafts = checked
                ctx.log(f"{label} found {len(ai_drafts)} issue(s) "
                        f"({sum(d.verification == 'confirmed' for d in ai_drafts)} confirmed by execution; {usage.describe()})")
                drafts = merge_ai_findings(drafts, ai_drafts)
                stats["ai"] = {"used": True, "model": usage.model, "findings": len(ai_drafts), "usage": usage.to_dict()}

        ranked = rank(drafts, structure)
        if not summary:
            lang_bits = ", ".join(f"{n} {lang}" for lang, n in languages.items() if lang in available)
            summary = (f"{analyzed_files} source files ({lang_bits}). "
                       f"{len(structure.symbols)} symbols. Static analysis found {len(ranked)} issue(s)."
                       + ("" if stats["ai"].get("used") else " Deep analysis was not run."))
        return _finish(ctx, project, scan_id, [(d, files) for d in ranked], file_findings, stats, summary)
    except Exception as exc:
        _update_scan(scan_id, status="failed", error=str(exc), finished_at=now_iso())
        raise


def _snippet(text: str, start: int, end: int) -> str:
    lines = text.split("\n")
    a = max(1, start - 3)
    return "\n".join(lines[a - 1 : min(len(lines), end + 3)])


FILE_ISSUE_PREFIXES = ("python.", "security.", "json.", "yaml.", "toml.", "xml.", "ini.", "env.", "csv.", "png.", "jpeg.", "gif.",
                       "pdf.", "archive.", "db.", "source.", "encoding.", "text.", "config.", "format.")
INTEGRITY = ("png", "jpeg", "gif", "pdf", "archive", "db")
CONFIGURATION = ("config", "env", "ini")


def _file_intelligence(ctx, ws, project_id: int, scan_id: int, cfg: dict, stats: dict) -> list[Finding]:
    from sqlmodel import select

    from autopatch.db import FileReport
    from autopatch.fileintel.scanner import analyze_project

    blobs = ws.blob_shas()
    with session() as s:
        last = s.exec(select(Scan).where(Scan.project_id == project_id, Scan.status == "completed", Scan.id != scan_id)
                      .order_by(Scan.id.desc())).first()
        previous = {r.path: r.model_dump() for r in s.exec(select(FileReport).where(FileReport.scan_id == last.id))} if last else {}
    ctx.log(f"File intelligence: identifying {len(blobs):,} files by their content")
    reports = analyze_project(ws.root, blobs, previous, cfg["limits.analysis_max_mb"] * 1024 * 1024, ctx.log)
    findings: list[Finding] = []
    counts: dict[str, int] = {}
    with session() as s:
        for r in reports:
            r = {k: v for k, v in r.items() if k not in ("id", "project_id", "scan_id")}
            s.add(FileReport(project_id=project_id, scan_id=scan_id, **r))
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            for issue in r["issues"]:
                if issue["severity"] == "info" and issue["repairability"] != "fixable":
                    continue
                if not issue["code"].startswith(FILE_ISSUE_PREFIXES):
                    continue
                group = issue["code"].split(".")[0]
                cat = "security" if group == "security" else "integrity" if group in INTEGRITY else \
                    "configuration" if group in CONFIGURATION else "format"
                sev = issue["severity"] if issue["severity"] != "info" else "low"
                findings.append(Finding(
                    project_id=project_id, scan_id=scan_id,
                    title=f"{issue['title']} ({r['path'].split('/')[-1]})"[:200],
                    description=issue["message"], file=r["path"], function="<file>",
                    line_start=issue.get("line") or 1, line_end=issue.get("line") or 1, category=cat, severity=sev,
                    confidence=issue["confidence"], sources=[f"fileintel:{issue['code']}"], root_cause=issue["reason"],
                    trigger=f"Detected by {r['method']}.", expected_behavior="; ".join(issue["options"]),
                    impact=issue.get("limitations", ""),
                    severity_reason=f"Repairability: {issue['repairability'].replace('_', ' ')}",
                    analyzed=True, verification="confirmed",
                    priority=round({"critical": 4, "high": 3, "medium": 2, "low": 1}[sev] * issue["confidence"] * 0.9, 3),
                    facts={"kind": "format", **issue, "format": r["format"]},
                ))
        s.commit()
    stats["file_intelligence"] = {"files": len(reports), "by_status": counts,
                                  "issues": sum(len(r["issues"]) for r in reports),
                                  "reused": sum(1 for r in reports if r.get("reused"))}
    not_full = sum(counts.get(k, 0) for k in ("skipped", "unreadable", "identified_only", "partially_analyzed"))
    ctx.log(f"File intelligence: {sum(len(r['issues']) for r in reports)} issue(s) in {len(reports)} files"
            + (f"; {not_full} file(s) not fully analyzed (each is listed with the reason)" if not_full else ""))
    return findings


def _finish(ctx, project, scan_id: int, drafts: list, file_findings: list[Finding], stats: dict, summary: str) -> dict:
    stats["counts"] = {sev: sum(1 for d, _ in drafts if d.severity == sev) + sum(1 for f in file_findings if f.severity == sev)
                       for sev in SEVERITIES}
    total = len(drafts) + len(file_findings)
    with session() as s:
        for d, files in drafts:
            facts = dict(d.facts or {})
            if getattr(d, "concept", None) and "concept" not in facts:
                facts["concept"] = d.concept
            s.add(Finding(
                project_id=project.id, scan_id=scan_id, title=d.title[:200], description=d.description,
                file=d.file, function=d.function, line_start=d.line_start, line_end=d.line_end,
                code_snippet=d.code_snippet or _snippet(files.get(d.file, ""), d.line_start, d.line_end),
                snippet_start=d.snippet_start or max(1, d.line_start - 3),
                category=d.category, severity=d.severity, confidence=d.confidence, sources=d.sources,
                root_cause=d.root_cause, trigger=d.trigger, expected_behavior=d.expected_behavior,
                impact=d.impact, related=d.related, severity_reason=d.severity_reason,
                analyzed=d.analyzed, verification=d.verification, priority=d.priority, facts=facts,
            ))
        for f in file_findings:
            s.add(f)
        scan = s.get(Scan, scan_id)
        scan.status = "completed"
        scan.summary = summary
        scan.stats = stats
        scan.finished_at = now_iso()
        s.add(scan)
        record_event(s, "scan_completed", f"Scan #{scan_id} found {total} issue(s)", project.id,
                     scan_id=scan_id, counts=stats["counts"])
        s.commit()
    ctx.log(f"Scan complete: {total} issue(s) " + ", ".join(f"{n} {sev}" for sev, n in stats["counts"].items() if n))
    return {"scan_id": scan_id, "findings": total}
