"""Universal file intelligence: identification by content, integrity checks, repairability, repairs."""

from __future__ import annotations

import base64
import io
import struct
import sys
import zipfile
import zlib

import pytest
from sqlmodel import select

from autopatch.core import jobs, settings
from autopatch.core.workspace import Workspace
from autopatch.db import FileReport, Finding, Patch, session
from autopatch.fileintel.identify import identify
from autopatch.fileintel.inspect import inspect
from autopatch.pipeline import fix, history, scan
from autopatch.pipeline.projects import create_project


def png(width=1, height=1, corrupt_crc=False) -> bytes:
    def chunk(t, body):
        crc = zlib.crc32(t + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", crc ^ (1 if corrupt_crc and t == b"IHDR" else 0))
    raw = b"\x00" + b"\xff\x00\x00" * width
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw * height)) + chunk(b"IEND", b""))


def mixed_project(root):
    files = {
        "config/settings.json": b'{\n  "name": "demo",\n  "debug": true,\n}\n',
        "config/tsconfig.json": b'{\n  // comments are allowed here\n  "compilerOptions": {"strict": true,},\n}\n',
        "deploy.yaml": b"service:\n\tname: web\n\tport: 80\n",
        "pyproject.toml": b'[project]\nname = "demo"\n',
        "broken.toml": b"[project\nname = 1\n",
        "layout.xml": b"<root><item></root>",
        ".env.production": b"DATABASE_URL=postgres://localhost/db\nAPI_SECRET_KEY=9f8e7d6c5b4a39281706\nAPI_SECRET_KEY=again\n",
        "images/logo.png": png(corrupt_crc=True),
        "images/ok.png": png(),
        "images/photo.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 100,
        "data/table.csv": b"a,b,c\n1,2,3\n4,5\n",
        "web/app.ts": b"export function f(x: number) {\n  return (x + 1;\n}\n",
        "blob.bin": bytes(range(256)) * 4,
        "fake.png": b"this is actually text\n",
        "notes/legacy.txt": "caf\xe9 cr\xe8me\n".encode("cp1252"),
        "main.py": b"def add(a, b):\n    return a + b\n",
    }
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "hello")
    (root / "bundle.zip").write_bytes(buf.getvalue())
    (root / "broken.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    return root


def test_identification_uses_content_not_extension(tmp_path):
    root = mixed_project(tmp_path / "p")
    assert identify(root, "images/logo.png").format == "PNG image"
    fake = identify(root, "fake.png")
    assert fake.is_text and "suggests binary" in fake.extension_mismatch
    assert identify(root, "blob.bin").category == "binary"
    assert identify(root, "notes/legacy.txt").details["codec"] == "cp1252"
    assert identify(root, "web/app.ts").language == "TypeScript"
    assert identify(root, ".env.production").category == "config"
    assert identify(root, "bundle.zip").format == "ZIP archive"


def test_inspectors_and_repairability(tmp_path):
    root = mixed_project(tmp_path / "p")

    def check(rel):
        ident = identify(root, rel)
        return inspect(root, ident, 10**8)

    j = check("config/settings.json")
    assert j.issues[0].code == "json.invalid" and j.issues[0].repairability == "fixable"
    assert check("config/tsconfig.json").issues == []  # comments and trailing commas are legal in tsconfig
    y = check("deploy.yaml")
    assert y.issues[0].code == "yaml.invalid" and y.issues[0].repair == "yaml_tabs"
    assert check("broken.toml").issues[0].repairability == "requires_human"
    assert check("layout.xml").issues[0].code == "xml.invalid"
    env = {i.code for i in check(".env.production").issues}
    assert {"security.env-secret", "env.duplicate"} <= env
    assert check("images/logo.png").issues[0].code == "png.crc"
    assert check("images/ok.png").issues == []
    assert check("images/photo.jpg").issues[0].repairability == "partially_fixable"
    assert check("data/table.csv").issues[0].code == "csv.ragged"
    ts = check("web/app.ts")
    assert ts.status == "partially_analyzed" and ts.issues[0].code == "source.brackets"
    assert "not available yet" in ts.notes[0]
    unknown = check("blob.bin")
    assert unknown.status == "identified_only" and unknown.issues[0].repairability == "unsupported"
    assert check("broken.zip").issues[0].repairability == "not_fixable"
    assert check("bundle.zip").issues == []
    assert check("notes/legacy.txt").issues[0].code == "encoding.not-utf8"


@pytest.mark.skipif(sys.platform != "win32", reason="uses the AppContainer sandbox")
def test_universal_scan_repairs_and_report(data_dir, tmp_path):
    from autopatch.core.files import copy_tree
    from autopatch.testing import sandbox

    sandbox.get_backend("appcontainer").prepare()
    settings.update({"testing.sandbox": "appcontainer"})
    src = mixed_project(tmp_path / "mixed")
    (src / "node_modules" / "lib").mkdir(parents=True)
    (src / "node_modules" / "lib" / "index.js").write_text("x")
    project = create_project(None, "path", str(src), lambda d: ("mixed", copy_tree(src, d)))
    skipped = project.import_report["skipped"]
    assert any(s["path"] == "node_modules/" and s["files"] == 1 for s in skipped)  # reported, not silent

    s_row, job = scan.start_scan(project.id, {})
    done = jobs.wait(job.id, 300)
    assert done.status == "succeeded", done.error
    with session() as s:
        reports = {r.path: r for r in s.exec(select(FileReport).where(FileReport.scan_id == s_row.id))}
        findings = s.exec(select(Finding).where(Finding.scan_id == s_row.id)).all()
    assert len(reports) == 18 and reports["blob.bin"].status == "identified_only"
    json_finding = next(f for f in findings if f.file == "config/settings.json")
    png_finding = next(f for f in findings if f.file == "images/logo.png")

    patch, job = fix.start_fix(json_finding.id)
    assert jobs.wait(job.id, 120).status == "succeeded"
    with session() as s:
        p = s.get(Patch, patch.id)
    assert p.validation == "validated" and '-  "debug": true,' in p.diff and "+  \"debug\": true" in p.diff
    history.accept_patch(p.id)
    assert Workspace(project.id).read_text("config/settings.json") == '{\n  "name": "demo",\n  "debug": true\n}\n'

    patch, job = fix.start_fix(png_finding.id)
    assert jobs.wait(job.id, 120).status == "succeeded"
    with session() as s:
        p = s.get(Patch, patch.id)
    assert p.validation == "partially_validated"  # checksums fixed; pixel data cannot be verified
    history.accept_patch(p.id)
    fixed = Workspace(project.id).read_bytes("images/logo.png")
    assert fixed == png() and base64.b64decode(p.validation_detail["after_b64"]) == fixed

    toml_finding = next(f for f in findings if f.file == "broken.toml")
    with pytest.raises(fix.FixError, match="requires human"):
        fix.start_fix(toml_finding.id)

    # incremental: a second scan reuses reports for unchanged files
    s2, job = scan.start_scan(project.id, {})
    jobs.wait(job.id, 300)
    with session() as s:
        again = {r.path: r for r in s.exec(select(FileReport).where(FileReport.scan_id == s2.id))}
    assert again["blob.bin"].reused and not again["config/settings.json"].reused
