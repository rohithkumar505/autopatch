"""More AutoPatch Core detectors, including ones that look across functions.

Per function:
  not-found-in-loop     the "not found" branch sits inside the loop, so it fires for every non-matching item
  off-by-one-range      range(len(x) + 1) / while i <= len(x) used to index x
  missing-return        some paths return a value, others fall off the end and return None
  unclosed-resource     open()/connect() kept in a variable and never closed
Across functions:
  unchecked-none-result a function returns None on failure and a caller uses the result without checking
  cross-function taint  input() or a caller's data flows into another function's dangerous call
"""

from __future__ import annotations

import ast
import re

from autopatch.core_engine.detectors import Candidate, _cand
from autopatch.core_engine.facts import Func, ModuleFacts, guarded, names_in, tainted_names

NOT_FOUND = re.compile(r"(not found|no such|does not exist|doesn't exist|unknown|invalid|not exist|missing)", re.I)


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def detect_not_found_in_loop(func: Func, mod: ModuleFacts) -> list[Candidate]:
    for loop in ast.walk(func.node):
        if not isinstance(loop, ast.For) or loop.orelse or len(loop.body) != 1 or not isinstance(loop.body[0], ast.If):
            continue
        branch = loop.body[0]
        if not branch.orelse or (len(branch.orelse) == 1 and isinstance(branch.orelse[0], ast.If)):
            continue
        msgs = [s for s in _strings(ast.Module(body=branch.orelse, type_ignores=[])) if NOT_FOUND.search(s)]
        if not msgs:
            continue
        test = branch.test
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
                and names_in(test.left) & names_in(loop.target)):
            continue
        param = next((n for n in names_in(test.comparators[0]) if n in func.params), None)
        if isinstance(loop.iter, ast.Name):
            return [_cand("not-found-in-loop", func, branch.orelse[0], "high", "incorrect_output", 0.85,
                          f"{func.name} reports '{msgs[0].strip()}' for every item that does not match",
                          message=msgs[0], loop_line=loop.lineno, if_line=branch.lineno, param=param,
                          collection=loop.iter.id, loop_var=ast.unparse(loop.target), left=ast.unparse(test.left),
                          collection_is_global=loop.iter.id not in func.params)]
    return []


def detect_off_by_one_range(func: Func, mod: ModuleFacts) -> list[Candidate]:
    for loop in ast.walk(func.node):
        target_seq = None
        if isinstance(loop, ast.For) and isinstance(loop.iter, ast.Call) and isinstance(loop.iter.func, ast.Name) \
                and loop.iter.func.id == "range" and isinstance(loop.target, ast.Name):
            last = loop.iter.args[-1] if len(loop.iter.args) <= 2 else None
            if isinstance(last, ast.BinOp) and isinstance(last.op, ast.Add) and isinstance(last.right, ast.Constant) \
                    and last.right.value == 1 and isinstance(last.left, ast.Call) and isinstance(last.left.func, ast.Name) \
                    and last.left.func.id == "len" and last.left.args and isinstance(last.left.args[0], ast.Name):
                target_seq, index = last.left.args[0].id, loop.target.id
        elif isinstance(loop, ast.While) and isinstance(loop.test, ast.Compare) and len(loop.test.ops) == 1 \
                and isinstance(loop.test.ops[0], ast.LtE) and isinstance(loop.test.left, ast.Name):
            right = loop.test.comparators[0]
            if isinstance(right, ast.Call) and isinstance(right.func, ast.Name) and right.func.id == "len" \
                    and right.args and isinstance(right.args[0], ast.Name):
                target_seq, index = right.args[0].id, loop.test.left.id
        if not target_seq:
            continue
        indexed = any(isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == target_seq
                      and isinstance(n.slice, ast.Name) and n.slice.id == index for n in ast.walk(loop))
        keys = sorted({n.slice.value for n in ast.walk(loop) if isinstance(n, ast.Subscript)
                       and isinstance(n.value, ast.Subscript) and isinstance(n.value.value, ast.Name)
                       and n.value.value.id == target_seq and isinstance(n.slice, ast.Constant)
                       and isinstance(n.slice.value, str)})
        if indexed:
            return [_cand("off-by-one-range", func, loop, "high", "crash", 0.85,
                          f"{func.name} reads one position past the end of `{target_seq}`",
                          sequence=target_seq, index=index, param=target_seq if target_seq in func.params else None,
                          item_keys=keys,
                          loop_kind="for" if isinstance(loop, ast.For) else "while")]
    return []


def _falls_through(body: list[ast.stmt]) -> bool:
    if not body:
        return True
    last = body[-1]
    if isinstance(last, (ast.Return, ast.Raise)):
        return False
    if isinstance(last, ast.If):
        return _falls_through(last.body) or _falls_through(last.orelse)
    if isinstance(last, ast.While) and isinstance(last.test, ast.Constant) and last.test.value is True:
        return any(isinstance(n, ast.Break) for n in ast.walk(last))
    if isinstance(last, ast.Try):
        return _falls_through(last.body) or any(_falls_through(h.body) for h in last.handlers)
    if isinstance(last, (ast.With, ast.For)):
        return True if isinstance(last, ast.For) else _falls_through(last.body)
    if isinstance(last, ast.Match):
        return True
    return True


