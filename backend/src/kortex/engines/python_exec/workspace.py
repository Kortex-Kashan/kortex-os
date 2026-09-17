"""Sandbox identity, runtime provisioning, and ephemeral execution workspaces.

Three concerns live here, in dependency order:

1. `SandboxIdentity` -- *who* an execution runs as. On Windows this is an
   AppContainer SID; on Linux the identity is the nsjail namespace and there
   is no separate SID to manage.
2. `RuntimeImage` -- *what interpreter* it may execute. KORTEX provisions and
   owns a private copy of the Python runtime rather than granting a sandbox
   identity access to a shared system or user-profile installation, so no
   directory outside KORTEX's own data tree is ever re-ACL'd.
3. `ExecutionWorkspace` -- *where* it may write. One unique, ACL-protected,
   disposable directory per execution.

The Windows ACL strategy deliberately sets a **protected** DACL on each
directory root *before* populating it. Windows propagates inheritable ACEs to
children at creation time, so setting the root's DACL first and then writing
files into it yields a correctly-ACL'd tree without walking it -- and a
protected DACL means the tree does not inherit whatever the parent directory
happened to grant.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import logging
import os
import platform
import shutil
import stat
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from kortex.engines.python_exec.exceptions import IsolationUnavailableError

logger = logging.getLogger("kortex.engine.python_exec.workspace")

_IS_WINDOWS = platform.system() == "Windows"

# Windows access masks, spelled out rather than used as bare hex at the call
# site. `_MODIFY` is FILE_GENERIC_READ|WRITE|EXECUTE|DELETE; `_READ_EXECUTE`
# is FILE_GENERIC_READ|FILE_GENERIC_EXECUTE.
_MODIFY = 0x1301BF
_READ_EXECUTE = 0x1200A9

# Top-level entries of a CPython installation that a sandboxed execution
# needs, and the ones it must not receive. `Lib/site-packages` is excluded
# outright: third-party packages installed for the *host* are not part of a
# reproducible execution contract, and shipping them into the sandbox would
# silently widen the dependency surface of every action.
_RUNTIME_INCLUDE_DIRS = ("DLLs", "Lib", "lib")
_RUNTIME_EXCLUDE_RELATIVE = ("Lib/site-packages", "lib/site-packages", "Lib/test", "lib/test")
_RUNTIME_ROOT_FILE_SUFFIXES = (".exe", ".dll", ".so")


def _sid_to_string(psid: ctypes.c_void_p) -> str:
    from kortex.engines.python_exec import winapi

    out = wt.LPWSTR()
    if not winapi.advapi32.ConvertSidToStringSidW(psid, ctypes.byref(out)):
        raise IsolationUnavailableError("Unable to convert sandbox SID to string form.")
    try:
        return str(out.value)
    finally:
        winapi.kernel32.LocalFree(out)


def current_user_sid() -> str:
    """The SID of the account the KORTEX backend itself runs as.

    Every KORTEX-owned directory grants this SID full control, so the backend
    can always read execution results and reclaim workspaces even though the
    sandbox identity is a different, far more restricted principal.
    """
    from kortex.engines.python_exec import winapi

    token = wt.HANDLE()
    if not winapi.advapi32.OpenProcessToken(
        winapi.kernel32.GetCurrentProcess(), winapi.TOKEN_ALL_ACCESS, ctypes.byref(token)
    ):
        raise IsolationUnavailableError("Unable to open the KORTEX process token.")
    try:
        size = wt.DWORD()
        winapi.advapi32.GetTokenInformation(token, winapi.TokenUser, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not winapi.advapi32.GetTokenInformation(token, winapi.TokenUser, buffer, size, ctypes.byref(size)):
            raise IsolationUnavailableError("Unable to read the KORTEX process token user.")
        entry = ctypes.cast(buffer, ctypes.POINTER(winapi.SID_AND_ATTRIBUTES)).contents
        return _sid_to_string(ctypes.c_void_p(entry.Sid))
    finally:
        winapi.close_handle(token)


def apply_protected_dacl(directory: Path, sddl: str) -> None:
    """Replace `directory`'s DACL with `sddl`, breaking inheritance.

    Windows-only. Applied to a directory root before it is populated so that
    inheritable ACEs reach every file created inside it.
    """
    from kortex.engines.python_exec import winapi

    descriptor = ctypes.c_void_p()
    size = wt.DWORD()
    if not winapi.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, winapi.SDDL_REVISION_1, ctypes.byref(descriptor), ctypes.byref(size)
    ):
        raise IsolationUnavailableError(
            f"Unable to build a security descriptor for {directory}.",
            details={"sddl": sddl},
        )
    try:
        information = winapi.DACL_SECURITY_INFORMATION | winapi.PROTECTED_DACL_SECURITY_INFORMATION
        if not winapi.advapi32.SetFileSecurityW(str(directory), information, descriptor):
            raise IsolationUnavailableError(
                f"Unable to apply the execution ACL to {directory}.",
                details={"win_error": winapi.last_error()},
            )
    finally:
        winapi.kernel32.LocalFree(descriptor)


def read_dacl(directory: Path) -> str:
    """Return `directory`'s DACL in SDDL form.

    Exists so the OS-level security tests can assert on the ACL that was
    *actually* applied by the kernel, rather than on the SDDL string KORTEX
    intended to apply.
    """
    from kortex.engines.python_exec import winapi

    size = wt.DWORD()
    winapi.advapi32.GetFileSecurityW(str(directory), winapi.DACL_SECURITY_INFORMATION, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not winapi.advapi32.GetFileSecurityW(
        str(directory), winapi.DACL_SECURITY_INFORMATION, buffer, size, ctypes.byref(size)
    ):
        raise IsolationUnavailableError(f"Unable to read the ACL of {directory}.")
    out = wt.LPWSTR()
    length = wt.DWORD()
    if not winapi.advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        buffer, winapi.SDDL_REVISION_1, winapi.DACL_SECURITY_INFORMATION, ctypes.byref(out), ctypes.byref(length)
    ):
        raise IsolationUnavailableError(f"Unable to render the ACL of {directory}.")
    try:
        return str(out.value)
    finally:
        winapi.kernel32.LocalFree(out)


@dataclass(frozen=True)
class SandboxIdentity:
    """The OS principal one or more executions run as.

    `container_sid` is populated on Windows only. On Linux it is `None`:
    nsjail establishes identity through user/mount/network namespaces, so
    there is no separate SID for KORTEX to create or grant.
    """

    name: str
    container_sid: str | None

    @property
    def is_windows_container(self) -> bool:
        return self.container_sid is not None


def create_sandbox_identity(name_prefix: str = "KortexPythonExec") -> SandboxIdentity:
    """Create (or re-derive) the AppContainer profile executions run under.

    The profile is derived from a stable name so repeated KORTEX starts reuse
    one identity rather than accumulating profiles. `CreateAppContainerProfile`
    returning `HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)` is the expected
    steady-state result and is not an error -- the SID is then re-derived from
    the same name, which is guaranteed to produce the identical SID.
    """
    if not _IS_WINDOWS:
        return SandboxIdentity(name=name_prefix, container_sid=None)

    from kortex.engines.python_exec import winapi

    psid = ctypes.c_void_p()
    result = winapi.userenv.CreateAppContainerProfile(
        name_prefix, name_prefix, "KORTEX governed Python execution boundary", None, 0, ctypes.byref(psid)
    )
    if result < 0:
        derived = winapi.userenv.DeriveAppContainerSidFromAppContainerName(name_prefix, ctypes.byref(psid))
        if derived < 0:
            raise IsolationUnavailableError(
                "Unable to establish the AppContainer sandbox identity required for the "
                "Windows Python execution boundary.",
                details={"create_hresult": hex(result & 0xFFFFFFFF), "derive_hresult": hex(derived & 0xFFFFFFFF)},
            )
    try:
        return SandboxIdentity(name=name_prefix, container_sid=_sid_to_string(psid))
    finally:
        winapi.advapi32.FreeSid(psid)


def delete_sandbox_identity(identity: SandboxIdentity) -> None:
    """Remove an AppContainer profile by name.

    Not part of the governed execution boundary itself, and never called by
    production code: `create_sandbox_identity`'s own contract is one stable,
    reused identity for the lifetime of a KORTEX installation, precisely so
    repeated starts never accumulate profiles that would need removing. This
    exists for test-fixture teardown, so a test suite that creates its own
    named identities (e.g. `KortexPythonExecTest`) does not leave AppContainer
    profile registrations behind after it finishes.

    Per `DeleteAppContainerProfile`'s own documented contract, deleting a
    profile that does not exist also returns success (`S_OK`) -- so this is
    naturally idempotent and safe to call from a `finally`/fixture-teardown
    path even when the corresponding `create_sandbox_identity` call never
    completed. Any other, genuinely unexpected result is raised rather than
    swallowed: a teardown that silently discards a real deletion failure is
    exactly how profile residue accumulates unnoticed.
    """
    if identity.container_sid is None:
        return  # Not a Windows AppContainer identity; nothing to remove.

    from kortex.engines.python_exec import winapi

    result = winapi.userenv.DeleteAppContainerProfile(identity.name)
    if result != 0:
        raise IsolationUnavailableError(
            f"Unable to delete the AppContainer profile {identity.name!r} during teardown.",
            details={"hresult": hex(result & 0xFFFFFFFF)},
        )


@dataclass(frozen=True)
class RuntimeImage:
    """A KORTEX-owned, ACL-controlled copy of the Python runtime."""

    root: Path
    interpreter: Path

    @property
    def directory(self) -> str:
        return str(self.root)


def _runtime_fingerprint(source_root: Path) -> str:
    """Identify a provisioned image by interpreter identity, not file content.

    Hashing the tree would cost more than the copy it is meant to avoid.
    Python's full version string plus the source path changes whenever the
    interpreter is upgraded or replaced, which is exactly when the image must
    be rebuilt.
    """
    material = f"{sys.version}|{source_root}|{platform.machine()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _copy_runtime_tree(source_root: Path, target_root: Path) -> None:
    """Copy the interpreter, its DLLs, and the standard library -- and nothing
    else -- into an already-ACL'd target root."""
    excluded = {(source_root / rel).resolve() for rel in _RUNTIME_EXCLUDE_RELATIVE}

    def _ignore(directory: str, names: list[str]) -> set[str]:
        here = Path(directory).resolve()
        return {name for name in names if (here / name).resolve() in excluded or name == "__pycache__"}

    for entry in source_root.iterdir():
        if entry.is_dir() and entry.name in _RUNTIME_INCLUDE_DIRS:
            shutil.copytree(entry, target_root / entry.name, ignore=_ignore, dirs_exist_ok=True)
        elif entry.is_file() and entry.suffix.lower() in _RUNTIME_ROOT_FILE_SUFFIXES:
            shutil.copy2(entry, target_root / entry.name)


