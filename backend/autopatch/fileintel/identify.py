"""Identify a file's real format from its bytes. Reads only the head and tail of large files."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from autopatch.analysis.base import LANGUAGE_BY_EXTENSION

HEAD = 64 * 1024

# (magic bytes, offset, format, mime, category)
SIGNATURES: list[tuple[bytes, int, str, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", 0, "PNG image", "image/png", "image"),
    (b"\xff\xd8\xff", 0, "JPEG image", "image/jpeg", "image"),
    (b"GIF87a", 0, "GIF image", "image/gif", "image"),
    (b"GIF89a", 0, "GIF image", "image/gif", "image"),
    (b"BM", 0, "BMP image", "image/bmp", "image"),
    (b"II*\x00", 0, "TIFF image", "image/tiff", "image"),
    (b"MM\x00*", 0, "TIFF image", "image/tiff", "image"),
    (b"\x00\x00\x01\x00", 0, "ICO icon", "image/x-icon", "image"),
    (b"8BPS", 0, "Photoshop document", "image/vnd.adobe.photoshop", "image"),
    (b"%PDF-", 0, "PDF document", "application/pdf", "document"),
    (b"{\\rtf", 0, "RTF document", "application/rtf", "document"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0, "OLE2 compound file (legacy Office/MSI)", "application/x-ole-storage", "document"),
    (b"PK\x03\x04", 0, "ZIP archive", "application/zip", "archive"),
    (b"PK\x05\x06", 0, "ZIP archive (empty)", "application/zip", "archive"),
    (b"\x1f\x8b", 0, "GZIP archive", "application/gzip", "archive"),
    (b"BZh", 0, "BZIP2 archive", "application/x-bzip2", "archive"),
    (b"\xfd7zXZ\x00", 0, "XZ archive", "application/x-xz", "archive"),
    (b"7z\xbc\xaf\x27\x1c", 0, "7-Zip archive", "application/x-7z-compressed", "archive"),
    (b"Rar!\x1a\x07", 0, "RAR archive", "application/vnd.rar", "archive"),
    (b"ustar", 257, "TAR archive", "application/x-tar", "archive"),
    (b"\x7fELF", 0, "ELF executable", "application/x-elf", "executable"),
    (b"MZ", 0, "Windows executable (PE)", "application/vnd.microsoft.portable-executable", "executable"),
    (b"\xcf\xfa\xed\xfe", 0, "Mach-O executable", "application/x-mach-binary", "executable"),
    (b"\xfe\xed\xfa\xcf", 0, "Mach-O executable", "application/x-mach-binary", "executable"),
    (b"\xca\xfe\xba\xbe", 0, "Java class file or Mach-O universal binary", "application/java-vm", "executable"),
    (b"\x00asm", 0, "WebAssembly module", "application/wasm", "executable"),
    (b"SQLite format 3\x00", 0, "SQLite database", "application/vnd.sqlite3", "database"),
    (b"ID3", 0, "MP3 audio", "audio/mpeg", "audio"),
    (b"fLaC", 0, "FLAC audio", "audio/flac", "audio"),
    (b"OggS", 0, "Ogg media", "audio/ogg", "audio"),
    (b"wOFF", 0, "WOFF font", "font/woff", "font"),
    (b"wOF2", 0, "WOFF2 font", "font/woff2", "font"),
    (b"\x00\x01\x00\x00\x00", 0, "TrueType font", "font/ttf", "font"),
    (b"OTTO", 0, "OpenType font", "font/otf", "font"),
]

CONFIG_NAMES = re.compile(
    r"(^\.env(\..+)?$|^\.(editorconfig|gitignore|gitattributes|dockerignore|npmrc|nvmrc|prettierrc|eslintrc|babelrc)(\..+)?$|"
    r"^(dockerfile|makefile|procfile|package\.json|tsconfig.*\.json|jsconfig\.json|pyproject\.toml|setup\.cfg|"
    r"tox\.ini|pytest\.ini|requirements.*\.txt|pipfile|cargo\.toml|go\.mod|pom\.xml|build\.gradle|\.?eslint\.config\.\w+|"
    r"vite\.config\.\w+|next\.config\.\w+|composer\.json|docker-compose.*\.ya?ml)$)", re.I)
DATA_EXT = {".json": "JSON", ".jsonc": "JSON with comments", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
            ".xml": "XML", ".csv": "CSV", ".tsv": "TSV", ".ini": "INI", ".cfg": "INI", ".conf": "Config text",
            ".svg": "SVG image", ".geojson": "JSON", ".ipynb": "Jupyter notebook (JSON)", ".lock": "Lock file",
            ".sql": "SQL", ".md": "Markdown", ".rst": "reStructuredText", ".txt": "Plain text", ".log": "Log text",
            ".html": "HTML", ".htm": "HTML", ".css": "CSS"}
DOC_EXT = {".md", ".rst", ".txt", ".adoc"}
SHEBANG = {"python": "Python", "node": "JavaScript", "bash": "Shell", "sh": "Shell", "zsh": "Shell", "ruby": "Ruby",
           "perl": "Perl", "php": "PHP", "pwsh": "PowerShell"}


@dataclass
class Identity:
    path: str
    size: int
    format: str
    mime: str
    category: str          # source | config | data | document | image | audio | video | archive | executable | database | font | binary | empty
    encoding: str = ""     # for text files
    language: str = ""     # programming language, when it is code
    is_text: bool = False
    extension_mismatch: str = ""  # set when the content contradicts the extension
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def detect_encoding(data: bytes) -> tuple[str | None, str]:
    """Return (encoding, note). encoding is None for binary data."""
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", "UTF-8 with byte-order mark"
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "utf-32", "UTF-32"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", "UTF-16"
    sample = data[:HEAD]
    if b"\x00" in sample:
        return None, "binary"
    try:
        sample.decode("utf-8")
        return "utf-8", "UTF-8"
    except UnicodeDecodeError as exc:
        if exc.start > len(sample) - 4:  # a multi-byte character cut by the sample boundary
            return "utf-8", "UTF-8"
    control = sum(1 for b in sample if b < 9 or 13 < b < 32)
    if control / max(len(sample), 1) > 0.05:
        return None, "binary"
    return "cp1252", "legacy 8-bit text (not valid UTF-8)"


def _zip_kind(path: Path) -> tuple[str, str, str]:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()[:2000]
    except (zipfile.BadZipFile, OSError):
        return "ZIP archive (damaged)", "application/zip", "archive"
    joined = " ".join(names)
    if "word/document.xml" in joined:
        return "Word document (DOCX)", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document"
    if "xl/workbook.xml" in joined:
        return "Excel workbook (XLSX)", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "document"
    if "ppt/presentation.xml" in joined:
        return "PowerPoint deck (PPTX)", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "document"
    if "AndroidManifest.xml" in joined:
        return "Android package (APK)", "application/vnd.android.package-archive", "archive"
    if "META-INF/MANIFEST.MF" in joined:
        return "Java archive (JAR)", "application/java-archive", "archive"
    if "mimetype" in names and any(n.startswith("content.xml") for n in names):
        return "OpenDocument file", "application/vnd.oasis.opendocument", "document"
    return "ZIP archive", "application/zip", "archive"


def identify(root: Path, rel: str) -> Identity:
    path = root / PurePosixPath(rel)
    size = path.stat().st_size
    name = PurePosixPath(rel).name
    ext = PurePosixPath(rel).suffix.lower()
    if size == 0:
        return Identity(rel, 0, "Empty file", "application/x-empty", "empty")
    with open(path, "rb") as fh:
        head = fh.read(HEAD)
    for magic, offset, fmt, mime, cat in SIGNATURES:
        if head[offset: offset + len(magic)] == magic:
            if magic == b"MZ" and len(head) < 64:
                continue
            if magic == b"BM" and ext not in (".bmp", ".dib"):
                continue  # "BM" is too short to trust without the extension
            if magic.startswith(b"\x00\x01\x00\x00") and ext not in (".ttf", ".ttc"):
                continue
            if magic == b"\x00\x00\x01\x00" and ext != ".ico":
                continue
            if fmt.startswith("ZIP archive") and magic == b"PK\x03\x04":
                fmt, mime, cat = _zip_kind(path)
            ident = Identity(rel, size, fmt, mime, cat)
            expected = _expected_category(ext)
            if expected and expected != cat and not (cat == "archive" and ext in (".jar", ".apk", ".whl", ".egg", ".nupkg", ".vsix")) \
                    and not (cat == "document" and ext in (".docx", ".xlsx", ".pptx", ".odt", ".ods")):
                ident.extension_mismatch = f"the {ext} extension suggests {expected}, but the content is a {fmt}"
            return ident
    if head[4:8] == b"ftyp":
        return Identity(rel, size, "MP4/QuickTime media", "video/mp4", "video")
    if head[:4] == b"RIFF" and head[8:12] in (b"WAVE", b"WEBP", b"AVI "):
        kind = {b"WAVE": ("WAV audio", "audio/wav", "audio"), b"WEBP": ("WebP image", "image/webp", "image"),
                b"AVI ": ("AVI video", "video/x-msvideo", "video")}[head[8:12]]
        return Identity(rel, size, *kind)

    encoding, note = detect_encoding(head)
    if encoding is None:
        return Identity(rel, size, "Unrecognized binary data", "application/octet-stream", "binary",
                        details={"note": "no known file signature"})
    text = head.decode(encoding if encoding != "utf-8-sig" else "utf-8-sig", errors="replace")
    stripped = text.lstrip()
    language = LANGUAGE_BY_EXTENSION.get(ext, "")
    fmt = DATA_EXT.get(ext, "")
    if not language and text.startswith("#!"):
        first = text.split("\n", 1)[0]
        for key, lang in SHEBANG.items():
            if re.search(rf"\b{key}[\d.]*\b", first):
                language = lang
                break
    if not fmt and not language:
        if stripped.startswith("<?xml") or stripped.startswith("<svg"):
            fmt = "SVG image" if "<svg" in stripped[:500] else "XML"
        elif stripped[:15].lower().startswith(("<!doctype html", "<html")):
            fmt = "HTML"
        elif stripped[:1] in "{[":
            fmt = "JSON"
        elif stripped.startswith("<?php"):
            language = "PHP"
    if CONFIG_NAMES.search(name):
        category = "config"
        fmt = fmt or ("Environment variables (.env)" if name.lower().startswith(".env") else
                      "Dockerfile" if name.lower() == "dockerfile" else "Configuration")
    elif language and language not in ("JSON", "YAML", "TOML", "Markdown", "HTML", "CSS"):
        category, fmt = "source", fmt or f"{language} source"
    elif ext in DOC_EXT:
        category, fmt = "document", fmt or "Text document"
    elif fmt:
        category = "data"
    else:
        category, fmt = "document", "Plain text"
    ident = Identity(rel, size, fmt or "Text", "text/plain", category, encoding=note, language=language, is_text=True)
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".exe", ".dll", ".so", ".db", ".sqlite"):
        ident.extension_mismatch = f"the {ext} extension suggests binary content, but the file is text"
    ident.details["codec"] = encoding
    return ident


def _expected_category(ext: str) -> str:
    return {".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".bmp": "image", ".webp": "image",
            ".pdf": "document", ".docx": "document", ".xlsx": "document", ".zip": "archive", ".gz": "archive",
            ".tgz": "archive", ".tar": "archive", ".exe": "executable", ".dll": "executable", ".so": "executable",
            ".db": "database", ".sqlite": "database", ".mp3": "audio", ".wav": "audio", ".mp4": "video",
            ".json": "data", ".txt": "document", ".py": "source", ".js": "source"}.get(ext, "")


def read_text(root: Path, rel: str, ident: Identity) -> str:
    codec = ident.details.get("codec", "utf-8")
    return (root / PurePosixPath(rel)).read_bytes().decode(codec, errors="replace")


__all__ = ["Identity", "identify", "detect_encoding", "read_text", "io"]
