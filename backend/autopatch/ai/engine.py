"""The AI engine: prompts and calls for scanning, root-cause analysis, test writing and fixing.

Every call sends the same system prompt and the same project listing first, so the listing is
cached and each later call for that project version mostly pays for its task text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath

from autopatch.ai.providers import AIProvider, AIUsage
from autopatch.ai.schemas import AIScanResult, FixProposal, GeneratedTests, RootCauseAnalysis
from autopatch.analysis.base import Structure
from autopatch.analysis.findings import DraftFinding

SYSTEM_PROMPT = """You are the analysis engine of AutoPatch, a tool that finds real defects in a software project, \
explains them, writes tests that prove them, and makes minimal, safe fixes that a developer reviews before they are applied.

How to work:
- Report real defects only: crashes, wrong results, edge-case failures, security weaknesses, shared or corrupted state, \
and serious performance problems. Do not report style, naming, formatting, missing docstrings or type hints.
- Ground every claim in the code provided. Use exact names, file paths and line numbers from the listing. When a \
claim depends on how the code is called, say so and lower your confidence.
- Explain causes, not just symptoms: trace from the triggering input, through the functions involved, to the faulty \
line, and say why it fails there. Write for a developer who is new to this code.
- Confidence: 0.9 or higher only when you can name a concrete input and trace the failure step by step; 0.6 to 0.85 \
when it depends on reasonable assumptions about usage; below 0.5 when speculative.
- Severity: critical = exploitable security hole, data loss, or the program cannot run at all; high = crash or wrong \
result on realistic input; medium = failure on edge cases or misuse; low = minor or unlikely.
- Test files show intended behavior. Treat them as part of the specification and do not report bugs in them unless a \
test is itself broken."""

CONTEXT_FILE_EXTS = {".py", ".pyi", ".toml", ".cfg", ".ini", ".txt", ".md", ".json", ".yaml", ".yml"}


@dataclass
class ProjectContext:
    text: str
    included: list[str]
    omitted: list[str]
    files: dict | None = None
    structure: Structure | None = None
    root: object = None


def build_context(name: str, files: dict[str, str], structure: Structure, max_kb: int, root=None) -> ProjectContext:
    """Render the project with line numbers. Sorted and deterministic so it caches well."""
    budget = max_kb * 1024
    def rank(path: str) -> tuple:
        p = PurePosixPath(path)
        mod = structure.modules.get(path)
        if mod and not mod.is_test:
            return (0, path)
        if mod and mod.is_test:
            return (1, path)
        if p.name in ("requirements.txt", "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini"):
            return (2, path)
        return (3, path)

    included, omitted, used = [], [], 0
    for path in sorted(files, key=rank):
        if PurePosixPath(path).suffix.lower() not in CONTEXT_FILE_EXTS:
            continue
        size = len(files[path].encode("utf-8"))
        if used + size > budget:
            omitted.append(path)
            continue
        included.append(path)
        used += size

    parts = [f'<project name="{name}">', "<call_graph>", structure.summary_for_ai(), "</call_graph>"]
    for path in sorted(included):
        mod = structure.modules.get(path)
        kind = "test" if mod and mod.is_test else ("source" if mod else "other")
        lines = files[path].split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        width = len(str(max(len(lines), 1)))
        body = "\n".join(f"{i:>{width}}| {line}" for i, line in enumerate(lines, 1))
        parts.append(f'<file path="{path}" kind="{kind}">\n{body}\n</file>')
    if omitted:
        parts.append("<omitted_files reason=\"size limit\">" + ", ".join(sorted(omitted)) + "</omitted_files>")
    parts.append("</project>")
    return ProjectContext("\n".join(parts), included, omitted, files, structure, root)


def _finding_brief(f) -> str:
    fields = {
        "title": f.title, "file": f.file, "function": f.function,
        "lines": f"{f.line_start}-{f.line_end}", "category": f.category, "severity": f.severity,
        "description": f.description, "root_cause": f.root_cause, "trigger": f.trigger,
        "expected_behavior": f.expected_behavior, "reported_by": f.sources,
    }
    return json.dumps({k: v for k, v in fields.items() if v}, indent=2)


class Engine:
    def __init__(self, provider: AIProvider, scan_effort: str = "high", fix_effort: str = "high"):
        self.provider = provider
        self.scan_effort = scan_effort
        self.fix_effort = fix_effort

    def _call(self, context: ProjectContext, task: str, schema, effort: str):
        return self.provider.generate(system=SYSTEM_PROMPT, context=context.text, task=task, schema=schema,
                                      effort=effort)

    # ------------------------------------------------------------------ scan

    def scan(self, context: ProjectContext, static: list[DraftFinding]) -> tuple[list[DraftFinding], str, AIUsage]:
        hints = "\n".join(
            f"- {d.sources} {d.file}:{d.line_start} in {d.function}: {d.description}" for d in static
        ) or "(none)"
        task = f"""Task: scan the project above for bugs.

