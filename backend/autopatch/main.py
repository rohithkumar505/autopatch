"""FastAPI application: the JSON API plus the built frontend."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from autopatch import __version__
from autopatch.ai.providers import AIError
from autopatch.api import bugs, editor, ide, projects, system
from autopatch.config import FRONTEND_DIST, REPO_ROOT
from autopatch.core import jobs, settings as settings_mod
from autopatch.core.files import ProjectImportError
from autopatch.core.workspace import GitError
from autopatch.db import get_engine
from autopatch.pipeline.fix import FixError
from autopatch.pipeline.history import ReviewError
from autopatch.testing.sandbox import SandboxUnavailable
from autopatch.storage.serverless import (
    persistence_status,
    serverless_persistence_enabled,
    sync_after_request,
    sync_before_request,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_SERVERLESS = os.environ.get("AUTOPATCH_SERVERLESS", "") == "1" or os.environ.get("VERCEL", "") == "1"


def _frontend_dist() -> Path:
    override = os.environ.get("AUTOPATCH_FRONTEND_DIST")
    if override:
        return Path(override)
    if _SERVERLESS:
        for candidate in (REPO_ROOT / "public", REPO_ROOT / "frontend" / "dist"):
            if candidate.is_dir() and (candidate / "index.html").is_file():
                return candidate
    return FRONTEND_DIST


def _bootstrap_serverless() -> None:
    if not _SERVERLESS:
        return
    cfg = settings_mod.get_all()
    # Vercel has no Docker/AppContainer; local limited sandbox is the only option.
    changes: dict = {}
    if not cfg.get("testing.allow_limited"):
        changes["testing.allow_limited"] = True
    if cfg.get("testing.sandbox") not in ("local", "auto"):
        changes["testing.sandbox"] = "auto"
    if changes:
        settings_mod.update(changes)


def _error(status: int):
    async def handler(_request: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=getattr(exc, "status", status))
    return handler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap_serverless()
    get_engine()
    jobs.mark_interrupted()  # anything left 'running' died with the previous process
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="AutoPatch", version=__version__, lifespan=lifespan)
    application.include_router(projects.router)
    application.include_router(ide.router)
    application.include_router(editor.router)
    application.include_router(bugs.router)
    application.include_router(system.router)
    for exc_type, code in [
        (ReviewError, 400), (ProjectImportError, 400), (AIError, 400), (SandboxUnavailable, 400),
        (FixError, 400), (GitError, 400), (FileNotFoundError, 404), (ValueError, 400),
    ]:
        application.add_exception_handler(exc_type, _error(code))
    dist = _frontend_dist()
    if _SERVERLESS:
        # Vercel ships only the FastAPI lambda (no separate static layer); serve built UI from public/.
        if dist.is_dir() and (dist / "index.html").is_file():
            assets = dist / "assets"
            if assets.is_dir():
                application.mount("/assets", StaticFiles(directory=assets), name="assets")

            @application.get("/", include_in_schema=False)
            async def _spa_root():
                return FileResponse(dist / "index.html")

            @application.get("/index.html", include_in_schema=False)
            async def _spa_index():
                return FileResponse(dist / "index.html")
    elif dist.is_dir():
        application.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    @application.middleware("http")
    async def vercel_state_sync(request: Request, call_next):
        if request.url.path.startswith("/api"):
            sync_before_request()
        response = await call_next(request)
        if serverless_persistence_enabled() and request.url.path.startswith("/api"):
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                try:
                    sync_after_request()
                except Exception as exc:
                    log = logging.getLogger("autopatch.storage")
                    log.exception("persist failed")
                    return JSONResponse({"detail": str(exc)}, status_code=503)
        return response

    return application


app = create_app()


def run() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Start the AutoPatch server")
    parser.add_argument("--host", default="127.0.0.1", help="Keep the default to stay local-only")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    run()
