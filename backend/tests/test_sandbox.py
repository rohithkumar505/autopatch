"""These tests run real code inside the real sandbox (AppContainer on Windows)."""

from __future__ import annotations

import shutil
import sys

import pytest

from autopatch.testing import sandbox
from autopatch.testing.runner import run_pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="AppContainer is Windows-only")


@pytest.fixture()
def appcontainer(data_dir):
    backend = sandbox.get_backend("appcontainer")
    backend.prepare()
    return backend


def test_self_test_proves_isolation(appcontainer):
    result = sandbox.self_test(appcontainer)
    assert result["passed"], result
    names = {c["check"]: c["result"] for c in result["checks"]}
    assert names["read a file outside the sandbox"] == "blocked"
    assert names["open an internet connection"] == "blocked"
    assert names["write inside the sandbox"] == "allowed"


def test_runs_sample_tests_and_reports_each_case(appcontainer, sample_copy):
    outcome = run_pytest(lambda dest: shutil.copytree(sample_copy, dest, dirs_exist_ok=True), appcontainer)
    assert outcome.status == "passed", outcome.output
    assert outcome.passed == 7 and outcome.sandbox == "appcontainer"
    assert {t.name for t in outcome.tests} >= {"test_grade_for_normal_marks", "test_parse_marks"}


def test_failures_timeouts_and_reach_back_are_contained(appcontainer, tmp_path):
    def populate(dest):
        (dest / "test_bad.py").write_text(
            "import socket, time\n"
            "def test_reproduce_fails():\n    assert 1 + 1 == 3\n"
            "def test_normal_network_blocked():\n"
            "    try:\n        socket.create_connection(('127.0.0.1', 8000), timeout=1)\n"
            "    except OSError:\n        return\n"
            "    raise AssertionError('network reachable')\n",
            encoding="utf-8",
        )
    outcome = run_pytest(populate, appcontainer)
    assert outcome.status == "failed"
    by_name = {t.name: t for t in outcome.tests}
    assert by_name["test_reproduce_fails"].status == "failed" and by_name["test_reproduce_fails"].kind == "reproduce"
    assert "== 3" in by_name["test_reproduce_fails"].message and "test_bad.py:3" in by_name["test_reproduce_fails"].message
    assert by_name["test_normal_network_blocked"].status == "passed"

    def hang(dest):
        (dest / "test_hang.py").write_text("def test_forever():\n    while True:\n        pass\n", encoding="utf-8")
    assert run_pytest(hang, appcontainer, timeout_s=8).status == "timeout"


def test_choose_refuses_limited_isolation_unless_allowed(data_dir, monkeypatch):
    monkeypatch.setattr(sandbox.DockerSandbox, "info",
                        lambda self: sandbox.SandboxInfo("docker", "Docker", "strong", False, False, "absent"))
    monkeypatch.setattr(sandbox.AppContainerSandbox, "info",
                        lambda self: sandbox.SandboxInfo("appcontainer", "AC", "strong", True, False, "not set up"))
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.choose("auto", allow_limited=False)
    assert sandbox.choose("auto", allow_limited=True).name == "local"
