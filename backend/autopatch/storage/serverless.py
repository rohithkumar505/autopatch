"""Shared state for Vercel: SQLite + project workspaces in Blob storage.

Lambda instances do not share /tmp. We pack the data directory into one snapshot
object in Vercel Blob and refresh it on each API request.
"""

from __future__ import annotations

import io
import logging
import os
import sqlite3
import tarfile
import threading
from pathlib import Path

import httpx
from fastapi import HTTPException

from autopatch.config import paths
from autopatch.db import reset_engine

log = logging.getLogger("autopatch.storage")

_SNAPSHOT = "autopatch/snapshot.tar.gz"
_BLOB_API = "https://blob.vercel-storage.com"
_lock = threading.Lock()
_remote_etag: str | None = None


def _serverless() -> bool:
    return os.environ.get("AUTOPATCH_SERVERLESS") == "1" or os.environ.get("VERCEL") == "1"


def serverless_persistence_enabled() -> bool:
    return _serverless() and bool(os.environ.get("BLOB_READ_WRITE_TOKEN"))


def persistence_status() -> dict:
    if not _serverless():
        return {"mode": "local", "ready": True}
    token = bool(os.environ.get("BLOB_READ_WRITE_TOKEN"))
    store = os.environ.get("BLOB_STORE_ID", "")
    return {
        "mode": "vercel_blob",
        "ready": token,
        "store_id": store or None,
        "message": None if token else "Blob storage is not linked — imports cannot persist on Vercel.",
    }


def require_persistence() -> None:
    if _serverless() and not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        raise HTTPException(
            503,
            "Serverless storage is not configured. Link a Vercel Blob store to this project.",
        )


def _headers() -> dict[str, str]:
    token = os.environ["BLOB_READ_WRITE_TOKEN"]
    return {"authorization": f"Bearer {token}", "x-api-version": "7"}


def _head_snapshot() -> str | None:
    if not serverless_persistence_enabled():
        return None
    pathname = _SNAPSHOT.lstrip("/")
    try:
        r = httpx.head(f"{_BLOB_API}/{pathname}", headers=_headers(), timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.headers.get("etag")
    except httpx.HTTPError as exc:
        log.warning("blob head failed: %s", exc)
        return None


def _download_snapshot() -> bytes | None:
    pathname = _SNAPSHOT.lstrip("/")
    r = httpx.get(f"{_BLOB_API}/{pathname}", headers=_headers(), timeout=120)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def _upload_snapshot(data: bytes) -> None:
    pathname = _SNAPSHOT.lstrip("/")
    r = httpx.put(
        f"{_BLOB_API}/{pathname}",
        headers={
            **_headers(),
            "content-type": "application/gzip",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        },
        content=data,
        timeout=180,
    )
    r.raise_for_status()


def _checkpoint_db(db_path: Path) -> None:
    if not db_path.is_file():
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def _pack_data(root: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if not root.is_dir():
            return buf.getvalue()
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix in (".pyc",) or "__pycache__" in path.parts:
                continue
            arc = path.relative_to(root).as_posix()
            tar.add(path, arcname=arc)
    return buf.getvalue()


def _unpack_data(root: Path, payload: bytes) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        tar.extractall(root)  # noqa: S202 — trusted own snapshot only
    for wal in root.glob("*.db-wal"):
        wal.unlink(missing_ok=True)
    for shm in root.glob("*.db-shm"):
        shm.unlink(missing_ok=True)


def sync_before_request() -> None:
    """Load latest shared state before handling an API request."""
    if not serverless_persistence_enabled():
        paths().ensure()
        return
    global _remote_etag
    with _lock:
        etag = _head_snapshot()
        data_dir = paths().data
        if etag and etag == _remote_etag and (data_dir / "autopatch.db").is_file():
            return
        payload = _download_snapshot()
        if payload:
            if data_dir.exists():
                for child in list(data_dir.iterdir()):
                    if child.name == "sandbox":
                        continue
                    if child.is_dir():
                        import shutil

                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
            _unpack_data(data_dir, payload)
            _remote_etag = etag
        else:
            paths().ensure()
        reset_engine()
        from autopatch.db import get_engine

        get_engine()


def sync_after_request() -> None:
    if not serverless_persistence_enabled():
        return
    global _remote_etag
    with _lock:
        data = paths().data
        _checkpoint_db(data / "autopatch.db")
        try:
            payload = _pack_data(data)
            _upload_snapshot(payload)
            _remote_etag = _head_snapshot()
        except httpx.HTTPError as exc:
            log.error("blob upload failed: %s", exc)
            raise HTTPException(503, f"Failed to save app state: {exc}") from exc