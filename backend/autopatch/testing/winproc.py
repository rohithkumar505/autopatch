"""Start a process on Windows inside an AppContainer and a Job Object.

AppContainer: the process gets a low-privilege identity with no network capability and no
access to files unless a folder's permissions explicitly include the container's SID.
Job Object: caps total memory and process count, blocks UI/clipboard access, and kills every
process in the job on timeout or when the handle closes.

Only Windows APIs from kernel32, userenv and advapi32 are used, through ctypes.
"""

from __future__ import annotations

import ctypes
import msvcrt
import os
import subprocess
import time
from ctypes import wintypes as wt
from dataclasses import dataclass

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
userenv = ctypes.WinDLL("userenv", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PSID = ctypes.c_void_p
HRESULT = ctypes.c_long

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x00000100
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
JobObjectBasicUIRestrictions = 4
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_UILIMIT_ALL = 0x000000FF
WAIT_TIMEOUT = 0x00000102
HRESULT_ALREADY_EXISTS = ctypes.c_long(0x800700B7).value


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", PSID), ("Attributes", wt.DWORD)]


class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", PSID),
        ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)),
        ("CapabilityCount", wt.DWORD),
        ("Reserved", wt.DWORD),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):
    _fields_ = [("UIRestrictionsClass", wt.DWORD)]


def _proto(fn, argtypes, restype):
    fn.argtypes = argtypes
    fn.restype = restype
    return fn


_CreateAppContainerProfile = _proto(
    userenv.CreateAppContainerProfile,
    [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.POINTER(SID_AND_ATTRIBUTES), wt.DWORD, ctypes.POINTER(PSID)], HRESULT)
_DeriveSid = _proto(userenv.DeriveAppContainerSidFromAppContainerName, [wt.LPCWSTR, ctypes.POINTER(PSID)], HRESULT)
_DeleteAppContainerProfile = _proto(userenv.DeleteAppContainerProfile, [wt.LPCWSTR], HRESULT)
_FreeSid = _proto(advapi32.FreeSid, [PSID], ctypes.c_void_p)
_ConvertSidToStringSidW = _proto(advapi32.ConvertSidToStringSidW, [PSID, ctypes.POINTER(wt.LPWSTR)], wt.BOOL)
_ConvertStringSidToSidW = _proto(advapi32.ConvertStringSidToSidW, [wt.LPCWSTR, ctypes.POINTER(PSID)], wt.BOOL)
_LocalFree = _proto(kernel32.LocalFree, [ctypes.c_void_p], ctypes.c_void_p)
_InitAttrList = _proto(kernel32.InitializeProcThreadAttributeList,
                       [ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)], wt.BOOL)
_UpdateAttr = _proto(kernel32.UpdateProcThreadAttribute,
                     [ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                      ctypes.c_void_p, ctypes.c_void_p], wt.BOOL)
_DeleteAttrList = _proto(kernel32.DeleteProcThreadAttributeList, [ctypes.c_void_p], None)
_CreateProcessW = _proto(kernel32.CreateProcessW,
                         [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
                          ctypes.c_void_p, wt.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(PROCESS_INFORMATION)], wt.BOOL)
_CreateJobObjectW = _proto(kernel32.CreateJobObjectW, [ctypes.c_void_p, wt.LPCWSTR], wt.HANDLE)
_SetInformationJobObject = _proto(kernel32.SetInformationJobObject,
                                  [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD], wt.BOOL)
_QueryInformationJobObject = _proto(kernel32.QueryInformationJobObject,
                                    [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
                                     ctypes.POINTER(wt.DWORD)], wt.BOOL)
_AssignProcessToJobObject = _proto(kernel32.AssignProcessToJobObject, [wt.HANDLE, wt.HANDLE], wt.BOOL)
_ResumeThread = _proto(kernel32.ResumeThread, [wt.HANDLE], wt.DWORD)
_WaitForSingleObject = _proto(kernel32.WaitForSingleObject, [wt.HANDLE, wt.DWORD], wt.DWORD)
_TerminateJobObject = _proto(kernel32.TerminateJobObject, [wt.HANDLE, wt.UINT], wt.BOOL)
_TerminateProcess = _proto(kernel32.TerminateProcess, [wt.HANDLE, wt.UINT], wt.BOOL)
_GetExitCodeProcess = _proto(kernel32.GetExitCodeProcess, [wt.HANDLE, ctypes.POINTER(wt.DWORD)], wt.BOOL)
_CloseHandle = _proto(kernel32.CloseHandle, [wt.HANDLE], wt.BOOL)


def _check(ok, what: str) -> None:
    if not ok:
        raise OSError(ctypes.get_last_error(), f"{what} failed: {ctypes.FormatError(ctypes.get_last_error())}")


# ---------------------------------------------------------------- AppContainer profile


def appcontainer_sid(name: str, display: str = "AutoPatch test sandbox") -> str:
    """Create (or look up) the AppContainer profile and return its SID string."""
    sid = PSID()
    hr = _CreateAppContainerProfile(name, display, "Runs untrusted project tests for AutoPatch", None, 0,
                                    ctypes.byref(sid))
    if hr == HRESULT_ALREADY_EXISTS:
        hr = _DeriveSid(name, ctypes.byref(sid))
    if hr != 0:
        raise OSError(f"Could not create the AppContainer profile (HRESULT 0x{hr & 0xFFFFFFFF:08X})")
    try:
        text = wt.LPWSTR()
        _check(_ConvertSidToStringSidW(sid, ctypes.byref(text)), "ConvertSidToStringSid")
        value = text.value
        _LocalFree(text)
        return value
    finally:
        _FreeSid(sid)


