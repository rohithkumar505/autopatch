"""Each project lives in its own git repository inside AutoPatch's data folder.

The user's original files are never edited. The first commit is the upload exactly as
received; every accepted patch or restore is a new commit, so any earlier version can be
viewed, diffed, exported or restored.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from autopatch.config import paths
from autopatch.core.files import ProjectImportError, copy_tree, rmtree, safe_relpath


class GitError(RuntimeError):
    pass


@dataclass
class Version:
    sha: str
    short: str
    message: str
    date: str
    parent: str | None


def _git(cwd: Path, *args: str, check: bool = True, binary: bool = False) -> str | bytes:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, env=env, timeout=120)
    if check and proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


class Workspace:
    def __init__(self, project_id: int):
        self.project_id = project_id
        self.root = paths().projects / str(project_id) / "repo"

    # ------------------------------------------------------------------ setup

    @classmethod
    def create(cls, project_id: int, source_dir: Path) -> "Workspace":
        ws = cls(project_id)
        if ws.root.exists():
            rmtree(ws.root)
        ws.root.mkdir(parents=True)
        copy_tree(source_dir, ws.root)
        ws._init_repo()
        return ws

    def _init_repo(self) -> None:
        g = self.git
        g("init", "-q", "-b", "main")
        no_hooks = self.root / ".git" / "autopatch-no-hooks"
        no_hooks.mkdir(exist_ok=True)
        # Repository-local settings for AutoPatch's private store: keep bytes exactly as
        # uploaded, and do not run the user's global hooks or signing on internal commits.
        for key, value in [
            ("user.name", "AutoPatch"),
            ("user.email", "autopatch@localhost"),
            ("core.autocrlf", "false"),
            ("core.safecrlf", "false"),
            ("core.quotepath", "false"),
            ("commit.gpgsign", "false"),
            ("core.hooksPath", no_hooks.as_posix()),
        ]:
            g("config", key, value)
        g("add", "-A")
        g("commit", "-q", "--allow-empty", "-m", "Original upload")

    def exists(self) -> bool:
        return (self.root / ".git").is_dir()

    def delete(self) -> None:
        rmtree(self.root.parent)

    # ------------------------------------------------------------------ git helpers

    def git(self, *args: str, check: bool = True) -> str:
        return _git(self.root, *args, check=check)  # type: ignore[return-value]

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def resolve(self, version: str | None) -> str:
        if not version or version == "HEAD":
            return self.head()
        out = self.git("rev-parse", "--verify", "--quiet", f"{version}^{{commit}}", check=False).strip()
        if not out:
            raise GitError(f"Unknown version {version}")
        return out

    def commit_all(self, message: str) -> str | None:
        self.git("add", "-A")
        if not self.git("status", "--porcelain").strip():
            return None
        self.git("commit", "-q", "-m", message)
        return self.head()

    # ------------------------------------------------------------------ reading

    def versions(self) -> list[Version]:
        out = self.git("log", "--format=%H%x1f%h%x1f%s%x1f%cI%x1f%P%x1e")
        result = []
        for record in out.split("\x1e"):
            record = record.strip()
            if not record:
                continue
            sha, short, msg, date, parents = (record.split("\x1f") + [""] * 5)[:5]
            result.append(Version(sha, short, msg, date, parents.split()[0] if parents.strip() else None))
        return result

    def list_files(self, version: str | None = None) -> list[str]:
        sha = self.resolve(version)
        out = self.git("ls-tree", "-r", "--name-only", "-z", sha)
        return sorted(p for p in out.split("\x00") if p)

    def _check(self, path: str) -> str:
        return str(safe_relpath(path))

    def resolve_contained(self, path: str) -> tuple[str, Path]:
        """Return (posix_rel, absolute Path) guaranteed to stay inside this workspace."""
        rel = self._check(path)
        root = self.root.resolve()
        target = (self.root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ProjectImportError(f"Rejected path outside workspace: {path!r}") from exc
        return rel, target

    def read_bytes(self, path: str, version: str | None = None) -> bytes:
        rel = self._check(path)
        sha = self.resolve(version)
        try:
            return _git(self.root, "show", f"{sha}:{rel}", binary=True)  # type: ignore[return-value]
        except GitError as exc:
            raise FileNotFoundError(f"{rel} does not exist in version {sha[:8]}") from exc

    def blob_shas(self, version: str | None = None) -> dict[str, str]:
        """{path: git blob id}. Same id means same content, so analysis can be reused."""
        out = self.git("ls-tree", "-r", "-z", self.resolve(version))
        result = {}
        for entry in out.split(chr(0)):
            if chr(9) in entry:
                meta, path = entry.split(chr(9), 1)
                result[path] = meta.split()[2]
        return result

    def write_bytes(self, path: str, data: bytes) -> None:
        _rel, target = self.resolve_contained(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def iter_worktree(self) -> tuple[list[str], list[str]]:
        """List files and directories on disk (includes empty folders). Skips .git."""
        root = self.root.resolve()
        files: list[str] = []
        dirs: list[str] = []
        if not root.is_dir():
            return files, dirs
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != ".git" and not d.startswith(".git")]
            rel_dir = Path(dirpath).resolve().relative_to(root)
            rel_posix = "" if str(rel_dir) == "." else rel_dir.as_posix()
            if rel_posix:
                dirs.append(rel_posix)
            for name in filenames:
                if name == ".DS_Store":
                    continue
                rel = f"{rel_posix}/{name}" if rel_posix else name
                files.append(rel)
        return sorted(files), sorted(dirs)

    def read_text(self, path: str, version: str | None = None) -> str:
        """File content with line endings normalized to \\n."""
        return self.read_bytes(path, version).decode("utf-8", "replace").replace("\r\n", "\n")

    def uses_crlf(self, path: str) -> bool:
        try:
            return b"\r\n" in self.read_bytes(path)
        except FileNotFoundError:
            return False

    def diff(self, base: str, target: str | None = None, path: str | None = None) -> str:
        args = ["diff", "--no-color", self.resolve(base), self.resolve(target)]
        if path:
            args += ["--", self._check(path)]
        return self.git(*args)

    def commit_diff(self, sha: str) -> str:
        return self.git("show", "--no-color", "--format=", self.resolve(sha))

    def changed_files(self, sha: str) -> list[str]:
        out = self.git("show", "--name-only", "--format=", "-z", self.resolve(sha))
        return [p for p in out.split("\x00") if p.strip()]

    def archive_zip(self, version: str | None = None) -> bytes:
        return _git(self.root, "archive", "--format=zip", self.resolve(version), binary=True)  # type: ignore[return-value]

    def export_to(self, dest: Path, version: str | None = None) -> None:
        """Write the files of a version into dest (used to build sandbox copies)."""
        sha = self.resolve(version)
        for rel in self.list_files(sha):
            target = dest / PurePosixPath(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.read_bytes(rel, sha))

    # ------------------------------------------------------------------ changing

    def write_text(self, path: str, content: str, crlf: bool | None = None) -> None:
        rel, target = self.resolve_contained(path)
        if crlf is None:
            crlf = self.uses_crlf(rel) if target.exists() else False
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\r\n" if crlf else "\n") as fh:
            fh.write(content)

    def mkdir(self, path: str) -> None:
        rel, target = self.resolve_contained(path)
        if target.exists() and not target.is_dir():
            raise ValueError(f"{rel} already exists as a file")
        target.mkdir(parents=True, exist_ok=True)

    def delete_path(self, path: str) -> None:
        rel, target = self.resolve_contained(path)
        if not target.exists():
            raise FileNotFoundError(f"{rel} does not exist")
        if target == self.root.resolve():
            raise ValueError("Cannot delete the workspace root")
        if target.is_dir():
            rmtree(target)
        else:
            target.unlink()

    def rename_path(self, src: str, dest: str) -> None:
        from_rel, source = self.resolve_contained(src)
        to_rel, target = self.resolve_contained(dest)
        if not source.exists():
            raise FileNotFoundError(f"{from_rel} does not exist")
        if target.exists():
            raise ValueError(f"{to_rel} already exists")
        if source.is_dir():
            try:
                target.resolve().relative_to(source.resolve())
            except ValueError:
                pass
            else:
                raise ValueError("Cannot move a folder into itself")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)

    def copy_path(self, src: str, dest: str) -> None:
        import shutil

        from_rel, source = self.resolve_contained(src)
        to_rel, target = self.resolve_contained(dest)
        if not source.exists():
            raise FileNotFoundError(f"{from_rel} does not exist")
        if target.exists():
            raise ValueError(f"{to_rel} already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)

    def working_tree_status(self) -> list[dict]:
        """Uncommitted changes as {path, status} using git porcelain."""
        out = self.git("status", "--porcelain", "-z")
        entries: list[dict] = []
        parts = [p for p in out.split("\x00") if p]
        i = 0
        while i < len(parts):
            item = parts[i]
            if len(item) < 4:
                i += 1
                continue
            code, path = item[:2], item[3:]
            # Rename/copy lines are followed by the original path.
            if code[0] in "RC" or code[1] in "RC":
                i += 1
            entries.append({"path": path, "status": code.strip() or code})
            i += 1
        return entries

    def discard_uncommitted(self) -> None:
        self.git("reset", "-q", "--hard", "HEAD")
        self.git("clean", "-q", "-fd")

    def restore(self, version: str, message: str) -> str | None:
        """Make the working tree identical to `version` and commit that as a new version."""
        sha = self.resolve(version)
        self.git("restore", f"--source={sha}", "--staged", "--worktree", "--", ":/")
        return self.commit_all(message)

    def revert(self, sha: str, message: str) -> str:
        """Undo one earlier commit on top of the current version."""
        target = self.resolve(sha)
        result = subprocess.run(
            ["git", "revert", "--no-commit", target], cwd=self.root, capture_output=True, timeout=120
        )
        if result.returncode != 0:
            self.git("revert", "--abort", check=False)
            self.discard_uncommitted()
            raise GitError(
                "This change cannot be undone automatically because later changes touch the same lines. "
                "Restore an earlier version instead."
            )
        new = self.commit_all(message)
        if new is None:
            raise GitError("Reverting produced no change; it was probably undone already")
        return new
