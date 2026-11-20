"""AutoPatch Core learns from your decisions.

Each accepted patch counts as evidence that a detector is right; each rejected or reverted patch
and each dismissed finding counts against it. For findings that were NOT proven by running the
code, the detector's track record raises or lowers the confidence it reports next time. Findings
proven in the sandbox keep their confidence: a proof does not depend on opinions.
"""

from __future__ import annotations

from sqlmodel import select

from autopatch.db import Finding, Patch, session


def _kind(sources: list[str], facts: dict | None) -> str:
    if facts and facts.get("kind") and facts["kind"] != "format":
        return facts["kind"]
    if facts and facts.get("kind") == "format":
        return "file:" + facts.get("code", "")
    for s in sources:
        if s.startswith("core:"):
            return s[5:]
    return ""


def stats() -> dict[str, dict]:
    out: dict[str, dict] = {}
    with session() as s:
        findings = {f.id: f for f in s.exec(select(Finding))}
        patches = list(s.exec(select(Patch)))
    def bucket(kind: str) -> dict:
        return out.setdefault(kind, {"accepted": 0, "rejected": 0, "reverted": 0, "dismissed": 0})
    seen_dismissed = set()
    for p in patches:
        f = findings.get(p.finding_id)
        if not f:
            continue
        kind = _kind(f.sources, f.facts)
        if not kind:
            continue
        if p.status == "accepted":
            bucket(kind)["accepted"] += 1
        elif p.status == "rejected":
            bucket(kind)["rejected"] += 1
        elif p.status == "reverted":
            bucket(kind)["reverted"] += 1
    for f in findings.values():
        if f.status == "dismissed" and f.id not in seen_dismissed:
            kind = _kind(f.sources, f.facts)
            if kind:
                bucket(kind)["dismissed"] += 1
                seen_dismissed.add(f.id)
    for kind, b in out.items():
        good = b["accepted"]
        bad = b["rejected"] + b["dismissed"] + 0.5 * b["reverted"]
        b["decisions"] = good + b["rejected"] + b["dismissed"] + b["reverted"]
        b["precision"] = round((good + 1) / (good + bad + 2), 2)  # Laplace-smoothed track record
    return out


_cache: dict | None = None


def adjust(kind: str, confidence: float, confirmed: bool) -> tuple[float, str]:
    """Return (new confidence, explanation). Proven findings are not adjusted."""
    global _cache
    if confirmed or not kind:
        return confidence, ""
    if _cache is None:
        try:
            _cache = stats()
        except Exception:
            _cache = {}
    b = _cache.get(kind)
    if not b or b["decisions"] < 2:
        return confidence, ""
    factor = 0.6 + 0.8 * b["precision"]          # 0.6x for a poor record, up to 1.4x for a strong one
    new = round(max(0.05, min(0.95, confidence * factor)), 2)
    direction = "raised" if new > confidence else "lowered"
    return new, (f"Confidence {direction} from {confidence:.0%} to {new:.0%} because this check's past suggestions were "
                 f"accepted {b['accepted']} time(s) and rejected or dismissed {b['rejected'] + b['dismissed']} time(s).")


def reset_cache() -> None:
    global _cache
    _cache = None
