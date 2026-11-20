"""Turn raw tool output and AI results into ranked findings.

Ranking: priority = severity weight x confidence x impact. Impact comes from the call graph:
code that other code depends on, or that has no tests, ranks higher.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autopatch.analysis.base import Issue, Structure

SEVERITY_WEIGHT = {"critical": 4.0, "high": 3.0, "medium": 2.0, "low": 1.0}
SEVERITIES = list(SEVERITY_WEIGHT)
CATEGORIES = ["crash", "incorrect_output", "edge_case", "security", "performance", "state_corruption"]

_CONCEPT_EXPLANATION = {
    "syntax": "Python cannot parse this file, so importing the module fails before any code runs.",
    "eval": "eval() executes whatever text it receives as Python code. If that text comes from a user or a file, "
            "the user can run any command on the machine.",
    "exec": "exec() executes whatever text it receives as Python code, so untrusted input becomes code execution.",
    "shell": "The command goes through a shell, so characters like ; or && in the data start extra commands.",
    "sql-injection": "The query text is assembled from values, so a value containing SQL changes the query itself.",
    "mutable-default": "The default object is created once when the function is defined and reused by every call, "
                       "so changes made in one call leak into the next.",
    "undefined-name": "The name is not defined in this scope, so the line raises NameError when it runs.",
    "before-assignment": "On some paths the variable is read before any assignment, which raises UnboundLocalError.",
    "loop-closure": "The inner function looks the loop variable up when it is called, not when it is created, "
                    "so every copy sees the last value.",
    "finally-exit": "Leaving a finally block with return/break discards any exception raised in the try block.",
    "missing-arg": "The call does not pass every required parameter, so it raises TypeError.",
    "extra-arg": "The call passes more arguments than the function accepts, so it raises TypeError.",
}


@dataclass
class DraftFinding:
    title: str
    description: str
    file: str
    function: str
    line_start: int
    line_end: int
    category: str
    severity: str
    confidence: float
    sources: list[str]
    concept: str = ""
    code_snippet: str = ""
    snippet_start: int = 0
    root_cause: str = ""
    trigger: str = ""
    expected_behavior: str = ""
    impact: str = ""
    related: list[str] = field(default_factory=list)
    severity_reason: str = ""
    verification: str = "possible"
    analyzed: bool = False
    priority: float = 0.0
    facts: dict = field(default_factory=dict)


def group_issues(issues: list[Issue]) -> list[list[Issue]]:
    """Merge reports of the same problem from different tools (same file, concept, nearby line)."""
    groups: list[list[Issue]] = []
    for issue in sorted(issues, key=lambda i: (i.file, i.line, -i.confidence)):
        for g in groups:
            head = g[0]
            if head.file == issue.file and head.concept == issue.concept and abs(head.line - issue.line) <= 1:
                g.append(issue)
                break
        else:
            groups.append([issue])
    return groups


def snippet_for(text: str, structure: Structure, file: str, line_start: int, line_end: int) -> tuple[str, int]:
    lines = text.split("\n")
    sym = structure.symbol_at(file, line_start)
    if sym and sym.kind != "class" and sym.line_end - sym.line_start <= 60:
        start, end = sym.line_start, sym.line_end
    else:
        start, end = max(1, line_start - 3), min(len(lines), line_end + 3)
    return "\n".join(lines[start - 1 : end]), start


def impact_for(structure: Structure, file: str, function: str, line: int) -> tuple[float, str, list[str]]:
    sym = structure.find_symbol(file, function) or structure.symbol_at(file, line)
    mod = structure.modules.get(file)
    if sym is None:
        reach = f"; imported by {len(mod.imported_by)} module(s)" if mod and mod.imported_by else ""
        return 1.0, f"Module-level code in {file}{reach}", []
    factor = 1.0
    notes = []
    callers = [c for c in sym.called_by]
    if callers:
        factor += 0.1 * min(len(callers), 3)
        notes.append("used by " + ", ".join(c.split(".")[-1] for c in callers[:4]))
    if not sym.name.startswith("_"):
        factor += 0.05
    if sym.tested_by:
        notes.append(f"covered by {len(sym.tested_by)} existing test(s)")
    else:
        factor += 0.1
        notes.append("no existing test calls it")
    if mod and mod.is_entry_point:
        factor += 0.05
        notes.append("module is an entry point")
    related = sorted(set(sym.called_by + sym.calls))[:12]
    return round(factor, 2), f"{sym.qualname}: " + "; ".join(notes), related


def priority(severity: str, confidence: float, impact_factor: float) -> float:
    return round(SEVERITY_WEIGHT.get(severity, 1.0) * max(0.0, min(confidence, 1.0)) * impact_factor, 3)


def issues_to_drafts(issues: list[Issue], structure: Structure, read_text) -> list[DraftFinding]:
    drafts = []
    texts: dict[str, str] = {}
    for group in group_issues(issues):
        head = max(group, key=lambda i: (SEVERITY_WEIGHT[i.severity], i.confidence))
        if head.file not in texts:
            texts[head.file] = read_text(head.file)
        sym = structure.symbol_at(head.file, head.line)
        func = sym.name if sym and sym.kind != "class" else "<module>"
        snippet, snip_start = snippet_for(texts[head.file], structure, head.file, head.line, head.end_line)
        tools = sorted({f"{i.tool}:{i.rule}" for i in group})
        confidence = min(0.98, max(i.confidence for i in group) + (0.05 if len({i.tool for i in group}) > 1 else 0))
        messages = "; ".join(sorted({i.message for i in group}))
        drafts.append(DraftFinding(
            title=head.title or head.message,
            description=f"{messages} (line {head.line}).",
            file=head.file, function=func, line_start=head.line, line_end=head.end_line,
            category=head.category, severity=head.severity, confidence=round(confidence, 2),
            sources=tools, concept=head.concept, code_snippet=snippet, snippet_start=snip_start,
            root_cause=_CONCEPT_EXPLANATION.get(head.concept, ""),
            severity_reason=f"Rated {head.severity} by the {head.concept or head.rule} rule of {', '.join(tools)}.",
            verification="confirmed" if any(i.confirmed for i in group) else "possible",
        ))
    return drafts


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int, slack: int = 2) -> bool:
    return a_start - slack <= b_end and b_start - slack <= a_end


def merge_ai_findings(static: list[DraftFinding], ai: list[DraftFinding]) -> list[DraftFinding]:
    """Combine AI findings with static ones that describe the same problem."""
    used: set[int] = set()
    merged: list[DraftFinding] = []
    for a in ai:
        match_idx = None
        for i, s in enumerate(static):
            if i in used or s.file != a.file:
                continue
            same_place = (s.function == a.function and a.function != "<module>") or _overlaps(
                a.line_start, a.line_end, s.line_start, s.line_end
            )
            if same_place and (s.category == a.category or s.concept in ("eval", "exec", "mutable-default")):
                match_idx = i
                break
        if match_idx is not None:
            s = static[match_idx]
            used.add(match_idx)
            a.sources = sorted(set(a.sources + s.sources))
            a.confidence = round(min(0.98, max(a.confidence, s.confidence) + 0.1), 2)
            if SEVERITY_WEIGHT[s.severity] > SEVERITY_WEIGHT[a.severity]:
                a.severity = s.severity
            if s.verification == "confirmed":
                a.verification = "confirmed"
            a.severity_reason = (a.severity_reason + " Static analysis reported the same problem.").strip()
        merged.append(a)
    merged.extend(s for i, s in enumerate(static) if i not in used)
    return merged


def rank(drafts: list[DraftFinding], structure: Structure) -> list[DraftFinding]:
    for d in drafts:
        factor, impact_text, related = impact_for(structure, d.file, d.function, d.line_start)
        d.impact = d.impact or impact_text
        d.related = d.related or related
        d.priority = priority(d.severity, d.confidence, factor)
    return sorted(drafts, key=lambda d: (-d.priority, d.file, d.line_start))
