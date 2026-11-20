"""User settings stored in SQLite. Secrets are never returned to the browser in full."""

from __future__ import annotations

import os
from typing import Any

from sqlmodel import select

from autopatch.db import Setting, session

DEFAULTS: dict[str, Any] = {
    "ai.provider": "builtin",                   # builtin (AutoPatch Core) | anthropic | ollama | none
    "ai.anthropic.model": "claude-opus-5-5",
    "ai.anthropic.api_key": "",
    "ai.anthropic.fallbacks": True,
    "ai.anthropic.workspace_id": "",            # only for keys not tied to a workspace
    "ai.ollama.url": "http://localhost:11434",
    "ai.ollama.model": "qwen2.5-coder:14b",
    "ai.effort.scan": "high",
    "ai.effort.fix": "high",
    "ai.max_context_kb": 400,
    "analysis.ruff": True,
    "analysis.pylint": True,
    "analysis.ai_scan": True,
    "testing.sandbox": "auto",                  # auto | docker | appcontainer | local
    "testing.allow_limited": False,
    "testing.timeout_s": 120,
    "testing.memory_mb": 1024,
    "testing.generate_tests": True,
    "testing.max_fix_attempts": 3,
    "limits.max_file_mb": 0,        # 0 = no limit per file
    "limits.max_project_mb": 0,     # 0 = no limit per project
    "limits.max_files": 0,          # 0 = no limit on file count
    "limits.analysis_max_mb": 25,   # larger files are identified from their header, and reported as not fully analyzed
}
SECRETS = {"ai.anthropic.api_key"}
CHOICES = {
    "ai.provider": {"builtin", "anthropic", "ollama", "none"},
    "ai.effort.scan": {"low", "medium", "high", "xhigh", "max"},
    "ai.effort.fix": {"low", "medium", "high", "xhigh", "max"},
    "testing.sandbox": {"auto", "docker", "appcontainer", "local"},
}
RANGES = {
    "ai.max_context_kb": (20, 2000),
    "testing.timeout_s": (10, 1800),
    "testing.memory_mb": (128, 16384),
    "testing.max_fix_attempts": (1, 6),
    "limits.max_file_mb": (0, 1_000_000),
    "limits.max_project_mb": (0, 10_000_000),
    "limits.max_files": (0, 10_000_000),
    "limits.analysis_max_mb": (1, 100_000),
}


def get_all() -> dict[str, Any]:
    values = dict(DEFAULTS)
    with session() as s:
        for row in s.exec(select(Setting)):
            if row.key in DEFAULTS:
                values[row.key] = row.value
    return values


def get(key: str) -> Any:
    return get_all()[key]


def public() -> dict[str, Any]:
    """Settings safe to send to the browser."""
    values = get_all()
    for key in SECRETS:
        secret = values.get(key) or ""
        values[key] = {"set": bool(secret), "hint": f"…{secret[-4:]}" if secret else ""}
    values["ai.anthropic.env_key"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return values


def update(changes: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise ValueError(f"Unknown setting {key}")
        default = DEFAULTS[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        elif isinstance(default, int):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number")
            value = int(value)
            lo, hi = RANGES.get(key, (None, None))
            if lo is not None and not lo <= value <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")
        else:
            if not isinstance(value, str):
                raise ValueError(f"{key} must be text")
            value = value.strip()
            if key in CHOICES and value not in CHOICES[key]:
                raise ValueError(f"{key} must be one of {sorted(CHOICES[key])}")
        clean[key] = value
    with session() as s:
        for key, value in clean.items():
            row = s.get(Setting, key) or Setting(key=key)
            row.value = value
            s.add(row)
        s.commit()
    return public()
