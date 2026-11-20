"""AutoPatch Core: the built-in engine. Same interface as the optional external AI engine.

scan        detectors + sandbox probes that confirm each bug + fuzzing for crashes
analyze     root-cause explanation built from the detector facts and probe evidence
write_tests tests that encode correct behavior, plus recorded normal-input tests
write_fix   minimal fix template; if that fails, a search over small mutations validated in the sandbox
"""

from __future__ import annotations

import copy
import shutil
import time
from pathlib import Path, PurePosixPath

from autopatch.ai.providers import AIError, AIUsage
from autopatch.ai.schemas import CodeEdit, FixProposal, GeneratedCase, GeneratedTests, RootCauseAnalysis
from autopatch.analysis.findings import DraftFinding
from autopatch.core_engine import codegen
from autopatch.core_engine.detectors import DETECTORS, Candidate, detect_all
from autopatch.core_engine.facts import (
    TYPICAL, Func, ModuleFacts, call_source, collect, collect_seeds, param_types,
)
from autopatch.core_engine.probe import INJECTION_PAYLOAD, LEAK_MARKERS, run_probes

NAME = "AutoPatch Core"
CRASH_EXC = {"ZeroDivisionError", "IndexError", "KeyError", "AttributeError", "TypeError", "UnboundLocalError",
             "RecursionError", "ValueError", "SyntaxError", "Timeout", "OverflowError"}
CONCEPT = {"mutable-default": "mutable-default"}
# Bug kinds where the correct behavior is a design decision: Core reports them with guidance but does not guess a fix.
NEEDS_HUMAN = {
    "missing-return": "Which value to return on the missing path (or whether to raise) is a design decision.",
    "unclosed-resource": "Moving the code into a `with` block changes its structure; review where the resource should be released.",
    "unchecked-none-result": "What to do when the call fails (stop, retry, show a message) is a product decision.",
    "command-injection": "The safe form depends on what the command must do; build it as an argument list with shell=False.",
    "sql-injection": "Rewrite the query with parameters, e.g. cursor.execute(sql, (value,)).",
    "none-deref": "What should happen when nothing is found is a design decision.",
}


def _usage(start: float) -> AIUsage:
    return AIUsage(provider="autopatch-core", model=NAME, duration_s=round(time.monotonic() - start, 1))


def _kw(values: dict) -> dict:
    return {k: repr(v) for k, v in values.items()}


