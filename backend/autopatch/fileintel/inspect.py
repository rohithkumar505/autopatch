"""Per-format integrity and structure checks, repairability assessment and format-aware repairs.

Nothing here executes the file. Binary formats are parsed structurally (chunks, headers, trailers,
archive directories) and are only ever changed by repairs that understand the format.
"""

from __future__ import annotations

import ast
import configparser
import csv
import gzip
import io
import json
import re
import sqlite3
import tarfile
import tempfile
import tomllib
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from autopatch.fileintel.identify import Identity

FIXABLE, PARTIAL, HUMAN, NOT_FIXABLE, UNSUPPORTED = (
    "fixable", "partially_fixable", "requires_human", "not_fixable", "unsupported")


@dataclass
class FileIssue:
    code: str
    title: str
    severity: str             # critical | high | medium | low | info
    message: str
    repairability: str
    reason: str               # why it has that repairability
    options: list[str] = field(default_factory=list)
    limitations: str = ""
    confidence: float = 0.8
    requirements: str = ""
    line: int = 0
    column: int = 0
    repair: str = ""          # repair id when one exists

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Inspection:
    status: str               # analyzed | partially_analyzed | identified_only | skipped | unreadable
    method: str               # what was used to inspect it
    issues: list[FileIssue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


JSONC_NAMES = re.compile(r"(^tsconfig.*\.json$|^jsconfig.*\.json$|^\.eslintrc(\.json)?$|^devcontainer\.json$|\.jsonc$|"
                         r"^\.babelrc$)", re.I)
SECRET_KEY = re.compile(r"(secret|token|passw(or)?d|api[_-]?key|private[_-]?key|credential|access[_-]?key|auth)", re.I)
PLACEHOLDER = re.compile(r"^(|changeme|change_me|your[-_ ].*|xxx+|<.*>|\$\{.*\}|placeholder|example|dummy|test|none|null|todo)$", re.I)
SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "Anthropic API key"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "API secret key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "GitHub token"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"[\"'](passw(or)?d|pwd|secret)[\"']\s*[:=]\s*[\"'][^\"'\s]{3,}[\"']", re.I), "hard-coded password"),
]

BLOCK_START = re.compile(r"^\s*(def |class |if |elif |else|for |while |try|except|finally|with |async def |match |case )")


def python_syntax_repair(text: str, max_steps: int = 50) -> tuple[str, list[str]] | None:
    """Repair common mechanical syntax errors. Returns (fixed text, changes) only if the result parses."""
    crlf = "\r\n" in text  # keep the file's own line endings
    lines = text.replace("\r\n", "\n").split("\n")
    changes: list[str] = []
    for _ in range(max_steps):
        try:
            joined = "\n".join(lines)
            ast.parse(joined)
            if not changes:
                return None
            return (joined.replace("\n", "\r\n") if crlf else joined), changes
        except SyntaxError as exc:
            n = (exc.lineno or 0) - 1
            msg = exc.msg or ""
            if not 0 <= n < len(lines):
                return None
            if msg.startswith("unterminated string literal") and n + 1 < len(lines):
                # A line break inside a "..." string: it was meant to be the escape sequence \n.
                lines[n:n + 2] = [lines[n] + "\\n" + lines[n + 1]]
                changes.append(f"line {n + 1}: joined a string that was split by a raw line break (now \\n)")
            elif msg == "expected ':'" and BLOCK_START.match(lines[n]) and not lines[n].rstrip().endswith(":"):
                lines[n] = lines[n].rstrip() + ":"
                changes.append(f"line {n + 1}: added the missing ':'")
            else:
                return None
    return None



# ---------------------------------------------------------------- JSON helpers (string-aware)


def _strip_json_extras(text: str, comments: bool = True, trailing: bool = True) -> str:
    """Remove // and /* */ comments and trailing commas, never touching string contents."""
    out, i, n = [], 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if comments and ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if comments and ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if trailing and ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _dup_keys(text: str) -> list[str]:
    dups: list[str] = []

    def hook(pairs):
        seen = set()
        for k, _ in pairs:
            if k in seen:
                dups.append(k)
            seen.add(k)
        return dict(pairs)

    try:
        json.loads(text, object_pairs_hook=hook)
    except ValueError:
        pass
    return dups


