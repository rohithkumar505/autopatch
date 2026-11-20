"""AutoPatch Core bug detectors.

Each detector looks for one well-understood bug class and records the facts the rest of the
engine needs: what to probe in the sandbox to confirm it, how to explain it, how to test it and
how to repair it. Detectors are general rules about code, not knowledge of any one project.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from autopatch.core_engine.facts import Func, ModuleFacts, guarded, names_in, tainted_names

DESCENDING_WORDS = re.compile(r"\b(top|highest|best|largest|biggest|greatest|most|maximum|richest|leaders?)\b")
ASCENDING_WORDS = re.compile(r"\b(lowest|smallest|least|bottom|worst|cheapest|minimum|fewest|poorest)\b")
AT_LEAST_NAMES = re.compile(r"(pass|min|minimum|required|threshold|cutoff|eligib|qualif|at_least)", re.I)
MAX_CONSTANT = re.compile(r"(^|_)(MAX|MAXIMUM|UPPER|LIMIT|TOTAL|FULL)(_|$)")
BAND_PARAM_HINT = re.compile(r"(mark|score|grade|percent|point|rating|age|weight|level|temp|speed|amount|price)", re.I)

CODE_SINKS = {"eval": "eval()", "exec": "exec()"}
SHELL_FUNCS = {"system", "popen"}
SUBPROCESS_FUNCS = {"call", "run", "Popen", "check_output", "check_call", "getoutput", "getstatusoutput"}


@dataclass
class Candidate:
    kind: str
    module: str
    file: str
    function: str
    line_start: int
    line_end: int
    severity: str
    category: str
    confidence: float
    title: str
    facts: dict = field(default_factory=dict)
    probes: list[dict] = field(default_factory=list)  # filled in by the engine
    confirmed: bool = False
    evidence: str = ""


def _cand(kind, func: Func, node: ast.AST, severity, category, confidence, title, **facts) -> Candidate:
    return Candidate(kind=kind, module=func.module, file=func.file, function=func.name,
                     line_start=getattr(node, "lineno", func.start),
                     line_end=getattr(node, "end_lineno", None) or getattr(node, "lineno", func.start),
                     severity=severity, category=category, confidence=confidence, title=title, facts=facts)


# ---------------------------------------------------------------- injection: caller data reaching dangerous calls


def detect_injection(func: Func, mod: ModuleFacts) -> list[Candidate]:
    found = []
    taint = tainted_names(func)

    def tainted_arg(call: ast.Call) -> tuple[str, list] | None:
        for arg in [*call.args, *[k.value for k in call.keywords]]:
            for name in names_in(arg):
                if name in taint:
                    return name, taint[name]
        return None

    for node in ast.walk(func.node):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        hit = tainted_arg(node)
        if not hit:
            continue
        var, path = hit
        root = path[0][1].split("`")[1] if "`" in path[0][1] else None
        trace = [{"line": ln, "step": step} for ln, step in path] + [{"line": node.lineno, "step": ast.unparse(node)[:80]}]
        if isinstance(f, ast.Name) and f.id in CODE_SINKS:
            found.append(_cand("code-injection", func, node, "critical", "security", 0.85,
                               f"{f.id}() runs code taken from `{root or var}` in {func.name}",
                               sink=f.id, variable=var, param=root, path=trace, call_col=f.col_offset))
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            owner, attr = f.value.id, f.attr
            shell_kw = any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
                           for k in node.keywords)
            if owner == "os" and attr in SHELL_FUNCS or owner == "subprocess" and attr in SUBPROCESS_FUNCS and shell_kw:
                found.append(_cand("command-injection", func, node, "critical", "security", 0.8,
                                   f"Shell command built from `{root or var}` in {func.name}",
                                   sink=f"{owner}.{attr}", variable=var, param=root, path=trace))
            elif owner in ("pickle", "marshal", "dill") and attr in ("loads", "load"):
                found.append(_cand("unsafe-deserialization", func, node, "high", "security", 0.75,
                                   f"{owner}.{attr}() on data from `{root or var}` can run code",
                                   sink=f"{owner}.{attr}", variable=var, param=root, path=trace))
            elif owner == "yaml" and attr == "load" and not any(
                    k.arg == "Loader" and "Safe" in ast.unparse(k.value) for k in node.keywords):
                found.append(_cand("unsafe-deserialization", func, node, "high", "security", 0.75,
                                   f"yaml.load() on data from `{root or var}` can build arbitrary objects",
                                   sink="yaml.load", variable=var, param=root, path=trace, yaml=True))
        if isinstance(f, ast.Attribute) and f.attr in ("execute", "executemany", "executescript") and node.args:
            query = node.args[0]
            built = isinstance(query, ast.JoinedStr) or (isinstance(query, ast.BinOp) and isinstance(query.op, (ast.Add, ast.Mod))) \
                or (isinstance(query, ast.Call) and isinstance(query.func, ast.Attribute) and query.func.attr == "format")
            if not built and isinstance(query, ast.Name):
                # the query text was assembled earlier, e.g. sql = f"... {name}"
                for n in ast.walk(func.node):
                    if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == query.id for t in n.targets) \
                            and isinstance(n.value, (ast.JoinedStr, ast.BinOp)):
                        built = True
            if built and names_in(query) & set(taint):
                found.append(_cand("sql-injection", func, node, "high", "security", 0.8,
                                   f"SQL query built from `{root or var}` in {func.name}",
                                   sink=f.attr, variable=var, param=root, path=trace))
    return found


# ---------------------------------------------------------------- crashes on empty or zero input


def detect_zero_division(func: Func, mod: ModuleFacts) -> list[Candidate]:
    found = []
    for node in ast.walk(func.node):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod))):
            continue
        right = node.right
        if isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            continue  # "%s" % value is formatting, not division
        if isinstance(right, ast.Call) and isinstance(right.func, ast.Name) and right.func.id == "len" \
                and right.args and isinstance(right.args[0], ast.Name) and right.args[0].id in func.params:
            p = right.args[0].id
            if not guarded(func, p, node.lineno, ("ZeroDivisionError",)):
                found.append(_cand("zero-division", func, node, "high", "crash", 0.75,
                                   f"{func.name} crashes when `{p}` is empty", param=p, trigger="empty",
                                   expression=ast.unparse(node)))
        elif isinstance(right, ast.Name) and right.id in func.params:
            p = right.id
            if not guarded(func, p, node.lineno, ("ZeroDivisionError",)):
                found.append(_cand("zero-division", func, node, "high", "crash", 0.7,
                                   f"{func.name} crashes when `{p}` is 0", param=p, trigger="zero",
                                   expression=ast.unparse(node)))
    return found[:1]


def detect_empty_index(func: Func, mod: ModuleFacts) -> list[Candidate]:
    for node in ast.walk(func.node):
        p = None
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in func.params \
                and isinstance(node.slice, (ast.Constant, ast.UnaryOp)):
            try:
                idx = ast.literal_eval(node.slice)
            except ValueError:
                continue
            if idx in (0, -1):
                p = node.value.id
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("max", "min") \
                and len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id in func.params \
                and not any(k.arg == "default" for k in node.keywords):
            p = node.args[0].id
        if p and not guarded(func, p, node.lineno, ("IndexError", "ValueError")):
            return [_cand("empty-input", func, node, "medium", "crash", 0.65,
                          f"{func.name} crashes when `{p}` is empty", param=p, trigger="empty",
                          expression=ast.unparse(node))]
    return []


# ---------------------------------------------------------------- shared mutable default


def detect_mutable_default(func: Func, mod: ModuleFacts) -> list[Candidate]:
    found = []
    for p, d in func.defaults.items():
        mutable = isinstance(d, (ast.List, ast.Dict, ast.Set)) or (
            isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in ("list", "dict", "set") and not d.args)
        if not mutable:
            continue
        mutated = False
        for n in ast.walk(func.node):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id == p and n.func.attr in ("append", "extend", "insert", "add", "update",
                                                                  "setdefault", "pop", "remove", "clear", "__setitem__"):
                mutated = True
            if isinstance(n, (ast.Assign, ast.AugAssign)):
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == p:
                        mutated = True
        returned = any(isinstance(n, ast.Return) and n.value is not None and p in names_in(n.value)
                       for n in ast.walk(func.node))
        if mutated or returned:
            found.append(_cand("mutable-default", func, d, "medium", "state_corruption", 0.7,
                               f"`{p}` default in {func.name} is shared between calls", param=p,
                               default_source=ast.unparse(d), mutated=mutated))
    return found[:1]


# ---------------------------------------------------------------- missing range validation


def _number(node: ast.AST, mod: ModuleFacts):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value, None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return -node.operand.value, None
    if isinstance(node, ast.Name) and node.id in mod.constants:
        return mod.constants[node.id], node.id
    return None, None


def detect_missing_range(func: Func, mod: ModuleFacts) -> list[Candidate]:
    """A value sorted into bands (if x >= 90 ... elif x >= 75 ...) with no check that x is in range."""
    for stmt in func.body:
        if not isinstance(stmt, ast.If):
            continue
        thresholds: list[float] = []
        param = None
        node: ast.stmt | None = stmt
        while isinstance(node, ast.If):
            t = node.test
            if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.left, ast.Name) \
                    and t.left.id in func.params and isinstance(t.ops[0], (ast.Gt, ast.GtE, ast.Lt, ast.LtE)):
                value, _ = _number(t.comparators[0], mod)
                if value is None or (param and t.left.id != param):
                    break
                param = t.left.id
                thresholds.append(value)
            else:
                break
            node = node.orelse[0] if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) else None
        if not param or len(thresholds) < 3:
            continue
        if guarded(func, param, stmt.lineno):
            return []
        lo = 0 if min(thresholds) >= 0 else None
        hi, hi_name = None, None
        candidates = {k: v for k, v in mod.constants.items() if MAX_CONSTANT.search(k) and v >= max(thresholds)}
        if candidates:
            hi_name = min(candidates, key=lambda k: candidates[k])
            hi = candidates[hi_name]
        elif max(thresholds) <= 100 and BAND_PARAM_HINT.search(param + " " + func.name):
            hi = 100
        if lo is None and hi is None:
            return []
        rng = f"{lo} to {hi_name or hi}" if hi is not None else f"{lo} or more"
        return [_cand("missing-range-check", func, stmt, "high", "incorrect_output", 0.7,
                      f"{func.name} accepts `{param}` outside {rng}", param=param, lo=lo, hi=hi, hi_name=hi_name,
                      thresholds=sorted(set(thresholds), reverse=True))]
    return []


# ---------------------------------------------------------------- sort direction vs what the function promises


def detect_sort_direction(func: Func, mod: ModuleFacts) -> list[Candidate]:
    text = func.intent_text
    want_desc = bool(DESCENDING_WORDS.search(text))
    want_asc = bool(ASCENDING_WORDS.search(text))
    if want_desc == want_asc:
        return []
    for node in ast.walk(func.node):
        if not isinstance(node, ast.Call):
            continue
        is_sorted = isinstance(node.func, ast.Name) and node.func.id == "sorted"
        is_sort = isinstance(node.func, ast.Attribute) and node.func.attr == "sort"
        if not (is_sorted or is_sort):
            continue
        rev = next((k for k in node.keywords if k.arg == "reverse"), None)
        reverse = isinstance(rev.value, ast.Constant) and rev.value.value is True if rev else False
        key = next((k for k in node.keywords if k.arg == "key"), None)
        negated_key = key is not None and "-" in ast.unparse(key.value)
        descending = reverse != negated_key
        if want_desc and not descending or want_asc and descending:
            src = node.args[0] if node.args else None
            shape = "items" if src is not None and isinstance(src, ast.Call) and isinstance(src.func, ast.Attribute) \
                and src.func.attr == "items" and isinstance(src.func.value, ast.Name) else \
                "list" if src is not None and isinstance(src, ast.Name) and src.id in func.params else "other"
            param = (src.func.value.id if shape == "items" else src.id if shape == "list" else None)
            word = (DESCENDING_WORDS if want_desc else ASCENDING_WORDS).search(text).group(0)
            return [_cand("sort-direction", func, node, "high", "incorrect_output", 0.75,
                          f"{func.name} sorts in the wrong direction for '{word}' results",
                          want="descending" if want_desc else "ascending", word=word, shape=shape, param=param,
                          has_reverse=rev is not None, call=ast.unparse(node))]
    return []


# ---------------------------------------------------------------- at-least thresholds compared with > or <


def detect_inclusive_threshold(func: Func, mod: ModuleFacts) -> list[Candidate]:
    for node in ast.walk(func.node):
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1):
            continue
        op, left, right = node.ops[0], node.left, node.comparators[0]
        if isinstance(op, ast.Gt) and isinstance(right, ast.Name) and AT_LEAST_NAMES.search(right.id):
            threshold, value_side = right.id, left
        elif isinstance(op, ast.Lt) and isinstance(left, ast.Name) and AT_LEAST_NAMES.search(left.id):
            threshold, value_side = left.id, right
        else:
            continue
        if "max" in threshold.lower():
            continue
        tvalue = None
        if threshold in func.params and threshold in func.defaults:
            d = func.defaults[threshold]
            tvalue = _number(d, mod)[0]
        elif threshold in mod.constants:
            tvalue = mod.constants[threshold]
        filtered = None
        for parent in ast.walk(func.node):
            if isinstance(parent, (ast.ListComp, ast.GeneratorExp)) and any(node in ast.walk(g) for g in parent.generators):
                gen = parent.generators[0]
                if isinstance(gen.iter, ast.Name) and gen.iter.id in func.params:
                    filtered = gen.iter.id
        counts = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("len", "sum")
                     for n in ast.walk(func.node))
        return [_cand("inclusive-threshold", func, node, "medium", "edge_case", 0.6,
                      f"{func.name} leaves out values exactly equal to `{threshold}`",
                      threshold=threshold, threshold_value=tvalue, value=ast.unparse(value_side),
                      op=">" if isinstance(op, ast.Gt) else "<", list_param=filtered, counts=counts,
                      compare=ast.unparse(node))]
    return []


# ---------------------------------------------------------------- None from lookups used directly


def detect_none_deref(func: Func, mod: ModuleFacts) -> list[Candidate]:
    for node in ast.walk(func.node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            inner = node.value.func
            if inner.attr == "get" or (isinstance(inner.value, ast.Name) and inner.value.id == "re"
                                       and inner.attr in ("match", "search", "fullmatch")):
                return [_cand("none-deref", func, node, "medium", "crash", 0.55,
                              f"{func.name} uses the result of {ast.unparse(node.value)[:40]} without checking for None",
                              expression=ast.unparse(node))]
    return []


# ---------------------------------------------------------------- loop accumulator overwritten instead of updated


def detect_overwritten_accumulator(func: Func, mod: ModuleFacts) -> list[Candidate]:
    """total = 0; for x in items: total = x.price  -> only the last item counts."""
    starts: dict[str, int] = {}
    for stmt in func.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)                 and isinstance(stmt.value, (ast.Constant, ast.List, ast.Dict))                 and (not isinstance(stmt.value, ast.Constant) or stmt.value.value in (0, 0.0, "")):
            starts[stmt.targets[0].id] = stmt.lineno
    for loop in ast.walk(func.node):
        if not isinstance(loop, (ast.For, ast.AsyncFor)):
            continue
        loop_vars = names_in(loop.target)
        for node in ast.walk(loop):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                continue
            var = node.targets[0].id
            if var not in starts or starts[var] > loop.lineno or var in names_in(node.value):
                continue
            if not (names_in(node.value) & loop_vars):
                continue
            used_after = any(isinstance(n, ast.Name) and n.id == var and isinstance(n.ctx, ast.Load)
                             and n.lineno > (loop.end_lineno or loop.lineno) for n in ast.walk(func.node))
            if used_after:
                return [_cand("overwritten-accumulator", func, node, "high", "incorrect_output", 0.8,
                              f"{func.name} overwrites `{var}` on every loop pass instead of adding to it",
                              variable=var, expression=ast.unparse(node), loop_line=loop.lineno)]
    return []


DETECTORS = [
    detect_injection, detect_zero_division, detect_empty_index, detect_mutable_default,
    detect_missing_range, detect_sort_direction, detect_inclusive_threshold, detect_none_deref,
    detect_overwritten_accumulator,
]


def detect_unguarded_main(mod: ModuleFacts) -> list[Candidate]:
    """`main()` at the bottom of a module runs on every import, so the module cannot be imported or tested."""
    for stmt in mod.tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name) \
                and stmt.value.func.id in mod.funcs:
            func = mod.funcs[stmt.value.func.id]
            c = _cand("unguarded-main", func, stmt, "medium", "state_corruption", 0.95,
                      f"Importing {mod.module} runs {func.name}() immediately",
                      call=ast.unparse(stmt), module_name=mod.module)
            c.confirmed = True
            c.evidence = (f"Line {stmt.lineno} calls {func.name}() at module level with no `if __name__ == \"__main__\":` "
                          "guard, so any import (including by tests) starts the program.")
            return [c]
    return []


def function_detectors():
    from autopatch.core_engine.detectors2 import PER_FUNCTION

    return [*DETECTORS, *PER_FUNCTION]


def detect_all(modules: dict[str, ModuleFacts]) -> list[Candidate]:
    from autopatch.core_engine.detectors2 import detect_unchecked_none, taint_summaries

    out: list[Candidate] = []
    for mod in modules.values():
        out.extend(detect_unguarded_main(mod))
        for func in mod.funcs.values():
            for detector in function_detectors():
                try:
                    out.extend(detector(func, mod))
                except (ValueError, TypeError, AttributeError, IndexError, KeyError, StopIteration):
                    continue  # one odd function must not stop the scan
    try:
        out.extend(detect_unchecked_none(modules))
        taint_summaries(modules, out)
    except (ValueError, TypeError, AttributeError, IndexError, KeyError):
        pass
    return out
