"""HTTP API tests through FastAPI's TestClient, against the real pipeline and sandbox."""

from __future__ import annotations

import io
import sys
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from autopatch.ai.engine import Engine
from autopatch.ai.schemas import FixProposal
from autopatch.pipeline import fix, scan
from autopatch.testing import sandbox
from test_pipeline import GOOD_EDIT, ScriptedProvider

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="uses the AppContainer sandbox")


@pytest.fixture()
def client(data_dir):
    from autopatch.main import app

    with TestClient(app) as c:
        yield c


def wait_job(client, job_id, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.2)
    raise TimeoutError(job_id)


def upload_sample(client, sample_copy):
    parts = []  # what a browser folder upload sends: each file plus its relative path
    for f in sorted(sample_copy.rglob("*.py")):
        parts.append(("files", (f.name, f.read_bytes(), "text/x-python")))
        parts.append(("paths", (None, "marks_calculator/" + f.relative_to(sample_copy).as_posix())))
    r = client.post("/api/projects/upload", files=parts)
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_explore_scan_and_settings(client, sample_copy):
    project = upload_sample(client, sample_copy)
    assert project["name"] == "marks_calculator" and project["source_type"] == "folder"
    assert project["languages"] == {"Python": 2}
    pid = project["id"]

    tree = client.get(f"/api/projects/{pid}/tree").json()
    names = [c["name"] for c in tree["root"]["children"]]
    assert names == ["tests", "grades.py"]  # folders first

    file = client.get(f"/api/projects/{pid}/file", params={"path": "grades.py"}).json()
    assert file["monaco_language"] == "python" and "def get_grade" in file["content"]
    assert client.get(f"/api/projects/{pid}/file", params={"path": "../x.py"}).status_code == 400

    structure = client.get(f"/api/projects/{pid}/structure").json()
    assert any(s["qualname"] == "grades.get_grade" for s in structure["symbols"])

    # static-only scan works without any AI configured
    r = client.post(f"/api/projects/{pid}/scans", json={"ai": False})
    assert wait_job(client, r.json()["job_id"])["status"] == "succeeded"
    findings = client.get(f"/api/projects/{pid}/findings").json()["findings"]
    assert findings[0]["function"] == "parse_marks" and findings[0]["severity"] == "high"
    detail = client.get(f"/api/findings/{findings[0]['id']}").json()
    assert "eval" in detail["code_snippet"]

    # AI actions explain clearly what is missing instead of failing silently
    client.put("/api/settings", json={"ai.provider": "none"})
    r = client.post(f"/api/findings/{findings[0]['id']}/fix")
    assert r.status_code == 400 and "turned off" in r.json()["detail"]

    assert client.put("/api/settings", json={"testing.timeout_s": 5}).status_code == 400
    assert client.put("/api/settings", json={"ai.effort.fix": "extreme"}).status_code == 400
    s = client.put("/api/settings", json={"ai.anthropic.api_key": "sk-ant-test-1234"}).json()
    assert s["ai.anthropic.api_key"] == {"set": True, "hint": "…1234"}  # never echoed back in full

    dash = client.get("/api/dashboard").json()
    assert dash["projects"] == 1 and dash["findings"]["detected"] == len(findings)
    assert client.get("/api/languages").json()[0]["language"] == "Python"
    assert {b["name"] for b in client.get("/api/sandbox").json()["backends"]} == {"docker", "appcontainer", "local"}

    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{pid}/download").content))
    assert "grades.py" in z.namelist()
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_zip_upload_fix_review_and_history(client, sample_copy, monkeypatch):
    sandbox.get_backend("appcontainer").prepare()
    client.put("/api/settings", json={"testing.sandbox": "appcontainer", "ai.provider": "anthropic"})
    provider = ScriptedProvider([FixProposal(file="grades.py", edits=[GOOD_EDIT], explanation="Range check.",
                                             behavior_change="Raises ValueError.")])
    monkeypatch.setattr(scan, "get_engine", lambda cfg: Engine(provider))
    monkeypatch.setattr(fix, "get_engine", lambda cfg: Engine(provider))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in sample_copy.rglob("*.py"):
            z.writestr("marks/" + f.relative_to(sample_copy).as_posix(), f.read_text())
    r = client.post("/api/projects/upload-zip", files={"file": ("marks.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    wait_job(client, client.post(f"/api/projects/{pid}/scans", json={"pylint": False}).json()["job_id"])
    findings = client.get(f"/api/projects/{pid}/findings").json()["findings"]
    target = next(f for f in findings if f["function"] == "get_grade")

    r = client.post(f"/api/findings/{target['id']}/fix").json()
    job = wait_job(client, r["job_id"])
    assert job["status"] == "succeeded", job
    assert any("Bug confirmed" in p["message"] for p in job["progress"])

    patch = client.get(f"/api/patches/{r['patch_id']}").json()
    assert patch["validation"] == "validated" and not patch["stale"]
    assert patch["before_content"] != patch["after_content"]
    assert [t["kind"] for t in patch["test_runs"]] == ["original", "patched"]

    accepted = client.post(f"/api/patches/{patch['id']}/accept", json={"include_tests": True}).json()
    assert accepted["status"] == "accepted"
    versions = client.get(f"/api/projects/{pid}/versions").json()
    assert versions[0]["patch"]["id"] == patch["id"] and versions[0]["is_head"]
    assert "grades.py" in versions[0]["files_changed"]
    assert "raise ValueError" in client.get(f"/api/projects/{pid}/versions/{versions[0]['sha']}/diff").json()["diff"]

    run = wait_job(client, client.post(f"/api/projects/{pid}/test-runs", json={}).json()["job_id"])
    assert run["status"] == "succeeded"
    runs = client.get(f"/api/projects/{pid}/test-runs").json()
    assert runs[0]["kind"] == "project" and runs[0]["status"] == "passed" and runs[0]["total"] == 12

    original = versions[-1]["sha"]
    assert client.post(f"/api/projects/{pid}/restore", json={"version": original}).json()["changed"]
    assert client.get(f"/api/patches/{patch['id']}").json()["status"] == "reverted"
    kinds = [e["kind"] for e in client.get(f"/api/projects/{pid}/events").json()]
    assert kinds[:3] == ["version_restored", "tests_run", "patch_accepted"]
    dash = client.get("/api/dashboard", params={"project_id": pid}).json()
    assert dash["patches"]["validated"] == 1 and dash["tests"]["runs"] >= 3