# ---------------------------------------------------------------- inspectors


def _text_checks(data: bytes, ident: Identity, issues: list[FileIssue]) -> str:
    codec = ident.details.get("codec", "utf-8")
    if codec == "cp1252":
        issues.append(FileIssue("encoding.not-utf8", "Not valid UTF-8", "low",
                                "The file is text but contains bytes that are not valid UTF-8.", FIXABLE,
                                "The bytes decode cleanly as Windows-1252, so they can be converted to UTF-8.",
                                ["Convert the file to UTF-8"], "Assumes the original encoding was Windows-1252.", 0.7,
                                repair="reencode_utf8"))
    if codec == "utf-8-sig" and ident.category in ("source", "config", "data"):
        issues.append(FileIssue("encoding.bom", "UTF-8 byte-order mark", "low",
                                "The file starts with a byte-order mark, which some tools reject.", FIXABLE,
                                "The mark carries no content and can be removed safely.", ["Remove the byte-order mark"],
                                confidence=0.95, repair="strip_bom"))
    text = data.decode(codec, errors="replace")
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if crlf and lf:
        issues.append(FileIssue("text.mixed-eol", "Mixed line endings", "low",
                                f"{crlf} Windows (CRLF) and {lf} Unix (LF) line endings in one file.", FIXABLE,
                                "Line endings can be made consistent without changing content.",
                                ["Use the more common line ending everywhere"], confidence=0.95, repair="normalize_eol"))
    for pattern, label in SECRET_PATTERNS:
        m = pattern.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            issues.append(FileIssue("security.secret", f"Possible {label} in the file", "high",
                                    f"Line {line} looks like a {label}. Secrets in project files are exposed to anyone with access.",
                                    HUMAN, "Removing the value is easy, but the secret must also be revoked by its owner.",
                                    ["Revoke the credential with its provider", "Move it to an untracked environment variable"],
                                    "AutoPatch cannot revoke credentials.", 0.75, "Access to the account that issued it.", line=line))
            break
    return text


def _json(text: str, ident: Identity, issues: list[FileIssue]) -> str:
    name = PurePosixPath(ident.path).name
    jsonc = bool(JSONC_NAMES.search(name)) or ident.path.startswith(".vscode/") or "/.vscode/" in ident.path
    body = text.lstrip("﻿")
    candidate = _strip_json_extras(body) if jsonc else body
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        fixed = _strip_json_extras(body)
        repairable = False
        try:
            json.loads(fixed)
            repairable = True
        except json.JSONDecodeError:
            pass
        issues.append(FileIssue(
            "json.invalid", "Invalid JSON", "high", f"{exc.msg} at line {exc.lineno}, column {exc.colno}.",
            FIXABLE if repairable else HUMAN,
            "The only problems are trailing commas or comments, which can be removed without changing any value."
            if repairable else "The structure is broken in a way that has more than one plausible fix.",
            ["Remove trailing commas and comments"] if repairable else ["Correct the structure by hand at the reported line"],
            "" if repairable else "Guessing the intended structure could silently change data.", 0.9 if repairable else 0.8,
            "" if repairable else "Knowledge of what the file should contain.", line=exc.lineno, column=exc.colno,
            repair="json_cleanup" if repairable else ""))
        return "JSON parser"
    dups = _dup_keys(candidate)
    if dups:
        issues.append(FileIssue("json.duplicate-key", "Duplicate keys", "medium",
                                f"Keys appear more than once: {', '.join(sorted(set(dups))[:5])}. Only the last value is used.",
                                HUMAN, "Which value is correct is a decision, not a formatting problem.",
                                ["Keep the intended value and delete the other"], confidence=0.95))
    if name == "package.json" and isinstance(value, dict):
        if not value.get("private") and not ("name" in value and "version" in value):
            issues.append(FileIssue("config.package-fields", "package.json is missing name or version", "low",
                                    "Publishable packages need both a name and a version.", HUMAN,
                                    "The right name and version are project decisions.", ["Add \"name\" and \"version\"", "Or mark it \"private\": true"],
                                    confidence=0.8))
        for section in ("dependencies", "devDependencies"):
            deps = value.get(section, {})
            if isinstance(deps, dict):
                for dep, ver in deps.items():
                    if isinstance(ver, str) and ver.strip() in ("", "*", "latest"):
                        issues.append(FileIssue("config.unpinned-dependency", f"Unpinned dependency {dep}", "low",
                                                f"{section}.{dep} is \"{ver}\", so installs are not reproducible.", HUMAN,
                                                "Choosing a version range is a project decision.", ["Pin a version range"], confidence=0.9))
                        break
    return "JSON parser" + (" (comments allowed)" if jsonc else "")


