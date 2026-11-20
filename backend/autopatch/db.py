"""SQLite storage. Timestamps are ISO-8601 UTC strings so they sort and serialize simply."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import JSON, Column, Text, event
from sqlmodel import Field, Session, SQLModel, create_engine

from autopatch.config import paths


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Project(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    source_type: str  # files | folder | zip | path | git
    source_ref: str = ""
    original_version: str = ""
    languages: dict = Field(default_factory=dict, sa_column=Column(JSON))
    dependencies: list = Field(default_factory=list, sa_column=Column(JSON))
    file_count: int = 0
    import_report: dict = Field(default_factory=dict, sa_column=Column(JSON))  # what was imported and skipped, and why
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class Scan(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    version: str = ""
    status: str = "queued"  # queued | running | completed | failed
    options: dict = Field(default_factory=dict, sa_column=Column(JSON))
    summary: str = Field(default="", sa_column=Column(Text))
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: str = ""
    job_id: str = ""
    started_at: str = Field(default_factory=now_iso)
    finished_at: str = ""


class Finding(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    scan_id: int = Field(index=True)
    title: str
    description: str = Field(default="", sa_column=Column(Text))
    file: str
    function: str = ""
    line_start: int = 0
    line_end: int = 0
    code_snippet: str = Field(default="", sa_column=Column(Text))
    snippet_start: int = 0  # line number of the first snippet line
    category: str = "incorrect_output"
    severity: str = "medium"
    confidence: float = 0.5
    sources: list = Field(default_factory=list, sa_column=Column(JSON))  # e.g. ["ai", "ruff:S307"]
    root_cause: str = Field(default="", sa_column=Column(Text))
    trigger: str = Field(default="", sa_column=Column(Text))
    expected_behavior: str = Field(default="", sa_column=Column(Text))
    impact: str = Field(default="", sa_column=Column(Text))
    related: list = Field(default_factory=list, sa_column=Column(JSON))
    severity_reason: str = Field(default="", sa_column=Column(Text))
    facts: dict = Field(default_factory=dict, sa_column=Column(JSON))  # engine evidence, e.g. probe results
    analyzed: bool = False  # deep root-cause analysis done
    # possible: reported but not proven | confirmed: a test reproduced it | not_reproduced: tests could not show it
    verification: str = "possible"
    # open | fixing | patch_ready | fixed | rejected | dismissed
    status: str = "open"
    priority: float = 0.0
    created_at: str = Field(default_factory=now_iso)


class FileReport(SQLModel, table=True):
    """Universal file intelligence result for one file in one scan."""

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    scan_id: int = Field(index=True)
    path: str = Field(index=True)
    blob: str = ""
    size: int = 0
    format: str = ""
    mime: str = ""
    category: str = ""
    encoding: str = ""
    language: str = ""
    extension_mismatch: str = ""
    status: str = ""          # analyzed | partially_analyzed | identified_only | skipped | unreadable
    method: str = ""
    issues: list = Field(default_factory=list, sa_column=Column(JSON))
    notes: list = Field(default_factory=list, sa_column=Column(JSON))
    reused: bool = False


class Patch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    finding_id: int = Field(index=True)
    job_id: str = ""
    base_version: str = ""
    # generating | ready | failed | accepted | rejected | reverted
    status: str = "generating"
    file: str = ""
    before_content: str = Field(default="", sa_column=Column(Text))
    after_content: str = Field(default="", sa_column=Column(Text))
    diff: str = Field(default="", sa_column=Column(Text))
    edits: list = Field(default_factory=list, sa_column=Column(JSON))
    explanation: str = Field(default="", sa_column=Column(Text))
    behavior_change: str = Field(default="", sa_column=Column(Text))
    test_file: str = ""
    test_code: str = Field(default="", sa_column=Column(Text))
    test_cases: list = Field(default_factory=list, sa_column=Column(JSON))
    attempts: int = 0
    # validated | partially_validated | failed | not_run
    validation: str = "not_run"
    validation_detail: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: str = Field(default="", sa_column=Column(Text))
    decision_note: str = Field(default="", sa_column=Column(Text))
    applied_version: str = ""
    tests_added: bool = False
    created_at: str = Field(default_factory=now_iso)
    decided_at: str = ""


class TestRunRow(SQLModel, table=True):
    __test__ = False
    __tablename__ = "testrun"

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    patch_id: int | None = Field(default=None, index=True)
    finding_id: int | None = None
    # project: the project's own tests | original: candidate tests on unchanged code | patched: on patched code
    kind: str = "project"
    version: str = ""
    status: str = ""  # passed | failed | no_tests | timeout | crashed | error
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    results: list = Field(default_factory=list, sa_column=Column(JSON))
    output: str = Field(default="", sa_column=Column(Text))
    sandbox: str = ""
    created_at: str = Field(default_factory=now_iso)


class Event(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(default=None, index=True)
    kind: str
    message: str
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: str = Field(default_factory=now_iso)


class Job(SQLModel, table=True):
    id: str = Field(primary_key=True)
    kind: str
    project_id: int | None = None
    status: str = "queued"  # queued | running | succeeded | failed
    progress: list = Field(default_factory=list, sa_column=Column(JSON))
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: str = Field(default="", sa_column=Column(Text))
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: Any = Field(default=None, sa_column=Column(JSON))


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            f"sqlite:///{paths().db.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(_engine, "connect")
        def _pragmas(conn, _record):  # pragma: no cover - trivial
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        SQLModel.metadata.create_all(_engine)
        _add_missing_columns(_engine)
    return _engine


def _add_missing_columns(engine) -> None:
    """Older databases: add columns introduced since they were created."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                kind = col.type.compile(engine.dialect)
                default = "'{}'" if "JSON" in kind.upper() else "''" if any(t in kind.upper() for t in ("CHAR", "TEXT")) else "0"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {kind} DEFAULT {default}'))


def reset_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def session() -> Session:
    return Session(get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session() as s:
        yield s


def record_event(s: Session, kind: str, message: str, project_id: int | None = None, **data: Any) -> Event:
    ev = Event(kind=kind, message=message, project_id=project_id, data=data)
    s.add(ev)
    return ev
