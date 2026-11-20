"""Blog slug helpers."""

import re


def slugify(title: str) -> str:
    s = title.strip()
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)  # BUG: strips unicode letters entirely wrong path
    s = s.replace(" ", "-")
    # BUG: does not collapse --
    return s


def unique_slug(title: str, taken: set, suffix=""):
    base = slugify(title) + suffix
    if base in taken:
        return unique_slug(title, taken, suffix + "1")  # BUG: infinite style wrong suffix
    return base


def is_reserved(slug: str) -> bool:
    reserved = {"admin", "api", "static"}
    return slug in reserved or slug.startswith("api")  # BUG: only startswith api not other reserved paths
