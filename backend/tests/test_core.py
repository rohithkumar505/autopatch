from __future__ import annotations

import io
import zipfile

import pytest

from autopatch.core.files import ProjectImportError, extract_zip, safe_relpath, save_uploaded_files
from autopatch.core.workspace import GitError, Workspace


def test_safe_relpath_rejects_escapes():
    assert str(safe_relpath("a\\b/./c.py")) == "a/b/c.py"
    for bad in ["../x.py", "/etc/passwd", "C:/x.py", "a/../../b", ""]:
        with pytest.raises(ProjectImportError):
            safe_relpath(bad)


def test_folder_upload_strips_top_folder(tmp_path):
    count, top = save_uploaded_files(
        [("proj/a.py", b"x = 1\n"), ("proj/pkg/b.py", b"y = 2\n"), ("proj/__pycache__/a.pyc", b"\0")],
        tmp_path,
    )
    assert (count, top) == (2, "proj")
    assert (tmp_path / "pkg" / "b.py").read_text() == "y = 2\n"
    assert not (tmp_path / "__pycache__").exists()


def test_zip_extract_and_traversal_guard(tmp_path):
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("project/main.py", "print('hi')\n")
        z.writestr("project/.git/config", "x")
    out = tmp_path / "out"
    assert extract_zip(good, out) == (1, "project")
    assert (out / "main.py").exists()

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.py", "boom")
    with pytest.raises(ProjectImportError):
        extract_zip(bad, tmp_path / "out2")


def test_workspace_versions_restore_and_revert(data_dir, sample_copy):
    ws = Workspace.create(1, sample_copy)
    original = ws.head()
    assert "grades.py" in ws.list_files()
    assert ws.versions()[0].message == "Original upload"

    before = ws.read_text("grades.py")
    ws.write_text("grades.py", before.replace("MAX_MARKS = 100", "MAX_MARKS = 100  # checked"))
    v2 = ws.commit_all("Apply patch #1")
    assert v2 and v2 != original
    assert "# checked" in ws.commit_diff(v2)
    assert ws.read_text("grades.py", original) == before  # earlier version still readable

    ws.write_text("tests/test_new.py", "def test_x():\n    assert True\n")
    v3 = ws.commit_all("Add tests")
    assert "tests/test_new.py" in ws.list_files(v3)

    restored = ws.restore(original, "Restore original")
    assert restored is not None
    assert ws.read_text("grades.py") == before
    assert "tests/test_new.py" not in ws.list_files()  # files added later are removed too

    # Re-apply v2's change by reverting the restore commit.
    again = ws.revert(restored, "Undo restore")
    assert "# checked" in ws.read_text("grades.py")
    assert len(ws.versions()) == 5
    with pytest.raises(GitError):
        ws.resolve("does-not-exist")
    assert zipfile.ZipFile(io.BytesIO(ws.archive_zip(again))).namelist()


def test_workspace_rejects_path_escape(data_dir, sample_copy):
    ws = Workspace.create(2, sample_copy)
    with pytest.raises(ProjectImportError):
        ws.read_text("../../etc/passwd")


def test_jobs_report_progress_and_errors(data_dir):
    from autopatch.core import jobs

    def work(ctx):
        ctx.log("step one")
        return {"answer": 42}

    def broken(ctx):
        ctx.log("about to fail")
        raise ValueError("bad input")

    ok = jobs.wait(jobs.submit("demo", None, work).id, 10)
    assert ok.status == "succeeded" and ok.result == {"answer": 42}
    assert [p["message"] for p in ok.progress] == ["step one"]

    bad = jobs.wait(jobs.submit("demo", None, broken).id, 10)
    assert bad.status == "failed" and bad.error == "bad input"