def provision_runtime(
    *,
    root_directory: Path,
    identity: SandboxIdentity,
    source_interpreter: Path | None = None,
) -> RuntimeImage:
    """Provision (idempotently) the KORTEX-owned runtime image.

    No directory outside `root_directory` is created, modified, or re-ACL'd:
    the shared interpreter installation is only ever *read*. That is the whole
    reason this function copies rather than granting the sandbox identity
    access to the system or user-profile Python.

    The completion marker is written last, so an interrupted provision leaves
    an unmarked directory that the next call rebuilds rather than a partial
    tree that the next call trusts.
    """
    interpreter_source = Path(source_interpreter or sys.executable).resolve()
    source_root = interpreter_source.parent
    fingerprint = _runtime_fingerprint(source_root)
    image_root = root_directory / f"runtime-{fingerprint}"
    marker = image_root / ".kortex-runtime-complete"
    interpreter = image_root / interpreter_source.name

    if marker.exists() and interpreter.exists():
        return RuntimeImage(root=image_root, interpreter=interpreter)

    if image_root.exists():
        shutil.rmtree(image_root, ignore_errors=True)
    image_root.mkdir(parents=True, exist_ok=True)

    if _IS_WINDOWS:
        if identity.container_sid is None:
            raise IsolationUnavailableError("A Windows runtime image requires an AppContainer sandbox identity.")
        apply_protected_dacl(
            image_root,
            "D:P"
            "(A;OICI;FA;;;SY)"
            "(A;OICI;FA;;;BA)"
            f"(A;OICI;FA;;;{current_user_sid()})"
            f"(A;OICI;0x{_READ_EXECUTE:X};;;{identity.container_sid})",
        )
    else:
        image_root.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)

    logger.info("Provisioning the KORTEX Python runtime image at %s", image_root)
    _copy_runtime_tree(source_root, image_root)
    if not interpreter.exists():
        raise IsolationUnavailableError(
            "The provisioned runtime image does not contain a Python interpreter.",
            details={"expected": str(interpreter)},
        )
    marker.write_text("complete", encoding="utf-8")
    return RuntimeImage(root=image_root, interpreter=interpreter)


