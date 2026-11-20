"""Python analysis: AST structure (modules, symbols, imports, call graph) plus Ruff and Pylint.

Nothing here imports or runs the project's code. Ruff and Pylint read source text only, and
both run with AutoPatch's own configuration so a project's config cannot load plugins or run
init hooks on the host.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath

from autopatch.analysis.base import Issue, LanguageAnalyzer, ModuleInfo, Structure, Symbol, ToolInfo

STDLIB = set(sys.stdlib_module_names)

# ---------------------------------------------------------------- rule knowledge
# rule -> (concept, severity, category, confidence, title). The concept lets the same
# problem reported by Ruff and Pylint merge into one finding.
RULES: dict[str, tuple[str, str, str, float, str]] = {
    "syntax-error": ("syntax", "critical", "crash", 1.0, "Syntax error: the module cannot be imported"),
    "E0001": ("syntax", "critical", "crash", 1.0, "Syntax error: the module cannot be imported"),
    "S307": ("eval", "high", "security", 0.8, "eval() can run arbitrary code"),
    "W0123": ("eval", "high", "security", 0.8, "eval() can run arbitrary code"),
    "S102": ("exec", "high", "security", 0.8, "exec() can run arbitrary code"),
    "W0122": ("exec", "high", "security", 0.8, "exec() can run arbitrary code"),
    "S602": ("shell", "high", "security", 0.75, "Shell command built from data (shell=True)"),
    "S604": ("shell", "high", "security", 0.7, "Shell command built from data"),
    "S605": ("shell", "high", "security", 0.7, "Process started through the shell"),
    "S608": ("sql-injection", "high", "security", 0.7, "SQL query built with string formatting"),
    "S301": ("pickle", "high", "security", 0.6, "pickle can run code when loading untrusted data"),
    "S506": ("yaml-load", "high", "security", 0.7, "yaml.load without a safe loader"),
    "S324": ("weak-hash", "medium", "security", 0.55, "Weak hash function"),
    "S105": ("secret", "medium", "security", 0.5, "Hard-coded password or secret"),
    "S106": ("secret", "medium", "security", 0.5, "Hard-coded password or secret"),
    "S107": ("secret", "medium", "security", 0.5, "Hard-coded password or secret"),
    "S113": ("timeout", "medium", "edge_case", 0.5, "Network request without a timeout"),
    "W3101": ("timeout", "medium", "edge_case", 0.5, "Network request without a timeout"),
    "S110": ("silenced", "low", "incorrect_output", 0.4, "Exception silently ignored"),
    "F821": ("undefined-name", "high", "crash", 0.9, "Name is used but never defined"),
    "E0602": ("undefined-name", "high", "crash", 0.9, "Name is used but never defined"),
    "F823": ("before-assignment", "high", "crash", 0.85, "Local variable used before it is assigned"),
    "E0601": ("before-assignment", "high", "crash", 0.85, "Local variable used before it is assigned"),
    "F811": ("redefined", "medium", "incorrect_output", 0.6, "Definition is replaced by a later one"),
    "E0102": ("redefined", "medium", "incorrect_output", 0.6, "Definition is replaced by a later one"),
    "F632": ("is-literal", "medium", "incorrect_output", 0.7, "`is` used to compare with a literal"),
    "F601": ("duplicate-key", "medium", "incorrect_output", 0.7, "Dictionary key is repeated"),
    "F841": ("unused-variable", "low", "incorrect_output", 0.35, "Value is computed but never used"),
    "W0612": ("unused-variable", "low", "incorrect_output", 0.35, "Value is computed but never used"),
    "B006": ("mutable-default", "medium", "state_corruption", 0.65, "Mutable default argument is shared between calls"),
    "W0102": ("mutable-default", "medium", "state_corruption", 0.65, "Mutable default argument is shared between calls"),
    "B008": ("call-in-default", "low", "state_corruption", 0.45, "Default argument is computed once, at import"),
    "B023": ("loop-closure", "medium", "incorrect_output", 0.6, "Function captures the loop variable by reference"),
    "W0640": ("loop-closure", "medium", "incorrect_output", 0.6, "Function captures the loop variable by reference"),
    "B012": ("finally-exit", "medium", "incorrect_output", 0.6, "return/break in finally swallows exceptions"),
    "W0150": ("finally-exit", "medium", "incorrect_output", 0.6, "return/break in finally swallows exceptions"),
    "B015": ("pointless", "medium", "incorrect_output", 0.5, "Comparison result is thrown away"),
    "B018": ("pointless", "low", "incorrect_output", 0.4, "Statement has no effect"),
    "W0104": ("pointless", "low", "incorrect_output", 0.4, "Statement has no effect"),
    "W0106": ("pointless", "low", "incorrect_output", 0.4, "Expression result is not used"),
    "B020": ("loop-shadow", "medium", "incorrect_output", 0.6, "Loop variable overwrites the iterable"),
    "W0101": ("unreachable", "low", "incorrect_output", 0.5, "Code can never run"),
    "W0702": ("bare-except", "low", "incorrect_output", 0.4, "Bare except hides real errors"),
    "W0718": ("broad-except", "low", "incorrect_output", 0.3, "Catching every exception hides real errors"),
    "W0631": ("loop-var-after", "medium", "crash", 0.55, "Loop variable may be undefined after the loop"),
    "W0632": ("unpacking", "medium", "crash", 0.6, "Tuple unpacking does not match the value's length"),
    "E1120": ("missing-arg", "high", "crash", 0.8, "Call is missing a required argument"),
    "E1121": ("extra-arg", "high", "crash", 0.8, "Call passes too many arguments"),
    "E1123": ("bad-kwarg", "high", "crash", 0.75, "Call passes an unknown keyword argument"),
    "E1102": ("not-callable", "high", "crash", 0.7, "Value is called but is not callable"),
    "E1101": ("no-member", "medium", "crash", 0.45, "Attribute may not exist"),
    "E1111": ("no-return", "medium", "incorrect_output", 0.6, "Result of a function that returns nothing is used"),
    "E1128": ("no-return", "medium", "incorrect_output", 0.55, "Result of a function that returns only None is used"),
}

RUFF_SELECT = "E9,F,B,S,PLE"
RUFF_IGNORE = "S101,S311,S603,S607,F401,F403,F405,B007,B028,B904,B905"
PYLINT_ENABLE = (
    "E,W0101,W0102,W0104,W0106,W0122,W0123,W0150,W0612,W0631,W0632,W0640,W0702,W0718,W3101"
)
PYLINT_DISABLE = "E0401,E0611,E0402"  # import errors for packages not installed here are noise
_IGNORED_PYLINT = {"unknown-option-value", "bad-option-value", "useless-option-value", "unrecognized-option"}


def _rule_info(tool: str, rule: str, message: str) -> tuple[str, str, str, float, str]:
    if rule in RULES:
        return RULES[rule]
    if tool == "ruff" and rule.startswith("S"):
        return (f"security-{rule}", "medium", "security", 0.5, message)
    if rule.startswith(("PLE", "E")) and tool == "pylint" or rule.startswith("PLE"):
        return (f"error-{rule}", "high", "crash", 0.6, message)
    if rule.startswith("B"):
        return (f"bugbear-{rule}", "medium", "incorrect_output", 0.5, message)
    return (f"{tool}-{rule}", "low", "incorrect_output", 0.4, message)


# ---------------------------------------------------------------- structure helpers


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if len(parts) > 1 and parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts) or PurePosixPath(path).stem


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        p.name.startswith("test_") or p.name.endswith("_test.py") or p.name == "conftest.py"
        or any(part in ("tests", "test") for part in p.parts[:-1])
    )


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


class _ModuleIndex:
    def __init__(self, modules: dict[str, ModuleInfo]):
        self.by_name = {m.module: m for m in modules.values()}
        self.by_last: dict[str, list[ModuleInfo]] = {}
        for m in modules.values():
            self.by_last.setdefault(m.module.rsplit(".", 1)[-1], []).append(m)

    def find(self, name: str) -> ModuleInfo | None:
        if name in self.by_name:
            return self.by_name[name]
        # "import utils" from inside a package folder: match on the last part if unique
        cands = self.by_last.get(name.rsplit(".", 1)[-1], [])
        return cands[0] if len(cands) == 1 and "." not in name else None


def _resolve_relative(current: ModuleInfo, level: int, module: str | None) -> str:
    base = current.module.split(".")
    if not current.file.endswith("__init__.py"):
        base = base[:-1]
    if level > 1:
        base = base[: max(0, len(base) - (level - 1))]
    return ".".join([*base, *( [module] if module else [])])


class PythonAnalyzer(LanguageAnalyzer):
    language = "Python"
    extensions = (".py",)
    test_framework = "pytest"

    # ------------------------------------------------------------ tools

    def tools(self) -> list[ToolInfo]:
        def version(mod: str) -> str:
            try:
                from importlib.metadata import version as v

                return v(mod)
            except Exception:
                return ""

        return [
            ToolInfo("ast", "Parsing, syntax errors, symbols, imports and call graph", True, sys.version.split()[0]),
            ToolInfo("ruff", "Fast lint: undefined names, bug patterns, security checks", bool(version("ruff")), version("ruff")),
            ToolInfo("pylint", "Deeper checks: wrong call signatures, unsafe patterns", bool(version("pylint")), version("pylint")),
            ToolInfo("pytest", "Runs existing and generated tests in the sandbox", bool(version("pytest")), version("pytest")),
        ]

    # ------------------------------------------------------------ structure

    def structure(self, root: Path, files: list[str]) -> Structure:
        root = Path(root).resolve()
        st = Structure(language=self.language)
        trees: dict[str, ast.Module] = {}
        for rel in files:
            if not self.handles(rel):
                continue
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
            info = ModuleInfo(file=rel, module=module_name(rel), is_test=is_test_file(rel), lines=text.count("\n") + 1)
            st.modules[rel] = info
            try:
                trees[rel] = ast.parse(text, filename=rel)
            except SyntaxError as exc:
                info.parse_error = f"line {exc.lineno}: {exc.msg}"
                st.syntax_issues.append(
                    Issue(
                        tool="python", rule="syntax-error", message=f"{exc.msg} (line {exc.lineno})",
                        file=rel, line=exc.lineno or 1, end_line=exc.end_lineno or exc.lineno or 1,
                        column=exc.offset or 0, severity="critical", category="crash", confidence=1.0,
                        concept="syntax", title=f"Syntax error: {exc.msg}", confirmed=True,
                    )
                )

        index = _ModuleIndex(st.modules)
        aliases: dict[str, dict[str, tuple[str, str]]] = {}

        # pass 1: symbols, imports, aliases
        for rel, tree in trees.items():
            mod = st.modules[rel]
            table: dict[str, tuple[str, str]] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        target = index.find(a.name)
                        if target:
                            mod.imports.append(target.file)
                            table[a.asname or a.name.split(".")[0]] = ("module", target.module if a.asname else a.name.split(".")[0])
                        else:
                            mod.external_imports.append(a.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    src = _resolve_relative(mod, node.level, node.module) if node.level else (node.module or "")
                    src_mod = index.find(src) if src else None
                    for a in node.names:
                        sub = index.find(f"{src}.{a.name}") if src else None
                        if sub:
                            mod.imports.append(sub.file)
                            table[a.asname or a.name] = ("module", sub.module)
                        elif src_mod:
                            mod.imports.append(src_mod.file)
                            table[a.asname or a.name] = ("symbol", f"{src_mod.module}.{a.name}")
                    if not src_mod and not node.level and node.module:
                        mod.external_imports.append(node.module.split(".")[0])
                elif isinstance(node, ast.If) and _is_main_guard(node):
                    mod.is_entry_point = True
            mod.imports = sorted(set(i for i in mod.imports if i != rel))
            mod.external_imports = sorted(set(e for e in mod.external_imports if e not in STDLIB and e != "__future__"))
            aliases[rel] = table

            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._add_symbol(st, mod, node, f"{mod.module}.{node.name}", "function")
                elif isinstance(node, ast.ClassDef):
                    cls_q = f"{mod.module}.{node.name}"
                    self._add_symbol(st, mod, node, cls_q, "class")
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            self._add_symbol(st, mod, item, f"{cls_q}.{item.name}", "method")

        for rel, mod in st.modules.items():
            for imp in mod.imports:
                st.modules[imp].imported_by.append(rel)

        # pass 2: calls
        for rel, tree in trees.items():
            mod = st.modules[rel]
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._link_calls(st, mod, aliases[rel], node, f"{mod.module}.{node.name}", None)
                elif isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            self._link_calls(st, mod, aliases[rel], item, f"{mod.module}.{node.name}.{item.name}", node.name)
                else:
                    self._link_calls(st, mod, aliases[rel], node, None, None)

        st.declared_dependencies = _declared_dependencies(root, files)
        return st

    def _add_symbol(self, st: Structure, mod: ModuleInfo, node, qualname: str, kind: str) -> None:
        params = []
        if not isinstance(node, ast.ClassDef):
            params = [a.arg for a in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]]
        doc = (ast.get_docstring(node) or "").strip().split("\n")[0][:200]
        st.symbols[qualname] = Symbol(
            qualname=qualname, name=node.name, kind=kind, file=mod.file,
            line_start=node.lineno, line_end=getattr(node, "end_lineno", node.lineno) or node.lineno,
            params=params, doc=doc,
        )
        mod.symbols.append(qualname)

    def _link_calls(self, st, mod: ModuleInfo, table, node, caller: str | None, class_name: str | None) -> None:
        caller_label = caller or f"{mod.module} (module level)"
        # Light local type inference: `cart = Cart()` lets `cart.total()` resolve to Cart.total.
        local_types: dict[str, str] = {}
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name) \
                    and isinstance(sub.value, ast.Call):
                cls = self._resolve(st, mod, table, sub.value.func, class_name, {})
                if cls and st.symbols[cls].kind == "class":
                    local_types[sub.targets[0].id] = cls
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            target = self._resolve(st, mod, table, sub.func, class_name, local_types)
            if not target or target == caller:
                continue
            callee = st.symbols[target]
            if caller and caller in st.symbols and target not in st.symbols[caller].calls:
                st.symbols[caller].calls.append(target)
            if mod.is_test and caller:
                if caller not in callee.tested_by:
                    callee.tested_by.append(caller)
            elif caller_label not in callee.called_by:
                callee.called_by.append(caller_label)

    def _resolve(self, st, mod: ModuleInfo, table, func: ast.AST, class_name: str | None,
                 local_types: dict[str, str]) -> str | None:
        # Cart().total(): a method called on a freshly built instance
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
            cls = self._resolve(st, mod, table, func.value.func, class_name, local_types)
            if cls and st.symbols[cls].kind == "class":
                cand = f"{cls}.{func.attr}"
                return cand if cand in st.symbols else None
            return None
        name = _dotted(func)
        if not name:
            return None
        head, _, rest = name.partition(".")
        if rest and head in local_types:
            cand = f"{local_types[head]}.{rest.split('.')[0]}"
            return cand if cand in st.symbols else None
        if not rest:
            local = f"{mod.module}.{name}"
            if local in st.symbols:
                return local
            kind, target = table.get(name, ("", ""))
            return target if kind == "symbol" and target in st.symbols else None
        if head in ("self", "cls") and class_name:
            cand = f"{mod.module}.{class_name}.{rest.split('.')[0]}"
            return cand if cand in st.symbols else None
        kind, target = table.get(head, ("", ""))
        if kind == "module":
            cand = f"{target}.{rest}"
            return cand if cand in st.symbols else None
        if kind == "symbol":  # Class.method via an imported class
            cand = f"{target}.{rest}"
            return cand if cand in st.symbols else None
        local = f"{mod.module}.{name}"
        return local if local in st.symbols else None

    # ------------------------------------------------------------ static tools

    def static_issues(self, root: Path, files: list[str], tools: set[str]) -> tuple[list[Issue], dict]:
        root = Path(root).resolve()
        py_files = [f for f in files if self.handles(f)]
        issues: list[Issue] = []
        stats: dict[str, dict] = {}
        if "ruff" in tools:
            found, stat = _run_ruff(root, py_files)
            issues += found
            stats["ruff"] = stat
        if "pylint" in tools:
            found, stat = _run_pylint(root, py_files)
            issues += found
            stats["pylint"] = stat
        # Lint rules that only make sense in application code are noise in tests.
        issues = [i for i in issues if not (is_test_file(i.file) and i.category == "security")]
        return issues, stats


def _is_main_guard(node: ast.If) -> bool:
    t = node.test
    return (
        isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "__name__"
        and len(t.comparators) == 1 and isinstance(t.comparators[0], ast.Constant)
        and t.comparators[0].value == "__main__"
    )


def _declared_dependencies(root: Path, files: list[str]) -> list[str]:
    deps: set[str] = set()
    for rel in files:
        name = PurePosixPath(rel).name
        try:
            if re.fullmatch(r"requirements[\w\-]*\.txt", name):
                for line in (root / rel).read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.split("#", 1)[0].strip()
                    if line and not line.startswith("-"):
                        deps.add(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0])
            elif name == "pyproject.toml":
                data = tomllib.loads((root / rel).read_text(encoding="utf-8", errors="replace"))
                for spec in data.get("project", {}).get("dependencies", []):
                    deps.add(re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0])
        except (OSError, tomllib.TOMLDecodeError):
            continue
    return sorted(d for d in deps if d)


def _chunks(items: list[str], size: int = 150):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _make_issue(tool: str, rule: str, message: str, file: str, line: int, end_line: int, col: int) -> Issue:
    concept, severity, category, confidence, title = _rule_info(tool, rule, message)
    return Issue(
        tool=tool, rule=rule, message=message, file=file, line=line, end_line=max(end_line, line),
        column=col, severity=severity, category=category, confidence=confidence, concept=concept,
        title=title, confirmed=concept == "syntax",
    )


def _run_ruff(root: Path, files: list[str]) -> tuple[list[Issue], dict]:
    if not files:
        return [], {"status": "skipped", "issues": 0}
    issues: list[Issue] = []
    for chunk in _chunks(files):
        proc = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache", "--exit-zero",
             "--output-format", "json", "--select", RUFF_SELECT, "--ignore", RUFF_IGNORE, "--", *chunk],
            cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        try:
            data = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return issues, {"status": "error", "error": (proc.stderr or proc.stdout)[-500:]}
        for item in data:
            code = item.get("code")
            if not code or not re.fullmatch(r"[A-Z]+[0-9]+", code):  # e.g. "invalid-syntax": the parser check covers it
                continue
            path = Path(item["filename"])
            rel = path.relative_to(root).as_posix() if path.is_absolute() else path.as_posix()
            loc, end = item.get("location") or {}, item.get("end_location") or {}
            issues.append(_make_issue("ruff", code, item.get("message", ""), rel, loc.get("row", 1),
                                      end.get("row", loc.get("row", 1)), loc.get("column", 0)))
    return issues, {"status": "ok", "issues": len(issues)}


_PYLINTRC = f"""[MAIN]
load-plugins=
persistent=no
jobs=1
unsafe-load-any-extension=no
[MESSAGES CONTROL]
disable=all
enable={PYLINT_ENABLE}
[REPORTS]
reports=no
score=no
"""


def _run_pylint(root: Path, files: list[str]) -> tuple[list[Issue], dict]:
    if not files:
        return [], {"status": "skipped", "issues": 0}
    issues: list[Issue] = []
    with tempfile.TemporaryDirectory(prefix="autopatch-pylint-") as tmp:
        rc = Path(tmp) / "pylintrc"
        rc.write_text(_PYLINTRC, encoding="utf-8")
        for chunk in _chunks(files):
            proc = subprocess.run(
                [sys.executable, "-m", "pylint", f"--rcfile={rc}", f"--disable={PYLINT_DISABLE}",
                 "--output-format=json", "--exit-zero", *chunk],
                cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
            )
            try:
                data = json.loads(proc.stdout or "[]")
            except json.JSONDecodeError:
                return issues, {"status": "error", "error": (proc.stderr or proc.stdout)[-500:]}
            for item in data:
                if item.get("symbol") in _IGNORED_PYLINT or item.get("message-id", "").startswith("E0001"):
                    continue
                issues.append(_make_issue(
                    "pylint", item.get("message-id", ""), item.get("message", ""),
                    PurePosixPath(item.get("path", "").replace("\\", "/")).as_posix(),
                    item.get("line") or 1, item.get("endLine") or item.get("line") or 1, item.get("column") or 0,
                ))
    return issues, {"status": "ok", "issues": len(issues)}
