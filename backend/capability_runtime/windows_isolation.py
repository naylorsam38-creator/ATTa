"""Windows isolation for tool workers (Stage 01B §16). Standard library + ctypes only: imported by the isolated child
(started with `python -I`) as well as by the runner.

Controls
  * Job Object around the supervisor and everything it starts: KILL_ON_JOB_CLOSE (process-tree termination), per-process
    memory limit, DIE_ON_UNHANDLED_EXCEPTION, no breakaway.
  * Restricted worker identity: before any capability code is loaded the child removes every privilege from its token
    except SeChangeNotifyPrivilege and lowers its own mandatory integrity level to LOW. A low-integrity process cannot
    write to medium-integrity objects (user profile, repository, configuration, model directories, registry) — the
    Windows "no-write-up" rule — and cannot regain privileges.
  * Filesystem ACLs: the task workspace gets an explicit DACL (inheritance removed; SYSTEM full, the service account
    modify) and a LOW integrity label, so the restricted worker can write there and nowhere else.
  * Firewall: see backend/deployment/windows_hardening.py (rules bound to the dedicated worker interpreter).

These code paths only run on Windows. Linux CI checks their construction; behaviour is certified only by S01B-T41 on a
Windows host (BLOCKERS B-S01B-01 until then)."""
from __future__ import annotations

import os
import subprocess

SE_PRIVILEGE_REMOVED = 0x4
TOKEN_QUERY, TOKEN_ADJUST_PRIVILEGES, TOKEN_ADJUST_DEFAULT = 0x8, 0x20, 0x80
TOKEN_PRIVILEGES_CLASS, TOKEN_INTEGRITY_LEVEL_CLASS = 3, 25
SE_GROUP_INTEGRITY = 0x20
LOW_INTEGRITY_SID = "S-1-16-4096"
INTEGRITY_RIDS = {0x0000: "untrusted", 0x1000: "low", 0x2000: "medium", 0x2100: "medium-plus", 0x3000: "high",
                  0x4000: "system"}
KEEP_PRIVILEGES = {"SeChangeNotifyPrivilege"}          # bypass traverse checking: needed to open nested paths

JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_EXTENDED_LIMIT_INFORMATION = 9


def job_limit_flags(memory_limit_mb: int | None) -> tuple[int, int]:
    """(LimitFlags, ProcessMemoryLimit bytes) for the worker Job Object. Breakaway is never allowed."""
    flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    mem = 0
    if memory_limit_mb:
        flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
        mem = int(memory_limit_mb) * 1024 * 1024
    return flags, mem


