"""Raw Windows API bindings used by the Windows Python execution boundary.

This module contains *only* `ctypes` declarations, structures, constants and
thin error-checked wrappers. It holds no KORTEX policy and makes no security
decisions -- that separation is what lets `windows_boundary.py` be read as
the security design and this file be read as a transcription of the Win32
headers.

Why `ctypes` rather than `pywin32`: every primitive needed here
(`CreateRestrictedToken`, `CreateAppContainerProfile`, Job Object limit
structures, `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`) is reachable from
the standard library, so the boundary introduces no new runtime dependency
into an offline-first product, and the exact structure layouts the kernel
sees are visible in this file rather than hidden behind a wrapper's own
versioned behaviour.

Every function is declared with explicit `argtypes`/`restype`. This is not
stylistic: `ctypes` defaults a return value to `c_int`, which silently
truncates the 64-bit `HANDLE` returned by `GetCurrentProcess()` and produces
a spurious `ERROR_INVALID_HANDLE` at the first call site. Declaring the
signatures is the fix, and omitting one reintroduces that class of bug.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
from typing import Any

__all__ = [
    "CREATE_NO_WINDOW",
    "CREATE_SUSPENDED",
    "CREATE_UNICODE_ENVIRONMENT",
    "DISABLE_MAX_PRIVILEGE",
    "EXTENDED_STARTUPINFO_PRESENT",
    "HANDLE_FLAG_INHERIT",
    "INFINITE",
    "JOBOBJECT_EXTENDED_LIMIT_INFORMATION",
    "JOB_OBJECT_LIMIT_ACTIVE_PROCESS",
    "JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION",
    "JOB_OBJECT_LIMIT_JOB_MEMORY",
    "JOB_OBJECT_LIMIT_JOB_TIME",
    "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
    "JOB_OBJECT_LIMIT_PROCESS_MEMORY",
    "JOB_OBJECT_LIMIT_PROCESS_TIME",
    "JOB_OBJECT_UILIMIT_ALL",
    "PROCESS_INFORMATION",
    "PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES",
    "SECURITY_CAPABILITIES",
    "SID_AND_ATTRIBUTES",
    "STARTF_USESHOWWINDOW",
    "STARTF_USESTDHANDLES",
    "STARTUPINFOEXW",
    "STARTUPINFOW",
    "SW_HIDE",
    "TOKEN_ALL_ACCESS",
    "WAIT_FAILED",
    "WAIT_OBJECT_0",
    "WAIT_TIMEOUT",
    "advapi32",
    "close_handle",
    "kernel32",
    "last_error",
    "userenv",
    "win_error",
]

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
userenv = ctypes.WinDLL("userenv", use_last_error=True)

# -- Constants ---------------------------------------------------------------

TOKEN_ALL_ACCESS = 0xF01FF
DISABLE_MAX_PRIVILEGE = 0x1
SANDBOX_INERT = 0x2
LUA_TOKEN = 0x4

TokenUser = 1
TokenGroups = 2
SE_GROUP_LOGON_ID = 0xC0000000

CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

STARTF_USESTDHANDLES = 0x00000100
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0

HANDLE_FLAG_INHERIT = 0x00000001
INFINITE = 0xFFFFFFFF
WAIT_TIMEOUT = 0x00000102
WAIT_OBJECT_0 = 0x00000000
# `WaitForSingleObject`'s failure return. Not a valid wait outcome: it means
# the wait call itself could not be performed (e.g. an invalid or already-
# closed handle), and must never be treated as "the object was signaled" nor
# as "the wait timed out". `winapi.last_error()` carries the real reason.
WAIT_FAILED = 0xFFFFFFFF

PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009

# Job Object limit flags (winnt.h).
JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectBasicUIRestrictions = 4
JobObjectExtendedLimitInformation = 9

# UI restrictions: the execution is headless, so every desktop-interaction
# right a job can withhold is withheld. This is what makes "headless
# subprocess" an enforced property rather than a description of intent.
JOB_OBJECT_UILIMIT_HANDLES = 0x00000001
JOB_OBJECT_UILIMIT_READCLIPBOARD = 0x00000002
JOB_OBJECT_UILIMIT_WRITECLIPBOARD = 0x00000004
JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS = 0x00000008
JOB_OBJECT_UILIMIT_DISPLAYSETTINGS = 0x00000010
JOB_OBJECT_UILIMIT_GLOBALATOMS = 0x00000020
JOB_OBJECT_UILIMIT_DESKTOP = 0x00000040
JOB_OBJECT_UILIMIT_EXITWINDOWS = 0x00000080
JOB_OBJECT_UILIMIT_ALL = (
    JOB_OBJECT_UILIMIT_HANDLES
    | JOB_OBJECT_UILIMIT_READCLIPBOARD
    | JOB_OBJECT_UILIMIT_WRITECLIPBOARD
    | JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS
    | JOB_OBJECT_UILIMIT_DISPLAYSETTINGS
    | JOB_OBJECT_UILIMIT_GLOBALATOMS
    | JOB_OBJECT_UILIMIT_DESKTOP
    | JOB_OBJECT_UILIMIT_EXITWINDOWS
)

DACL_SECURITY_INFORMATION = 0x00000004
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
SDDL_REVISION_1 = 1


# -- Structures --------------------------------------------------------------


class SID_AND_ATTRIBUTES(ctypes.Structure):  # noqa: N801 - Win32 struct name
    """winnt.h `SID_AND_ATTRIBUTES`."""

    _fields_ = (("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD))


class TOKEN_GROUPS(ctypes.Structure):  # noqa: N801 - Win32 struct name
    """winnt.h `TOKEN_GROUPS`.

    `Groups` is a variable-length array in the real header; 256 is a fixed
    upper bound used only for casting an already-correctly-sized buffer
    returned by `GetTokenInformation`, never for allocating one.
    """

    _fields_ = (("GroupCount", wt.DWORD), ("Groups", SID_AND_ATTRIBUTES * 256))


class SECURITY_ATTRIBUTES(ctypes.Structure):  # noqa: N801 - Win32 struct name
    """winbase.h `SECURITY_ATTRIBUTES`."""

    _fields_ = (
        ("nLength", wt.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wt.BOOL),
    )


class STARTUPINFOW(ctypes.Structure):
    """processthreadsapi.h `STARTUPINFOW`."""

    _fields_ = (
        ("cb", wt.DWORD),
        ("lpReserved", wt.LPWSTR),
        ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD),
        ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD),
        ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD),
        ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    )


class STARTUPINFOEXW(ctypes.Structure):
    """processthreadsapi.h `STARTUPINFOEXW`."""

    _fields_ = (("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p))


class PROCESS_INFORMATION(ctypes.Structure):  # noqa: N801 - Win32 struct name
    """processthreadsapi.h `PROCESS_INFORMATION`."""

    _fields_ = (
        ("hProcess", wt.HANDLE),
        ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId", wt.DWORD),
    )


class SECURITY_CAPABILITIES(ctypes.Structure):  # noqa: N801 - Win32 struct name
    """winnt.h `SECURITY_CAPABILITIES` -- the AppContainer identity a process
    is created under.

    `CapabilityCount = 0` is the whole point: an AppContainer with no
    capability SIDs has no `internetClient`, no `privateNetworkClientServer`,
    and no broker access, which is what makes network denial a kernel-enforced
    property of the identity rather than a firewall rule KORTEX would have to
    maintain.
    """

    _fields_ = (
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)),
        ("CapabilityCount", wt.DWORD),
        ("Reserved", wt.DWORD),
    )


class IO_COUNTERS(ctypes.Structure):  # noqa: N801 - Win32 struct name
    """winnt.h `IO_COUNTERS`."""

    _fields_ = tuple(
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    )


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801 - Win32 struct name
    """winnt.h `JOBOBJECT_BASIC_LIMIT_INFORMATION`."""

    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    )


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801 - Win32 struct name
    """winnt.h `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`."""

    _fields_ = (
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):  # noqa: N801 - Win32 struct name
    """winnt.h `JOBOBJECT_BASIC_UI_RESTRICTIONS`."""

    _fields_ = (("UIRestrictionsClass", wt.DWORD),)


# -- Signatures --------------------------------------------------------------

kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
kernel32.GetExitCodeProcess.restype = wt.BOOL
kernel32.SetHandleInformation.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
kernel32.SetHandleInformation.restype = wt.BOOL
kernel32.ResumeThread.argtypes = [wt.HANDLE]
kernel32.ResumeThread.restype = wt.DWORD
kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
kernel32.TerminateProcess.restype = wt.BOOL

kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
kernel32.CreateJobObjectW.restype = wt.HANDLE
kernel32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
kernel32.SetInformationJobObject.restype = wt.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
kernel32.AssignProcessToJobObject.restype = wt.BOOL
kernel32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
kernel32.TerminateJobObject.restype = wt.BOOL
kernel32.IsProcessInJob.argtypes = [wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.BOOL)]
kernel32.IsProcessInJob.restype = wt.BOOL

kernel32.CreateProcessW.argtypes = [
    wt.LPCWSTR,
    wt.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wt.BOOL,
    wt.DWORD,
    ctypes.c_void_p,
    wt.LPCWSTR,
    ctypes.c_void_p,
    ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wt.BOOL

kernel32.InitializeProcThreadAttributeList.argtypes = [
    ctypes.c_void_p,
    wt.DWORD,
    wt.DWORD,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.InitializeProcThreadAttributeList.restype = wt.BOOL
kernel32.UpdateProcThreadAttribute.argtypes = [
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
kernel32.UpdateProcThreadAttribute.restype = wt.BOOL
kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
kernel32.DeleteProcThreadAttributeList.restype = None

advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi32.OpenProcessToken.restype = wt.BOOL
advapi32.GetTokenInformation.argtypes = [
    wt.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
]
advapi32.GetTokenInformation.restype = wt.BOOL
advapi32.CreateRestrictedToken.argtypes = [
    wt.HANDLE,
    wt.DWORD,
    wt.DWORD,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.c_void_p,
    ctypes.POINTER(wt.HANDLE),
]
advapi32.CreateRestrictedToken.restype = wt.BOOL
advapi32.CreateProcessAsUserW.argtypes = [
    wt.HANDLE,
    wt.LPCWSTR,
    wt.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wt.BOOL,
    wt.DWORD,
    ctypes.c_void_p,
    wt.LPCWSTR,
    ctypes.c_void_p,
    ctypes.POINTER(PROCESS_INFORMATION),
]
advapi32.CreateProcessAsUserW.restype = wt.BOOL
advapi32.ConvertStringSidToSidW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
advapi32.ConvertStringSidToSidW.restype = wt.BOOL
advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)]
advapi32.ConvertSidToStringSidW.restype = wt.BOOL
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wt.LPCWSTR,
    wt.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wt.DWORD),
]
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wt.BOOL
advapi32.SetFileSecurityW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.c_void_p]
advapi32.SetFileSecurityW.restype = wt.BOOL
advapi32.GetFileSecurityW.argtypes = [
    wt.LPCWSTR,
    wt.DWORD,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
]
advapi32.GetFileSecurityW.restype = wt.BOOL
advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
    ctypes.c_void_p,
    wt.DWORD,
    wt.DWORD,
    ctypes.POINTER(wt.LPWSTR),
    ctypes.POINTER(wt.DWORD),
]
advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wt.BOOL

userenv.CreateAppContainerProfile.argtypes = [
    wt.LPCWSTR,
    wt.LPCWSTR,
    wt.LPCWSTR,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
]
userenv.CreateAppContainerProfile.restype = ctypes.c_long
userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
userenv.DeleteAppContainerProfile.argtypes = [wt.LPCWSTR]
userenv.DeleteAppContainerProfile.restype = ctypes.c_long
advapi32.FreeSid.argtypes = [ctypes.c_void_p]
advapi32.FreeSid.restype = ctypes.c_void_p

# Named-pipe server primitives, used by the Trusted-Python capability bridge.
# A loopback TCP socket is not an option: an AppContainer with zero capability
# SIDs has no `privateNetworkClientServer`, so it cannot connect to 127.0.0.1
# at all. A named pipe whose DACL names the container SID is the only local
# channel the sandbox can reach, and it is reachable by exactly that one
# identity.
kernel32.CreateNamedPipeW.argtypes = [
    wt.LPCWSTR,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    ctypes.c_void_p,
]
kernel32.CreateNamedPipeW.restype = wt.HANDLE
kernel32.ConnectNamedPipe.argtypes = [wt.HANDLE, ctypes.c_void_p]
kernel32.ConnectNamedPipe.restype = wt.BOOL
kernel32.DisconnectNamedPipe.argtypes = [wt.HANDLE]
kernel32.DisconnectNamedPipe.restype = wt.BOOL
kernel32.ReadFile.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    ctypes.c_void_p,
]
kernel32.ReadFile.restype = wt.BOOL
kernel32.WriteFile.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    ctypes.c_void_p,
]
kernel32.WriteFile.restype = wt.BOOL
kernel32.FlushFileBuffers.argtypes = [wt.HANDLE]
kernel32.FlushFileBuffers.restype = wt.BOOL

PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
ERROR_PIPE_CONNECTED = 535
ERROR_BROKEN_PIPE = 109
ERROR_NO_DATA = 232
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value

kernel32.LocalFree.argtypes = [ctypes.c_void_p]
kernel32.LocalFree.restype = ctypes.c_void_p


# -- Helpers -----------------------------------------------------------------


def last_error() -> int:
    """The calling thread's last Win32 error, captured via `use_last_error`."""
    return ctypes.get_last_error()


def win_error(operation: str) -> OSError:
    """Build an `OSError` carrying the Win32 error for `operation`.

    Returned rather than raised so the caller decides the exception type --
    the boundary converts most of these into `IsolationUnavailableError`, and
    a helper that raised directly would take that decision away.
    """
    code = ctypes.get_last_error()
    return OSError(f"{operation} failed: [WinError {code}] {ctypes.FormatError(code)}")


def close_handle(handle: Any) -> None:
    """Close a handle, ignoring failure.

    Handle cleanup runs in `finally` blocks during teardown, frequently while
    another exception is already propagating. A raise here would replace the
    real failure with a cleanup failure.
    """
    with contextlib.suppress(Exception):
        if handle:
            kernel32.CloseHandle(handle)
