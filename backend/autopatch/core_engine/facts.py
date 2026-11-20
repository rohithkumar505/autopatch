"""Static facts about the functions in a Python project. Nothing here executes project code."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from autopatch.analysis.base import Structure

NUMBER_NAMES = re.compile(
    r"(^|_)(n|num|count|total|amount|price|cost|qty|quantity|age|score|mark|marks|grade|percent|percentage|"
    r"rate|value|weight|size|limit|index|idx|level|year|days|hours|minutes|seconds|width|height)($|_)"
)
STR_NAMES = re.compile(r"(^|_)(text|name|s|string|word|line|path|filename|query|title|label|message|msg|code|key)($|_)")
LIST_NAMES = re.compile(r"(^all_|_list$|^list_|^items$|^values$|^numbers$|^nums$|^rows$|^records$|^prices$|^scores$)")
DICT_NAMES = re.compile(r"(_map$|_dict$|^mapping$|^data$|^catalog$|^inventory$|^students$|^config$|^counts$)")


@dataclass
class Func:
    file: str
    module: str
    name: str
    node: ast.FunctionDef
    lines: list[str]
    params: list[str]
    defaults: dict[str, ast.expr]
    doc: str

    @property
    def start(self) -> int:
        return self.node.lineno

    @property
    def end(self) -> int:
        return self.node.end_lineno or self.node.lineno

    @property
    def body(self) -> list[ast.stmt]:
        body = self.node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            return body[1:]
        return body

    @property
    def first_body_line(self) -> int:
        body = self.body
        return body[0].lineno if body else self.end + 1

    @property
    def body_indent(self) -> str:
        body = self.body or self.node.body
        line = self.lines[body[0].lineno - 1]
        return line[: len(line) - len(line.lstrip())]

    @property
    def public(self) -> bool:
        return not self.name.startswith("_")

    @property
    def intent_text(self) -> str:
        return (self.name.replace("_", " ") + " " + self.doc).lower()

    def segment(self, node: ast.AST) -> str:
        return ast.get_source_segment("\n".join(self.lines), node) or ""


@dataclass
class ModuleFacts:
    file: str
    module: str
    text: str
    lines: list[str]
    tree: ast.Module
    constants: dict[str, float] = field(default_factory=dict)
    funcs: dict[str, Func] = field(default_factory=dict)


def _params(node: ast.FunctionDef) -> tuple[list[str], dict[str, ast.expr]]:
    a = node.args
    positional = [*a.posonlyargs, *a.args]
    names = [p.arg for p in positional] + [p.arg for p in a.kwonlyargs]
    defaults: dict[str, ast.expr] = {}
    for p, d in zip(positional[len(positional) - len(a.defaults):], a.defaults):
        defaults[p.arg] = d
    for p, d in zip(a.kwonlyargs, a.kw_defaults):
        if d is not None:
            defaults[p.arg] = d
    return names, defaults


def collect(files: dict[str, str], structure: Structure) -> dict[str, ModuleFacts]:
    """Facts for every parseable, non-test Python module, keyed by module name."""
    result: dict[str, ModuleFacts] = {}
    for path, info in structure.modules.items():
        if info.is_test or info.parse_error or path not in files:
            continue
        text = files[path]
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError:
            continue
        lines = text.split("\n")
        mf = ModuleFacts(file=path, module=info.module, text=text, lines=lines, tree=tree)
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)) \
                    and not isinstance(node.value.value, bool):
                mf.constants[node.targets[0].id] = node.value.value
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names, defaults = _params(node)
                mf.funcs[node.name] = Func(path, info.module, node.name, node, lines, names, defaults,
                                           (ast.get_docstring(node) or "").strip())
        result[info.module] = mf
    return result


# ---------------------------------------------------------------- seeds: real calls with literal arguments


def collect_seeds(files: dict[str, str], structure: Structure, modules: dict[str, ModuleFacts]):
    """Calls to project functions with literal arguments, found in tests and source.

    Returns {(module, func): [(args, kwargs), ...]}. These show the types and realistic
    values each function is meant to receive."""
    by_name = {m: mf for m, mf in modules.items()}
    seeds: dict[tuple[str, str], list[tuple[list, dict]]] = {}
    for path, info in structure.modules.items():
        if path not in files or info.parse_error:
            continue
        try:
            tree = ast.parse(files[path])
        except SyntaxError:
            continue
        names: dict[str, tuple[str, str]] = {}
        mods: dict[str, str] = {}
        if info.module in by_name and not info.is_test:
            for fname in by_name[info.module].funcs:
                names[fname] = (info.module, fname)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                target = node.module
                if node.level:
                    base = info.module.split(".")[:-node.level] if not path.endswith("__init__.py") else \
                        info.module.split(".")[: len(info.module.split(".")) - node.level + 1]
                    target = ".".join([*base, node.module])
                for cand in (target, target.split(".")[-1]):
                    if cand in by_name:
                        for a in node.names:
                            if a.name in by_name[cand].funcs:
                                names[a.asname or a.name] = (cand, a.name)
                        break
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in by_name:
                        mods[a.asname or a.name] = a.name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = None
            if isinstance(node.func, ast.Name) and node.func.id in names:
                target = names[node.func.id]
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id in mods and node.func.attr in by_name[mods[node.func.value.id]].funcs:
                target = (mods[node.func.value.id], node.func.attr)
            if not target:
                continue
            try:
                args = [ast.literal_eval(a) for a in node.args]
                kwargs = {k.arg: ast.literal_eval(k.value) for k in node.keywords if k.arg}
            except (ValueError, TypeError, SyntaxError, RecursionError):
                continue
            if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
                continue
            bucket = seeds.setdefault(target, [])
            if (args, kwargs) not in bucket:
                bucket.append((args, kwargs))
    return seeds


# ---------------------------------------------------------------- types and example values


def _type_of(value) -> str | None:
    if isinstance(value, bool):
        return "bool"
    for t, name in ((int, "int"), (float, "float"), (str, "str"), (list, "list"), (tuple, "list"), (dict, "dict"),
                    (set, "list")):
        if isinstance(value, t):
            return name
    return None


def _annotation_type(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    text = ast.unparse(node).lower()
    for key, name in (("int", "int"), ("float", "float"), ("str", "str"), ("list", "list"), ("sequence", "list"),
                      ("iterable", "list"), ("tuple", "list"), ("dict", "dict"), ("mapping", "dict"), ("bool", "bool")):
        if text.startswith(key):
            return name
    return None


def param_types(func: Func, seeds: list[tuple[list, dict]], module: ModuleFacts) -> dict[str, str | None]:
    types: dict[str, str | None] = {p: None for p in func.params}
    for args, kwargs in seeds:
        for p, v in zip(func.params, args):
            types[p] = types[p] or _type_of(v)
        for k, v in kwargs.items():
            if k in types:
                types[k] = types[k] or _type_of(v)
    all_args = {a.arg: a for a in [*func.node.args.posonlyargs, *func.node.args.args, *func.node.args.kwonlyargs]}
    for p in func.params:
        if types[p]:
            continue
        d = func.defaults.get(p)
        if d is not None:
            try:
                types[p] = _type_of(ast.literal_eval(d))
            except (ValueError, SyntaxError, TypeError):
                if isinstance(d, ast.Name) and d.id in module.constants:
                    types[p] = _type_of(module.constants[d.id])
        types[p] = types[p] or _annotation_type(all_args[p].annotation if p in all_args else None)
        if not types[p] and p not in ("self", "cls"):
            if LIST_NAMES.search(p) or (p.endswith("s") and not p.endswith("ss") and len(p) > 3 and not STR_NAMES.search(p)
                                        and not NUMBER_NAMES.search(p)):
                types[p] = "list"
            elif DICT_NAMES.search(p):
                types[p] = "dict"
            elif NUMBER_NAMES.search(p):
                types[p] = "int"
            elif STR_NAMES.search(p):
                types[p] = "str"
    return types


TYPICAL = {"int": 10, "float": 2.5, "str": "abc", "list": [1, 2, 3], "dict": {"a": 1, "b": 2}, "bool": True}


def default_value(func: Func, p: str, module: ModuleFacts):
    d = func.defaults.get(p)
    if d is None:
        raise KeyError(p)
    try:
        return ast.literal_eval(d)
    except (ValueError, SyntaxError, TypeError):
        if isinstance(d, ast.Name) and d.id in module.constants:
            return module.constants[d.id]
        raise KeyError(p) from None


def typical_call(func: Func, seeds: list[tuple[list, dict]], types: dict[str, str | None],
                 module: ModuleFacts) -> dict[str, object] | None:
    """Realistic named arguments for one call, or None when a required argument cannot be built."""
    values: dict[str, object] = {}
    if seeds:
        args, kwargs = seeds[0]
        values.update(dict(zip(func.params, args)))
        values.update(kwargs)
    for p in func.params:
        if p in values:
            continue
        if p in func.defaults:
            continue  # let the function use its default
        if types.get(p) in TYPICAL:
            values[p] = TYPICAL[types[p]]
        else:
            return None
    return values


def call_source(func: Func, values: dict[str, object]) -> str:
    """Python source for calling func with these values, positional where possible."""
    parts = []
    positional = True
    for p in func.params:
        if p in values:
            parts.append(repr(values[p]) if positional else f"{p}={values[p]!r}")
        else:
            positional = False
    for k, v in values.items():
        if k not in func.params:
            parts.append(f"{k}={v!r}")
    return f"{func.name}({', '.join(parts)})"


# ---------------------------------------------------------------- helpers for detectors


def names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def guarded(func: Func, var: str, before_line: int, exc_names: tuple[str, ...] = ()) -> bool:
    """True if the function checks `var` (and raises/returns) before `before_line`, or catches the error."""
    for node in ast.walk(func.node):
        if isinstance(node, (ast.If, ast.While)) and node.lineno < before_line and var in names_in(node.test):
            exits = [n for stmt in node.body for n in ast.walk(stmt) if isinstance(n, (ast.Raise, ast.Return, ast.Continue))]
            if exits:
                return True
        if isinstance(node, ast.IfExp) and var in names_in(node.test) and node.lineno <= before_line:
            return True
        if isinstance(node, ast.Assert) and node.lineno < before_line and var in names_in(node.test):
            return True
        if isinstance(node, ast.Try) and node.lineno <= before_line <= (node.end_lineno or node.lineno):
            for h in node.handlers:
                caught = names_in(h.type) if h.type else {"Exception"}
                if caught & {"Exception", "BaseException", "ArithmeticError", *exc_names}:
                    return True
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And) and node.lineno <= before_line \
                and var in names_in(node.values[0]):
            return True
    return False


def tainted_names(func: Func) -> dict[str, list[tuple[int, str]]]:
    """Names that can carry caller-controlled data, each with the path it took.

    Sources are the function's parameters and input()/sys.argv/os.environ. Data spreads through
    assignments, loops, comprehensions, string methods and formatting."""
    paths: dict[str, list[tuple[int, str]]] = {
        p: [(func.start, f"parameter `{p}`")] for p in func.params if p not in ("self", "cls")
    }

    def source_desc(expr: ast.AST) -> list[tuple[int, str]] | None:
        for n in ast.walk(expr):
            if isinstance(n, ast.Name) and n.id in paths:
                return paths[n.id]
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "input":
                return [(n.lineno, "user input from input()")]
            text = ast.unparse(n) if isinstance(n, (ast.Attribute, ast.Subscript)) else ""
            if text.startswith(("sys.argv", "os.environ")):
                return [(n.lineno, f"external data from {text.split('[')[0]}")]
        return None

    for _ in range(3):  # repeat so values flowing around loops are picked up
        for node in ast.walk(func.node):
            targets: list[ast.AST] = []
            value = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
                targets, value = [node.target], node.value
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                targets, value = [node.target], node.iter
            elif isinstance(node, ast.comprehension):
                targets, value = [node.target], node.iter
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                targets, value = [node.optional_vars], node.context_expr
            if value is None:
                continue
            origin = source_desc(value)
            if origin is None:
                continue
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name) and n.id not in paths:
                        paths[n.id] = [*origin, (node.lineno if hasattr(node, "lineno") else origin[-1][0],
                                                 f"`{n.id}` = {ast.unparse(value)[:60]}")]
    return paths
