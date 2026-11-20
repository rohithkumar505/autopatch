"""Shared state for Vercel: SQLite + project workspaces in Blob storage.

Lambda instances do not share /tmp. We pack the data directory into one snapshot
in Vercel Blob and refresh it on each API request.
"""

from __future__ import annotations

import io
import logging
import os
import sqlite3
import tarfile
import threading
from pathlib import Path

from fastapi import HTTPException
from vercel.blob.errors import BlobNotFoundError
from vercel.blob.sync import get_blob, head_blob, put_blob

from autopatch.config import paths
from autopatch.db import reset_engine

log = logging.getLogger("autopatch.storage")

_SNAPSHOT = "autopatch-snapshot.tar.gz"
_access = "private"
_lock = threading.Lock()
_remote_etag: str | None = None


def _serverless() -> bool:
    return os.environ.get("AUTOPATCH_SERVERLESS") == "1" or os.environ.get("VERCEL") == "1"


def serverless_persistence_enabled() -> bool:
    if not _serverless():
        return False
    if os.environ.get("BLOB_READ_WRITE_TOKEN"):
        return True
    return bool(os.environ.get("BLOB_STORE_ID") and os.environ.get("VERCEL_OIDC_TOKEN"))


def persistence_status() -> dict:
    if not _serverless():
        return {"mode": "local", "ready": True}
    ready = serverless_persistence_enabled()
    store = os.environ.get("BLOB_STORE_ID", "")
    return {
        "mode": "vercel_blob",
        "ready": ready,
        "store_id": store or None,
        "message": None if ready else "Blob storage is not linked — imports cannot persist on Vercel.",
    }


def require_persistence() -> None:
    if _serverless() and not serverless_persistence_enabled():
        raise HTTPException(
            503,
            "Serverless storage is not configured. Link a Vercel Blob store to this project.",
        )


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


def _remote_etag_current() -> str | None:
    try:
        meta = head_blob(_SNAPSHOT, access=_access)
        return meta.etag
    except BlobNotFoundError:
        return None
    except Exception as exc:  # pragma: no cover - network
        log.warning("blob head failed: %s", exc)
        return None


def sync_before_request() -> None:
    """Load latest shared state before handling an API request."""
    if not serverless_persistence_enabled():
        paths().ensure()
        return
    global _remote_etag
    with _lock:
        etag = _remote_etag_current()
        data_dir = paths().data
        if etag and etag == _remote_etag and (data_dir / "autopatch.db").is_file():
            return
        try:
            result = get_blob(_SNAPSHOT, access=_access)
            payload = result.body
            _remote_etag = result.metadata.etag
        except BlobNotFoundError:
            paths().ensure()
            reset_engine()
            from autopatch.db import get_engine

            get_engine()
            return
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
        payload = _pack_data(data)
        try:
            result = put_blob(
                _SNAPSHOT,
                payload,
                access=_access,
                allow_overwrite=True,
                add_random_suffix=False,
                content_type="application/gzip",
            )
            _remote_etag = result.etag
        except Exception as exc:
            log.error("blob upload failed: %s", exc)
            raise HTTPException(503, f"Failed to save app state: {exc}") from exc