Static analysis (Ruff, Pylint and Python's parser) reported the issues below. Confirm or refine the ones that are real \
problems by including them in your findings, leave out the ones that are harmless, and find the bugs these tools \
cannot see: logic errors, wrong comparisons or boundaries, missing input validation, wrong sort order, unhandled \
empty or zero inputs, shared mutable state, injection risks, and wrong results for edge cases.
{hints}

Report one finding per distinct bug. If one root cause affects several functions, report it once, at the place it \
should be fixed. line_start and line_end use the numbers in the listing. Return an empty list if there are no real bugs."""
        result, usage = self._call(context, task, AIScanResult, self.scan_effort)
        drafts = [
            DraftFinding(
                title=f.title, description=f.description, file=f.file.replace("\\", "/"), function=f.function,
                line_start=max(1, f.line_start), line_end=max(f.line_start, f.line_end), category=f.category,
                severity=f.severity, confidence=round(max(0.0, min(f.confidence, 0.95)), 2), sources=["ai"],
                root_cause=f.root_cause, trigger=f.trigger, expected_behavior=f.expected_behavior,
                severity_reason=f.severity_reason, analyzed=True,
            )
            for f in result.findings
        ]
        return drafts, result.project_summary, usage

    # ------------------------------------------------------------------ root cause

    def analyze(self, context: ProjectContext, finding, related: str) -> tuple[RootCauseAnalysis, AIUsage]:
        task = f"""Task: root-cause analysis of this reported issue.

{_finding_brief(finding)}

How it connects to the rest of the project (from the call graph):
{related or "(no callers or callees found)"}

Decide whether it is a real bug (is_real_bug). Then explain, in plain language: what observably goes wrong; the root \
cause traced step by step from the triggering input to the failure, including the related functions that pass the \
bad value in or depend on the result; the exact inputs or conditions that trigger it; what the code should do \
instead; and the impact on users of this code. Re-assess category, severity and confidence from the code."""
        return self._call(context, task, RootCauseAnalysis, self.fix_effort)

    # ------------------------------------------------------------------ tests

    def write_tests(self, context: ProjectContext, finding, test_path: str, import_hint: str,
                    feedback: str = "") -> tuple[GeneratedTests, AIUsage]:
        retry = f"\n\nYour previous attempt had a problem, fix it:\n{feedback}" if feedback else ""
        task = f"""Task: write pytest tests for this bug before any fix exists. The tests encode the correct behavior: \
they must FAIL on the current code where the bug shows, and PASS once the bug is fixed.

{_finding_brief(finding)}

Write one complete test module that will be saved as `{test_path}` in the project root. The project root is on \
sys.path, so import the code under test like this: {import_hint}

Name every test function with a prefix that states its kind:
- test_reproduce_...: uses the triggering input from the bug report. Must fail on the current code.
- test_normal_...: ordinary valid inputs. Must pass both before and after the fix, to catch regressions.
- test_invalid_...: invalid inputs (wrong type, out-of-range values) and how they should be handled.
- test_minimum_... and test_maximum_...: values at the lower and upper limits.
- test_edge_...: other edge cases such as empty input, zero, duplicates or very large values.
Include at least one reproduce test and at least two normal tests, plus every other kind that applies to this bug.

Rules: use only the standard library and pytest. Tests must be deterministic and fast: no network, no sleeping, no \
files outside tmp_path. For invalid input, follow the project's existing conventions; if it has none, expect \
ValueError (TypeError for wrong types) using pytest.raises. Keep current behavior for valid inputs and never assert \
something the existing tests contradict. List every test in cases with its exact function name.{retry}"""
        return self._call(context, task, GeneratedTests, self.fix_effort)

    # ------------------------------------------------------------------ fix

    def write_fix(self, context: ProjectContext, finding, test_code: str, before_results: str,
                  feedback: str = "") -> tuple[FixProposal, AIUsage]:
        retry = (f"\n\nYour previous patch was applied and tested, and it was not good enough:\n{feedback}\n"
                 "Write a corrected patch against the ORIGINAL file shown in the listing.") if feedback else ""
        tests_block = (f"Tests that must pass after your fix:\n```python\n{test_code}\n```\n\n"
                       f"Results on the current, unfixed code:\n{before_results}") if test_code else \
            "No generated tests are available; the project's existing tests must keep passing."
        task = f"""Task: fix this bug with the smallest safe change.

{_finding_brief(finding)}

{tests_block}

Rules:
- Change only {finding.file}. Fix the root cause; do not refactor, rename, reformat or touch unrelated code.
- Keep current behavior for valid inputs: the project's existing tests must keep passing. Do not modify any tests.
- Express the change as edits. Each original_code is an exact, contiguous copy of whole lines from the current \
{finding.file}, without the "N| " line-number prefix, and long enough to be unique in the file. patched_code \
replaces it completely, with the same indentation. To add an import, use a separate edit that extends an existing \
line near the top of the file.
- explanation: why the change fixes the root cause. behavior_change: what callers will notice.{retry}"""
        return self._call(context, task, FixProposal, self.fix_effort)
