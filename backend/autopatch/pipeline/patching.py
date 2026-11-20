"""Apply proposed edits to a file and produce a reviewable diff."""

from __future__ import annotations

import ast
import difflib
import re

_LINE_NO = re.compile(r"^\s*\d+\| ?")


class PatchApplyError(Exception):
    pass


def _strip_line_numbers(text: str) -> tuple[str, bool]:
    """Remove "12| " prefixes if the model copied them from the numbered listing."""
    lines = text.split("\n")
    non_empty = [line for line in lines if line.strip()]
    if non_empty and all(_LINE_NO.match(line) for line in non_empty):
        return "\n".join(_LINE_NO.sub("", line, count=1) for line in lines), True
    return text, False


def _replace_ignoring_trailing_space(content: str, original: str, patched: str) -> str | None:
    lines = content.split("\n")
    wanted = [line.rstrip() for line in original.strip("\n").split("\n")]
    stripped = [line.rstrip() for line in lines]
    hits = [i for i in range(len(lines) - len(wanted) + 1) if stripped[i : i + len(wanted)] == wanted]
    if len(hits) != 1:
        return None
    i = hits[0]
    return "\n".join(lines[:i] + patched.strip("\n").split("\n") + lines[i + len(wanted):])


def apply_edits(content: str, edits: list[dict], path: str = "<file>") -> str:
    """edits: [{"original_code": ..., "patched_code": ...}], applied in order. Content uses \\n newlines."""
    if not edits:
        raise PatchApplyError("The patch contains no edits")
    for n, edit in enumerate(edits, 1):
        original, had_numbers = _strip_line_numbers(edit["original_code"].replace("\r\n", "\n"))
        patched = edit["patched_code"].replace("\r\n", "\n")
        if had_numbers:
            patched = _strip_line_numbers(patched)[0]
        if not original.strip():
            raise PatchApplyError(f"Edit {n}: original_code is empty")
        count = content.count(original)
        if count == 1:
            content = content.replace(original, patched, 1)
            continue
        if count > 1:
            raise PatchApplyError(f"Edit {n}: original_code appears {count} times in {path}; it must be unique")
        fallback = _replace_ignoring_trailing_space(content, original, patched)
        if fallback is None:
            raise PatchApplyError(f"Edit {n}: original_code was not found in {path}")
        content = fallback
    if path.endswith(".py"):
        try:
            ast.parse(content, filename=path)
        except SyntaxError as exc:
            raise PatchApplyError(f"Patched {path} has a syntax error on line {exc.lineno}: {exc.msg}") from exc
    return content


def unified_diff(path: str, before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    )
    # A last line without a newline must not run into the next diff line.
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def changed_line_ranges(before: str, after: str) -> list[tuple[int, int]]:
    """Line ranges (1-based, in the original) touched by the change; used to check scope."""
    ranges = []
    matcher = difflib.SequenceMatcher(a=before.split("\n"), b=after.split("\n"), autojunk=False)
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag != "equal":
            ranges.append((i1 + 1, max(i1 + 1, i2)))
    return ranges
