"""Isolated execution for project code and tests.

Backends, strongest first:
  docker        container with no network, read-only root, dropped capabilities, memory/CPU/PID caps
  appcontainer  Windows AppContainer + Job Object: no network, no file access outside the run
                folder and AutoPatch's private Python runtime, memory/process/time caps
  local         Job Object (Windows) or rlimits (POSIX) plus a Python-level network block.
                Limited isolation: only used when the user explicitly allows it in Settings.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

from autopatch.config import paths
from autopatch.testing import runtime

APPCONTAINER_NAME = "AutoPatch.Sandbox"
DOCKER_IMAGE = "autopatch-sandbox:py3.13"
DOCKERFILE = "FROM python:3.13-slim\nRUN pip install --no-cache-dir pytest hypothesis\n"

NETWORK_GUARD = '''"""Installed by AutoPatch: no network access from tests in the sandbox."""
import socket as _socket

def _blocked(*_args, **_kwargs):
    raise PermissionError("Network access is disabled in the AutoPatch sandbox")

for _name in ("connect", "connect_ex", "sendto", "bind", "listen"):
    setattr(_socket.socket, _name, _blocked)
_socket.create_connection = _blocked
_socket.getaddrinfo = _blocked
'''


class SandboxUnavailable(RuntimeError):
    pass


@dataclass
class ExecResult:
    exit_code: int | None
    timed_out: bool
    duration_s: float
    output: str
    peak_memory_mb: float = 0.0


@dataclass
class SandboxInfo:
    name: str
    label: str
    isolation: str  # strong | limited
    available: bool
    ready: bool
    detail: str
    guarantees: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Sandbox(ABC):
    name: str
    label: str
    isolation: str

    @abstractmethod
    def info(self) -> SandboxInfo: ...

    def prepare(self, log=lambda m: None) -> SandboxInfo:
        return self.info()

    @abstractmethod
    def run(self, run_dir: Path, python_args: list[str], timeout_s: float, memory_mb: int) -> ExecResult: ...


def _read_output(path: Path, limit: int = 40_000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return text if len(text) <= limit else "...(output truncated)...\n" + text[-limit:]


def _fill(args: list[str], run_root: str) -> list[str]:
    return [a.replace("{run}", run_root) for a in args]


def python_package_roots(project: Path) -> list[Path]:
    """Project root plus first-level dirs that look like importable packages / app folders.

    Demo and challenge zips often use flat layouts (library_app/library.py imported as
    `from library import ...`), so each app folder must be on PYTHONPATH.
    """
    roots = [project]
    if (project / "src").is_dir():
        roots.append(project / "src")
    try:
        children = sorted(project.iterdir())
    except OSError:
        return roots
    for child in children:
        if not child.is_dir() or child.name.startswith(".") or child.name in ("tests", "test", "node_modules", "__pycache__", "venv", ".venv"):
            continue
        if any(child.glob("*.py")) or (child / "__init__.py").exists():
            roots.append(child)
    return roots


def pythonpath_env(run_dir: Path, *, posix: bool = True) -> str:
    """PYTHONPATH for a sandbox run: network guard, project root, and package folders."""
    sep = ":" if posix else ";"
    root = run_dir
    project = root / "project"
    parts = [str(root / "guard"), str(project)]
    for pkg in python_package_roots(project)[1:]:  # skip project itself (already added)
        parts.append(str(pkg))
    return sep.join(parts)


def docker_pythonpath(run_dir: Path) -> str:
    """Same as pythonpath_env but with /work paths used inside the container."""
    parts = ["/work/guard", "/work/project"]
    project = run_dir / "project"
    for pkg in python_package_roots(project)[1:]:
        rel = pkg.relative_to(project).as_posix()
        parts.append(f"/work/project/{rel}")
    return ":".join(parts)


# ---------------------------------------------------------------- Windows AppContainer


class AppContainerSandbox(Sandbox):
    name = "appcontainer"
    label = "Windows AppContainer"
    isolation = "strong"
    guarantees = [
        "No network access (internet, DNS and localhost are blocked)",
        "No access to your files: only the test's own folder and AutoPatch's private Python",
        "Memory, process-count and CPU-time limits; everything is killed on timeout",
        "No clipboard, desktop or display-settings access",
    ]

    def _marker(self) -> Path:
        return paths().sandbox / ".appcontainer.json"

    def info(self) -> SandboxInfo:
        if sys.platform != "win32":
            return SandboxInfo(self.name, self.label, self.isolation, False, False, "Windows only")
        rt = runtime.status()
        marker = self._marker()
        state = json.loads(marker.read_text()) if marker.exists() else {}
        ready = rt.get("ready", False) and state.get("runtime") == str(paths().runtime) and bool(state.get("sid"))
        if ready:
            detail = f"Ready. Self-test: {state.get('self_test', 'not run')}"
        elif not rt.get("ready"):
            detail = "Needs setup: AutoPatch will copy a private Python runtime and create the sandbox identity"
        else:
            detail = "Needs setup: the sandbox identity has not been given access to the runtime yet"
        return SandboxInfo(self.name, self.label, self.isolation, True, ready, detail, self.guarantees)

    def prepare(self, log=lambda m: None) -> SandboxInfo:
        from autopatch.testing import winproc

        if not runtime.status().get("ready"):
            runtime.build(log)
        log("Creating the AppContainer identity")
        sid = winproc.appcontainer_sid(APPCONTAINER_NAME)
        log("Granting it read access to the private runtime")
        winproc.grant(str(paths().runtime), sid, "RX")
        log("Granting it write access to the sandbox work folder")
        winproc.grant(str(paths().sandbox), sid, "M")
        self._marker().write_text(json.dumps({"sid": sid, "runtime": str(paths().runtime)}), encoding="utf-8")
        log("Running the isolation self-test")
        result = self_test(self)
        state = json.loads(self._marker().read_text())
        state["self_test"] = "passed" if result["passed"] else "FAILED"
        state["self_test_detail"] = result["checks"]
        self._marker().write_text(json.dumps(state), encoding="utf-8")
        if not result["passed"]:
            raise RuntimeError("Sandbox self-test failed: " + "; ".join(
                f"{c['check']}: {c['result']}" for c in result["checks"] if not c["ok"]))
        log("Sandbox ready")
        return self.info()

    def run(self, run_dir: Path, python_args: list[str], timeout_s: float, memory_mb: int) -> ExecResult:
        from autopatch.testing import winproc

        state = json.loads(self._marker().read_text())
        root = str(run_dir)
        env = _windows_env(run_dir)
        env["LOCALAPPDATA"] = os.environ.get("LOCALAPPDATA", root)  # required; Windows maps it into the container
        out = run_dir / "out" / "console.txt"
        res = winproc.run(
            [str(runtime.python_exe()), "-s", *_fill(python_args, root)],
            cwd=str(run_dir / "project"), env=env, output_path=str(out), timeout_s=timeout_s,
            memory_mb=memory_mb, appcontainer_sid_str=state["sid"],
        )
        return ExecResult(res.exit_code, res.timed_out, res.duration_s, _read_output(out), res.peak_memory_mb)


def _windows_env(run_dir: Path) -> dict[str, str]:
    root = str(run_dir)
    sysroot = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return {
        "SYSTEMROOT": sysroot,
        "WINDIR": os.environ.get("WINDIR", sysroot),
        "PATH": f"{runtime.python_exe().parent};{sysroot}\\System32",
        "TEMP": f"{root}\\tmp",
        "TMP": f"{root}\\tmp",
        "HOME": f"{root}\\home",
        "PYTHONHOME": str(runtime.python_exe().parent),
        "PYTHONPATH": pythonpath_env(run_dir, posix=False),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONHASHSEED": "0",
        "NUMBER_OF_PROCESSORS": os.environ.get("NUMBER_OF_PROCESSORS", "1"),
        "PROCESSOR_ARCHITECTURE": os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"),
        "AUTOPATCH_SANDBOX": "1",
    }


# ---------------------------------------------------------------- Docker


class DockerSandbox(Sandbox):
    name = "docker"
    label = "Docker container"
    isolation = "strong"
    guarantees = [
        "No network (--network none)",
        "Read-only container filesystem; only the test folder is mounted",
        "Unprivileged user, all Linux capabilities dropped",
        "Memory, CPU and process-count limits; killed on timeout",
    ]

    def _docker(self, *args: str, timeout: float = 15, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, **kw)

    def info(self) -> SandboxInfo:
        if not shutil.which("docker"):
            return SandboxInfo(self.name, self.label, self.isolation, False, False,
                               "Docker is not installed", self.guarantees)
        try:
            ver = self._docker("version", "--format", "{{.Server.Version}}")
        except subprocess.TimeoutExpired:
            return SandboxInfo(self.name, self.label, self.isolation, False, False,
                               "Docker did not respond", self.guarantees)
        if ver.returncode != 0:
            return SandboxInfo(self.name, self.label, self.isolation, False, False,
                               "Docker is installed but its engine is not running", self.guarantees)
        image = self._docker("image", "inspect", DOCKER_IMAGE)
        ready = image.returncode == 0
        detail = f"Docker {ver.stdout.strip()}" + ("" if ready else f"; image {DOCKER_IMAGE} needs to be built")
        return SandboxInfo(self.name, self.label, self.isolation, True, ready, detail, self.guarantees)

    def prepare(self, log=lambda m: None) -> SandboxInfo:
        log(f"Building {DOCKER_IMAGE}")
        proc = self._docker("build", "-t", DOCKER_IMAGE, "-", input=DOCKERFILE, timeout=900)
        if proc.returncode != 0:
            raise RuntimeError(f"docker build failed: {proc.stderr[-500:]}")
        return self.info()

    def run(self, run_dir: Path, python_args: list[str], timeout_s: float, memory_mb: int) -> ExecResult:
        name = f"autopatch-{uuid.uuid4().hex[:10]}"
        argv = [
            "docker", "run", "--rm", "--name", name, "--network", "none",
            "--memory", f"{memory_mb}m", "--memory-swap", f"{memory_mb}m", "--cpus", "1", "--pids-limit", "128",
            "--read-only", "--tmpfs", "/tmp:rw,size=64m", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", "65534:65534",
            "-v", f"{run_dir}:/work", "-w", "/work/project",
            "-e", f"PYTHONPATH={docker_pythonpath(run_dir)}", "-e", "HOME=/work/home",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONHASHSEED=0", "-e", "AUTOPATCH_SANDBOX=1",
            DOCKER_IMAGE, "python", "-s", *_fill(python_args, "/work"),
        ]
        start = time.monotonic()
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout_s + 30)
            return ExecResult(proc.returncode, False, round(time.monotonic() - start, 2),
                              (proc.stdout + proc.stderr)[-40_000:])
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
            return ExecResult(None, True, round(time.monotonic() - start, 2), f"Killed after {timeout_s}s")


# ---------------------------------------------------------------- limited local


class LocalSandbox(Sandbox):
    name = "local"
    label = "Local process (limited isolation)"
    isolation = "limited"
    guarantees = [
        "Runs on a throwaway copy of the project, never on your files",
        "Memory, process-count and time limits; killed on timeout",
        "Network blocked inside Python only (code that bypasses Python's socket module is not stopped)",
        "Can read other files your user account can read",
    ]

    def info(self) -> SandboxInfo:
        if sys.platform == "win32" and not runtime.status().get("ready"):
            return SandboxInfo(self.name, self.label, self.isolation, True, False,
                               "Needs the private Python runtime (created by sandbox setup)", self.guarantees)
        return SandboxInfo(self.name, self.label, self.isolation, True, True,
                           "Available; weaker than the other options", self.guarantees)

    def prepare(self, log=lambda m: None) -> SandboxInfo:
        if sys.platform == "win32" and not runtime.status().get("ready"):
            runtime.build(log)
        return self.info()

    def run(self, run_dir: Path, python_args: list[str], timeout_s: float, memory_mb: int) -> ExecResult:
        root = str(run_dir)
        out = run_dir / "out" / "console.txt"
        if sys.platform == "win32":
            from autopatch.testing import winproc

            res = winproc.run([str(runtime.python_exe()), "-s", *_fill(python_args, root)],
                              cwd=str(run_dir / "project"), env=_windows_env(run_dir), output_path=str(out),
                              timeout_s=timeout_s, memory_mb=memory_mb)
            return ExecResult(res.exit_code, res.timed_out, res.duration_s, _read_output(out), res.peak_memory_mb)
        return self._run_posix(run_dir, python_args, timeout_s, memory_mb)

    def _run_posix(self, run_dir: Path, python_args: list[str], timeout_s: float, memory_mb: int) -> ExecResult:
        import resource
        import signal

        def limits():  # runs in the child before exec
            # macOS does not allow setting RLIMIT_AS / RLIMIT_RSS from user code reliably.
            if sys.platform != "darwin":
                mem = memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
            resource.setrlimit(resource.RLIMIT_CPU, (int(timeout_s * 2),) * 2)
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))

        root = str(run_dir)
        env = {"PATH": "/usr/bin:/bin", "HOME": f"{root}/home", "TMPDIR": f"{root}/tmp",
               "PYTHONPATH": pythonpath_env(run_dir, posix=True), "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONHASHSEED": "0", "AUTOPATCH_SANDBOX": "1"}
        start = time.monotonic()
        proc = subprocess.Popen([sys.executable, "-s", *_fill(python_args, root)], cwd=run_dir / "project",
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, preexec_fn=limits,
                                start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout_s)
            return ExecResult(proc.returncode, False, round(time.monotonic() - start, 2),
                              out.decode("utf-8", "replace")[-40_000:])
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            return ExecResult(None, True, round(time.monotonic() - start, 2), f"Killed after {timeout_s}s")


# ---------------------------------------------------------------- selection

_BACKENDS: list[Sandbox] = [DockerSandbox(), AppContainerSandbox(), LocalSandbox()]


def backends() -> list[Sandbox]:
    return _BACKENDS


def get_backend(name: str) -> Sandbox:
    for b in _BACKENDS:
        if b.name == name:
            return b
    raise SandboxUnavailable(f"Unknown sandbox {name!r}")


def choose(mode: str = "auto", allow_limited: bool = False) -> Sandbox:
    if mode != "auto":
        backend = get_backend(mode)
        info = backend.info()
        if backend.isolation == "limited" and not allow_limited:
            raise SandboxUnavailable("Limited isolation is selected but not allowed. Enable it in Settings under Testing.")
        if not (info.available and info.ready):
            raise SandboxUnavailable(f"{info.label} is not ready: {info.detail}. Set it up in Settings under Test sandbox.")
        return backend
    for backend in _BACKENDS:
        if backend.isolation == "limited" and not allow_limited:
            continue
        info = backend.info()
        if info.available and info.ready:
            return backend
    local = next((b for b in _BACKENDS if b.name == "local"), None)
    if local and local.info().available and local.info().ready:
        raise SandboxUnavailable(
            "No strong sandbox is ready. On macOS/Linux without Docker: enable "
            "'Allow limited isolation' under Settings → Testing and Save, or start Docker "
            "and click 'Set up sandbox' for the Docker backend."
        )
    raise SandboxUnavailable(
        "No isolated test sandbox is ready. Open Settings and click 'Set up sandbox' under Test sandbox."
    )


# ---------------------------------------------------------------- run folders

_run_lock = threading.Lock()  # one sandboxed run at a time, so runs cannot see each other's folders


def new_run_dir() -> Path:
    run_dir = paths().sandbox / f"run-{uuid.uuid4().hex[:12]}"
    for sub in ("project", "tmp", "home", "out", "guard"):
        (run_dir / sub).mkdir(parents=True)
    (run_dir / "guard" / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
    return run_dir


def execute(backend: Sandbox, run_dir: Path, python_args: list[str], timeout_s: float, memory_mb: int) -> ExecResult:
    with _run_lock:
        return backend.run(run_dir, python_args, timeout_s, memory_mb)


SELF_TEST = r'''
import os, socket, sys
results = []
def check(name, fn, expect_blocked=True):
    try:
        fn()
        blocked = False
    except Exception:
        blocked = True
    results.append((name, blocked == expect_blocked, "blocked" if blocked else "allowed"))
outside = os.environ.get("AUTOPATCH_OUTSIDE_FILE", "")
check("read a file outside the sandbox", lambda: open(outside).read())
check("write outside the sandbox", lambda: open(os.path.join(os.path.dirname(outside), "autopatch-escape.txt"), "w").write("x"))
check("write inside the sandbox", lambda: open(os.path.join("..", "tmp", "probe.txt"), "w").write("x"), expect_blocked=False)
import _socket
def raw_connect():
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect(("1.1.1.1", 443))
check("open an internet connection", raw_connect)
for name, ok, result in results:
    print(f"{'OK  ' if ok else 'FAIL'}|{name}|{result}")
'''


def self_test(backend: Sandbox) -> dict:
    """Prove the isolation claims by trying to break them from inside the sandbox."""
    run_dir = new_run_dir()
    try:
        (run_dir / "project" / "self_test.py").write_text(SELF_TEST, encoding="utf-8")
        outside = paths().data / "autopatch.db"
        if not outside.exists():
            outside.write_text("", encoding="utf-8")
        # The sandbox environment is fixed, so the outside path is passed in the command itself.
        args = ["-c", f"import os; os.environ['AUTOPATCH_OUTSIDE_FILE']={str(outside)!r}; "
                      f"exec(open('self_test.py').read())"]
        res = execute(backend, run_dir, args, timeout_s=60, memory_mb=512)
        checks = []
        for line in res.output.splitlines():
            if "|" in line and line[:4] in ("OK  ", "FAIL"):
                status, name, result = line.split("|", 2)
                checks.append({"check": name, "ok": status.strip() == "OK", "result": result})
        escaped = (paths().data / "autopatch-escape.txt").exists()
        if escaped:
            (paths().data / "autopatch-escape.txt").unlink()
        passed = bool(checks) and all(c["ok"] for c in checks) and not escaped
        return {"passed": passed, "checks": checks, "output": res.output[-2000:]}
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