def detect_missing_return(func: Func, mod: ModuleFacts) -> list[Candidate]:
    returns = [n for n in ast.walk(func.node) if isinstance(n, ast.Return) and n.value is not None
               and not (isinstance(n.value, ast.Constant) and n.value.value is None)]
    nested = {id(n) for f in ast.walk(func.node) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
              and f is not func.node for n in ast.walk(f)}
    returns = [r for r in returns if id(r) not in nested]
    if not returns or any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(func.node)):
        return []
    if _falls_through(func.body):
        last = func.body[-1]
        return [_cand("missing-return", func, last, "medium", "incorrect_output", 0.65,
                      f"{func.name} returns nothing (None) on some paths",
                      value_lines=[r.lineno for r in returns][:5])]
    return []


def detect_unclosed_resource(func: Func, mod: ModuleFacts) -> list[Candidate]:
    for node in ast.walk(func.node):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Call):
            f = node.value.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name not in ("open", "connect", "urlopen", "socket"):
                continue
            var = node.targets[0].id
            closed = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "close"
                         and isinstance(n.func.value, ast.Name) and n.func.value.id == var for n in ast.walk(func.node))
            returned = any(isinstance(n, ast.Return) and n.value is not None and var in names_in(n.value)
                           for n in ast.walk(func.node))
            in_with = any(isinstance(n, ast.With) and any(var in names_in(i.context_expr) for i in n.items)
                          for n in ast.walk(func.node))
            if not (closed or returned or in_with):
                return [_cand("unclosed-resource", func, node, "medium", "state_corruption", 0.7,
                              f"{func.name} opens `{var}` with {name}() and never closes it",
                              variable=var, opener=name)]
            if closed and not in_with:
                close = next(n for n in ast.walk(func.node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                             and n.func.attr == "close" and isinstance(n.func.value, ast.Name) and n.func.value.id == var)
                between = [n for n in ast.walk(func.node) if isinstance(n, ast.Call) and node.lineno < n.lineno < close.lineno]
                protected = any(isinstance(t, ast.Try) and t.finalbody for t in ast.walk(func.node))
                if between and not protected:
                    return [_cand("unclosed-resource", func, node, "low", "state_corruption", 0.5,
                                  f"{func.name} leaves `{var}` open if an error happens before close()",
                                  variable=var, opener=name, partial=True)]
    return []


PER_FUNCTION = [detect_not_found_in_loop, detect_off_by_one_range, detect_missing_return, detect_unclosed_resource]


# ---------------------------------------------------------------- across functions


def _none_returning(modules: dict[str, ModuleFacts]) -> dict[tuple[str, str], int]:
    """Functions that return a value on success but None on some path (failure)."""
    out = {}
    for mod in modules.values():
        for func in mod.funcs.values():
            values = none = 0
            for n in ast.walk(func.node):
                if isinstance(n, ast.Return):
                    if n.value is None or (isinstance(n.value, ast.Constant) and n.value.value is None):
                        none = none or n.lineno
                    else:
                        values += 1
            if values and none:
                out[(mod.module, func.name)] = none
    return out


def detect_unchecked_none(modules: dict[str, ModuleFacts]) -> list[Candidate]:
    risky = _none_returning(modules)
    found = []
    for mod in modules.values():
        for func in mod.funcs.values():
            for node in ast.walk(func.node):
                if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)):
                    continue
                callee = (mod.module, node.value.func.id)
                if callee not in risky:
                    continue
                var = node.targets[0].id
                uses = [n for n in ast.walk(func.node) if n is not node.targets[0] and isinstance(n, ast.Name)
                        and n.id == var and isinstance(n.ctx, ast.Load) and n.lineno > node.lineno]
                if not uses or guarded(func, var, max(u.lineno for u in uses) + 1):
                    continue
                first = min(uses, key=lambda u: u.lineno)
                found.append(_cand("unchecked-none-result", func, node, "high", "crash", 0.75,
                                   f"{func.name} uses the result of {callee[1]}() without checking for None",
                                   variable=var, callee=callee[1], none_line=risky[callee], use_line=first.lineno))
                break
    return found


def taint_summaries(modules: dict[str, ModuleFacts], candidates: list[Candidate]) -> None:
    """Link injection findings to the callers that feed them, e.g. input() in main() -> eval() in parse()."""
    sinks = {(c.module, c.function, c.facts.get("param")): c for c in candidates
             if c.kind in ("code-injection", "command-injection", "sql-injection", "unsafe-deserialization")}
    if not sinks:
        return
    for mod in modules.values():
        for func in mod.funcs.values():
            taint = tainted_names(func)
            for node in ast.walk(func.node):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in mod.funcs):
                    continue
                callee = mod.funcs[node.func.id]
                for i, arg in enumerate(node.args):
                    if i >= len(callee.params):
                        break
                    c = sinks.get((mod.module, callee.name, callee.params[i]))
                    if not c:
                        continue
                    origin = None
                    for n in names_in(arg):
                        if n in taint:
                            origin = taint[n][0][1]
                    if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "input":
                        origin = "user input from input()"
                    if origin:
                        c.facts.setdefault("reached_from", []).append(
                            f"{func.name}() line {node.lineno} passes {origin} into {callee.name}({callee.params[i]})")
                        if "input()" in origin:
                            c.confidence = max(c.confidence, 0.95)