def _yaml(text: str, ident: Identity, issues: list[FileIssue]) -> str:
    try:
        import yaml
    except ImportError:
        issues.append(FileIssue("yaml.unchecked", "YAML not validated", "info", "The YAML validator is not installed.",
                                UNSUPPORTED, "AutoPatch lacks a YAML parser on this machine.", ["Install PyYAML"], confidence=1.0))
        return "none"
    try:
        list(yaml.safe_load_all(text.lstrip("﻿")))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark else 0
        tabs = bool(re.search(r"^\t+", text, re.M))
        fixed_ok = False
        if tabs:
            try:
                list(yaml.safe_load_all(_yaml_tabs(text)))
                fixed_ok = True
            except yaml.YAMLError:
                pass
        issues.append(FileIssue("yaml.invalid", "Invalid YAML", "high",
                                f"{getattr(exc, 'problem', None) or exc}" + (f" (line {line})" if line else ""),
                                FIXABLE if fixed_ok else HUMAN,
                                "YAML forbids tabs for indentation; replacing them with spaces makes it valid." if fixed_ok
                                else "The structure has more than one plausible fix.",
                                ["Replace indentation tabs with spaces"] if fixed_ok else ["Fix the indentation or syntax at the reported line"],
                                confidence=0.85, line=line, repair="yaml_tabs" if fixed_ok else ""))
    return "YAML parser (safe loader)"


def _yaml_tabs(text: str) -> str:
    return re.sub(r"^\t+", lambda m: "  " * len(m.group(0)), text, flags=re.M)


def _toml(text: str, ident: Identity, issues: list[FileIssue]) -> str:
    try:
        tomllib.loads(text.lstrip("﻿"))
    except tomllib.TOMLDecodeError as exc:
        m = re.search(r"line (\d+)", str(exc))
        issues.append(FileIssue("toml.invalid", "Invalid TOML", "high", str(exc), HUMAN,
                                "TOML errors usually need a decision about the intended value.",
                                ["Fix the syntax at the reported line"], confidence=0.9, line=int(m.group(1)) if m else 0))
    return "TOML parser"


def _xml(text: str, ident: Identity, issues: list[FileIssue]) -> str:
    if re.search(r"<!ENTITY", text[:20000]):
        issues.append(FileIssue("xml.entities", "XML declares entities", "medium",
                                "Entity declarations can be abused (billion-laughs, external entities). The file was not expanded.",
                                HUMAN, "Whether the entities are needed is a design decision.",
                                ["Remove the DOCTYPE entity declarations if they are not needed"], confidence=0.7))
        return "XML scan (entities not expanded)"
    try:
        ET.fromstring(text.lstrip("﻿").encode("utf-8"))
    except ET.ParseError as exc:
        line, col = exc.position if hasattr(exc, "position") else (0, 0)
        issues.append(FileIssue("xml.invalid", "Malformed XML", "high", str(exc), HUMAN,
                                "Mismatched or unclosed tags have several possible corrections.",
                                ["Fix the tag structure at the reported line"], confidence=0.9, line=line, column=col))
    return "XML parser"


