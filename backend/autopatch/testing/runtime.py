"""A private copy of Python used only inside the sandbox.

The sandboxed process is allowed to read this folder and nothing of the user's own Python
install, virtualenv or files. It contains the standard library plus pytest and hypothesis.
"""

from __future__ import annotations

import json
import shutil
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path, PurePosixPath

from packaging.requirements import Requirement

from autopatch.config import paths

PACKAGES = ["pytest", "hypothesis"]
_SKIP_LIB = {"site-packages", "idlelib", "tkinter", "turtledemo", "ensurepip", "test", "lib2to3"}
MARKER = "runtime.json"


def python_exe(runtime: Path | None = None) -> Path:
    root = runtime or paths().runtime
    return root / ("python.exe" if sys.platform == "win32" else "bin/python3")


def status() -> dict:
    root = paths().runtime
    marker = root / MARKER
    if not marker.exists() or not python_exe(root).exists():
        return {"ready": False, "path": str(root)}
    info = json.loads(marker.read_text(encoding="utf-8"))
    ready = info.get("python") == sys.version.split()[0]
    return {"ready": ready, "path": str(root), **info, **({} if ready else {"stale": True})}


def _closure(names: list[str]) -> list:
    seen: dict[str, object] = {}
    queue = list(names)
    while queue:
        name = queue.pop()
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue
        seen[key] = dist
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                queue.append(req.name)
    return list(seen.values())


def build(log=lambda msg: None) -> dict:
    """Copy the interpreter, standard library and test packages into the runtime folder."""
    if sys.platform != "win32":
        raise RuntimeError("The private runtime is only needed on Windows; use Docker elsewhere")
    root = paths().runtime
    base = Path(sys.base_prefix)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)

    log(f"Copying the Python {sys.version.split()[0]} interpreter")
    for item in base.iterdir():
        if item.is_file() and item.suffix.lower() in (".exe", ".dll") and not item.name.lower().startswith(("idle", "pip")):
            shutil.copy2(item, root / item.name)
    shutil.copytree(base / "DLLs", root / "DLLs", ignore=shutil.ignore_patterns("tcl*", "tk*", "_tkinter*"))

    log("Copying the standard library")
    (root / "Lib").mkdir()
    for item in (base / "Lib").iterdir():
        if item.name in _SKIP_LIB:
            continue
        if item.is_dir():
            shutil.copytree(item, root / "Lib" / item.name)
        else:
            shutil.copy2(item, root / "Lib" / item.name)

    site = root / "Lib" / "site-packages"
    site.mkdir()
    installed = {}
    for dist in _closure(PACKAGES):
        name = dist.metadata["Name"]
        log(f"Adding {name} {dist.version}")
        for f in dist.files or []:
            rel = PurePosixPath(str(f).replace("\\", "/"))
            if ".." in rel.parts or rel.suffix in (".pyc",) or (rel.parts and rel.parts[0] == "__pycache__"):
                continue
            src = Path(dist.locate_file(f))
            if not src.is_file():
                continue
            dest = site / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        installed[name] = dist.version

    info = {"python": sys.version.split()[0], "packages": installed}
    (root / MARKER).write_text(json.dumps(info, indent=2), encoding="utf-8")
    log("Private runtime ready")
    return status()