class CoreEngine:
    name = NAME

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._test_path = ""

    # ------------------------------------------------------------ helpers

    def _backend(self):
        from autopatch.pipeline.common import backend_from_settings
        from autopatch.testing.sandbox import SandboxUnavailable

        try:
            return backend_from_settings(self.cfg)
        except SandboxUnavailable:
            return None

    @staticmethod
    def _populate(root: Path, overrides: dict[str, str] | None = None):
        def populate(dest: Path) -> None:
            shutil.copytree(root, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
            for rel, text in (overrides or {}).items():
                target = dest / PurePosixPath(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
        return populate

    def _load(self, context):
        modules = collect(context.files, context.structure)
        seeds = collect_seeds(context.files, context.structure, modules)
        return modules, seeds

    @staticmethod
    def _base(func: Func, mod: ModuleFacts, seeds: list, types: dict, fixed: dict | None = None) -> dict | None:
        values: dict = {}
        if seeds:
            args, kwargs = seeds[0]
            values.update(dict(zip(func.params, args)))
            values.update(kwargs)
        values.update(fixed or {})
        for p in func.params:
            if p in values or p in func.defaults or p in ("self", "cls"):
                continue
            if types.get(p) in TYPICAL:
                values[p] = copy.deepcopy(TYPICAL[types[p]])
            else:
                return None
        return values

    def _find_func(self, modules: dict[str, ModuleFacts], file: str, name: str):
        for mod in modules.values():
            if mod.file == file and name in mod.funcs:
                return mod.funcs[name], mod
        return None, None

    def _match(self, finding, modules) -> tuple[Candidate | None, Func | None, ModuleFacts | None]:
        func, mod = self._find_func(modules, finding.file, finding.function)
        facts = dict(getattr(finding, "facts", None) or {})
        kind = facts.get("kind") or next((s.split(":", 1)[1] for s in finding.sources if s.startswith("core:")), "")
        if not kind:
            concepts = " ".join(finding.sources)
            kind = "mutable-default" if ("B006" in concepts or "W0102" in concepts) else \
                "code-injection" if ("S307" in concepts or "W0123" in concepts) else ""
        if func is None:
            return None, None, None
        from autopatch.core_engine.detectors import detect_unguarded_main

        from autopatch.core_engine.detectors import function_detectors
        from autopatch.core_engine.detectors2 import detect_unchecked_none

        fresh = [c for d in function_detectors() for c in d(func, mod)] + detect_unguarded_main(mod) + \
            [c for c in detect_unchecked_none(modules) if c.file == func.file and c.function == func.name]
        same = [c for c in fresh if c.kind == kind and (not facts.get("param") or c.facts.get("param") == facts.get("param"))]
        if same:
            same[0].facts = {**facts, **same[0].facts}
            return same[0], func, mod
        if kind == "edge-crash" and facts:
            return Candidate(kind, func.module, func.file, func.name, finding.line_start, finding.line_end,
                             finding.severity, finding.category, finding.confidence, finding.title, facts), func, mod
        return None, func, mod

    # ------------------------------------------------------------ probes

    def _probes(self, cid: str, c: Candidate, func: Func, mod: ModuleFacts, types: dict, seeds: list) -> list[dict]:
        f, p = c.facts, c.facts.get("param")
        base = lambda fixed=None: self._base(func, mod, seeds, types, fixed)  # noqa: E731
        case = lambda suffix, values, **extra: {"id": f"{cid}-{suffix}", "mode": "call", "module": func.module,  # noqa: E731
                                                "func": func.name, "args": [], "kwargs": _kw(values), **extra}
        if c.kind == "code-injection" and p and f.get("sink") in ("eval", "exec"):
            payload = [INJECTION_PAYLOAD] if types.get(p) == "list" else INJECTION_PAYLOAD
            values = base({p: payload})
            return [] if values is None else [{**case("inject", values), "mode": "inject"}]
        if c.kind in ("zero-division", "empty-input") and p:
            t = types.get(p)
            trigger = 0 if f["trigger"] == "zero" else ({} if t == "dict" else "" if t == "str" else [])
            f["param_type"] = t
            values = base({p: trigger})
            return [] if values is None else [case("trigger", values)]
        if c.kind == "missing-range-check":
            out = []
            for label, v in (("below", f["lo"] - 1 if f.get("lo") is not None else None),
                             ("above", f["hi"] + 1 if f.get("hi") is not None else None)):
                if v is not None and (values := base({p: v})) is not None:
                    out.append(case(label, values))
            return out
        if c.kind == "sort-direction" and p:
            n_param = next((q for q in func.params if q != p), None)
            data = {"autopatch_low": 10, "autopatch_mid": 50, "autopatch_high": 90} if f["shape"] == "items" else [10, 90, 50]
            values = base({p: data, **({n_param: 1} if n_param and f["shape"] == "items" else {})})
            return [] if values is None or f["shape"] == "other" else [case("order", values)]
        if c.kind == "inclusive-threshold" and f.get("list_param") and f.get("threshold_value") is not None:
            values = base({f["list_param"]: [f["threshold_value"]]})
            if values is not None:
                values.pop(f["threshold"], None)
                return [case("equal", values)]
        if c.kind == "off-by-one-range" and p:
            t = types.get(p)
            item = {k: 1 for k in f.get("item_keys", [])} or 1
            values = base({p: [item, item, item] if t in (None, "list") else "abc"})
            return [] if values is None else [case("full", values)]
        if c.kind == "not-found-in-loop" and f.get("param"):
            first = f"{f['collection']}[0]"
            expr = f["left"].replace(f["loop_var"], first, 1) if f["left"].startswith(f["loop_var"]) else None
            if expr is None or not f.get("collection_is_global"):
                return []
            kwargs = {f["param"]: expr}
            for q in func.params:
                if q not in kwargs and q not in func.defaults:
                    t = types.get(q)
                    if t not in TYPICAL:
                        return []
                    kwargs[q] = repr(1 if t in ("int", "float") else TYPICAL[t])
            f["probe_kwargs"] = kwargs
            return [{"id": f"{cid}-match", "mode": "expr", "module": func.module, "func": func.name, "args": [],
                     "kwargs": kwargs}]
        if c.kind == "mutable-default":
            def marked(ms, mn):
                vals = {}
                for q in func.params:
                    if q == p or q in func.defaults or q in ("self", "cls"):
                        continue
                    vals[q] = mn if types.get(q) in ("int", "float") else ms
                return vals
            return [{"id": f"{cid}-twice", "mode": "twice", "module": func.module, "func": func.name,
                     "args": [], "kwargs": _kw(marked(LEAK_MARKERS[0], 987001)),
                     "args2": [], "kwargs2": _kw(marked(LEAK_MARKERS[1], 987002)), "marker": LEAK_MARKERS[0]}]
        return []

    def _confirm(self, cid: str, c: Candidate, func: Func, results: dict) -> None:
        r = {k.split("-", 1)[1]: v for k, v in results.items() if k.startswith(cid + "-")}
        f = c.facts
        if c.kind == "code-injection" and r.get("inject", {}).get("executed"):
            c.confirmed, c.evidence = True, (f"Ran {func.name}() in the sandbox with the text {INJECTION_PAYLOAD!r}: "
                                             "the text was executed as Python code (it set a marker variable).")
        elif c.kind in ("zero-division", "empty-input") and (t := r.get("trigger")) and not t.get("ok") \
                and not t.get("explicit") and t.get("exc") in CRASH_EXC:
            f["exception"] = t["exc"]
            c.confirmed, c.evidence = True, (f"Ran {func.name}() in the sandbox with an empty/zero `{f['param']}`: it "
                                             f"crashed with {t['exc']}: {t.get('msg', '')}")
        elif c.kind == "missing-range-check":
            accepted = [(label, v) for label, v in r.items() if v.get("ok")]
            if accepted:
                label, v = accepted[0]
                val = f["lo"] - 1 if label == "below" else f["hi"] + 1
                c.confirmed, c.evidence = True, (f"Ran {func.name}({val}) in the sandbox: it returned {v.get('result')} "
                                                 "instead of rejecting a value outside the valid range.")
        elif c.kind == "sort-direction" and (o := r.get("order")) and o.get("ok"):
            res = o.get("result", "")
            wrong = ("autopatch_low" in res and ("autopatch_high" not in res or res.index("autopatch_low") < res.index("autopatch_high"))) \
                if f["want"] == "descending" else ("autopatch_high" in res and ("autopatch_low" not in res or res.index("autopatch_high") < res.index("autopatch_low")))
            if f["shape"] == "list":
                first = res.strip("[(").split(",")[0].strip()
                wrong = first != ("90" if f["want"] == "descending" else "10")
            if wrong:
                c.confirmed, c.evidence = True, (f"Ran {func.name}() in the sandbox with items valued 10, 50 and 90: it "
                                                 f"returned {res} — the {'lowest' if f['want'] == 'descending' else 'highest'} first.")
        elif c.kind == "inclusive-threshold" and (e := r.get("equal")) and e.get("ok"):
            res = e.get("result", "")
            if (f.get("counts") and res.strip() == "0") or (not f.get("counts") and repr(f["threshold_value"]) not in res):
                c.confirmed, c.evidence = True, (f"Ran {func.name}() in the sandbox with a single value equal to "
                                                 f"{f['threshold']} ({f['threshold_value']}): it returned {res}, so that value was left out.")
        elif c.kind == "off-by-one-range" and (t := r.get("full")) and not t.get("ok") and t.get("exc") == "IndexError":
            f["exception"] = "IndexError"
            c.confirmed, c.evidence = True, (f"Ran {func.name}() in the sandbox with a normal 3-item `{f['sequence']}`: "
                                             f"it crashed with IndexError: {t.get('msg', '')}")
        elif c.kind == "not-found-in-loop" and (t := r.get("match")) and t.get("ok"):
            said = (t.get("stdout", "") + t.get("result", "")).lower()
            if f["message"].strip().lower()[:40] in said:
                c.confirmed, c.evidence = True, (f"Ran {func.name}() in the sandbox with the first item's own key: it "
                                                 f"still reported '{f['message'].strip()}' (output: {t.get('stdout', '').strip()[:160]!r}).")
        elif c.kind == "mutable-default" and r.get("twice", {}).get("leak"):
            c.confirmed, c.evidence = True, (f"Called {func.name}() twice in the sandbox: the second result "
                                             f"{r['twice'].get('result', '')[:120]} still contains data from the first call.")
        if c.confirmed:
            c.confidence = 0.97

    def _fuzz_cases(self, modules, seeds, covered: set) -> list[dict]:
        cases = []
        for mod in modules.values():
            for func in mod.funcs.values():
                if not func.public or (mod.module, func.name) in covered or len(cases) > 240:
                    continue
                s = seeds.get((mod.module, func.name), [])
                types = param_types(func, s, mod)
                base = self._base(func, mod, s, types)
                if base is None:
                    continue
                gid = f"g-{mod.module}-{func.name}"
                cases.append({"id": f"{gid}-base", "mode": "call", "module": mod.module, "func": func.name,
                              "args": [], "kwargs": _kw(base)})
                for p in func.params:
                    t = types.get(p)
                    for label, v in {"int": (("zero", 0), ("negative", -1)), "float": (("zero", 0.0), ("negative", -1.0)),
                                     "str": (("empty", ""),), "list": (("empty", []),), "dict": (("empty", {}),)}.get(t, ()):
                        cases.append({"id": f"{gid}-{p}~{label}", "mode": "call", "module": mod.module,
                                      "func": func.name, "args": [], "kwargs": _kw({**base, p: v}),
                                      "meta": {"param": p, "label": label, "value": repr(v), "type": t}})
        return cases

    def _generic(self, cases, results, cands, modules) -> list[Candidate]:
        out: list[Candidate] = []
        seen: set = set()
        by_loc = {}
        for c in cands:
            by_loc.setdefault((c.file, c.function), []).append(c)
        for case in cases:
            meta = case.get("meta")
            if not meta:
                continue
            res = results.get(case["id"])
            base = results.get(case["id"].rsplit("-", 1)[0].rsplit("~", 1)[0].rsplit("-", 0)[0])
            base = results.get(f"g-{case['module']}-{case['func']}-base")
            if not res or res.get("ok") or res.get("explicit") or res.get("harness_error") or res.get("exc") not in CRASH_EXC:
                continue
            if not base or not base.get("ok"):
                continue
            func, mod = None, None
            for m in modules.values():
                if m.file == res.get("file"):
                    func = next((fn for fn in m.funcs.values() if fn.start <= res.get("line", 0) <= fn.end), None)
                    mod = m
            caller = modules_func(modules, case["module"], case["func"])
            func, mod = (func, mod) if func else caller
            if func is None:
                continue
            call = call_source(caller[0], {k: _lit(v) for k, v in case["kwargs"].items()})
            existing = [c for c in by_loc.get((func.file, func.name), []) if c.kind in ("zero-division", "empty-input")]
            if existing:
                existing[0].facts.setdefault("reached_from", []).append(f"{call} -> {res['exc']}")
                continue
            key = (func.file, func.name, res["exc"])
            if key in seen:
                continue
            seen.add(key)
            direct = func.name == case["func"]
            trigger = "empty" if meta["label"] == "empty" else "zero" if meta["label"] == "zero" else meta["label"]
            sev = "high" if res["exc"] == "Timeout" else "medium"
            c = Candidate("edge-crash", func.module, func.file, func.name, res.get("line") or func.start,
                          res.get("line") or func.start, sev, "crash", 0.95,
                          f"{func.name} {'hangs' if res['exc'] == 'Timeout' else 'crashes with ' + res['exc']} on "
                          f"{'an ' if meta['label'] == 'empty' else 'a '}{meta['label']} `{meta['param']}`"
                          + ("" if direct else f" passed through {case['func']}"),
                          {"param": meta["param"] if direct else None, "trigger": trigger, "trigger_value": meta["value"],
                           "param_type": meta["type"], "exception": res["exc"], "message": res.get("msg", ""),
                           "called_as": call, "via": None if direct else case["func"]})
            c.confirmed = True
            c.evidence = f"Ran {call} in the sandbox: it raised {res['exc']}: {res.get('msg', '')} (line {res.get('line')})."
            out.append(c)
        return out

    # ------------------------------------------------------------ explanations

    def _explain(self, c: Candidate, func: Func, callers: list[str]) -> dict:
        f, p = c.facts, c.facts.get("param")
        callers_txt = (" It is used by " + ", ".join(x.split(".")[-1] for x in callers[:4]) + ", which inherit the problem.") if callers else ""
        k = c.kind
        if k == "code-injection":
            steps = " → ".join(f"{s['step']} (line {s['line']})" for s in f.get("path", []))
            return dict(description=f"{func.name} passes text from `{p}` to {f['sink']}(), which runs it as Python code. "
                                    "Whoever controls that text can run any command on the machine.",
                        root_cause=f"The data flows straight from the caller into {f['sink']}() with no check: {steps}." + callers_txt,
                        trigger=f"{func.name}({p}={INJECTION_PAYLOAD!r}) runs the code inside the text.",
                        expected_behavior="Only plain values (numbers, strings, lists) should be accepted; anything else rejected with ValueError.")
        if k in ("command-injection", "sql-injection", "unsafe-deserialization"):
            steps = " → ".join(f"{s['step']} (line {s['line']})" for s in f.get("path", []))
            advice = {"command-injection": "Pass the command as a list with shell=False and validate the values.",
                      "sql-injection": "Use a parameterized query, e.g. cursor.execute(sql, (value,)).",
                      "unsafe-deserialization": "Only load data from trusted sources, or use a safe format such as JSON."}[k]
            return dict(description=c.title + ".", root_cause=f"Caller-controlled data reaches {f['sink']}: {steps}." + callers_txt,
                        trigger=f"Any value of `{p}` containing special characters changes what {f['sink']} does.",
                        expected_behavior=advice)
        if k in ("zero-division", "empty-input", "edge-crash"):
            via = f" (reached through {f['via']})" if f.get("via") else ""
            reached = ("; also reached as " + "; ".join(f["reached_from"][:3])) if f.get("reached_from") else ""
            what = "an empty value" if f.get("trigger") == "empty" else "0" if f.get("trigger") == "zero" else f.get("trigger_value", "edge input")
            return dict(description=f"{func.name} crashes{via} when `{p or 'its input'}` is {what}"
                                    + (f" ({f.get('exception')})" if f.get("exception") else "") + ".",
                        root_cause=(f"`{f.get('expression')}` on line {c.line_start} assumes `{p}` is never {what}, and nothing "
                                    "checks it first." if f.get("expression") else
                                    f"An internal operation on line {c.line_start} fails for this input and nothing checks for it.")
                                   + reached + callers_txt,
                        trigger=f.get("called_as") or f"Calling {func.name} with `{p}` = {what}.",
                        expected_behavior=f"Reject {what} for `{p}` with a clear ValueError instead of crashing.")
        if k == "missing-range-check":
            return dict(description=f"{func.name} sorts `{p}` into bands ({', '.join(map(str, f['thresholds']))}) but never checks "
                                    f"that `{p}` is valid, so impossible values get a normal-looking answer.",
                        root_cause=f"The if/elif chain starting on line {c.line_start} only compares against thresholds. A value "
                                   f"below {f.get('lo')} falls through to the last branch and a value above "
                                   f"{f.get('hi_name') or f.get('hi')} matches the first one." + callers_txt,
                        trigger=f"{func.name}({(f.get('lo') or 0) - 1}) or {func.name}({(f.get('hi') or 100) + 1}).",
                        expected_behavior=f"Reject `{p}` outside {f.get('lo')}–{f.get('hi_name') or f.get('hi')} with ValueError.")
        if k == "sort-direction":
            return dict(description=f"{func.name} promises the {f['word']} results but sorts the other way, so it returns the opposite end.",
                        root_cause=f"`{f['call']}` on line {c.line_start} sorts {'ascending' if f['want'] == 'descending' else 'descending'}; "
                                   f"taking the first items then gives the {'lowest' if f['want'] == 'descending' else 'highest'}." + callers_txt,
                        trigger=f"Any input with two or more different values, e.g. scores 10, 50 and 90.",
                        expected_behavior=f"Sort {f['want']} so the {f['word']} items come first.")
        if k == "inclusive-threshold":
            return dict(description=f"{func.name} leaves out values exactly equal to `{f['threshold']}`.",
                        root_cause=f"`{f['compare']}` on line {c.line_start} uses {f['op']}, which excludes equality, but "
                                   f"`{f['threshold']}` names the minimum that still qualifies." + callers_txt,
                        trigger=f"A value equal to {f['threshold']} ({f.get('threshold_value')}).",
                        expected_behavior=f"Values equal to {f['threshold']} should count ({'>=' if f['op'] == '>' else '<='}).")
        if k == "mutable-default":
            return dict(description=f"Calls to {func.name} that omit `{p}` all share one `{f.get('default_source')}` object, "
                                    "so data from one call leaks into the next.",
                        root_cause=f"Python creates the default `{f.get('default_source')}` once, when the function is defined, "
                                   f"and {func.name} modifies it." + callers_txt,
                        trigger=f"Call {func.name} twice without `{p}`: the second result contains the first call's data.",
                        expected_behavior="Each call should start with a fresh, empty object.")
        if k == "not-found-in-loop":
            return dict(description=f"{func.name} reports '{f['message'].strip()}' once for every item that does not match, "
                                    "even when the item it was looking for exists.",
                        root_cause=f"The `else:` on the if inside the loop (line {f['if_line']}) belongs to the comparison, "
                                   "not to the search: it runs for each non-matching item. 'Not found' is only known after "
                                   "the whole loop has finished without a match." + callers_txt,
                        trigger=f"Look up any existing item that is not the only one in `{f['collection']}`.",
                        expected_behavior="Report 'not found' once, after the loop, only when nothing matched (for ... else).")
        if k == "off-by-one-range":
            return dict(description=f"{func.name} indexes `{f['sequence']}` one position past its end.",
                        root_cause=f"The loop runs `{f['index']}` up to and including len({f['sequence']}), but the last valid "
                                   f"index is len({f['sequence']}) - 1, so the final pass raises IndexError." + callers_txt,
                        trigger=f"Any non-empty `{f['sequence']}`.",
                        expected_behavior=f"Stop before len({f['sequence']}).")
        if k == "missing-return":
            return dict(description=f"{func.name} returns a value on some paths but falls off the end on others, "
                                    "returning None.",
                        root_cause=f"Values are returned on lines {', '.join(map(str, f.get('value_lines', [])))}, but the "
                                   "last statement can complete without a return, so callers get None." + callers_txt,
                        trigger="An input that takes none of the returning paths.",
                        expected_behavior="Return a value (or raise an error) on every path. Which one is a design decision.")
        if k == "unclosed-resource":
            return dict(description=c.title + ".",
                        root_cause=f"`{f['variable']}` comes from {f['opener']}() but is not used in a `with` block"
                                   + (" and close() is skipped if an error happens first." if f.get("partial") else
                                      " and close() is never called.") + callers_txt,
                        trigger="Calling the function repeatedly, or an error while the resource is open.",
                        expected_behavior=f"Use `with {f['opener']}(...) as {f['variable']}:` so it is always closed.")
        if k == "unchecked-none-result":
            return dict(description=f"{f['callee']}() returns None when it fails (line {f['none_line']}), and {func.name} "
                                    f"uses the result on line {f['use_line']} without checking.",
                        root_cause=f"`{f['variable']} = {f['callee']}()` can be None, and nothing checks it before it is "
                                   "used, so the failure case crashes or misbehaves later." + callers_txt,
                        trigger=f"Make {f['callee']}() take its failure path (e.g. invalid input).",
                        expected_behavior=f"Check `if {f['variable']} is None:` and handle the failure (stop, retry or report).")
        if k == "unguarded-main":
            return dict(description=f"The module calls {f['call']} at the top level, so importing it starts the program.",
                        root_cause=f"Line {c.line_start} runs `{f['call']}` unconditionally. Python executes every top-level "
                                   "statement on import, so tests, tools and other modules that import this file trigger "
                                   "the program (here it waits for keyboard input).",
                        trigger=f"`import {f.get('module_name', func.module)}`",
                        expected_behavior='Run it only under `if __name__ == "__main__":`.')
        if k == "overwritten-accumulator":
            v = f["variable"]
            return dict(description=f"{func.name} keeps only the last item: `{v}` is replaced on every pass of the loop "
                                    "instead of being added to.",
                        root_cause=f"`{v}` starts empty before the loop (line {f.get('loop_line')}) and is used after it, "
                                   f"but `{f['expression']}` on line {c.line_start} assigns instead of accumulating." + callers_txt,
                        trigger="Any input with two or more items; the result reflects only the last one.",
                        expected_behavior=f"Accumulate every item: `{v} += ...`.")
        if k == "none-deref":
            return dict(description=c.title + ".", root_cause=f"`{f.get('expression')}` on line {c.line_start} can be None." + callers_txt,
                        trigger="A lookup or pattern that finds nothing.", expected_behavior="Check for None before using the result.")
        return dict(description=c.title, root_cause="", trigger="", expected_behavior="")

    # ------------------------------------------------------------ engine interface

    def scan(self, context, static):
        start = time.monotonic()
        from autopatch.core_engine.learning import reset_cache

        reset_cache()  # use the latest accept/reject decisions
        modules, seeds = self._load(context)
        cands = detect_all(modules)
        backend = self._backend()
        cases: list[dict] = []
        for i, c in enumerate(cands):
            func, mod = modules[c.module].funcs[c.function], modules[c.module]
            s = seeds.get((c.module, c.function), [])
            cases += self._probes(f"c{i}", c, func, mod, param_types(func, s, mod), s)
        covered = {(c.module, c.function) for c in cands}
        fuzz = self._fuzz_cases(modules, seeds, covered)
        results = run_probes(cases + fuzz, self._populate(context.root), backend,
                             timeout_s=max(30, self.cfg.get("testing.timeout_s", 120) // 2),
                             memory_mb=self.cfg.get("testing.memory_mb", 1024)) if backend and context.root else {}
        for i, c in enumerate(cands):
            self._confirm(f"c{i}", c, modules[c.module].funcs[c.function], results)
        generic = self._generic(fuzz, results, cands, modules) if results else []
        drafts = []
        for c in cands + generic:
            func = modules[c.module].funcs[c.function]
            sym = context.structure.symbols.get(f"{c.module}.{c.function}")
            text = self._explain(c, func, sym.called_by if sym else [])
            concept = CONCEPT.get(c.kind, "eval" if c.facts.get("sink") == "eval" else "exec" if c.facts.get("sink") == "exec" else c.kind)
            facts = {**c.facts, "kind": c.kind, "module": c.module, "evidence": c.evidence, "confirmed": c.confirmed,
                     "repairability": "requires_human" if c.kind in NEEDS_HUMAN else "fixable",
                     "human_reason": NEEDS_HUMAN.get(c.kind, "")}
            drafts.append(DraftFinding(
                title=c.title, description=text["description"] + (f"\n\nProof: {c.evidence}" if c.evidence else ""),
                file=c.file, function=c.function, line_start=c.line_start, line_end=c.line_end, category=c.category,
                severity=c.severity, confidence=c.confidence, sources=[f"core:{c.kind}"], concept=concept,
                root_cause=text["root_cause"], trigger=text["trigger"], expected_behavior=text["expected_behavior"],
                severity_reason=("Repairability: requires human intervention. " if c.kind in NEEDS_HUMAN else
                                 "Repairability: fixable. ") + f"{c.severity.title()}: " + {
                    "security": "caller-controlled data reaches a dangerous operation.",
                    "crash": "the function stops with an unhandled exception.",
                    "incorrect_output": "the function returns a wrong answer without any error.",
                    "edge_case": "boundary values are handled incorrectly.",
                    "state_corruption": "hidden shared state changes results between calls.",
                }.get(c.category, ""),
                verification="confirmed" if c.confirmed else "possible", analyzed=True, facts=facts,
            ))
        from autopatch.core_engine.learning import adjust

        for d in drafts:
            d.confidence, note = adjust(d.facts.get("kind", ""), d.confidence, d.verification == "confirmed")
            if note:
                d.severity_reason += f" {note}"
                d.facts["learned"] = note
        n_funcs = sum(len(m.funcs) for m in modules.values())
        confirmed = sum(1 for c in cands + generic if c.confirmed)
        where = f"in the {backend.label}" if backend else "(no sandbox ready, so nothing was executed)"
        summary = (f"{NAME} checked {n_funcs} functions with {len(DETECTORS)} detectors and ran {len(cases) + len(fuzz)} "
                   f"probes {where}: {confirmed} bug(s) confirmed by execution, {len(drafts) - confirmed} more suspected.")
        return drafts, summary, _usage(start)

    def analyze(self, context, finding, related):
        start = time.monotonic()
        modules, _ = self._load(context)
        c, func, _mod = self._match(finding, modules)
        if c is None:
            return RootCauseAnalysis(
                is_real_bug=finding.verification == "confirmed" or finding.confidence >= 0.6, title=finding.title,
                description=finding.description, root_cause=finding.root_cause or "Reported by static analysis.",
                trigger=finding.trigger or "See the reported line.", expected_behavior=finding.expected_behavior or "",
                related_code=[related] if related else [], impact="", category=finding.category,
                severity=finding.severity, confidence=finding.confidence, severity_reason=finding.severity_reason,
            ), _usage(start)
        sym = context.structure.symbols.get(f"{c.module}.{c.function}")
        text = self._explain(c, func, sym.called_by if sym else [])
        evidence = (finding.facts or {}).get("evidence", "")
        return RootCauseAnalysis(
            is_real_bug=True, title=c.title,
            description=text["description"] + (f"\n\nProof: {evidence}" if evidence else ""),
            root_cause=text["root_cause"], trigger=text["trigger"], expected_behavior=text["expected_behavior"],
            related_code=[f"{x} calls {c.function}" for x in (sym.called_by if sym else [])] +
                         [f"{c.function} calls {x}" for x in (sym.calls if sym else [])],
            impact=finding.impact or "", category=c.category, severity=c.severity,
            confidence=0.97 if (finding.facts or {}).get("confirmed") else c.confidence,
            severity_reason=finding.severity_reason,
        ), _usage(start)

    def write_tests(self, context, finding, test_path, import_hint, feedback=""):
        start = time.monotonic()
        self._test_path = test_path
        modules, seeds = self._load(context)
        c, func, mod = self._match(finding, modules)
        if func is None:
            raise AIError(f"{NAME} could not find {finding.function} in {finding.file}")
        s = seeds.get((func.module, func.name), [])
        types = param_types(func, s, mod)
        base = self._base(func, mod, s, types)
        cases = codegen.bug_tests(c, func, mod, base) if c else []
        # normal-input tests: record what the original code returns for inputs the bug does not touch
        f = c.facts if c else {}
        inputs = []
        for args, kwargs in s or [([], {})]:
            values = {**dict(zip(func.params, args)), **kwargs}
            if values or not func.params:
                inputs.append(values)
        if base is not None and base not in inputs:
            inputs.append(base)

        def unaffected(v: dict) -> bool:
            if not c:
                return True
            p = f.get("param")
            if c.kind in ("zero-division", "empty-input", "edge-crash"):
                return bool(v.get(p, 1))
            if c.kind == "missing-range-check":
                x = v.get(p)
                return isinstance(x, (int, float)) and (f.get("lo") is None or x >= f["lo"]) and (f.get("hi") is None or x <= f["hi"])
            if c.kind == "inclusive-threshold":
                return f.get("threshold_value") not in (v.get(f.get("list_param")) or [])
            if c.kind == "sort-direction":
                return False
            if c.kind == "code-injection":
                return INJECTION_PAYLOAD not in repr(v)
            return True

        normal = [v for v in inputs if unaffected(v)]
        if c and c.kind == "mutable-default":
            normal = [{**v, f["param"]: copy.deepcopy(codegen.ast.literal_eval(f.get("default_source", "{}")))} for v in inputs]
        boundaries = []
        if c and c.kind == "unguarded-main":
            normal = []  # the module cannot be imported safely before the fix
        if c and c.kind == "missing-range-check" and base is not None:
            for label, val in (("minimum", f.get("lo")), ("edge", f.get("hi"))):
                if val is not None:
                    boundaries.append((label, {**base, f["param"]: val}))
        probe_inputs = [("normal", v) for v in normal[:4]] + boundaries
        backend = self._backend()
        recorded = run_probes([{"id": f"n{i}", "mode": "call", "module": func.module, "func": func.name, "args": [],
                                "kwargs": _kw(v)} for i, (_, v) in enumerate(probe_inputs)],
                              self._populate(context.root), backend) if backend and probe_inputs else {}
        for i, (label, v) in enumerate(probe_inputs):
            r = recorded.get(f"n{i}")
            if r and r.get("ok") and r.get("literal"):
                name = f"test_{label}_{func.name}_{i + 1}" if label == "normal" else \
                    f"test_{label}_{f['param']}_at_{'lower' if label == 'minimum' else 'upper'}_limit_is_accepted"
                why = "Ordinary input keeps working" if label == "normal" else "The boundary value itself is valid"
                cases.append(codegen.recorded_test(func, name, "normal" if label == "normal" else label, v, r["result"], why))
        if not cases:
            raise AIError(f"{NAME} could not build tests for {func.name}: its arguments could not be inferred")
        code = codegen.assemble(func.module, func.name, cases, import_at_top=not (c and c.kind == "unguarded-main"))
        return GeneratedTests(test_code=code, cases=[GeneratedCase(name=t.name, kind=t.kind, description=t.description)
                                                     for t in cases]), _usage(start)

    def write_fix(self, context, finding, test_code, before_results, feedback=""):
        start = time.monotonic()
        modules, _ = self._load(context)
        c, func, mod = self._match(finding, modules)
        if func is None:
            raise AIError(f"{NAME} could not find {finding.function} in {finding.file}")
        if c is not None and not feedback:
            try:
                text, why, change = codegen.repair(c, func, mod)
                return FixProposal(file=func.file, edits=[CodeEdit(original_code=mod.text, patched_code=text)],
                                   explanation=why, behavior_change=change), _usage(start)
            except codegen.NoRepair:
                pass
        found = self._search(context, func, mod, test_code)
        if found is None:
            reason = "it has no fix template for this kind of issue" if c is None else "no candidate passed every test"
            raise AIError(f"{NAME} could not find a safe automatic repair ({reason}). The finding stays open for a manual fix.")
        desc, text = found
        return FixProposal(file=func.file, edits=[CodeEdit(original_code=mod.text, patched_code=text)],
                           explanation=f"Found by searching small changes to {func.name} and running every test in the "
                                       f"sandbox for each one: {desc} is the smallest change that makes all tests pass.",
                           behavior_change="Only the changed expression behaves differently."), _usage(start)

    def _search(self, context, func: Func, mod: ModuleFacts, test_code: str):
        from autopatch.testing.runner import run_pytest

        backend = self._backend()
        if backend is None or not test_code or not self._test_path:
            return None
        gen_mod = self._test_path[:-3].replace("/", ".")
        timeout = self.cfg.get("testing.timeout_s", 120)
        before = run_pytest(self._populate(context.root, {self._test_path: test_code}), backend, timeout_s=timeout)
        passing = before.passing()
        demonstrated = [t for t in before.tests if t.id.startswith(gen_mod) and t.kind == "reproduce" and t.status == "failed"]
        if not demonstrated:
            # Without a test that shows the bug, any change that keeps the other tests green would "pass".
            # That is how wrong fixes slip through, so the search refuses to run.
            return None
        for desc, text in codegen.mutations(func, mod, limit=25):
            after = run_pytest(self._populate(context.root, {func.file: text, self._test_path: test_code}), backend,
                               timeout_s=timeout)
            gen = [t for t in after.tests if t.id.startswith(gen_mod)]
            if gen and all(t.status == "passed" for t in gen) and passing <= after.passing():
                return desc, text
        return None


def modules_func(modules, module, name):
    mod = modules.get(module)
    return (mod.funcs.get(name), mod) if mod else (None, None)


def _lit(text: str):
    import ast

    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text
