"""Run pytest for a project inside the sandbox and collect structured results."""

from __future__ import annotations

import configparser
import re
import shutil
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from autopatch.testing.sandbox import Sandbox, execute, new_run_dir

TEST_KINDS = ("reproduce", "normal", "invalid", "minimum", "maximum", "edge")


@dataclass
class CaseResult:
    id: str
    name: str
    file: str
    status: str  # passed | failed | error | skipped
    message: str = ""
    kind: str = ""  # for generated tests: reproduce | normal | invalid | minimum | maximum | edge


@dataclass
class RunOutcome:
    status: str  # passed | failed | error | no_tests | timeout | crashed
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    tests: list[CaseResult] = field(default_factory=list)
    output: str = ""
    sandbox: str = ""
    peak_memory_mb: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def by_id(self) -> dict[str, CaseResult]:
        return {t.id: t for t in self.tests}

    def passing(self) -> set[str]:
        return {t.id for t in self.tests if t.status == "passed"}


def kind_of(test_name: str) -> str:
    m = re.match(r"test_(" + "|".join(TEST_KINDS) + r")(_|$)", test_name)
    return m.group(1) if m else ""


def _has_pytest_config(project: Path) -> str | None:
    for name in ("pytest.ini", ".pytest.ini"):
        if (project / name).is_file():
            return name
    pyproject = project / "pyproject.toml"
    if pyproject.is_file():
        try:
            if "ini_options" in tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {}).get("pytest", {}):
                return "pyproject.toml"
        except tomllib.TOMLDecodeError:
            pass
    for name, section in (("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        if (project / name).is_file():
            cp = configparser.ConfigParser()
            try:
                cp.read(project / name, encoding="utf-8")
            except configparser.Error:
                continue
            if cp.has_section(section):
                return name
    return None


def _parse_junit(path: Path) -> list[CaseResult]:
    results: list[CaseResult] = []
    if not path.exists():
        return results
    for case in ET.parse(path).getroot().iter("testcase"):
        classname, name = case.get("classname", ""), case.get("name", "")
        status, message = "passed", ""
        for child in case:
            if child.tag in ("failure", "error", "skipped"):
                status = {"failure": "failed", "error": "error", "skipped": "skipped"}[child.tag]
                message = (child.get("message") or "").strip()
                detail = (child.text or "").strip()
                if detail:
                    message = (message + "\n" + detail).strip()
                message = message[-3000:]
                break
        file = case.get("file") or (classname.replace(".", "/") + ".py" if classname else "")
        results.append(CaseResult(
            id=f"{classname}::{name}" if classname else name, name=name, file=file,
            status=status, message=message, kind=kind_of(name),
        ))
    return results


def run_pytest(
    populate: Callable[[Path], None],
    backend: Sandbox,
    *,
    timeout_s: float = 120,
    memory_mb: int = 1024,
    targets: list[str] | None = None,
    keep: bool = False,
) -> RunOutcome:
    """Copy the project into a fresh sandbox folder (via populate) and run its tests there."""
    run_dir = new_run_dir()
    try:
        project = run_dir / "project"
        populate(project)
        # Nested app folders (library_app/, …) must be on pythonpath for flat imports.
        pkg_dirs = [
            child.name for child in sorted(project.iterdir())
            if child.is_dir() and not child.name.startswith(".") and any(child.glob("*.py"))
        ]
        config = _has_pytest_config(project)
        if config:
            cfg_arg = "{run}/project/" + config
        else:
            ini = "[pytest]\npythonpath =\n    .\n" + "".join(f"    {d}\n" for d in pkg_dirs)
            (run_dir / "pytest-autopatch.ini").write_text(ini, encoding="utf-8")
            cfg_arg = "{run}/pytest-autopatch.ini"
        args = [
            "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-c", cfg_arg, "--rootdir", "{run}/project", "--confcutdir", "{run}/project",
            "--junitxml", "{run}/out/junit.xml", "-o", "junit_family=xunit2",
            "-o", "console_output_style=classic", "--color=no",
            *(targets or []),
        ]
        res = execute(backend, run_dir, args, timeout_s, memory_mb)
        tests = _parse_junit(run_dir / "out" / "junit.xml")
        counts = {s: sum(1 for t in tests if t.status == s) for s in ("passed", "failed", "error", "skipped")}
        if res.timed_out:
            status = "timeout"
        elif res.exit_code == 5:
            status = "no_tests"
        elif res.exit_code == 0 and not counts["failed"] and not counts["error"]:
            status = "passed"
        elif res.exit_code == 1:
            status = "failed"
        elif res.exit_code == 2:
            status = "error"  # interrupted, usually collection errors (e.g. import failure)
        else:
            status = "crashed"
        return RunOutcome(
            status=status, passed=counts["passed"], failed=counts["failed"], errors=counts["error"],
            skipped=counts["skipped"], duration_s=res.duration_s, tests=tests, output=res.output,
            sandbox=backend.name, peak_memory_mb=res.peak_memory_mb,
        )
    finally:
        if not keep:
            shutil.rmtree(run_dir, ignore_errors=True)