def _ini(text: str, ident: Identity, issues: list[FileIssue]) -> str:
    cp = configparser.ConfigParser(strict=True, interpolation=None)
    try:
        cp.read_string(text.lstrip("﻿"))
    except configparser.MissingSectionHeaderError as exc:
        issues.append(FileIssue("ini.no-section", "Settings outside any [section]", "medium", str(exc).split("\n")[0], HUMAN,
                                "The settings must belong to a section, and which one is a decision.",
                                ["Add the right [section] header"], confidence=0.85, line=exc.lineno))
    except (configparser.DuplicateSectionError, configparser.DuplicateOptionError) as exc:
        issues.append(FileIssue("ini.duplicate", "Duplicate section or option", "medium", str(exc).split("\n")[0], HUMAN,
                                "Only one of the values can be correct.", ["Keep the intended value"], confidence=0.9,
                                line=getattr(exc, "lineno", 0) or 0))
    except configparser.Error as exc:
        issues.append(FileIssue("ini.invalid", "Invalid configuration file", "medium", str(exc).split("\n")[0], HUMAN,
                                "The syntax needs a manual correction.", ["Fix the reported line"], confidence=0.8))
    return "INI parser"


def _env(text: str, ident: Identity, issues: list[FileIssue]) -> str:
    name = PurePosixPath(ident.path).name.lower()
    example = any(w in name for w in ("example", "sample", "template", "dist"))
    seen: dict[str, int] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(export\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*=(.*)$", line)
        if not m:
            issues.append(FileIssue("env.malformed", "Malformed line", "medium", f"Line {n} is not KEY=value.", HUMAN,
                                    "The intended key and value cannot be inferred.", ["Rewrite the line as KEY=value"],
                                    confidence=0.9, line=n))
            continue
        key, value = m.group(2), m.group(3).strip().strip("'\"")
        if key in seen:
            issues.append(FileIssue("env.duplicate", f"{key} is defined twice", "medium",
                                    f"Lines {seen[key]} and {n} both set {key}; the later one wins.", HUMAN,
                                    "Which value is intended is a decision.", ["Delete the unintended definition"],
                                    confidence=0.95, line=n))
        seen[key] = n
        if not example and SECRET_KEY.search(key) and not PLACEHOLDER.match(value) and len(value) >= 8:
            issues.append(FileIssue("security.env-secret", f"{key} holds a real-looking secret", "high",
                                    f"Line {n} stores a value for {key} in a project file. The value is not shown here.",
                                    HUMAN, "The file can be removed from version control, but the secret must be rotated by its owner.",
                                    ["Rotate the secret", "Keep real values out of the repository (.gitignore)",
                                     "Commit only an .env.example with placeholders"],
                                    "AutoPatch cannot rotate credentials.", 0.7, line=n))
    return "environment-file checks"


def _csv(text: str, ident: Identity, issues: list[FileIssue]) -> str:
    sample = text[:50000]
    try:
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if rows:
        width = len(rows[0])
        bad = [i + 1 for i, r in enumerate(rows) if r and len(r) != width]
        if bad:
            issues.append(FileIssue("csv.ragged", "Rows with the wrong number of fields", "medium",
                                    f"{len(bad)} row(s) do not have {width} fields, e.g. line {bad[0]}.", HUMAN,
                                    "Missing or extra values cannot be guessed.", ["Correct the listed rows"],
                                    confidence=0.85, line=bad[0]))
    return "CSV reader"


def _brackets(text: str, ident: Identity, issues: list[FileIssue]) -> str:
    """Language-neutral structure check for source code we have no deep analyzer for."""
    pairs, stack = {")": "(", "]": "[", "}": "{"}, []
    i, n, line = 0, len(text), 1
    hash_comments = ident.language in ("Python", "Shell", "Ruby", "Perl", "PowerShell", "YAML")
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
        elif ch in "\"'`":
            q = ch
            i += 1
            while i < n and text[i] != q:
                if text[i] == "\\":
                    i += 1
                elif text[i] == "\n":
                    line += 1
                    if q != "`":
                        break
                i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "/" and not hash_comments:
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif ch == "/" and i + 1 < n and text[i + 1] == "*" and not hash_comments:
            end = text.find("*/", i + 2)
            line += text.count("\n", i, end if end != -1 else n)
            i = n if end == -1 else end + 2
            continue
        elif ch == "#" and hash_comments:
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif ch in "([{":
            stack.append((ch, line))
        elif ch in ")]}":
            if stack and stack[-1][0] == pairs[ch]:
                stack.pop()
            else:
                issues.append(FileIssue("source.brackets", "Unbalanced brackets", "medium",
                                        f"Unexpected '{ch}' on line {line}.", HUMAN,
                                        "Where the missing bracket belongs depends on intent.", ["Check the code around the line"],
                                        "Regular expressions and unusual string syntax can confuse this check.", 0.5, line=line))
                return "bracket structure check"
        i += 1
    if stack:
        ch, ln = stack[-1]
        issues.append(FileIssue("source.brackets", "Unclosed bracket", "medium", f"'{ch}' opened on line {ln} is never closed.",
                                HUMAN, "Where it should close depends on intent.", ["Check the code from that line"],
                                "Regular expressions and unusual string syntax can confuse this check.", 0.5, line=ln))
    return "bracket structure check"


def _png(data: bytes, ident: Identity, issues: list[FileIssue]) -> str:
    pos, seen_end, bad_crc, chunks = 8, False, [], 0
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        if pos + 12 + length > len(data):
            issues.append(FileIssue("png.truncated", "PNG is truncated", "high",
                                    f"Chunk {ctype.decode('latin-1')} claims {length} bytes but the file ends first.",
                                    NOT_FIXABLE, "The missing image data is gone; it cannot be reconstructed.",
                                    ["Restore the image from its source or a backup"], confidence=0.95,
                                    requirements="The original image."))
            return "PNG chunk parser"
        crc = int.from_bytes(data[pos + 8 + length:pos + 12 + length], "big")
        if zlib.crc32(ctype + data[pos + 8:pos + 8 + length]) & 0xFFFFFFFF != crc:
            bad_crc.append(ctype.decode("latin-1"))
        chunks += 1
        pos += 12 + length
        if ctype == b"IEND":
            seen_end = True
            break
    if bad_crc:
        issues.append(FileIssue("png.crc", "PNG checksum errors", "medium",
                                f"{len(bad_crc)} chunk(s) have wrong checksums: {', '.join(bad_crc[:5])}.", PARTIAL,
                                "The checksums can be recomputed so viewers accept the file, but if the chunk data itself is "
                                "damaged the picture may still look wrong.", ["Recompute the chunk checksums"],
                                "Cannot verify that the pixel data is intact.", 0.7, repair="png_fix_crc"))
    if not seen_end and not issues:
        issues.append(FileIssue("png.no-end", "PNG has no end marker", "medium", "The IEND chunk is missing.", PARTIAL,
                                "An end marker can be added; any image data after the cut is lost.", ["Append the IEND chunk"],
                                confidence=0.7, repair="png_append_iend"))
    return f"PNG chunk parser ({chunks} chunks)"


def _jpeg(data: bytes, ident: Identity, issues: list[FileIssue]) -> str:
    if not data.rstrip(b"\x00").endswith(b"\xff\xd9"):
        issues.append(FileIssue("jpeg.truncated", "JPEG has no end-of-image marker", "medium",
                                "The file does not end with FFD9, so it was probably cut off.", PARTIAL,
                                "Adding the marker lets viewers open it, but the missing lower part of the image stays missing.",
                                ["Append the end-of-image marker", "Restore the original"], confidence=0.75, repair="jpeg_append_eoi"))
    return "JPEG marker check"


def _gif(data: bytes, ident: Identity, issues: list[FileIssue]) -> str:
    if not data.endswith(b"\x3b"):
        issues.append(FileIssue("gif.truncated", "GIF has no trailer", "medium", "The file does not end with the 0x3B trailer.",
                                PARTIAL, "A trailer can be appended; frames after the cut are lost.", ["Append the trailer"],
                                confidence=0.7, repair="gif_append_trailer"))
    return "GIF trailer check"


def _pdf(data: bytes, ident: Identity, issues: list[FileIssue]) -> str:
    tail = data[-2048:]
    if b"%%EOF" not in tail:
        issues.append(FileIssue("pdf.truncated", "PDF end marker missing", "high",
                                "The %%EOF marker is not at the end of the file, so the document is probably incomplete.",
                                HUMAN, "Readers may recover pages, but missing content cannot be recreated.",
                                ["Re-export or re-download the document"], confidence=0.8, requirements="The original document."))
    if b"startxref" not in data[-8192:]:
        issues.append(FileIssue("pdf.no-xref", "PDF cross-reference table missing", "medium",
                                "Without startxref the reader has to rebuild the object index.", HUMAN,
                                "A PDF tool can rebuild the index; AutoPatch does not rewrite PDFs.",
                                ["Open and re-save it with a PDF tool (e.g. qpdf)"], confidence=0.7))
    if b"/Encrypt" in data[-65536:] or b"/Encrypt" in data[:65536]:
        issues.append(FileIssue("pdf.encrypted", "PDF is encrypted", "info", "Its content cannot be inspected without the password.",
                                HUMAN, "Inspection needs the password.", ["Provide an unencrypted copy"], confidence=0.9,
                                requirements="The document password."))
    return "PDF structure check"


def _zip(path: Path, ident: Identity, issues: list[FileIssue]) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if any(i.flag_bits & 0x1 for i in infos):
                issues.append(FileIssue("archive.encrypted", "Archive is password-protected", "info",
                                        "Some entries are encrypted, so they cannot be inspected.", HUMAN,
                                        "Inspection needs the password.", ["Provide the password or an unencrypted copy"],
                                        confidence=0.95, requirements="The archive password."))
                return "ZIP directory"
            total = sum(i.file_size for i in infos)
            packed = sum(i.compress_size for i in infos) or 1
            if total > 1_000_000_000 and total / packed > 200:
                issues.append(FileIssue("archive.bomb", "Archive expands enormously", "high",
                                        f"{packed:,} bytes expand to {total:,} bytes (ratio {total // packed}:1).", HUMAN,
                                        "This looks like a decompression bomb; it was not extracted.", ["Check where the archive came from"],
                                        confidence=0.8))
                return "ZIP directory"
            bad = z.testzip()
            if bad:
                issues.append(FileIssue("archive.corrupt-entry", "Damaged archive entry", "high",
                                        f"{bad} fails its checksum.", NOT_FIXABLE,
                                        "The damaged bytes cannot be reconstructed from the archive.",
                                        ["Restore the archive from its source"], confidence=0.95))
            return f"ZIP integrity test ({len(infos)} entries)"
    except zipfile.BadZipFile as exc:
        issues.append(FileIssue("archive.damaged", "Archive cannot be opened", "high", str(exc), NOT_FIXABLE,
                                "The central directory is missing or damaged.", ["Restore the archive from its source",
                                                                                  "Try a ZIP recovery tool to salvage entries"],
                                confidence=0.85))
        return "ZIP reader"


def _gzip(path: Path, ident: Identity, issues: list[FileIssue], limit: int) -> str:
    seen = 0
    try:
        with gzip.open(path, "rb") as fh:
            while chunk := fh.read(1024 * 1024):
                seen += len(chunk)
                if seen > limit:
                    return f"GZIP stream check (first {limit // (1024 * 1024)} MB)"
    except (EOFError, gzip.BadGzipFile, zlib.error, OSError) as exc:
        issues.append(FileIssue("archive.gzip-damaged", "Compressed stream is damaged or truncated", "high", str(exc),
                                NOT_FIXABLE, "The compressed data after the damage cannot be recovered.",
                                ["Restore the file from its source"], confidence=0.9))
    return "GZIP stream check"


def _tar(path: Path, ident: Identity, issues: list[FileIssue]) -> str:
    try:
        with tarfile.open(path) as t:
            count = sum(1 for _ in t)
        return f"TAR directory ({count} entries)"
    except (tarfile.TarError, EOFError, OSError) as exc:
        issues.append(FileIssue("archive.tar-damaged", "TAR archive is damaged", "high", str(exc), NOT_FIXABLE,
                                "Entries after the damage cannot be read.", ["Restore the archive from its source"], confidence=0.85))
        return "TAR reader"


def _sqlite(path: Path, ident: Identity, issues: list[FileIssue]) -> str:
    with tempfile.TemporaryDirectory(prefix="autopatch-db-") as tmp:
        copy = Path(tmp) / "copy.db"
        copy.write_bytes(path.read_bytes())
        try:
            conn = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
            rows = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchmany(5)]
            tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            conn.close()
        except sqlite3.DatabaseError as exc:
            issues.append(FileIssue("db.unreadable", "Database cannot be read", "high", str(exc), NOT_FIXABLE,
                                    "The database file is malformed.", ["Restore from a backup", "Try `sqlite3 file .recover`"],
                                    confidence=0.85))
            return "SQLite reader (read-only copy)"
    if rows != ["ok"]:
        issues.append(FileIssue("db.integrity", "Database integrity check failed", "high", "; ".join(rows), HUMAN,
                                "SQLite can sometimes salvage rows, but which data survives must be reviewed.",
                                ["Run `sqlite3 file .recover` and review the result", "Restore from a backup"], confidence=0.9))
    return f"SQLite integrity_check on a read-only copy ({tables} tables)"


