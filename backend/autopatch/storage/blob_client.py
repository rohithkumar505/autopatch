"""Minimal Vercel Blob client (httpx only — avoids the `vercel` PyPI package name clash on Lambda)."""

from __future__ import annotations

import os
from typing import Any

import httpx

_API = "https://vercel.com/api/blob"
_VERSION = "12"


def _token() -> str:
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not set")
    return token


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {_token()}",
        "x-api-version": _VERSION,
    }
    if extra:
        headers.update(extra)
    return headers


def head_meta(pathname: str) -> dict[str, Any]:
    r = httpx.get(
        _API,
        params={"prefix": pathname, "limit": "10"},
        headers=_headers(),
        timeout=30,
    )
    if r.status_code == 404:
        raise FileNotFoundError(pathname)
    r.raise_for_status()
    data = r.json()
    for blob in data.get("blobs", []):
        if blob.get("pathname") == pathname:
            return blob
    raise FileNotFoundError(pathname)


def get(pathname: str) -> bytes:
    meta = head_meta(pathname)
    url = meta["downloadUrl"]
    r = httpx.get(url, headers={"authorization": f"Bearer {_token()}"}, timeout=120)
    r.raise_for_status()
    return r.content


def put(pathname: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
    put_headers = {
        "x-vercel-blob-access": "private",
        "x-add-random-suffix": "0",
        "x-allow-overwrite": "1",
        "x-content-type": content_type,
        "content-length": str(len(data)),
    }
    r = httpx.put(
        _API,
        params={"pathname": pathname},
        headers=_headers(put_headers),
        content=data,
        timeout=180,
    )
    r.raise_for_status()
    return r.json().get("etag", "")