def delete_appcontainer(name: str) -> None:
    _DeleteAppContainerProfile(name)


def grant(path: str, sid: str, rights: str) -> None:
    """Give the container SID access to a folder (inherited by everything inside it)."""
    proc = subprocess.run(
        ["icacls", path, "/grant", f"*{sid}:(OI)(CI)({rights})", "/T", "/C", "/Q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise OSError(f"icacls failed for {path}: {(proc.stderr or proc.stdout).strip()[-300:]}")


# ---------------------------------------------------------------- process launch


@dataclass
class ProcResult:
    exit_code: int | None
    timed_out: bool
    duration_s: float
    peak_memory_mb: float


def _env_block(env: dict[str, str]):
    items = sorted(env.items(), key=lambda kv: kv[0].upper())
    block = "".join(f"{k}={v}\0" for k, v in items) + "\0"
    return (ctypes.c_wchar * len(block))(*block)


def run(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    output_path: str,
    timeout_s: float,
    memory_mb: int,
    max_processes: int = 32,
    appcontainer_sid_str: str | None = None,
) -> ProcResult:
    """Run argv; stdout and stderr go to output_path. Blocks until exit or timeout."""
    sid = PSID()
    attr_buf = None
    job = None
    pi = PROCESS_INFORMATION()
    out_file = open(output_path, "wb")
    nul = open(os.devnull, "rb")
    try:
        h_out = msvcrt.get_osfhandle(out_file.fileno())
        h_in = msvcrt.get_osfhandle(nul.fileno())
        os.set_handle_inheritable(h_out, True)
        os.set_handle_inheritable(h_in, True)
        handles = (wt.HANDLE * 2)(h_in, h_out)

        n_attrs = 2 if appcontainer_sid_str else 1
        size = ctypes.c_size_t(0)
        _InitAttrList(None, n_attrs, 0, ctypes.byref(size))
        attr_buf = ctypes.create_string_buffer(size.value)
        _check(_InitAttrList(attr_buf, n_attrs, 0, ctypes.byref(size)), "InitializeProcThreadAttributeList")
        _check(_UpdateAttr(attr_buf, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles, ctypes.sizeof(handles),
                           None, None), "UpdateProcThreadAttribute(handles)")
        caps = None
        if appcontainer_sid_str:
            _check(_ConvertStringSidToSidW(appcontainer_sid_str, ctypes.byref(sid)), "ConvertStringSidToSid")
            caps = SECURITY_CAPABILITIES(AppContainerSid=sid, Capabilities=None, CapabilityCount=0, Reserved=0)
            _check(_UpdateAttr(attr_buf, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, ctypes.byref(caps),
                               ctypes.sizeof(caps), None, None), "UpdateProcThreadAttribute(security)")

        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput = h_in
        si.StartupInfo.hStdOutput = h_out
        si.StartupInfo.hStdError = h_out
        si.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)

        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT
        env_block = _env_block(env)
        _check(_CreateProcessW(argv[0], cmdline, None, None, True, flags, ctypes.cast(env_block, ctypes.c_void_p),
                               cwd, ctypes.byref(si), ctypes.byref(pi)), "CreateProcess")

        job = _CreateJobObjectW(None, None)
        _check(job, "CreateJobObject")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_JOB_MEMORY
            | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION | JOB_OBJECT_LIMIT_JOB_TIME
        )
        limits.BasicLimitInformation.ActiveProcessLimit = max_processes
        # CPU time cap: twice the wall-clock timeout across all processes, in 100 ns units
        limits.BasicLimitInformation.PerJobUserTimeLimit = int(timeout_s * 2 * 10_000_000)
        limits.JobMemoryLimit = memory_mb * 1024 * 1024
        _check(_SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(limits),
                                        ctypes.sizeof(limits)), "SetInformationJobObject(limits)")
        ui = JOBOBJECT_BASIC_UI_RESTRICTIONS(JOB_OBJECT_UILIMIT_ALL)
        _check(_SetInformationJobObject(job, JobObjectBasicUIRestrictions, ctypes.byref(ui), ctypes.sizeof(ui)),
               "SetInformationJobObject(ui)")
        if not _AssignProcessToJobObject(job, pi.hProcess):
            _TerminateProcess(pi.hProcess, 1)
            _check(False, "AssignProcessToJobObject")
        start = time.monotonic()
        _ResumeThread(pi.hThread)

        wait = _WaitForSingleObject(pi.hProcess, int(timeout_s * 1000))
        timed_out = wait == WAIT_TIMEOUT
        if timed_out:
            _TerminateJobObject(job, 1)
            _WaitForSingleObject(pi.hProcess, 5000)
        duration = time.monotonic() - start
        code = wt.DWORD()
        _GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        _QueryInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info),
                                   ctypes.sizeof(info), None)
        return ProcResult(
            exit_code=None if timed_out else int(code.value),
            timed_out=timed_out,
            duration_s=round(duration, 2),
            peak_memory_mb=round(info.PeakJobMemoryUsed / (1024 * 1024), 1),
        )
    finally:
        if pi.hThread:
            _CloseHandle(pi.hThread)
        if pi.hProcess:
            _CloseHandle(pi.hProcess)
        if job:
            _CloseHandle(job)  # KILL_ON_JOB_CLOSE ends anything still running
        if attr_buf is not None:
            _DeleteAttrList(attr_buf)
        if sid:
            _LocalFree(sid)
        out_file.close()
        nul.close()