def inspect(root: Path, ident: Identity, analysis_limit: int) -> Inspection:
    path = root / PurePosixPath(ident.path)
    if ident.size > analysis_limit and ident.category not in ("archive",):
        return Inspection("skipped", "file signature only", notes=[
            f"{ident.size:,} bytes is above the analysis limit of {analysis_limit:,} bytes (Settings > Limits). "
            "It was identified from its header but its contents were not checked."])
    issues: list[FileIssue] = []
    try:
        if ident.category == "empty":
            return Inspection("analyzed", "size check", notes=["Empty file."])
        if ident.is_text:
            data = path.read_bytes()
            text = _text_checks(data, ident, issues)
            fmt = ident.format.lower()
            name = PurePosixPath(ident.path).name.lower()
            if "json" in fmt or name.endswith((".json", ".jsonc")) or name in (".eslintrc", ".babelrc", ".prettierrc"):
                if name == ".prettierrc" and not text.lstrip().startswith("{"):
                    method = _yaml(text, ident, issues)
                else:
                    method = _json(text, ident, issues)
            elif "yaml" in fmt or name.endswith((".yaml", ".yml")):
                method = _yaml(text, ident, issues)
            elif "toml" in fmt or name.endswith(".toml"):
                method = _toml(text, ident, issues)
            elif fmt in ("xml", "svg image") or name.endswith((".xml", ".svg", ".xsd", ".plist", ".csproj")):
                method = _xml(text, ident, issues)
            elif name.endswith((".ini", ".cfg")) or name in ("setup.cfg", "tox.ini", ".editorconfig"):
                method = _ini(text, ident, issues) if name != ".editorconfig" else "editorconfig"
            elif name.startswith(".env"):
                method = _env(text, ident, issues)
            elif name.endswith((".csv", ".tsv")):
                method = _csv(text, ident, issues)
            elif ident.category == "source" and ident.language == "Python":
                method = "Python parser + analyzers (Ruff, Pylint, AutoPatch Core)"
                try:
                    ast.parse(text.lstrip("\ufeff"))
                except SyntaxError as exc:
                    fix = python_syntax_repair(text.lstrip("\ufeff"))
                    issues.append(FileIssue(
                        "python.syntax", f"Syntax error: {exc.msg}", "critical",
                        f"{exc.msg} on line {exc.lineno}. Python cannot run any code in this file until it is fixed.",
                        FIXABLE if fix else HUMAN,
                        ("A mechanical repair makes the whole file parse: " + "; ".join(fix[1][:5])) if fix else
                        "The intended code cannot be inferred from the error alone.",
                        ["Apply the syntax repair"] if fix else ["Correct the code at the reported line"],
                        "Only the broken lines change; the repaired file is checked with Python's parser.",
                        0.85 if fix else 0.9, line=exc.lineno or 0, column=exc.offset or 0,
                        repair="python_syntax" if fix else ""))
            elif ident.category == "source":
                method = _brackets(text, ident, issues)
                return Inspection("partially_analyzed", method, issues, [
                    f"Deep analysis for {ident.language} is not available yet: only encoding, secret and structure "
                    "checks ran. This is a missing AutoPatch capability, not a property of the file."])
            else:
                method = "text checks (encoding, line endings, secrets)"
            return Inspection("analyzed", method, issues)
        data_head = path.read_bytes() if ident.size <= 64 * 1024 * 1024 else b""
        fmt = ident.format
        if fmt == "PNG image" and data_head:
            return Inspection("analyzed", _png(data_head, ident, issues), issues)
        if fmt == "JPEG image" and data_head:
            return Inspection("analyzed", _jpeg(data_head, ident, issues), issues)
        if fmt == "GIF image" and data_head:
            return Inspection("analyzed", _gif(data_head, ident, issues), issues)
        if fmt == "PDF document" and data_head:
            return Inspection("analyzed", _pdf(data_head, ident, issues), issues)
        if ident.mime in ("application/zip", "application/java-archive", "application/vnd.android.package-archive") \
                or "openxmlformats" in ident.mime or "opendocument" in ident.mime:
            return Inspection("analyzed", _zip(path, ident, issues), issues)
        if fmt == "GZIP archive":
            return Inspection("analyzed", _gzip(path, ident, issues, analysis_limit), issues)
        if fmt == "TAR archive":
            return Inspection("analyzed", _tar(path, ident, issues), issues)
        if fmt == "SQLite database":
            return Inspection("analyzed", _sqlite(path, ident, issues), issues)
        if ident.category == "binary":
            return Inspection("identified_only", "file signature scan", [FileIssue(
                "format.unknown", "Format not recognized", "info",
                "No known signature matched, so the content could not be interpreted.", UNSUPPORTED,
                "AutoPatch has no inspector for this format yet. That says nothing about whether the file is healthy.",
                ["Tell AutoPatch what the format is (add an inspector)"], "Contents were not checked.", 1.0)])
        return Inspection("identified_only", "file signature", notes=[
            f"{fmt}: identified from its header. AutoPatch never modifies {ident.category} files it has no "
            "format-specific tool for, and has no deep integrity check for this format yet."])
    except PermissionError as exc:
        return Inspection("unreadable", "none", notes=[f"The file could not be read: {exc}"])
    except OSError as exc:
        return Inspection("unreadable", "none", notes=[f"Reading failed: {exc}"])


