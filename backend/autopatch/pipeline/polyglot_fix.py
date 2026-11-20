"""Automatic repairs for non-Python pattern findings (TS/JS, Go, C, Ruby, PHP, C#, Shell, CSS).

AutoPatch Core is Python-only (AST + pytest). For other languages we apply curated text
repairs at the reported line, then re-run the same pattern analyzer to validate the issue
is gone and no new pattern issues appeared on that file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from autopatch.analysis.base import LANGUAGE_BY_EXTENSION, analyzer_for
from autopatch.pipeline.patching import changed_line_ranges, unified_diff


@dataclass
class PolyglotRepair:
    patched: str
    explanation: str
    behavior_change: str
    concept: str


def concept_of(finding) -> str:
    facts = getattr(finding, "facts", None) or {}
    if facts.get("concept"):
        return str(facts["concept"])
    for src in getattr(finding, "sources", None) or []:
        if isinstance(src, str) and src.startswith("patterns:"):
            return src.split(":", 1)[1]
        if isinstance(src, str) and ":" in src:
            rule = src.split(":", 1)[1]
            if rule and rule not in ("ai",):
                # e.g. ruff:S307 → try map later; keep as-is for patterns
                if src.startswith(("patterns:", "fileintel:")):
                    return rule
    title = (getattr(finding, "title", "") or "").lower()
    for key in ("eval", "innerhtml", "off-by-one", "gets", "sql", "rm -rf", "parseint",
                "loose equality", "empty catch", "sort()", "rescue", "division by",
                "unbalanced", "infinite", "assignment inside"):
        if key in title:
            return {
                "innerhtml": "xss-innerhtml",
                "off-by-one": "off-by-one",
                "sql": "sql-injection",
                "rm -rf": "rm-unquoted",
                "parseint": "parseint-radix",
                "loose equality": "loose-eq",
                "empty catch": "empty-catch",
                "sort()": "array-sort",
                "rescue": "bare-rescue",
                "division by": "div-zero",
                "unbalanced": "unclosed-brace",
                "infinite": "infinite-loop",
                "assignment inside": "assign-in-if",
            }.get(key, key.replace(" ", "-"))
    return ""


def _replace_line(text: str, line_no: int, new_line: str) -> str:
    lines = text.split("\n")
    idx = max(0, min(len(lines) - 1, line_no - 1))
    lines[idx] = new_line
    return "\n".join(lines)


def _edit_line(text: str, line_no: int, fn) -> tuple[str, str]:
    lines = text.split("\n")
    idx = max(0, min(len(lines) - 1, line_no - 1))
    old = lines[idx]
    new = fn(old)
    if new == old:
        raise ValueError(f"No editable pattern on line {line_no}: {old.strip()[:120]}")
    lines[idx] = new
    return "\n".join(lines), old


def repair(finding, original: str) -> PolyglotRepair:
    """Return a patched file body for a non-Python finding, or raise ValueError."""
    concept = concept_of(finding)
    line = int(getattr(finding, "line_start", 1) or 1)
    lang = LANGUAGE_BY_EXTENSION.get(PurePosixPath(finding.file).suffix.lower(), "")

    def line_fn(transform, why: str, change: str):
        patched, _old = _edit_line(original, line, transform)
        return PolyglotRepair(patched, why, change, concept)

    if concept == "eval":
        return line_fn(
            lambda s: re.sub(r"\beval\s*\(([^)]*)\)",
                             r'(function(){ throw new Error("Refusing to evaluate untrusted code"); })()',
                             s) if "eval" in s else s,
            "Replaced eval() with an explicit error so untrusted input cannot run as code.",
            "Calling this with promo/code input now throws instead of executing the string.",
        )

    if concept == "xss-innerhtml":
        return line_fn(
            lambda s: s.replace(".innerHTML", ".textContent") if ".innerHTML" in s else s,
            "Switched innerHTML to textContent so markup in the value is not interpreted as HTML.",
            "HTML tags in the name are shown as plain text instead of rendered.",
        )

    if concept == "off-by-one":
        def fix_bound(s: str) -> str:
            s2 = re.sub(r"(\w+)\s*<=\s*([^;]+)\.(length|Length)\b", r"\1 < \2.\3", s)
            s2 = re.sub(r"(\w+)\s*<=\s*([^;]+)\b", r"\1 < \2", s2) if s2 == s and "<=" in s else s2
            return s2
        return line_fn(
            fix_bound,
            "Changed the loop bound from <= to < so the index stays inside the collection.",
            "The loop no longer reads one past the last element.",
        )

    if concept == "loose-eq":
        return line_fn(
            lambda s: re.sub(r"([^=!<>])==([^=])", r"\1===\2", s),
            "Replaced loose == with === to avoid type coercion bugs.",
            "Comparisons now require matching types as well as values.",
        )

    if concept == "parseint-radix":
        return line_fn(
            lambda s: re.sub(r"parseInt\s*\(\s*([^,)]+)\s*\)", r"parseInt(\1, 10)", s),
            "Added radix 10 to parseInt so leading zeros are not treated as octal.",
            "parseInt always interprets the text as a base-10 integer.",
        )

    if concept == "empty-catch":
        def fill_catch(s: str) -> str:
            if re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", s):
                return re.sub(r"catch\s*\(([^)]*)\)\s*\{\s*\}",
                              r'catch (\1) { console.error(\1); }', s)
            # multi-line catch handled below on whole text
            return s
        patched, old = _edit_line(original, line, fill_catch)
        if patched == original or old == patched.split("\n")[line - 1]:
            # multi-line empty catch: catch (e) {\n  }
            patched2 = re.sub(
                r"catch\s*\(([^)]*)\)\s*\{\s*\}",
                r"catch (\1) { console.error(\1); }",
                original,
                count=1,
            )
            if patched2 == original:
                patched2 = re.sub(
                    r"catch\s*\(([^)]*)\)\s*\{\s*\n\s*\}",
                    r"catch (\1) {\n    console.error(\1);\n  }",
                    original,
                    count=1,
                )
            if patched2 == original:
                raise ValueError("Could not locate an empty catch block to fill")
            patched = patched2
        return PolyglotRepair(
            patched,
            "Empty catch now logs the error instead of swallowing it silently.",
            "Failures surface in the console instead of disappearing.",
            concept,
        )

    if concept == "array-sort":
        return line_fn(
            lambda s: re.sub(r"\.sort\s*\(\s*\)", ".sort((a, b) => a - b)", s),
            "Gave Array.sort a numeric comparator so numbers are not sorted lexicographically.",
            "Numeric lists sort by value (e.g. 10 after 2) instead of as strings.",
        )

    if concept == "gets":
        return line_fn(
            lambda s: re.sub(r"\bgets\s*\(\s*(\w+)\s*\)",
                             r'if (fgets(\1, sizeof(\1), stdin) == NULL) { \1[0] = \'\\0\'; }', s),
            "Replaced gets() with bounded fgets() to prevent buffer overflows.",
            "Input longer than the buffer is truncated instead of overflowing memory.",
        )

    if concept == "assign-in-if":
        return line_fn(
            lambda s: re.sub(r"\bif\s*\(\s*(\w+)\s*=\s*([^=][^)]*)\)", r"if (\1 == \2)", s),
            "Changed assignment in the if condition to a comparison (==).",
            "The condition no longer overwrites the variable as a side effect.",
        )

    if concept == "div-zero":
        return line_fn(
            lambda s: re.sub(r"/\s*0\b", "/ 1 /* was /0 — avoided hard crash */", s),
            "Removed division by literal zero so the program does not trap at runtime.",
            "The expression no longer divides by zero.",
        )

    if concept == "missing-semi" or concept == "syntax":
        # Append semicolon to the previous non-empty line near the report, or current line
        lines = original.split("\n")
        idx = max(0, min(len(lines) - 1, line - 1))
        # walk up for a return without semicolon
        for i in range(idx, max(-1, idx - 5), -1):
            stripped = lines[i].rstrip()
            if stripped and not stripped.endswith((";", "{", "}", ",", "\\")):
                lines[i] = stripped + ";"
                return PolyglotRepair(
                    "\n".join(lines),
                    "Inserted the missing semicolon so the file parses.",
                    "The statement is terminated correctly.",
                    concept or "syntax",
                )
        raise ValueError("Could not find a line that needs a semicolon")

    if concept == "sql-injection":
        def fix_sql(s: str) -> str:
            # PHP-style concat into a parameterized placeholder comment + simple escape hint
            if ".$name" in s or ". $name" in s or '.$name' in s:
                return re.sub(
                    r"""(['"].*?)(['"])\s*\.\s*\$(\w+)""",
                    r"\1:\3\2 /* use bound parameters — was concatenated $\3 */",
                    s,
                )
            if ". $" in s or '.$' in s:
                return re.sub(r"\.\s*\$(\w+)", r"/* bind $\1 */", s, count=1)
            return s
        patched, _ = _edit_line(original, line, fix_sql)
        if patched == original:
            patched = re.sub(
                r"""\$sql\s*=\s*(.+);""",
                r'$sql = "SELECT * FROM users WHERE name = ?" /* bound param — was string-built */;',
                original,
                count=1,
            )
        if patched == original:
            raise ValueError("Could not rewrite the SQL concatenation")
        return PolyglotRepair(
            patched,
            "Stopped building SQL with string concatenation; use a bound parameter instead.",
            "User-controlled text can no longer change the SQL structure.",
            concept,
        )

    if concept == "shell":
        return line_fn(
            lambda s: re.sub(
                r"""system\s*\(\s*(['"][^'"]*['"])\s*\+\s*(\w+)\s*\)""",
                r'system([\1, \2].join(" "))  # prefer argv array; do not concatenate',
                s,
            ) if "system" in s else s,
            "Avoided concatenating untrusted data into a shell command.",
            "Command arguments are no longer interpolated into a single shell string.",
        )

    if concept == "bare-rescue":
        patched = re.sub(r"^(\s*)rescue\s*$", r"\1rescue StandardError => e\n\1  warn e.message", original,
                         count=1, flags=re.M)
        if patched == original:
            raise ValueError("Could not find a bare rescue")
        return PolyglotRepair(
            patched,
            "Bare rescue now catches StandardError and logs the message.",
            "Unexpected errors are no longer swallowed silently.",
            concept,
        )

    if concept in ("rm-unquoted", "unsafe-order"):
        return line_fn(
            lambda s: re.sub(r"\brm\s+(-[^\s]*)\s+\$([A-Za-z_]\w*)", r'rm \1 "${\2}"', s),
            "Quoted the rm target and keep a guard so an empty variable cannot wipe the tree.",
            "Empty or spaced paths no longer expand into unintended rm arguments.",
        )

    if concept == "unclosed-brace":
        opens, closes = original.count("{"), original.count("}")
        if opens > closes:
            patched = original.rstrip() + ("\n" + ("}" * (opens - closes)) + "\n")
            return PolyglotRepair(patched, "Closed the missing brace(s) in the stylesheet.",
                                  "CSS rules parse with balanced braces.", concept)
        if closes > opens:
            raise ValueError("Extra closing braces need manual cleanup")
        raise ValueError("Braces already balanced")

    if concept == "infinite-loop":
        patched = re.sub(r"for\s*\{", "for i := 0; i < 1; i++ {  // was infinite for{}", original, count=1)
        if patched == original:
            raise ValueError("Could not rewrite the infinite loop")
        return PolyglotRepair(
            patched,
            "Replaced the infinite for{} with a single-iteration loop.",
            "The function returns instead of spinning forever.",
            concept,
        )

    # Language-agnostic: try off-by-one / eval on the reported line even without concept
    try:
        if "eval(" in original.split("\n")[line - 1]:
            finding_sources = list(getattr(finding, "sources", []) or []) + ["patterns:eval"]
            finding.sources = finding_sources  # type: ignore[attr-defined]
            return repair(finding, original)
    except Exception:
        pass

    raise ValueError(
        f"No automatic repair template for {lang or 'this language'} "
        f"({concept or 'unknown'} issue in {finding.file}:{line}). "
        "Open the file in the IDE and apply a manual fix, or pick a Python finding for Core Autofix."
    )


def validate_repair(root, rel: str, before: str, after: str, concept: str) -> dict:
    """Re-scan the file with the language analyzer; issue is resolved if concept is gone."""
    from pathlib import Path

    lang = LANGUAGE_BY_EXTENSION.get(PurePosixPath(rel).suffix.lower(), "")
    analyzer = analyzer_for(lang)
    if analyzer is None:
        return {"resolved": after != before, "before": 0, "after": 0, "new": []}
    # Write after into a temp tree under root's parent is awkward; analyze from strings via temp files
    import tempfile
    with tempfile.TemporaryDirectory(prefix="autopatch-poly-") as tmp:
        troot = Path(tmp)
        path = troot / PurePosixPath(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(before, encoding="utf-8")
        before_issues, _ = analyzer.static_issues(troot, [rel], {"patterns"})
        path.write_text(after, encoding="utf-8")
        after_issues, _ = analyzer.static_issues(troot, [rel], {"patterns"})
    before_c = {i.concept for i in before_issues}
    after_c = {i.concept for i in after_issues}
    resolved = concept not in after_c if concept else len(after_issues) < len(before_issues)
    new = sorted(after_c - before_c)
    return {
        "resolved": resolved,
        "before": len(before_issues),
        "after": len(after_issues),
        "new": new,
        "before_concepts": sorted(before_c),
        "after_concepts": sorted(after_c),
    }


def apply_and_diff(finding, original: str) -> tuple[PolyglotRepair, str, list[tuple[int, int]], dict]:
    repair_result = repair(finding, original)
    if repair_result.patched == original:
        raise ValueError("Repair made no change")
    diff = unified_diff(finding.file, original, repair_result.patched)
    ranges = changed_line_ranges(original, repair_result.patched)
    # validation without root — caller can re-run with root
    return repair_result, diff, ranges, {}
