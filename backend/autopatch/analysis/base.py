"""Language-neutral analysis interfaces.

A LanguageAnalyzer understands one language: its project structure, its static analysis
tools and its test framework. Python is the only one implemented so far; adding Java or
JavaScript means writing another analyzer and registering it here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

LANGUAGE_BY_EXTENSION = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".cs": "C#",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++",
    ".swift": "Swift", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell", ".sql": "SQL",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "CSS", ".sass": "CSS", ".less": "CSS",
    ".json": "JSON", ".jsonc": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".md": "Markdown", ".markdown": "Markdown", ".ini": "TOML", ".cfg": "TOML", ".env": "Shell",
}


def detect_languages(files: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in files:
        lang = LANGUAGE_BY_EXTENSION.get(PurePosixPath(f).suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


@dataclass
class Issue:
    """One problem reported by a static check, before it becomes a Finding."""

    tool: str
    rule: str
    message: str
    file: str
    line: int
    end_line: int
    column: int = 0
    severity: str = "medium"
    category: str = "incorrect_output"
    confidence: float = 0.5
    concept: str = ""  # shared key so duplicates from different tools merge
    title: str = ""
    confirmed: bool = False  # deterministic checks (e.g. syntax errors) are proven, not suspected

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Symbol:
    qualname: str          # module-qualified, e.g. "grades.get_grade" or "shop.Cart.total"
    name: str
    kind: str              # function | class | method
    file: str
    line_start: int
    line_end: int
    params: list[str] = field(default_factory=list)
    doc: str = ""
    calls: list[str] = field(default_factory=list)       # resolved qualnames inside the project
    called_by: list[str] = field(default_factory=list)
    tested_by: list[str] = field(default_factory=list)   # test functions that call it

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ModuleInfo:
    file: str
    module: str
    is_test: bool
    lines: int
    imports: list[str] = field(default_factory=list)       # project files this module imports
    imported_by: list[str] = field(default_factory=list)
    external_imports: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    is_entry_point: bool = False
    parse_error: str = ""


@dataclass
class Structure:
    language: str
    modules: dict[str, ModuleInfo] = field(default_factory=dict)  # by file path
    symbols: dict[str, Symbol] = field(default_factory=dict)      # by qualname
    syntax_issues: list[Issue] = field(default_factory=list)
    declared_dependencies: list[str] = field(default_factory=list)

    def symbol_at(self, file: str, line: int) -> Symbol | None:
        """Innermost function, method or class containing a line."""
        best: Symbol | None = None
        for sym in self.symbols.values():
            if sym.file == file and sym.line_start <= line <= sym.line_end:
                if best is None or (sym.line_end - sym.line_start) < (best.line_end - best.line_start):
                    best = sym
        return best

    def find_symbol(self, file: str, name: str) -> Symbol | None:
        if not name or name == "<module>":
            return None
        for sym in self.symbols.values():
            if sym.file == file and (sym.name == name or sym.qualname.endswith("." + name)):
                return sym
        return None

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "modules": [asdict(m) for m in self.modules.values()],
            "symbols": [s.to_dict() for s in self.symbols.values()],
            "declared_dependencies": self.declared_dependencies,
        }

    def summary_for_ai(self, max_symbols: int = 400) -> str:
        """Compact call-graph listing that helps the model trace bugs across files."""
        lines = []
        for i, sym in enumerate(sorted(self.symbols.values(), key=lambda s: (s.file, s.line_start))):
            if i >= max_symbols:
                lines.append("...")
                break
            parts = [f"{sym.qualname} ({sym.kind}, {sym.file}:{sym.line_start}-{sym.line_end})"]
            if sym.called_by:
                parts.append("called by " + ", ".join(sym.called_by[:6]))
            if sym.tested_by:
                parts.append(f"tested by {len(sym.tested_by)} test(s)")
            else:
                parts.append("no direct tests")
            lines.append(" - " + "; ".join(parts))
        return "\n".join(lines)


@dataclass
class ToolInfo:
    name: str
    purpose: str
    available: bool
    version: str = ""


class LanguageAnalyzer(ABC):
    language: str
    extensions: tuple[str, ...]
    test_framework: str

    def handles(self, path: str) -> bool:
        return PurePosixPath(path).suffix.lower() in self.extensions

    @abstractmethod
    def structure(self, root: Path, files: list[str]) -> Structure: ...

    @abstractmethod
    def static_issues(self, root: Path, files: list[str], tools: set[str]) -> tuple[list[Issue], dict]: ...

    @abstractmethod
    def tools(self) -> list[ToolInfo]: ...


_REGISTRY: dict[str, LanguageAnalyzer] = {}


def register(analyzer: LanguageAnalyzer) -> None:
    _REGISTRY[analyzer.language] = analyzer


def analyzers() -> dict[str, LanguageAnalyzer]:
    if not _REGISTRY:
        from autopatch.analysis.python_analyzer import PythonAnalyzer
        import autopatch.analysis.polyglot  # noqa: F401 — registers pattern analyzers

        register(PythonAnalyzer())
    return _REGISTRY


def analyzer_for(language: str) -> LanguageAnalyzer | None:
    return analyzers().get(language)
