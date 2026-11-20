"""Run project functions inside the sandbox to confirm suspected bugs and to discover crashes.

A probe is one call (or a pair of calls) with chosen arguments. The harness writes one JSON line
per probe as it goes, so a probe that hangs is detected and the others still report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from autopatch.testing.sandbox import Sandbox, execute, new_run_dir

INJECTION_PAYLOAD = "__import__('builtins').__dict__.update(AUTOPATCH_PWNED=1)"
LEAK_MARKERS = ("autopatch_first_call", "autopatch_second_call")

HARNESS = r'''
import ast, builtins, copy, importlib, json, os, sys, traceback
ROOT = os.path.abspath(".")
extras = [ROOT, os.path.join(ROOT, "src")]
try:
    for name in sorted(os.listdir(ROOT)):
        path = os.path.join(ROOT, name)
        if not os.path.isdir(path) or name.startswith(".") or name in ("tests", "test", "node_modules", "__pycache__", "venv", ".venv"):
            continue
        if any(fn.endswith(".py") for fn in os.listdir(path)) or os.path.exists(os.path.join(path, "__init__.py")):
            extras.append(path)
except OSError:
    pass
for extra in extras:
    if extra not in sys.path:
        sys.path.insert(0, extra)
OUT = os.path.join("..", "out", "probe.jsonl")
CASES = json.load(open(os.path.join("..", "out", "cases.json"), encoding="utf-8"))
DONE = set()
if os.path.exists(OUT):
    for line in open(OUT, encoding="utf-8"):
        try:
            DONE.add(json.loads(line)["id"])
        except Exception:
            pass

def literal(v):
    try:
        ast.literal_eval(repr(v))
        return True
    except Exception:
        return False

def emit(rec):
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")

def call(case, args, kwargs):
    mod = importlib.import_module(case["module"])
    fn = getattr(mod, case["func"])
    return fn(*[ast.literal_eval(a) for a in args], **{k: ast.literal_eval(v) for k, v in kwargs.items()})

for case in CASES:
    if case["id"] in DONE:
        continue
    emit({"id": case["id"], "started": True})
    rec = {"id": case["id"]}
    try:
        if case["mode"] == "inject":
            if hasattr(builtins, "AUTOPATCH_PWNED"):
                delattr(builtins, "AUTOPATCH_PWNED")
            try:
                call(case, case["args"], case["kwargs"])
                rec["raised"] = None
            except Exception as e:
                rec["raised"] = type(e).__name__
            rec["executed"] = bool(getattr(builtins, "AUTOPATCH_PWNED", False))
            rec["ok"] = True
        elif case["mode"] == "twice":
            importlib.reload(importlib.import_module(case["module"]))
            call(case, case["args"], case["kwargs"])
            second = call(case, case["args2"], case["kwargs2"])
            rec["result"] = repr(second)[:500]
            rec["leak"] = case["marker"] in repr(second)
            rec["ok"] = True
        elif case["mode"] == "expr":
            import contextlib, io
            mod = importlib.import_module(case["module"])
            env = dict(vars(mod))
            kwargs = {k: eval(v, env) for k, v in case["kwargs"].items()}
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                out = getattr(mod, case["func"])(**kwargs)
            rec["ok"] = True
            rec["result"] = repr(out)[:500]
            rec["stdout"] = buf.getvalue()[:2000]
        else:
            out = call(case, case["args"], case["kwargs"])
            rec["ok"] = True
            rec["result"] = repr(out)[:500]
            rec["literal"] = literal(out)
    except BaseException as e:
        frames = traceback.extract_tb(e.__traceback__)
        project = [f for f in frames if os.path.abspath(f.filename).startswith(ROOT)
                   and not f.filename.endswith("_autopatch_probe.py")]
        last = project[-1] if project else (frames[-1] if frames else None)
        rec.update({
            "ok": False, "exc": type(e).__name__, "msg": str(e)[:300],
            "explicit": bool(last and (last.line or "").lstrip().startswith("raise")),
            "file": os.path.relpath(last.filename, ROOT).replace(os.sep, "/") if last else "",
            "line": last.lineno if last else 0,
            "harness_error": not project,
        })
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            rec["exc"] = type(e).__name__
    emit(rec)
'''


def run_probes(cases: list[dict], populate: Callable[[Path], None], backend: Sandbox,
               timeout_s: float = 60, memory_mb: int = 1024, passes: int = 3) -> dict[str, dict]:
    """Run cases in the sandbox. Returns {case_id: result}; hung cases get {"ok": False, "exc": "Timeout"}."""
    if not cases:
        return {}
    import shutil

    run_dir = new_run_dir()
    try:
        populate(run_dir / "project")
        (run_dir / "out" / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
        (run_dir / "project" / "_autopatch_probe.py").write_text(HARNESS, encoding="utf-8")
        results: dict[str, dict] = {}
        for _ in range(passes):
            res = execute(backend, run_dir, ["_autopatch_probe.py"], timeout_s, memory_mb)
            started: list[str] = []
            out = run_dir / "out" / "probe.jsonl"
            if out.exists():
                for line in out.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("started"):
                        started.append(rec["id"])
                    else:
                        results[rec["id"]] = rec
            hung = [cid for cid in started if cid not in results]
            for cid in hung:  # the probe that was running when the sandbox killed the harness
                rec = {"id": cid, "ok": False, "exc": "Timeout", "msg": f"did not finish within {timeout_s}s",
                       "explicit": False, "harness_error": False}
                results[cid] = rec
                with open(out, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")
            if not res.timed_out or len(results) >= len(cases):
                break
        return results
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
