"""Pick the analysis engine: AutoPatch Core (built in, the default) or an optional external AI."""

from __future__ import annotations

from autopatch.ai.engine import Engine
from autopatch.ai.providers import AINotConfigured, get_provider


def get_engine(cfg: dict):
    provider = cfg["ai.provider"]
    if provider == "builtin":
        from autopatch.core_engine.engine import CoreEngine

        return CoreEngine(cfg)
    if provider == "none":
        raise AINotConfigured("Deep analysis is turned off in Settings. Choose AutoPatch Core to enable it.")
    return Engine(get_provider(cfg), cfg["ai.effort.scan"], cfg["ai.effort.fix"])


def engine_label(engine) -> str:
    return getattr(engine, "name", None) or engine.provider.model
