"""Test generation and code repair for AutoPatch Core.

Tests encode the correct behavior for each bug class, independent of any fix:
  - invalid input is rejected with ValueError instead of crashing or giving a wrong answer
  - input is never executed as code
  - calls do not share hidden state
  - "top"/"lowest" functions return the right end of the ordering
  - a value exactly at an "at least" threshold counts
Normal-input tests record what the original code returns for realistic inputs the bug does not
touch, so a repair that changes ordinary behavior is caught as a regression.

Repairs are minimal text edits at exact AST positions, so the diff shows only the lines that
change. When a fix template is not enough, a search tries small mutations of the faulty function
and keeps the first one that passes every test in the sandbox.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from autopatch.core_engine.detectors import Candidate
from autopatch.core_engine.facts import Func, ModuleFacts, call_source
from autopatch.core_engine.probe import INJECTION_PAYLOAD, LEAK_MARKERS

TRIGGERS = {"empty-list": [], "empty-dict": {}, "empty-str": "", "zero": 0}


class NoRepair(Exception):
    """AutoPatch Core has no safe automatic repair for this finding."""


# ---------------------------------------------------------------- tests


@dataclass
class TestCase:
    __test__ = False
    name: str
    kind: str
    description: str
    code: str


def _expect(result_repr: str) -> str:
    try:
        value = ast.literal_eval(result_repr)
    except (ValueError, SyntaxError):
        return result_repr
    if isinstance(value, float):
        return f"pytest.approx({result_repr})"
    return result_repr


def _short(values: dict) -> str:
    text = ", ".join(f"{k}={v!r}" for k, v in values.items())
    return text if len(text) <= 60 else text[:57] + "..."


def recorded_test(func: Func, name: str, kind: str, values: dict, result_repr: str, why: str) -> TestCase:
    call = call_source(func, values)
    return TestCase(name, kind, f"{why}: {func.name}({_short(values)}) returns {result_repr[:40]}",
                    f"def {name}():\n    assert {call} == {_expect(result_repr)}\n")


def raises_test(func: Func, name: str, kind: str, values: dict, why: str) -> TestCase:
    call = call_source(func, values)
    return TestCase(name, kind, f"{why}: {func.name}({_short(values)}) raises ValueError",
                    f"def {name}():\n    with pytest.raises(ValueError):\n        {call}\n")


def bug_tests(c: Candidate, func: Func, mod: ModuleFacts, base: dict | None) -> list[TestCase]:
    """Tests that fail on the buggy code and pass once it is fixed."""
    f = c.facts
    tests: list[TestCase] = []
    base = dict(base or {})
    if c.kind == "code-injection" and f.get("param"):
        values = {**base, f["param"]: INJECTION_PAYLOAD}
        tests.append(TestCase(
            "test_reproduce_input_is_not_executed_as_code", "reproduce",
            f"Text passed in `{f['param']}` must never run as Python code",
            "def test_reproduce_input_is_not_executed_as_code():\n"
            "    import builtins\n"
            "    if hasattr(builtins, 'AUTOPATCH_PWNED'):\n"
            "        delattr(builtins, 'AUTOPATCH_PWNED')\n"
            "    try:\n"
            f"        {call_source(func, values)}\n"
            "    except Exception:\n"
            "        pass  # rejecting the input is the correct outcome\n"
            "    assert not getattr(builtins, 'AUTOPATCH_PWNED', False), 'the input was executed as code'\n"))
        if f.get("sink") == "eval":
            tests.append(raises_test(func, "test_invalid_non_literal_input_is_rejected", "invalid",
                                     {**base, f["param"]: "not_a_value"}, "Text that is not a plain value"))
    elif c.kind in ("zero-division", "empty-input", "edge-crash") and f.get("param"):
        trigger = TRIGGERS.get(f.get("trigger_value_key", ""), [] if f.get("trigger") == "empty" else 0)
        if f.get("trigger") == "empty" and f.get("param_type") == "dict":
            trigger = {}
        if f.get("trigger") == "empty" and f.get("param_type") == "str":
            trigger = ""
        tests.append(raises_test(func, f"test_reproduce_{f['param']}_{'empty' if f.get('trigger') == 'empty' else 'zero'}_is_rejected",
                                 "reproduce", {**base, f["param"]: trigger},
                                 f"{'Empty' if f.get('trigger') == 'empty' else 'Zero'} `{f['param']}` is invalid input"))
    elif c.kind == "missing-range-check":
        p, lo, hi = f["param"], f.get("lo"), f.get("hi")
        if lo is not None:
            tests.append(raises_test(func, f"test_reproduce_{p}_below_minimum_is_rejected", "reproduce",
                                     {**base, p: lo - 1}, f"`{p}` below {lo}"))
        if hi is not None:
            kind = "maximum" if lo is not None else "reproduce"
            tests.append(raises_test(func, f"test_maximum_{p}_above_maximum_is_rejected", kind,
                                     {**base, p: hi + 1}, f"`{p}` above {hi}"))
    elif c.kind == "sort-direction" and f.get("param"):
        p = f["param"]
        others = [q for q in func.params if q != p and q not in func.defaults]
        extra = {q: 1 for q in others}
        desc = f["want"] == "descending"
        if f["shape"] == "items":
            data = {"low_item": 10, "mid_item": 50, "high_item": 90}
            want, avoid = ("high_item", "low_item") if desc else ("low_item", "high_item")
            n_param = next((q for q in func.params if q != p), None)
            values = {**extra, p: data, **({n_param: 1} if n_param else {})}
            body = (f"    result = {call_source(func, values)}\n"
                    f"    assert {want!r} in repr(result) and {avoid!r} not in repr(result)\n") if n_param else \
                   (f"    result = repr({call_source(func, values)})\n"
                    f"    assert result.index({want!r}) < result.index({avoid!r})\n")
            tests.append(TestCase(f"test_reproduce_{func.name}_returns_{f['word']}_first", "reproduce",
                                  f"'{f['word']}' means {f['want']} order", f"def test_reproduce_{func.name}_returns_{f['word']}_first():\n{body}"))
            if n_param:
                values2 = {**extra, p: data, n_param: 2}
                keep = ("high_item", "mid_item") if desc else ("low_item", "mid_item")
                tests.append(TestCase(
                    f"test_normal_{func.name}_two_results", "normal", f"Asking for two results returns {keep[0]} and {keep[1]}",
                    f"def test_normal_{func.name}_two_results():\n"
                    f"    result = repr({call_source(func, values2)})\n"
                    f"    assert {keep[0]!r} in result and {keep[1]!r} in result and {avoid!r} not in result\n"))
        elif f["shape"] == "list":
            data = [10, 90, 50]
            values = {**extra, p: data}
            first = 90 if desc else 10
            tests.append(TestCase(f"test_reproduce_{func.name}_returns_{f['word']}_first", "reproduce",
                                  f"'{f['word']}' means {f['want']} order",
                                  f"def test_reproduce_{func.name}_returns_{f['word']}_first():\n"
                                  f"    assert list({call_source(func, values)})[0] == {first}\n"))
    elif c.kind == "inclusive-threshold" and f.get("list_param") and f.get("threshold_value") is not None:
        t = f["threshold_value"]
        values = {**base, f["list_param"]: [t]}
        values = {k: v for k, v in values.items() if k != f["threshold"]}
        call = call_source(func, values)
        check = f"{call} == 1" if f.get("counts") else f"{t!r} in {call}"
        tests.append(TestCase(f"test_reproduce_value_equal_to_{f['threshold'].lower()}_counts", "reproduce",
                              f"A value exactly equal to {f['threshold']} ({t}) meets the threshold",
                              f"def test_reproduce_value_equal_to_{f['threshold'].lower()}_counts():\n    assert {check}\n"))
    elif c.kind == "off-by-one-range" and f.get("param"):
        item = {k: 1 for k in f.get("item_keys", [])} or 1
        values = {**base, f["param"]: [item, item, item]}
        tests.append(TestCase(f"test_reproduce_{func.name}_handles_every_item_without_overrunning", "reproduce",
                              f"A normal 3-item `{f['param']}` must not raise IndexError",
                              f"def test_reproduce_{func.name}_handles_every_item_without_overrunning():\n"
                              f"    {call_source(func, values)}\n"))
    elif c.kind == "not-found-in-loop" and f.get("probe_kwargs"):
        m = func.module
        args = ", ".join(f"{k}={v if k != f['param'] else '_target'}" for k, v in f["probe_kwargs"].items())
        target = f["probe_kwargs"][f["param"]].replace(f["collection"], f"_mod.{f['collection']}", 1)
        msg = f["message"].strip().lower()[:40]
        tests.append(TestCase(
            f"test_reproduce_existing_item_is_not_reported_as_missing", "reproduce",
            f"Looking up an item that exists must not print '{f['message'].strip()}'",
            "def test_reproduce_existing_item_is_not_reported_as_missing(capsys):\n"
            f"    import {m} as _mod\n"
            f"    _target = {target}\n"
            f"    {func.name}({args})\n"
            f"    out = capsys.readouterr().out.lower()\n"
            f"    assert {msg!r} not in out\n"))
        tests.append(TestCase(
            f"test_normal_missing_item_is_reported_once", "normal",
            f"A key that does not exist is reported exactly once",
            "def test_normal_missing_item_is_reported_once(capsys):\n"
            f"    {func.name}({', '.join(f'{k}={v}' if k != f['param'] else f'{k}=-987654' for k, v in f['probe_kwargs'].items())})\n"
            f"    assert capsys.readouterr().out.lower().count({msg!r}) == 1\n"))
    elif c.kind == "unguarded-main":
        m = f.get("module_name", func.module)
        tests.append(TestCase(
            "test_reproduce_import_does_not_start_the_program", "reproduce",
            f"Importing {m} must not run {func.name}() or wait for keyboard input",
            "def test_reproduce_import_does_not_start_the_program(monkeypatch):\n"
            "    import builtins, importlib, sys\n"
            "    asked = []\n"
            "    def fake_input(*args):\n"
            "        asked.append(args)\n"
            "        raise EOFError('input() called during import')\n"
            "    monkeypatch.setattr(builtins, 'input', fake_input)\n"
            f"    sys.modules.pop({m!r}, None)\n"
            f"    importlib.import_module({m!r})\n"
            "    assert not asked\n"))
    elif c.kind == "mutable-default":
        p = f["param"]
        def marked(marker_str, marker_num):
            vals = {}
            for q in func.params:
                if q == p or (q in func.defaults and q not in base):
                    continue
                v = base.get(q, None)
                vals[q] = marker_str if isinstance(v, str) or v is None else (marker_num if isinstance(v, (int, float)) else v)
            return vals
        first, second = marked(LEAK_MARKERS[0], 987001), marked(LEAK_MARKERS[1], 987002)
        tests.append(TestCase(
            "test_reproduce_separate_calls_do_not_share_state", "reproduce",
            f"A second call must not see data from the first through the `{p}` default",
            "def test_reproduce_separate_calls_do_not_share_state():\n"
            f"    {call_source(func, first)}\n"
            f"    second = {call_source(func, second)}\n"
            f"    assert {LEAK_MARKERS[0]!r} not in repr(second) and '987001' not in repr(second)\n"))
    return tests


def assemble(module: str, func_name: str, cases: list[TestCase], import_at_top: bool = True) -> str:
    header = ('"""Tests generated by AutoPatch Core. Each one states the correct behavior."""\n\n'
              "import pytest\n\n" + (f"from {module} import {func_name}\n" if import_at_top else "") + "\n\n")
    return header + "\n\n".join(c.code for c in cases)


# ---------------------------------------------------------------- text edits at AST positions


def _byte_to_char(line: str, col: int) -> int:
    return len(line.encode("utf-8")[:col].decode("utf-8", "ignore"))


def _replace(lines: list[str], lineno: int, col: int, end_lineno: int, end_col: int, new: str) -> list[str]:
    out = list(lines)
    a = _byte_to_char(out[lineno - 1], col)
    b = _byte_to_char(out[end_lineno - 1], end_col)
    merged = out[lineno - 1][:a] + new + out[end_lineno - 1][b:]
    out[lineno - 1: end_lineno] = merged.split("\n")
    return out


def _insert_guard(lines: list[str], func: Func, guard: list[str]) -> list[str]:
    indent = func.body_indent
    at = func.first_body_line - 1
    inner = indent + "    "
    block = [indent + guard[0]] + [inner + g for g in guard[1:]]
    return lines[:at] + block + lines[at:]


def _ensure_import(lines: list[str], tree: ast.Module, name: str) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.Import) and any(a.name == name and not a.asname for a in node.names):
            return lines
    first_import = next((n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                         and not (isinstance(n, ast.ImportFrom) and n.module == "__future__")), None)
    if first_import is not None:
        at = first_import.lineno - 1
        return lines[:at] + [f"import {name}"] + lines[at:]
    at = 0
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        at = body[0].end_lineno or 1
    if at < len(lines) and not lines[at].strip():
        return lines[: at + 1] + [f"import {name}", ""] + lines[at + 1:]
    return lines[:at] + ["", f"import {name}", ""] + lines[at:]


def _find(func: Func, pred):
    for node in ast.walk(func.node):
        if pred(node):
            return node
    return None


def repair(c: Candidate, func: Func, mod: ModuleFacts) -> tuple[str, str, str]:
    """Return (new_file_text, explanation, behavior_change) for a template repair."""
    f = c.facts
    lines = list(mod.lines)
    p = f.get("param")
    if c.kind == "code-injection" and f.get("sink") == "eval":
        node = _find(func, lambda n: isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "eval"
                     and n.lineno == c.line_start)
        if node is None:
            raise NoRepair("the eval() call was not found")
        lines = _replace(lines, node.func.lineno, node.func.col_offset, node.func.end_lineno, node.func.end_col_offset,
                         "ast.literal_eval")
        lines = _ensure_import(lines, mod.tree, "ast")
        return ("\n".join(lines),
                f"`eval()` executes any Python expression, so text passed to {func.name} could run arbitrary code. "
                "`ast.literal_eval()` only accepts plain values such as numbers, strings, lists and dicts, so "
                "ordinary input keeps working while code is rejected.",
                "Input that is not a plain literal value now raises ValueError instead of being executed.")
    if c.kind == "unsafe-deserialization" and f.get("yaml"):
        node = _find(func, lambda n: isinstance(n, ast.Attribute) and n.attr == "load" and n.lineno == c.line_start)
        if node is None:
            raise NoRepair("the yaml.load() call was not found")
        lines = _replace(lines, node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, "yaml.safe_load")
        return ("\n".join(lines), "`yaml.safe_load()` builds only plain data, never arbitrary Python objects.",
                "Documents that rely on Python-specific YAML tags are now rejected.")
    if c.kind in ("zero-division", "empty-input", "edge-crash") and p:
        if f.get("trigger") == "empty":
            guard = [f"if not {p}:", f'raise ValueError("{p} must not be empty")']
            what = f"an empty `{p}`"
        elif f.get("trigger") == "zero":
            guard = [f"if {p} == 0:", f'raise ValueError("{p} must not be zero")']
            what = f"`{p}` equal to 0"
        else:
            raise NoRepair("no safe guard for this input")
        lines = _insert_guard(lines, func, guard)
        return ("\n".join(lines),
                f"{func.name} now checks for {what} before using it, so the invalid input is rejected with a clear "
                f"ValueError instead of crashing with {f.get('exception', 'an internal error')} deep inside the function.",
                f"Calling {func.name} with {what} raises ValueError; all other input behaves as before.")
    if c.kind == "missing-range-check":
        lo, hi, hi_name = f.get("lo"), f.get("hi"), f.get("hi_name")
        hi_expr = hi_name or (repr(hi) if hi is not None else None)
        if lo is not None and hi_expr is not None:
            guard = [f"if not {lo} <= {p} <= {hi_expr}:",
                     f'raise ValueError(f"{p} must be between {lo} and {{{hi_expr}}}, got {{{p}!r}}")']
            rng = f"between {lo} and {hi_expr}"
        elif lo is not None:
            guard = [f"if {p} < {lo}:", f'raise ValueError(f"{p} must not be below {lo}, got {{{p}!r}}")']
            rng = f"at least {lo}"
        else:
            guard = [f"if {p} > {hi_expr}:", f'raise ValueError(f"{p} must not exceed {{{hi_expr}}}, got {{{p}!r}}")']
            rng = f"at most {hi_expr}"
        lines = _insert_guard(lines, func, guard)
        return ("\n".join(lines),
                f"The thresholds in {func.name} only make sense for `{p}` {rng}. The new check rejects values outside "
                "that range before they are sorted into a band, so they can no longer produce a believable but wrong result.",
                f"Values of `{p}` outside the valid range now raise ValueError; values inside it give the same results as before.")
    if c.kind == "sort-direction":
        node = _find(func, lambda n: isinstance(n, ast.Call) and n.lineno == c.line_start and (
            isinstance(n.func, ast.Name) and n.func.id == "sorted" or isinstance(n.func, ast.Attribute) and n.func.attr == "sort"))
        if node is None:
            raise NoRepair("the sort call was not found")
        want = "True" if f["want"] == "descending" else "False"
        rev = next((k for k in node.keywords if k.arg == "reverse"), None)
        if rev is not None:
            lines = _replace(lines, rev.value.lineno, rev.value.col_offset, rev.value.end_lineno, rev.value.end_col_offset, want)
        else:
            end_line = lines[node.end_lineno - 1]
            close = _byte_to_char(end_line, node.end_col_offset) - 1
            lines[node.end_lineno - 1] = end_line[:close] + ", reverse=True" + end_line[close:]
        return ("\n".join(lines),
                f"{func.name} promises the {f['word']} results, so the items must be ordered {f['want']} before the "
                "first ones are taken. The sort now uses that order.",
                f"{func.name} returns the {f['word']} items instead of the opposite end of the list.")
    if c.kind == "inclusive-threshold":
        node = _find(func, lambda n: isinstance(n, ast.Compare) and n.lineno == c.line_start and len(n.ops) == 1
                     and isinstance(n.ops[0], (ast.Gt, ast.Lt)))
        if node is None:
            raise NoRepair("the comparison was not found")
        line = lines[node.lineno - 1]
        a = _byte_to_char(line, node.left.end_col_offset)
        b = _byte_to_char(line, node.comparators[0].col_offset)
        gap = line[a:b]
        new_op = ">=" if f["op"] == ">" else "<="
        lines[node.lineno - 1] = line[:a] + gap.replace(f["op"], new_op, 1) + line[b:]
        return ("\n".join(lines),
                f"`{f['threshold']}` names the minimum that still qualifies, so a value equal to it must be included. "
                f"The comparison now uses {new_op} instead of {f['op']}.",
                f"Values exactly equal to {f['threshold']} are now counted; every other value is treated as before.")
    if c.kind == "mutable-default":
        d = func.defaults.get(p)
        if d is None:
            raise NoRepair("the default value was not found")
        default_src = f.get("default_source") or ast.unparse(d)
        lines = _insert_guard(lines, func, [f"if {p} is None:", f"{p} = {default_src}"])
        lines = _replace(lines, d.lineno, d.col_offset, d.end_lineno, d.end_col_offset, "None")
        return ("\n".join(lines),
                f"A default like `{default_src}` is created once, when the function is defined, and every call that "
                f"omits `{p}` then shares it. Using None as the default and creating a fresh object inside the "
                "function gives each call its own.",
                f"Calls that omit `{p}` no longer see data left behind by earlier calls.")
    if c.kind == "off-by-one-range":
        node = _find(func, lambda n: isinstance(n, (ast.For, ast.While)) and n.lineno == c.line_start)
        if node is None:
            raise NoRepair("the loop was not found")
        line = lines[node.lineno - 1]
        if isinstance(node, ast.For):
            new = re.sub(r"len\(\s*" + re.escape(f["sequence"]) + r"\s*\)\s*\+\s*1", f"len({f['sequence']})", line, count=1)
        else:
            new = line.replace("<=", "<", 1)
        if new == line:
            raise NoRepair("the loop bound was not found on its line")
        lines[node.lineno - 1] = new
        return (chr(10).join(lines),
                f"Valid indexes of `{f['sequence']}` run from 0 to len({f['sequence']}) - 1; the loop now stops there "
                "instead of one step later.",
                f"{func.name} no longer raises IndexError on its last pass.")
    if c.kind == "not-found-in-loop":
        loop = _find(func, lambda n: isinstance(n, ast.For) and n.lineno == f["loop_line"])
        if loop is None:
            raise NoRepair("the loop was not found")
        branch = loop.body[0]
        else_start = branch.orelse[0].lineno - 1          # the "else:" line sits just above the first statement
        while else_start > branch.lineno and not lines[else_start - 1].strip().startswith("else"):
            else_start -= 1
        else_end = branch.orelse[-1].end_lineno
        loop_indent = lines[loop.lineno - 1][: len(lines[loop.lineno - 1]) - len(lines[loop.lineno - 1].lstrip())]
        if_indent = lines[branch.lineno - 1][: len(lines[branch.lineno - 1]) - len(lines[branch.lineno - 1].lstrip())]
        body_indent = lines[branch.body[0].lineno - 1][: len(lines[branch.body[0].lineno - 1]) - len(lines[branch.body[0].lineno - 1].lstrip())]
        else_body = lines[else_start: else_end]
        shift = len(if_indent) - len(loop_indent)
        moved = [ln[shift:] if ln.startswith(" " * shift) else ln for ln in else_body]
        head = lines[: else_start - 1]
        body_end = branch.body[-1].end_lineno
        head = lines[:body_end] + [body_indent + "break"] + lines[body_end: else_start - 1]
        new_lines = head + [loop_indent + "else:"] + moved + lines[else_end:]
        return (chr(10).join(new_lines),
                "The 'not found' branch now belongs to the loop (for ... else): the loop stops at the first match, and the "
                "message appears only if the loop finishes without finding anything.",
                f"{func.name} stops at the first matching item and reports 'not found' at most once.")
    if c.kind == "unguarded-main":
        stmt = next((s for s in mod.tree.body if isinstance(s, ast.Expr) and s.lineno == c.line_start), None)
        if stmt is None:
            raise NoRepair("the module-level call was not found")
        original = lines[stmt.lineno - 1]
        indent = original[: len(original) - len(original.lstrip())]
        lines[stmt.lineno - 1: stmt.end_lineno] = [f'{indent}if __name__ == "__main__":'] + \
            ["    " + ln for ln in lines[stmt.lineno - 1: stmt.end_lineno]]
        return (chr(10).join(lines),
                f"`{f['call']}` now runs only when the file is executed as a program, not when it is imported. "
                "Running the script behaves exactly as before; importing it (for tests or reuse) no longer starts it.",
                "Importing the module no longer starts the program; `python file.py` works as before.")
    if c.kind == "overwritten-accumulator":
        node = _find(func, lambda n: isinstance(n, ast.Assign) and n.lineno == c.line_start and len(n.targets) == 1)
        if node is None:
            raise NoRepair("the assignment was not found")
        line = lines[node.lineno - 1]
        a = _byte_to_char(line, node.targets[0].end_col_offset)
        b = _byte_to_char(line, node.value.col_offset)
        lines[node.lineno - 1] = line[:a] + line[a:b].replace("=", "+=", 1) + line[b:]
        v = f["variable"]
        return (chr(10).join(lines),
                f"`{v}` starts empty before the loop and is used after it, so each pass must add to it. The loop "
                f"replaced it instead, so only the last item counted. It now uses `{v} +=`.",
                f"{func.name} now combines every item instead of keeping only the last one.")
    raise NoRepair(f"AutoPatch Core has no automatic repair for {c.kind.replace('-', ' ')} issues")


# ---------------------------------------------------------------- mutation search


def mutations(func: Func, mod: ModuleFacts, limit: int = 40) -> list[tuple[str, str]]:
    """Small single-point changes to the function: (description, new file text)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    flips = {ast.Gt: ">=", ast.GtE: ">", ast.Lt: "<=", ast.LtE: "<"}
    olds = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<="}

    def add(desc: str, lines: list[str]):
        text = "\n".join(lines)
        if text == mod.text or text in seen:
            return
        try:
            ast.parse(text)
        except SyntaxError:
            return
        seen.add(text)
        out.append((desc, text))

    for node in ast.walk(func.node):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in flips \
                and node.lineno == node.end_lineno:
            line = mod.lines[node.lineno - 1]
            a = _byte_to_char(line, node.left.end_col_offset)
            b = _byte_to_char(line, node.comparators[0].col_offset)
            old, new = olds[type(node.ops[0])], flips[type(node.ops[0])]
            gap = line[a:b]
            if old in gap:
                lines = list(mod.lines)
                lines[node.lineno - 1] = line[:a] + gap.replace(old, new, 1) + line[b:]
                add(f"line {node.lineno}: {old} -> {new}", lines)
        if isinstance(node, ast.Call) and (isinstance(node.func, ast.Name) and node.func.id == "sorted"
                                           or isinstance(node.func, ast.Attribute) and node.func.attr == "sort"):
            rev = next((k for k in node.keywords if k.arg == "reverse"), None)
            lines = list(mod.lines)
            if rev is not None:
                flipped = "False" if ast.unparse(rev.value) == "True" else "True"
                lines = _replace(lines, rev.value.lineno, rev.value.col_offset, rev.value.end_lineno,
                                 rev.value.end_col_offset, flipped)
            else:
                end_line = lines[node.end_lineno - 1]
                close = _byte_to_char(end_line, node.end_col_offset) - 1
                lines[node.end_lineno - 1] = end_line[:close] + ", reverse=True" + end_line[close:]
            add(f"line {node.lineno}: reverse the sort", lines)
        if isinstance(node, ast.Name) and node.id in ("max", "min") and isinstance(node.ctx, ast.Load):
            lines = _replace(list(mod.lines), node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                             "min" if node.id == "max" else "max")
            add(f"line {node.lineno}: {node.id} -> {'min' if node.id == 'max' else 'max'}", lines)
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool) \
                and abs(node.value) <= 1000 and node.lineno == node.end_lineno:
            for delta in (1, -1):
                lines = _replace(list(mod.lines), node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                                 str(node.value + delta))
                add(f"line {node.lineno}: {node.value} -> {node.value + delta}", lines)
        if len(out) >= limit:
            break
    return out[:limit]
