"""Pattern-based static analysis for non-Python languages.

These analyzers do not execute project code. They extract a light symbol map and flag
high-signal bug / security patterns so polyglot demos and mixed-language repos get real
findings in Scan — not a "Python only" silence.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from autopatch.analysis.base import Issue, LanguageAnalyzer, ModuleInfo, Structure, Symbol, ToolInfo, register

# (concept, severity, category, confidence, title, regex) — group 0 whole match; line from search
Pattern = tuple[str, str, str, float, str, re.Pattern[str]]


def _pat(concept: str, severity: str, category: str, confidence: float, title: str, regex: str,
         flags: int = 0) -> Pattern:
    return (concept, severity, category, confidence, title, re.compile(regex, flags))


JS_TS_PATTERNS: list[Pattern] = [
    _pat("eval", "high", "security", 0.85, "eval() can run arbitrary code", r"\beval\s*\("),
    _pat("xss-innerhtml", "high", "security", 0.75, "innerHTML assigned from data (XSS risk)",
         r"\.innerHTML\s*="),
    _pat("off-by-one", "high", "crash", 0.7, "Loop bound uses <= length (off-by-one)",
         r"for\s*\([^;]*;\s*\w+\s*<=\s*[^;]*\.(?:length|Length)\s*;"),
    _pat("loose-eq", "medium", "incorrect_output", 0.45, "Loose equality (==) instead of ===",
         r"[^=!]==[^=]"),
    _pat("parseint-radix", "medium", "incorrect_output", 0.5, "parseInt without a radix",
         r"\bparseInt\s*\(\s*[^,)]+\s*\)"),
    _pat("empty-catch", "medium", "incorrect_output", 0.55, "Empty catch swallows errors",
         r"catch\s*\([^)]*\)\s*\{\s*\}"),
    _pat("array-sort", "medium", "incorrect_output", 0.55, "Array.sort() without a comparator (lexicographic)",
         r"\.sort\s*\(\s*\)"),
]

GO_PATTERNS: list[Pattern] = [
    _pat("infinite-loop", "high", "performance", 0.7, "Infinite for{} loop with no break",
         r"for\s*\{\s*\n(?:[^\n]*\n){0,6}[^\n]*Println"),
    _pat("err-ignored", "medium", "incorrect_output", 0.4, "Error return value discarded with _",
         r":=\s*[^;\n]+;\s*_+\s*="),
]

C_PATTERNS: list[Pattern] = [
    _pat("gets", "critical", "security", 0.95, "gets() is unsafe (buffer overflow)", r"\bgets\s*\("),
    _pat("off-by-one", "high", "crash", 0.7, "Loop bound uses <= on a count/length",
         r"for\s*\([^;]*;\s*\w+\s*<=\s*[^;]+;\s*"),
    _pat("assign-in-if", "medium", "incorrect_output", 0.65, "Assignment inside if condition (likely ==)",
         r"\bif\s*\(\s*\w+\s*=\s*[^=]"),
    _pat("div-zero", "high", "crash", 0.8, "Division by literal zero", r"/\s*0\b"),
    _pat("missing-semi", "critical", "crash", 0.9, "Likely missing semicolon before closing brace",
         r"return\s+\w+\s*\n\s*\}"),
]

RUBY_PATTERNS: list[Pattern] = [
    _pat("shell", "high", "security", 0.8, "Shell command built with string concatenation",
         r"\bsystem\s*\(\s*[\"'].*\+\s*\w+|\bsystem\s*\(\s*[^)]*\+"),
    _pat("bare-rescue", "medium", "incorrect_output", 0.55, "Bare rescue hides every error",
         r"^\s*rescue\s*$", re.M),
]

PHP_PATTERNS: list[Pattern] = [
    _pat("sql-injection", "high", "security", 0.8, "SQL query built with string concatenation",
         r"(?i)(?:SELECT|INSERT|UPDATE|DELETE)[\s\S]{0,120}?\.\s*\$"),
    _pat("syntax", "critical", "crash", 0.85, "Likely missing punctuation in statement",
         r"->query\s*\(\s*\$\w+\s*\)\s*\n\s*\}"),
]

CS_PATTERNS: list[Pattern] = [
    _pat("off-by-one", "high", "crash", 0.7, "Loop bound uses <= Length (off-by-one)",
         r"for\s*\([^;]*;\s*\w+\s*<=\s*[^;]*\.Length\s*;"),
    _pat("empty-catch", "medium", "incorrect_output", 0.55, "Empty catch swallows exceptions",
         r"catch\s*\([^)]*\)\s*\{\s*\}"),
]

SHELL_PATTERNS: list[Pattern] = [
    _pat("rm-unquoted", "high", "security", 0.75, "rm -rf on an unquoted variable",
         r"\brm\s+-[^\n]*\$[A-Za-z_]"),
    _pat("unsafe-order", "high", "security", 0.7, "Destructive command before empty-argument check",
         r"rm\s+-[^\n]+\n(?:[^\n]*\n){0,3}if\s+\[\s+-z"),
]

CSS_PATTERNS: list[Pattern] = [
    _pat("unclosed-brace", "high", "incorrect_output", 0.8, "Unbalanced braces in stylesheet",
         r"\{[^}]*\{[^}]*$"),
]


FUNC_PATTERNS: dict[str, re.Pattern[str]] = {
    "JavaScript": re.compile(
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)|(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\("
    ),
    "TypeScript": re.compile(
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)|(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\("
    ),
    "Go": re.compile(r"func\s+(?:\([^)]+\)\s*)?(\w+)\s*\("),
    "C": re.compile(r"^\s*(?:[\w\s\*]+)\s+(\w+)\s*\([^;]*\)\s*\{", re.M),
    "C++": re.compile(r"^\s*(?:[\w\s\*\:]+)\s+(\w+)\s*\([^;]*\)\s*\{", re.M),
    "Ruby": re.compile(r"^\s*def\s+(\w+)", re.M),
    "PHP": re.compile(r"function\s+(\w+)\s*\("),
    "C#": re.compile(r"(?:public|private|static|protected|internal|\s)+\s+\w+\s+(\w+)\s*\([^;]*\)\s*\{"),
    "Shell": re.compile(r"^\s*(?:function\s+(\w+)|(\w+)\s*\(\s*\))", re.M),
    "CSS": re.compile(r"([.#]?[\w-]+)\s*\{"),
}


class PatternAnalyzer(LanguageAnalyzer):
    """Regex structure + curated bug patterns for one language."""

    def __init__(self, language: str, extensions: tuple[str, ...], patterns: list[Pattern],
                 test_framework: str = "none"):
        self.language = language
        self.extensions = extensions
        self.test_framework = test_framework
        self._patterns = patterns

    def tools(self) -> list[ToolInfo]:
        return [ToolInfo("patterns", f"Static pattern scan for {self.language}", True, "builtin")]

    def structure(self, root: Path, files: list[str]) -> Structure:
        st = Structure(language=self.language)
        func_re = FUNC_PATTERNS.get(self.language)
        for rel in files:
            if not self.handles(rel):
                continue
            path = root / PurePosixPath(rel)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = text.splitlines()
            mod = PurePosixPath(rel).with_suffix("").as_posix().replace("/", ".")
            info = ModuleInfo(file=rel, module=mod, is_test=False, lines=len(lines))
            st.modules[rel] = info
            if not func_re:
                continue
            for m in func_re.finditer(text):
                name = next((g for g in m.groups() if g), None)
                if not name or name in ("if", "for", "while", "switch", "catch"):
                    continue
                line = text[: m.start()].count("\n") + 1
                # rough end: next blank-ish or end of file
                end = min(len(lines), line + 40)
                qn = f"{mod}.{name}"
                sym = Symbol(qualname=qn, name=name, kind="function", file=rel,
                             line_start=line, line_end=end)
                st.symbols[qn] = sym
                info.symbols.append(qn)
        return st

    def static_issues(self, root: Path, files: list[str], tools: set[str]) -> tuple[list[Issue], dict]:
        issues: list[Issue] = []
        scanned = 0
        for rel in files:
            if not self.handles(rel):
                continue
            path = root / PurePosixPath(rel)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned += 1
            # CSS brace balance
            if self.language == "CSS":
                if text.count("{") != text.count("}"):
                    issues.append(Issue(
                        tool="patterns", rule="unbalanced-braces",
                        message="Opening and closing braces do not match",
                        file=rel, line=1, end_line=1, severity="high", category="incorrect_output",
                        confidence=0.85, concept="unclosed-brace",
                        title="Unbalanced braces in stylesheet", confirmed=True,
                    ))
            for concept, severity, category, confidence, title, rx in self._patterns:
                for m in rx.finditer(text):
                    line = text[: m.start()].count("\n") + 1
                    issues.append(Issue(
                        tool="patterns", rule=concept, message=title, file=rel,
                        line=line, end_line=line, severity=severity, category=category,
                        confidence=confidence, concept=concept, title=title, confirmed=False,
                    ))
        return issues, {"patterns": {"status": "ok", "issues": len(issues), "files": scanned}}


def _register_all() -> None:
    specs: list[tuple[str, tuple[str, ...], list[Pattern]]] = [
        ("JavaScript", (".js", ".mjs", ".cjs", ".jsx"), JS_TS_PATTERNS),
        ("TypeScript", (".ts", ".tsx"), JS_TS_PATTERNS),
        ("Go", (".go",), GO_PATTERNS),
        ("C", (".c", ".h"), C_PATTERNS),
        ("C++", (".cpp", ".cc", ".cxx", ".hpp", ".hh"), C_PATTERNS),
        ("Ruby", (".rb",), RUBY_PATTERNS),
        ("PHP", (".php",), PHP_PATTERNS),
        ("C#", (".cs",), CS_PATTERNS),
        ("Shell", (".sh", ".bash", ".zsh"), SHELL_PATTERNS),
        ("CSS", (".css", ".scss", ".sass", ".less"), CSS_PATTERNS),
    ]
    for language, exts, patterns in specs:
        register(PatternAnalyzer(language, exts, patterns))


_register_all()
