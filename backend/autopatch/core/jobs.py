"""Background jobs (scans, fixes, test runs) with progress the UI can poll.

Progress messages are written as the work happens, so what the UI shows is the real state
of the job, not an animation.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from sqlmodel import select

from autopatch.db import Job, now_iso, session

log = logging.getLogger("autopatch.jobs")

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="autopatch-job")
_lock = threading.Lock()


class JobCancelled(Exception):
    pass


class JobContext:
    def __init__(self, job_id: str, project_id: int | None):
        self.job_id = job_id
        self.project_id = project_id

    def log(self, message: str, level: str = "info") -> None:
        with _lock, session() as s:
            job = s.get(Job, self.job_id)
            if job is None:
                return
            job.progress = [*job.progress, {"time": now_iso(), "level": level, "message": message}]
            job.updated_at = now_iso()
            s.add(job)
            s.commit()


def submit(kind: str, project_id: int | None, fn: Callable[[JobContext], dict[str, Any] | None]) -> Job:
    job = Job(id=uuid.uuid4().hex[:16], kind=kind, project_id=project_id)
    with session() as s:
        s.add(job)
        s.commit()
        s.refresh(job)
    # Serverless (Vercel): background threads are killed when the HTTP response returns.
    if os.environ.get("AUTOPATCH_SERVERLESS") == "1" or os.environ.get("VERCEL"):
        _run(job.id, project_id, fn)
        finished = get(job.id)
        return finished or job
    _executor.submit(_run, job.id, project_id, fn)
    return job


def _update(job_id: str, **fields: Any) -> None:
    with _lock, session() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)
        job.updated_at = now_iso()
        s.add(job)
        s.commit()


def _run(job_id: str, project_id: int | None, fn: Callable[[JobContext], dict[str, Any] | None]) -> None:
    ctx = JobContext(job_id, project_id)
    _update(job_id, status="running")
    try:
        result = fn(ctx) or {}
        _update(job_id, status="succeeded", result=result)
    except Exception as exc:  # the job's own code reports user-facing errors this way
        log.error("Job %s failed\n%s", job_id, traceback.format_exc())
        ctx.log(str(exc) or exc.__class__.__name__, level="error")
        _update(job_id, status="failed", error=str(exc) or exc.__class__.__name__)


def get(job_id: str) -> Job | None:
    with session() as s:
        return s.get(Job, job_id)


def active_jobs(project_id: int | None = None) -> list[Job]:
    with session() as s:
        q = select(Job).where(Job.status.in_(["queued", "running"]))  # type: ignore[attr-defined]
        if project_id is not None:
            q = q.where(Job.project_id == project_id)
        return list(s.exec(q))


def mark_interrupted() -> None:
    """Called at startup: anything still 'running' died with the previous process."""
    with session() as s:
        for job in s.exec(select(Job).where(Job.status.in_(["queued", "running"]))):  # type: ignore[attr-defined]
            job.status = "failed"
            job.error = "AutoPatch was restarted before this job finished"
            job.updated_at = now_iso()
            s.add(job)
        s.commit()


def wait(job_id: str, timeout_s: float = 600) -> Job:
    """Block until a job finishes (used by tests and the CLI)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = get(job_id)
        if job and job.status in ("succeeded", "failed"):
            return job
        time.sleep(0.05)
    raise TimeoutError(f"Job {job_id} did not finish in {timeout_s}s")
