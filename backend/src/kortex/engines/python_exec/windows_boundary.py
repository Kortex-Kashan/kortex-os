"""The Windows governed Trusted-Python execution boundary.

This is a **governed trusted-Python execution boundary**, not a malicious-code
sandbox and not equivalent to kernel-level isolation. It exists to contain
code KORTEX already trusts -- administrator-authored or explicitly trusted
versioned Python Actions -- so that a bug in that code cannot reach unrelated
tenant data, host credentials, or the network. Untrusted Python never reaches
this module: `policy.resolve_policy` rejects it before a workspace exists.

Four mechanisms are layered, and each one is applied to the real process:

* **Restricted token** -- `CreateRestrictedToken` with `DISABLE_MAX_PRIVILEGE`
  strips every privilege except `SeChangeNotifyPrivilege`, and the
  Administrators/Power Users/Backup Operators SIDs are converted to deny-only,
  so the execution cannot run administratively and cannot regain privilege
  through ordinary token inheritance.
* **AppContainer identity** -- the process is created under an AppContainer SID
  with **zero** capability SIDs. This, not the restricted token, is what
  actually denies the host filesystem and the network.
* **Job Object** -- process-tree containment, active-process/memory/CPU limits
  where configured, full UI restrictions, and `TerminateJobObject` for
  deterministic timeout termination of the *entire* tree.
* **Explicit ACLs** -- the provisioned runtime grants the container SID
  read+execute and the ephemeral workspace grants it modify. Nothing else in
  the filesystem names that SID, so nothing else is reachable.

A measured, deliberately-stated limitation of the restricted token, verified
empirically on Windows 11 during this milestone's implementation: a restricted
token whose restricting-SID list *excludes* the interactive user SID cannot
initialize a process at all (`STATUS_DLL_INIT_FAILED`, 0xC0000142) -- confirmed
across window-station, working-directory, and SID-set variations. The user SID
is therefore present in the restricting list, which means the restricted token
alone imposes **no** filesystem denial. That denial comes entirely from the
AppContainer identity. This module must not be described, in code or in
documentation, as deriving filesystem isolation from the restricted token.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import logging
import msvcrt
import os
import threading
import time
from pathlib import Path

from kortex.engines.python_exec import winapi
from kortex.engines.python_exec.exceptions import IsolationUnavailableError
from kortex.engines.python_exec.models import BoundaryResult, PythonExecutionLimits
from kortex.engines.python_exec.workspace import RuntimeImage, SandboxIdentity

logger = logging.getLogger("kortex.engine.python_exec.windows")

# Converted to deny-only in the execution token. Membership is not merely
# unused -- it is actively neutralised, so an execution cannot satisfy an ACL
# that grants access on the strength of one of these groups.
_DENY_ONLY_GROUP_SIDS = (
    "S-1-5-32-544",  # BUILTIN\Administrators
    "S-1-5-32-547",  # BUILTIN\Power Users
    "S-1-5-32-551",  # BUILTIN\Backup Operators
)

# 100-nanosecond units, the unit every Job Object time limit uses.
_HUNDRED_NS_PER_SECOND = 10_000_000


def _string_sid(value: str) -> ctypes.c_void_p:
    pointer = ctypes.c_void_p()
    if not winapi.advapi32.ConvertStringSidToSidW(value, ctypes.byref(pointer)):
        raise IsolationUnavailableError(f"Unable to convert SID {value!r}.")
    return pointer


def _sid_array(values: tuple[str, ...]) -> ctypes.Array[winapi.SID_AND_ATTRIBUTES]:
    array = (winapi.SID_AND_ATTRIBUTES * len(values))()
    for index, value in enumerate(values):
        array[index].Sid = _string_sid(value)
        array[index].Attributes = 0
    return array


def _token_logon_sids(token: wt.HANDLE) -> tuple[str, ...]:
    """The logon-session SIDs of a token.

    Required in the restricting-SID list: the window station and desktop of
    the interactive session grant access to the logon SID, and a process whose
    restricted check cannot satisfy that never finishes initializing.
    """
    size = wt.DWORD()
    winapi.advapi32.GetTokenInformation(token, winapi.TokenGroups, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not winapi.advapi32.GetTokenInformation(token, winapi.TokenGroups, buffer, size, ctypes.byref(size)):
        raise IsolationUnavailableError("Unable to read token group membership.")
    groups = ctypes.cast(buffer, ctypes.POINTER(winapi.TOKEN_GROUPS)).contents
    found: list[str] = []
    for index in range(groups.GroupCount):
        entry = groups.Groups[index]
        if (entry.Attributes & winapi.SE_GROUP_LOGON_ID) == winapi.SE_GROUP_LOGON_ID:
            out = wt.LPWSTR()
            if winapi.advapi32.ConvertSidToStringSidW(entry.Sid, ctypes.byref(out)):
                found.append(str(out.value))
                winapi.kernel32.LocalFree(out)
    return tuple(found)


def _token_user_sid(token: wt.HANDLE) -> str:
    size = wt.DWORD()
    winapi.advapi32.GetTokenInformation(token, winapi.TokenUser, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not winapi.advapi32.GetTokenInformation(token, winapi.TokenUser, buffer, size, ctypes.byref(size)):
        raise IsolationUnavailableError("Unable to read the token user SID.")
    entry = ctypes.cast(buffer, ctypes.POINTER(winapi.SID_AND_ATTRIBUTES)).contents
    out = wt.LPWSTR()
    if not winapi.advapi32.ConvertSidToStringSidW(ctypes.c_void_p(entry.Sid), ctypes.byref(out)):
        raise IsolationUnavailableError("Unable to render the token user SID.")
    try:
        return str(out.value)
    finally:
        winapi.kernel32.LocalFree(out)


def create_execution_token() -> wt.HANDLE:
    """Build the restricted primary token the execution actually runs under.

    See the module docstring for why the user SID appears in the restricting
    list: without it the process cannot initialize on Windows 11. The security
    value delivered here is privilege removal and deny-only administrative
    groups; filesystem and network denial come from the AppContainer identity
    layered on top by `WindowsExecutionBoundary.run`.
    """
    process_token = wt.HANDLE()
    if not winapi.advapi32.OpenProcessToken(
        winapi.kernel32.GetCurrentProcess(), winapi.TOKEN_ALL_ACCESS, ctypes.byref(process_token)
    ):
        raise IsolationUnavailableError("Unable to open the KORTEX process token.")

    try:
        restricting = (
            "S-1-5-12",  # RESTRICTED
            "S-1-5-32-545",  # BUILTIN\Users
            "S-1-1-0",  # Everyone
            "S-1-5-11",  # Authenticated Users
            _token_user_sid(process_token),
            *_token_logon_sids(process_token),
        )
        disable_array = _sid_array(_DENY_ONLY_GROUP_SIDS)
        restrict_array = _sid_array(restricting)

        restricted = wt.HANDLE()
        created = winapi.advapi32.CreateRestrictedToken(
            process_token,
            winapi.DISABLE_MAX_PRIVILEGE,
            len(_DENY_ONLY_GROUP_SIDS),
            ctypes.byref(disable_array),
            0,
            None,
            len(restricting),
            ctypes.byref(restrict_array),
            ctypes.byref(restricted),
        )
        if not created:
            raise IsolationUnavailableError(
                "Unable to create the restricted execution token.",
                details={"win_error": winapi.last_error()},
            )
        return restricted
    finally:
        winapi.close_handle(process_token)


def create_execution_job(limits: PythonExecutionLimits) -> wt.HANDLE:
    """Create the Job Object that contains the execution's process tree.

    Only limits that are actually configured are requested: a `None` limit
    contributes no flag, so a limit that appears in the job's `LimitFlags` is
    always one the kernel is genuinely enforcing.
    """
    job = winapi.kernel32.CreateJobObjectW(None, None)
    if not job:
        raise IsolationUnavailableError(
            "Unable to create the execution Job Object.", details={"win_error": winapi.last_error()}
        )

    try:
        information = winapi.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = winapi.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | winapi.JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION

        if limits.max_processes is not None:
            flags |= winapi.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            information.BasicLimitInformation.ActiveProcessLimit = limits.max_processes
        if limits.memory_bytes is not None:
            flags |= winapi.JOB_OBJECT_LIMIT_PROCESS_MEMORY | winapi.JOB_OBJECT_LIMIT_JOB_MEMORY
            information.ProcessMemoryLimit = limits.memory_bytes
            information.JobMemoryLimit = limits.memory_bytes
        if limits.cpu_seconds is not None:
            flags |= winapi.JOB_OBJECT_LIMIT_PROCESS_TIME | winapi.JOB_OBJECT_LIMIT_JOB_TIME
            budget = int(limits.cpu_seconds) * _HUNDRED_NS_PER_SECOND
            information.BasicLimitInformation.PerProcessUserTimeLimit = budget
            information.BasicLimitInformation.PerJobUserTimeLimit = budget

        information.BasicLimitInformation.LimitFlags = flags
        if not winapi.kernel32.SetInformationJobObject(
            job,
            winapi.JobObjectExtendedLimitInformation,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise IsolationUnavailableError(
                "Unable to apply Job Object resource limits.", details={"win_error": winapi.last_error()}
            )

        restrictions = winapi.JOBOBJECT_BASIC_UI_RESTRICTIONS()
        restrictions.UIRestrictionsClass = winapi.JOB_OBJECT_UILIMIT_ALL
        if not winapi.kernel32.SetInformationJobObject(
            job,
            winapi.JobObjectBasicUIRestrictions,
            ctypes.byref(restrictions),
            ctypes.sizeof(restrictions),
        ):
            raise IsolationUnavailableError(
                "Unable to apply Job Object UI restrictions.", details={"win_error": winapi.last_error()}
            )
        return wt.HANDLE(job)
    except BaseException:
        winapi.close_handle(job)
        raise


def _build_environment_block(environment: dict[str, str]) -> ctypes.Array[ctypes.c_wchar]:
    """Encode an environment as a `CREATE_UNICODE_ENVIRONMENT` block.

    Sorted so a given environment always produces an identical block, which
    makes the boundary's process-creation call reproducible in tests.
    """
    parts = [f"{key}={value}" for key, value in sorted(environment.items())]
    return ctypes.create_unicode_buffer("\0".join(parts) + "\0\0")


class _Pipe:
    """One anonymous pipe, with the child's end marked inheritable.

    The parent's end is explicitly *not* inheritable: a duplicate of the read
    end inside the sandbox would let an execution observe its own output
    stream, and more importantly any handle the child inherits but should not
    hold is a hole in the boundary.
    """

    def __init__(self, *, child_writes: bool) -> None:
        read_fd, write_fd = os.pipe()
        if child_writes:
            self.parent_fd, self.child_fd = read_fd, write_fd
        else:
            self.parent_fd, self.child_fd = write_fd, read_fd
        self._child_closed = False
        self._parent_closed = False

        self.child_handle = msvcrt.get_osfhandle(self.child_fd)
        winapi.kernel32.SetHandleInformation(
            wt.HANDLE(self.child_handle), winapi.HANDLE_FLAG_INHERIT, winapi.HANDLE_FLAG_INHERIT
        )
        winapi.kernel32.SetHandleInformation(
            wt.HANDLE(msvcrt.get_osfhandle(self.parent_fd)), winapi.HANDLE_FLAG_INHERIT, 0
        )

    # Both ends are closed from more than one place: `run` closes the child
    # ends as soon as the process is created (so the readers see EOF), and its
    # `finally` closes everything again on the way out; `_write_stdin` closes
    # stdin's parent end itself. Tracking closure is not merely tidy --
    # `os.close` on an already-closed descriptor can close a *different*,
    # unrelated file that has since been handed the same descriptor number,
    # and `suppress(OSError)` would hide that completely.

    def close_child(self) -> None:
        if not self._child_closed:
            self._child_closed = True
            with contextlib.suppress(OSError):
                os.close(self.child_fd)

    def close_parent(self) -> None:
        if not self._parent_closed:
            self._parent_closed = True
            with contextlib.suppress(OSError):
                os.close(self.parent_fd)


def _drain(fd: int, limit: int, sink: list[bytes], truncated: list[bool]) -> None:
    """Read `fd` to EOF, retaining at most `limit` bytes.

    Reading continues past the limit rather than closing the pipe early:
    closing it would hand the execution a `BrokenPipeError` and turn an
    output-size overrun into an unrelated-looking crash. Bytes beyond the
    limit are discarded and the overrun is recorded.
    """
    total = 0
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        if total < limit:
            room = limit - total
            sink.append(chunk[:room])
            if len(chunk) > room:
                truncated[0] = True
        else:
            truncated[0] = True
        total += len(chunk)


class WindowsExecutionBoundary:
    """Runs one Python execution under the layered Windows boundary."""

    def __init__(self, identity: SandboxIdentity, runtime: RuntimeImage) -> None:
        if identity.container_sid is None:
            raise IsolationUnavailableError(
                "The Windows Python execution boundary requires an AppContainer sandbox identity."
            )
        self._identity = identity
        self._runtime = runtime

    def run(
        self,
        *,
        arguments: list[str],
        working_directory: Path,
        environment: dict[str, str],
        stdin_payload: bytes,
        limits: PythonExecutionLimits,
    ) -> BoundaryResult:
        """Execute, enforce, and terminate deterministically.

        Every failure to establish a mechanism raises `IsolationUnavailableError`
        *before* the process is resumed. The process is created suspended and
        only resumed once it is inside the Job Object, so there is no window in
        which unconstrained code is running.
        """
        started = time.monotonic()
        token = create_execution_token()
        job = create_execution_job(limits)
        attribute_list: ctypes.Array[ctypes.c_char] | None = None
        stdin_pipe = stdout_pipe = stderr_pipe = None
        process_information = winapi.PROCESS_INFORMATION()
        created = False

        try:
            stdin_pipe = _Pipe(child_writes=False)
            stdout_pipe = _Pipe(child_writes=True)
            stderr_pipe = _Pipe(child_writes=True)

            size = ctypes.c_size_t(0)
            winapi.kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attribute_list = ctypes.create_string_buffer(size.value)
            if not winapi.kernel32.InitializeProcThreadAttributeList(attribute_list, 1, 0, ctypes.byref(size)):
                raise IsolationUnavailableError(
                    "Unable to initialize the process attribute list.",
                    details={"win_error": winapi.last_error()},
                )

            container_sid = _string_sid(self._identity.container_sid or "")
            capabilities = winapi.SECURITY_CAPABILITIES()
            capabilities.AppContainerSid = container_sid
            capabilities.Capabilities = None
            # Zero capability SIDs: no networkClient, no broker access. This is
            # what makes default-deny network a kernel-enforced property of the
            # identity rather than an unenforced policy statement.
            capabilities.CapabilityCount = 0
            capabilities.Reserved = 0

            if not winapi.kernel32.UpdateProcThreadAttribute(
                attribute_list,
                0,
                winapi.PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(capabilities),
                ctypes.sizeof(capabilities),
                None,
                None,
            ):
                raise IsolationUnavailableError(
                    "Unable to attach the AppContainer identity to the execution.",
                    details={"win_error": winapi.last_error()},
                )

            startup = winapi.STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(winapi.STARTUPINFOEXW)
            startup.StartupInfo.dwFlags = winapi.STARTF_USESTDHANDLES | winapi.STARTF_USESHOWWINDOW
            startup.StartupInfo.wShowWindow = winapi.SW_HIDE
            startup.StartupInfo.hStdInput = stdin_pipe.child_handle
            startup.StartupInfo.hStdOutput = stdout_pipe.child_handle
            startup.StartupInfo.hStdError = stderr_pipe.child_handle
            startup.lpAttributeList = ctypes.cast(attribute_list, ctypes.c_void_p)

            command_line = ctypes.create_unicode_buffer(_quote_command(arguments))
            environment_block = _build_environment_block(environment)

            created = bool(
                winapi.advapi32.CreateProcessAsUserW(
                    token,
                    None,
                    command_line,
                    None,
                    None,
                    True,
                    winapi.EXTENDED_STARTUPINFO_PRESENT
                    | winapi.CREATE_SUSPENDED
                    | winapi.CREATE_NO_WINDOW
                    | winapi.CREATE_UNICODE_ENVIRONMENT,
                    ctypes.cast(environment_block, ctypes.c_void_p),
                    str(working_directory),
                    ctypes.byref(startup),
                    ctypes.byref(process_information),
                )
            )
            if not created:
                raise IsolationUnavailableError(
                    "Unable to create the governed Python execution process.",
                    details={"win_error": winapi.last_error()},
                )

            if not winapi.kernel32.AssignProcessToJobObject(job, process_information.hProcess):
                # The real failure code must be captured *before* any other
                # Win32 call, including cleanup: `TerminateProcess` below makes
                # its own kernel call and can silently overwrite the thread's
                # last-error value, so reading it after that call risks
                # reporting the cleanup's outcome instead of the actual
                # `AssignProcessToJobObject` failure being diagnosed.
                assign_error = winapi.last_error()
                # The process exists but is suspended and uncontained. Kill it
                # rather than resuming something the Job Object cannot bound.
                winapi.kernel32.TerminateProcess(process_information.hProcess, 1)
                raise IsolationUnavailableError(
                    "Unable to assign the execution to its Job Object.",
                    details={"win_error": assign_error},
                )

            in_job = wt.BOOL()
            contained = winapi.kernel32.IsProcessInJob(process_information.hProcess, job, ctypes.byref(in_job))
            if not contained:
                # Same ordering requirement as above: capture before cleanup.
                is_process_in_job_error = winapi.last_error()
                winapi.kernel32.TerminateProcess(process_information.hProcess, 1)
                raise IsolationUnavailableError(
                    "Unable to query whether the execution is contained by its Job Object.",
                    details={"win_error": is_process_in_job_error},
                )
            if not in_job:
                winapi.kernel32.TerminateProcess(process_information.hProcess, 1)
                raise IsolationUnavailableError("The execution process is not contained by its Job Object.")

            # The child's duplicates must be closed in the parent, or the read
            # loops below never observe EOF.
            stdin_pipe.close_child()
            stdout_pipe.close_child()
            stderr_pipe.close_child()

            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []
            stdout_over = [False]
            stderr_over = [False]
            readers = [
                threading.Thread(
                    target=_drain,
                    args=(stdout_pipe.parent_fd, limits.max_output_bytes, stdout_chunks, stdout_over),
                    daemon=True,
                ),
                threading.Thread(
                    target=_drain,
                    args=(stderr_pipe.parent_fd, limits.max_output_bytes, stderr_chunks, stderr_over),
                    daemon=True,
                ),
            ]
            for reader in readers:
                reader.start()

            winapi.kernel32.ResumeThread(process_information.hThread)
            _write_stdin(stdin_pipe, stdin_payload)

            wait_ms = int(limits.timeout_seconds * 1000)
            waited = winapi.kernel32.WaitForSingleObject(process_information.hProcess, wait_ms)

            if waited == winapi.WAIT_FAILED:
                # `WaitForSingleObject` could not be performed at all -- this is
                # neither "the process finished" nor "the process timed out". We
                # have no confirmed knowledge of the execution's state, so the
                # only fail-closed response is to contain what we can (terminate
                # the job, exactly as the timeout path does) and refuse to
                # report a result. Reading `GetExitCodeProcess` here and
                # returning it as a real outcome would report an *unconfirmed*
                # process state as a successful, observed execution -- precisely
                # the failure mode this branch exists to prevent. The error must
                # be captured immediately: any further Win32 call (including the
                # containment `TerminateJobObject` below) can overwrite it.
                wait_failed_error = winapi.last_error()
                winapi.kernel32.TerminateJobObject(job, 1)
                winapi.kernel32.WaitForSingleObject(process_information.hProcess, 5000)
                for reader in readers:
                    reader.join(timeout=5.0)
                raise IsolationUnavailableError(
                    "Unable to wait on the governed execution process; its outcome could not be "
                    "confirmed and is not reported as a result.",
                    details={"win_error": wait_failed_error},
                )

            timed_out = waited == winapi.WAIT_TIMEOUT

            if timed_out:
                # Terminate the *job*, not the process: this is what makes
                # "the complete process tree is terminated" true rather than
                # aspirational.
                winapi.kernel32.TerminateJobObject(job, 1)
                winapi.kernel32.WaitForSingleObject(process_information.hProcess, 5000)

            for reader in readers:
                reader.join(timeout=5.0)

            exit_code = wt.DWORD()
            winapi.kernel32.GetExitCodeProcess(process_information.hProcess, ctypes.byref(exit_code))

            return BoundaryResult(
                exit_code=None if timed_out else int(exit_code.value),
                stdout=b"".join(stdout_chunks),
                stderr=b"".join(stderr_chunks),
                timed_out=timed_out,
                duration_ms=(time.monotonic() - started) * 1000,
                stdout_truncated=stdout_over[0],
                stderr_truncated=stderr_over[0],
            )
        finally:
            if created:
                winapi.close_handle(process_information.hThread)
                winapi.close_handle(process_information.hProcess)
            for pipe in (stdin_pipe, stdout_pipe, stderr_pipe):
                if pipe is not None:
                    pipe.close_child()
                    pipe.close_parent()
            if attribute_list is not None:
                winapi.kernel32.DeleteProcThreadAttributeList(attribute_list)
            # Closing the job last: KILL_ON_JOB_CLOSE means this is the final
            # guarantee that nothing outlives the boundary, including on the
            # error paths above that never reached the timeout handling.
            winapi.close_handle(job)
            winapi.close_handle(token)


def _write_stdin(pipe: _Pipe, payload: bytes) -> None:
    """Deliver the structured JSON input and close the stream.

    Closing is what signals end-of-input to the execution. A `BrokenPipeError`
    here means the execution exited before reading its input -- a legitimate
    outcome (a syntax error, an immediate failure), not a boundary fault, so
    it is swallowed and the real outcome is read from the exit code.
    """
    try:
        os.write(pipe.parent_fd, payload)
    except OSError as exc:
        logger.debug("Execution closed stdin before input was delivered: %s", exc)
    finally:
        pipe.close_parent()


def _quote_command(arguments: list[str]) -> str:
    """Build a Windows command line from an already-structured argument list.

    Structured arguments are quoted here exactly once, at the boundary. No
    caller ever supplies a command *string*, and no shell is ever involved --
    `CreateProcessAsUserW` receives the interpreter path directly, so there is
    no shell metacharacter interpretation anywhere in this path.
    """
    quoted: list[str] = []
    for argument in arguments:
        escaped = argument.replace('"', '\\"')
        quoted.append(f'"{escaped}"')
    return " ".join(quoted)


__all__ = [
    "BoundaryResult",
    "WindowsExecutionBoundary",
    "create_execution_job",
    "create_execution_token",
]