# ---------------------------------------------------------------- format-aware repairs


def _png_fix(data: bytes) -> bytes:
    out = bytearray(data[:8])
    pos = 8
    while pos + 12 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        out += data[pos:pos + 8 + length] + (zlib.crc32(ctype + body) & 0xFFFFFFFF).to_bytes(4, "big")
        pos += 12 + length
        if ctype == b"IEND":
            break
    return bytes(out)


def _png_iend(data: bytes) -> bytes:
    return data + (0).to_bytes(4, "big") + b"IEND" + (zlib.crc32(b"IEND") & 0xFFFFFFFF).to_bytes(4, "big")


def _eol(data: bytes) -> bytes:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    unix = data.replace(b"\r\n", b"\n")
    return unix if lf >= crlf else unix.replace(b"\n", b"\r\n")


REPAIRS = {
    "strip_bom": (lambda d: d[3:] if d.startswith(b"\xef\xbb\xbf") else d, "Removed the UTF-8 byte-order mark."),
    "normalize_eol": (_eol, "Made all line endings the same as the majority."),
    "reencode_utf8": (lambda d: d.decode("cp1252", errors="replace").encode("utf-8"), "Converted Windows-1252 text to UTF-8."),
    "json_cleanup": (lambda d: _strip_json_extras(d.decode("utf-8-sig")).encode("utf-8"),
                     "Removed trailing commas and comments; every value is unchanged."),
    "python_syntax": (lambda d: (python_syntax_repair(d.decode("utf-8-sig")) or (d.decode("utf-8-sig"), []))[0].encode("utf-8"),
                      "Repaired the syntax errors so Python can parse the file."),
    "yaml_tabs": (lambda d: _yaml_tabs(d.decode("utf-8-sig")).encode("utf-8"), "Replaced indentation tabs with spaces."),
    "png_fix_crc": (_png_fix, "Recomputed the PNG chunk checksums; chunk data is unchanged."),
    "png_append_iend": (_png_iend, "Appended the missing IEND chunk."),
    "jpeg_append_eoi": (lambda d: d + b"\xff\xd9", "Appended the JPEG end-of-image marker."),
    "gif_append_trailer": (lambda d: d + b"\x3b", "Appended the GIF trailer byte."),
}