class ExecutionWorkspace:
    """One unique, ACL-protected, disposable directory for a single execution.

    Two executions never share a workspace, and on Windows a workspace's
    protected DACL names only SYSTEM, the KORTEX account, and the sandbox
    identity -- so an execution cannot reach another execution's workspace
    even though both run under the same AppContainer SID, because each
    workspace path is unguessable and reclaimed at teardown.
    """

    def __init__(self, root: Path, identity: SandboxIdentity) -> None:
        self._identity = identity
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / f"exec-{uuid.uuid4().hex}"
        self.path.mkdir(parents=False, exist_ok=False)

        if _IS_WINDOWS:
            if identity.container_sid is None:
                raise IsolationUnavailableError("A Windows execution workspace requires an AppContainer identity.")
            apply_protected_dacl(
                self.path,
                "D:P"
                "(A;OICI;FA;;;SY)"
                f"(A;OICI;FA;;;{current_user_sid()})"
                f"(A;OICI;0x{_MODIFY:X};;;{identity.container_sid})",
            )
        else:
            self.path.chmod(stat.S_IRWXU)

    def write_text(self, relative_name: str, content: str) -> Path:
        """Write a UTF-8 file into the workspace and return its path."""
        target = self.path / relative_name
        target.write_text(content, encoding="utf-8")
        return target

    def dispose(self) -> None:
        """Reclaim the workspace. Never raises.

        Teardown runs in a `finally` during both success and failure paths; a
        workspace that cannot be removed (a file still mapped by a process the
        Job Object is still tearing down) must not convert a completed
        execution into a failed one. It is logged and left for the next
        provisioning sweep instead.
        """
        try:
            shutil.rmtree(self.path, ignore_errors=True)
        except Exception as exc:
            logger.warning("Unable to reclaim execution workspace %s: %s", self.path, exc)


def default_execution_root() -> Path:
    """The root under which runtime images and workspaces are created.

    Resolved the same way `kortex.core.db` resolves its own default data
    location -- a stable per-user application directory, never a path relative
    to the process's current working directory.
    """
    override = os.environ.get("KORTEX_PYTHON_EXEC_ROOT")
    if override:
        return Path(override)
    if _IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "KORTEX" / "python-exec"
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "KORTEX" / "python-exec"


def temporary_execution_root() -> Path:
    """A process-unique execution root, used by tests and by diagnostics."""
    return Path(tempfile.mkdtemp(prefix="kortex-pyexec-"))


__all__ = [
    "ExecutionWorkspace",
    "RuntimeImage",
    "SandboxIdentity",
    "apply_protected_dacl",
    "create_sandbox_identity",
    "current_user_sid",
    "default_execution_root",
    "delete_sandbox_identity",
    "provision_runtime",
    "read_dacl",
    "temporary_execution_root",
]