def _structs():
    import ctypes
    from ctypes import wintypes

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IOC(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TOKEN_MANDATORY_LABEL(ctypes.Structure):
        _fields_ = [("Label", SID_AND_ATTRIBUTES)]

    return {"BASIC": BASIC, "IOC": IOC, "EXT": EXT, "LUID": LUID, "LUID_AND_ATTRIBUTES": LUID_AND_ATTRIBUTES,
            "SID_AND_ATTRIBUTES": SID_AND_ATTRIBUTES, "TOKEN_MANDATORY_LABEL": TOKEN_MANDATORY_LABEL}


# ------------------------------------------------------------------------------------------------ Job Objects
def create_job(proc, memory_limit_mb: int | None = None):
    """Put `proc` (the supervisor, before it starts the child) in a new Job Object with the worker limits."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    s = _structs()
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = s["EXT"]()
    flags, mem = job_limit_flags(memory_limit_mb)
    info.BasicLimitInformation.LimitFlags = flags
    info.ProcessMemoryLimit = mem
    if not k32.SetInformationJobObject(job, JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    if not k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle))):
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
    return job


def kill_job(job) -> None:
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.TerminateJobObject(job, 1)
    k32.CloseHandle(job)


# ------------------------------------------------------------------------------------------------ restricted identity
def _open_own_token(access: int):
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    tok = wintypes.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), access, ctypes.byref(tok)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    return adv, k32, tok


def _token_info(adv, tok, cls: int):
    import ctypes
    from ctypes import wintypes
    need = wintypes.DWORD()
    adv.GetTokenInformation(tok, cls, None, 0, ctypes.byref(need))
    buf = ctypes.create_string_buffer(need.value)
    if not adv.GetTokenInformation(tok, cls, buf, need, ctypes.byref(need)):
        raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")
    return buf


def remove_privileges() -> dict:
    """Remove (not merely disable) every token privilege except KEEP_PRIVILEGES. Removed privileges cannot return."""
    import ctypes
    from ctypes import wintypes
    s = _structs()
    adv, k32, tok = _open_own_token(TOKEN_QUERY | TOKEN_ADJUST_PRIVILEGES)
    try:
        buf = _token_info(adv, tok, TOKEN_PRIVILEGES_CLASS)
        count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
        arr = ctypes.cast(ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD),
                          ctypes.POINTER(s["LUID_AND_ATTRIBUTES"]))
        removed, kept = [], []
        for i in range(count):
            name_len = wintypes.DWORD(64)
            name = ctypes.create_unicode_buffer(64)
            adv.LookupPrivilegeNameW(None, ctypes.byref(arr[i].Luid), name, ctypes.byref(name_len))
            if name.value in KEEP_PRIVILEGES:
                kept.append(name.value)
                continue
            arr[i].Attributes = SE_PRIVILEGE_REMOVED
            removed.append(name.value)
        adv.AdjustTokenPrivileges(tok, False, buf, 0, None, None)
        err = ctypes.get_last_error()
        if err not in (0, 1300):          # 1300 = ERROR_NOT_ALL_ASSIGNED (informational)
            raise OSError(err, "AdjustTokenPrivileges failed")
        return {"privileges_removed": removed, "privileges_kept": kept}
    finally:
        k32.CloseHandle(tok)


def set_low_integrity() -> str:
    import ctypes
    s = _structs()
    adv, k32, tok = _open_own_token(TOKEN_QUERY | TOKEN_ADJUST_DEFAULT)
    try:
        sid = ctypes.c_void_p()
        if not adv.ConvertStringSidToSidW(LOW_INTEGRITY_SID, ctypes.byref(sid)):
            raise OSError(ctypes.get_last_error(), "ConvertStringSidToSidW failed")
        label = s["TOKEN_MANDATORY_LABEL"]()
        label.Label.Sid, label.Label.Attributes = sid, SE_GROUP_INTEGRITY
        size = ctypes.sizeof(label) + adv.GetLengthSid(sid)
        if not adv.SetTokenInformation(tok, TOKEN_INTEGRITY_LEVEL_CLASS, ctypes.byref(label), size):
            raise OSError(ctypes.get_last_error(), "SetTokenInformation(TokenIntegrityLevel) failed")
        k32.LocalFree(sid)
    finally:
        k32.CloseHandle(tok)
    return current_integrity()


def current_integrity() -> str:
    import ctypes
    from ctypes import wintypes
    s = _structs()
    adv, k32, tok = _open_own_token(TOKEN_QUERY)
    try:
        buf = _token_info(adv, tok, TOKEN_INTEGRITY_LEVEL_CLASS)
        label = ctypes.cast(buf, ctypes.POINTER(s["TOKEN_MANDATORY_LABEL"]))[0]
        n = ctypes.cast(adv.GetSidSubAuthorityCount(ctypes.c_void_p(label.Label.Sid)), ctypes.POINTER(ctypes.c_ubyte))[0]
        adv.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
        rid = adv.GetSidSubAuthority(ctypes.c_void_p(label.Label.Sid), n - 1)[0]
        return INTEGRITY_RIDS.get(rid, hex(rid))
    finally:
        k32.CloseHandle(tok)


def restrict_current_process() -> dict:
    """Called by the child BEFORE capability code is loaded. Order: privileges first (needs the medium token)."""
    applied = remove_privileges()
    applied["integrity"] = set_low_integrity()
    return applied


# ------------------------------------------------------------------------------------------------ filesystem ACLs
def current_user_sid() -> str:
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True).stdout
    return out.strip().split(",")[-1].strip().strip('"')


def workspace_acl_commands(path: str, user_sid: str) -> list[list[str]]:
    """icacls commands giving the task workspace an explicit DACL and a LOW integrity label:
    no inherited entries; SYSTEM full control; the service account modify (inherited to children);
    low mandatory label so a low-integrity worker may write here (and only here)."""
    return [
        ["icacls", path, "/inheritance:r"],
        ["icacls", path, "/grant:r", "*S-1-5-18:(OI)(CI)F", f"*{user_sid}:(OI)(CI)M"],
        ["icacls", path, "/setintegritylevel", "(OI)(CI)low"],
    ]


def secure_workspace(path: str) -> dict:
    sid = current_user_sid()
    for cmd in workspace_acl_commands(path, sid):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError(f"{' '.join(cmd[:3])} failed: {r.stdout.strip()} {r.stderr.strip()}")
    return {"workspace_acl": "explicit DACL (SYSTEM full, service account modify), low integrity label",
            "service_sid": sid}


def isolation_active() -> bool:
    return os.name == "nt"
