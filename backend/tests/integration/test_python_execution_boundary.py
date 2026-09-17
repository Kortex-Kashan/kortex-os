"""OS-level security tests for the Windows Python execution boundary.

Every test in this file spawns a **real** process under the **real** boundary
and asserts on what the operating system actually did. Nothing here is mocked:
there is no fake token, no simulated Job Object, and no stubbed ACL. A test
that cannot observe real enforcement does not belong in this file -- it belongs
in `tests/unit/test_python_execution_policy.py`, which tests the *decision*.

These are marked `os_security` and skipped off Windows. A green run on a
platform where they skip is not evidence the boundary was verified; the
milestone report states their status separately for that reason.

What the boundary is, precisely: a **governed trusted-Python execution
boundary**. Its filesystem and network denial come from the AppContainer
identity. Its privilege denial comes from the restricted token. Its process-tree
containment, resource limits and deterministic termination come from the Job
Object. Those attributions are asserted individually below rather than as one
undifferentiated claim of "sandboxed".
"""

from __future__ import annotations

import json
import platform
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from kortex.engines.python_exec.models import PythonExecutionLimits
from kortex.engines.python_exec.policy import build_environment

_IS_WINDOWS = platform.system() == "Windows"

pytestmark = [
    pytest.mark.os_security,
    pytest.mark.integration,
    pytest.mark.skipif(not _IS_WINDOWS, reason="The Windows execution boundary requires Windows."),
]

if _IS_WINDOWS:  # pragma: no branch - import guard, not a behavioural branch
    from kortex.engines.python_exec import winapi
    from kortex.engines.python_exec.exceptions import IsolationUnavailableError
    from kortex.engines.python_exec.gateway import RUNNER_FILENAME
    from kortex.engines.python_exec.windows_boundary import WindowsExecutionBoundary
    from kortex.engines.python_exec.workspace import (
        ExecutionWorkspace,
        create_sandbox_identity,
        delete_sandbox_identity,
        provision_runtime,
        read_dacl,
    )

_RUNNER_SOURCE = Path("src/kortex/engines/python_exec/sandbox_runner.py")

# The identity name this test module's own fixture creates -- distinct from
# `create_sandbox_identity`'s production default ("KortexPythonExec") and from
# every other test module's own name (e.g. "KortexPythonSliceTest"), so
# teardown here can never remove a production identity or one another module's
# still-active fixture depends on.
_TEST_IDENTITY_NAME = "KortexPythonExecTest"


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[object, object, Path]]:
    """One sandbox identity and one provisioned runtime image for the module.

    Provisioning copies the interpreter and standard library (~40MB) and is
    idempotent, so it is done once per module rather than per test.

    Teardown removes the `KortexPythonExecTest` AppContainer profile this
    fixture itself created. This always runs -- a `yield`-based fixture's
    teardown executes regardless of whether the tests that used it passed or
    failed -- and a genuine deletion failure is raised, not swallowed, so it
    surfaces as a visible pytest teardown error rather than silent residue.
    The temp directory is still reclaimed even if profile deletion fails.
    """
    root = tmp_path_factory.mktemp("kortex-pyexec")
    identity = create_sandbox_identity(_TEST_IDENTITY_NAME)
    image = provision_runtime(root_directory=root, identity=identity)
    try:
        yield identity, image, root
    finally:
        try:
            delete_sandbox_identity(identity)
        finally:
            shutil.rmtree(root, ignore_errors=True)


def _run(
    sandbox: tuple[object, object, Path],
    action_source: str,
    *,
    limits: PythonExecutionLimits | None = None,
    payload: dict[str, object] | None = None,
) -> tuple[object, dict[str, object]]:
    """Execute `action_source` under the real boundary; return (raw, parsed)."""
    identity, image, root = sandbox
    workspace = ExecutionWorkspace(root / "workspaces", identity)  # type: ignore[arg-type]
    try:
        workspace.write_text("kortex_action.py", action_source)
        shutil.copy2(_RUNNER_SOURCE, workspace.path / RUNNER_FILENAME)

        import os

        environment = build_environment(
            parent_environment=dict(os.environ),
            workspace_path=str(workspace.path),
            runtime_path=str(image.root),  # type: ignore[attr-defined]
            input_file=str(workspace.path / "input.json"),
            bridge_address=None,
            execution_token=None,
        )
        stdin_payload = json.dumps(
            {
                "action_path": str(workspace.path / "kortex_action.py"),
                "entrypoint": "main",
                "input": payload or {},
            }
        ).encode("utf-8")

        result = WindowsExecutionBoundary(identity, image).run(  # type: ignore[arg-type]
            arguments=[str(image.interpreter), "-I", "-B", str(workspace.path / RUNNER_FILENAME)],  # type: ignore[attr-defined]
            working_directory=workspace.path,
            environment=environment,
            stdin_payload=stdin_payload,
            limits=limits or PythonExecutionLimits(timeout_seconds=60),
        )
        try:
            parsed = json.loads(result.stdout.decode("utf-8"))
        except ValueError:
            parsed = {}
        return result, parsed
    finally:
        workspace.dispose()


# ---------------------------------------------------------------------------
# Restricted token
# ---------------------------------------------------------------------------


_TOKEN_PROBE = """
import ctypes
import ctypes.wintypes as wt

TOKEN_QUERY = 0x0008
TokenPrivileges = 3
TokenRestrictedSids = 11
TokenElevation = 20
TokenIsAppContainer = 29


def main(payload):
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    # argtypes/restype are mandatory: ctypes defaults a return value to c_int,
    # which truncates the 64-bit pseudo-handle from GetCurrentProcess.
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wt.HANDLE
    advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [
        wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]

    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return {'error': 'OpenProcessToken failed'}

    def leading_count(info_class):
        size = wt.DWORD()
        advapi32.GetTokenInformation(token, info_class, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, info_class, buffer, size, ctypes.byref(size)):
            return -1
        return ctypes.cast(buffer, ctypes.POINTER(wt.DWORD)).contents.value

    def dword(info_class):
        value = wt.DWORD()
        written = wt.DWORD()
        ok = advapi32.GetTokenInformation(token, info_class, ctypes.byref(value), 4, ctypes.byref(written))
        return value.value if ok else -1

    return {
        'privilege_count': leading_count(TokenPrivileges),
        'restricted_sid_count': leading_count(TokenRestrictedSids),
        'is_app_container': dword(TokenIsAppContainer),
        'elevation': dword(TokenElevation),
    }
"""


def test_restricted_token_and_appcontainer_are_actually_applied_to_the_process(sandbox) -> None:
    """Both identity layers are applied to the *real* process, not just built.

    Read from inside the boundary, via the process's own token:

    * `restricted_sid_count > 0` is the definitive proof the restricted token
      reached the process -- only `CreateRestrictedToken` produces a token with
      a restricting-SID list.
    * `privilege_count <= 1` proves `DISABLE_MAX_PRIVILEGE` took effect;
      it leaves at most `SeChangeNotifyPrivilege`.
    * `is_app_container == 1` proves the AppContainer identity was attached --
      the layer that actually supplies filesystem and network denial.

    `TokenIsRestricted` is deliberately *not* used: that information class
    fails with ERROR_INSUFFICIENT_BUFFER for a 4-byte query on this platform,
    and a failed query is indistinguishable from a negative answer. The
    restricting-SID count is both queryable and stronger evidence.
    """
    _, parsed = _run(sandbox, _TOKEN_PROBE)
    assert parsed.get("status") == "ok", parsed
    output = parsed["output"]

    assert output["restricted_sid_count"] > 0, (
        f"the process token carries no restricting SIDs "
        f"({output['restricted_sid_count']}) -- the restricted token was not applied"
    )
    assert output["privilege_count"] <= 1, f"expected privileges stripped, got {output['privilege_count']}"
    assert output["is_app_container"] == 1, "the process is not running under an AppContainer identity"
    assert output["elevation"] == 0, "the execution process is elevated"


def test_administrative_group_membership_is_denied_inside_the_boundary(sandbox) -> None:
    """The Administrators SID is deny-only, so an admin check fails inside."""
    _, parsed = _run(
        sandbox,
        "import ctypes\ndef main(payload):\n    return {'is_admin': bool(ctypes.WinDLL('shell32').IsUserAnAdmin())}\n",
    )
    assert parsed.get("status") == "ok", parsed
    assert parsed["output"]["is_admin"] is False


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------


def test_host_user_profile_is_not_readable_from_inside_the_boundary(sandbox, tmp_path: Path) -> None:
    """A real host file the KORTEX account can read must be denied inside.

    Written to the *user profile* rather than to a temp directory, because the
    profile is exactly the "unrelated host data" the architecture requires be
    denied, and it is ACL'd to the user rather than to the AppContainer SID.
    """
    secret = Path.home() / f"kortex_boundary_probe_{int(time.time())}.txt"
    secret.write_text("HOST_SECRET_THAT_MUST_NOT_BE_READABLE", encoding="utf-8")
    try:
        # Sanity: the KORTEX account itself *can* read it, so a denial from
        # inside the boundary is caused by the boundary, not by a missing file.
        assert secret.read_text(encoding="utf-8").startswith("HOST_SECRET")

        _, parsed = _run(
            sandbox,
            "def main(payload):\n"
            "    try:\n"
            "        with open(payload['path'], 'r') as handle:\n"
            "            return {'read': 'ALLOWED', 'content': handle.read()}\n"
            "    except Exception as exc:\n"
            "        return {'read': 'DENIED', 'error': type(exc).__name__}\n",
            payload={"path": str(secret)},
        )
        assert parsed.get("status") == "ok", parsed
        assert parsed["output"]["read"] == "DENIED"
        assert parsed["output"]["error"] == "PermissionError"
    finally:
        secret.unlink(missing_ok=True)


def test_execution_workspace_is_writable(sandbox) -> None:
    """The explicit workspace ACL is what makes the boundary usable at all."""
    _, parsed = _run(
        sandbox,
        "def main(payload):\n"
        "    with open('artifact.txt', 'w') as handle:\n"
        "        handle.write('produced inside the boundary')\n"
        "    with open('artifact.txt') as handle:\n"
        "        return {'roundtrip': handle.read()}\n",
    )
    assert parsed.get("status") == "ok", parsed
    assert parsed["output"]["roundtrip"] == "produced inside the boundary"


def test_one_execution_cannot_reach_another_executions_workspace(sandbox) -> None:
    """Workspaces are per-execution and reclaimed, so a path from a finished
    execution resolves to nothing for a later one."""
    identity, _image, root = sandbox
    other = ExecutionWorkspace(root / "workspaces", identity)  # type: ignore[arg-type]
    other.write_text("tenant-b-secret.txt", "OTHER_EXECUTION_DATA")
    leaked_path = str(other.path / "tenant-b-secret.txt")
    other.dispose()

    _, parsed = _run(
        sandbox,
        "def main(payload):\n"
        "    try:\n"
        "        with open(payload['path']) as handle:\n"
        "            return {'read': 'ALLOWED', 'content': handle.read()}\n"
        "    except Exception as exc:\n"
        "        return {'read': 'DENIED', 'error': type(exc).__name__}\n",
        payload={"path": leaked_path},
    )
    assert parsed["output"]["read"] == "DENIED"


def test_workspace_and_runtime_acls_name_the_sandbox_identity(sandbox) -> None:
    """The ACL the kernel actually holds -- read back, not merely intended."""
    identity, image, root = sandbox
    container_sid = identity.container_sid  # type: ignore[attr-defined]

    workspace = ExecutionWorkspace(root / "workspaces", identity)  # type: ignore[arg-type]
    try:
        workspace_dacl = read_dacl(workspace.path)
        runtime_dacl = read_dacl(image.root)  # type: ignore[attr-defined]

        # Protected: the tree does not inherit whatever the parent granted.
        assert workspace_dacl.startswith("D:P"), workspace_dacl
        assert runtime_dacl.startswith("D:P"), runtime_dacl
        # The sandbox identity is named explicitly in both.
        assert container_sid in workspace_dacl
        assert container_sid in runtime_dacl
        # Modify on the workspace, read+execute only on the runtime.
        assert "0x1301bf" in workspace_dacl.lower()
        assert "0x1200a9" in runtime_dacl.lower()
    finally:
        workspace.dispose()


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


def test_default_deny_network_policy_is_enforced_by_the_kernel(sandbox) -> None:
    """An AppContainer with zero capability SIDs has no network access.

    Both an outbound internet connection and a loopback connection are
    attempted: loopback matters because it is the channel a sandbox escape
    would most plausibly use to reach a local service, and it requires the
    `privateNetworkClientServer` capability the identity deliberately lacks.
    """
    _, parsed = _run(
        sandbox,
        "import socket\n"
        "def main(payload):\n"
        "    out = {}\n"
        "    for label, target in (('internet', ('1.1.1.1', 53)), ('loopback', ('127.0.0.1', 445))):\n"
        "        try:\n"
        "            socket.create_connection(target, 3).close()\n"
        "            out[label] = 'ALLOWED'\n"
        "        except OSError as exc:\n"
        "            out[label] = 'DENIED:' + type(exc).__name__\n"
        "    return out\n",
        limits=PythonExecutionLimits(timeout_seconds=45),
    )
    assert parsed.get("status") == "ok", parsed
    assert parsed["output"]["internet"].startswith("DENIED")
    assert parsed["output"]["loopback"].startswith("DENIED")


# ---------------------------------------------------------------------------
# Job Object
# ---------------------------------------------------------------------------


def test_timeout_terminates_the_entire_process_tree(sandbox) -> None:
    """`TerminateJobObject`, not process kill: the whole tree dies.

    The wall-clock assertion is what proves termination actually happened --
    a boundary that merely stopped waiting would return promptly while the
    process kept running, and the elapsed time would look identical.
    """
    started = time.monotonic()
    result, _ = _run(
        sandbox,
        "import time\ndef main(payload):\n    time.sleep(120)\n    return 'unreachable'\n",
        limits=PythonExecutionLimits(timeout_seconds=5),
    )
    elapsed = time.monotonic() - started

    assert result.timed_out is True  # type: ignore[attr-defined]
    assert result.exit_code is None  # type: ignore[attr-defined]
    assert elapsed < 30, f"the boundary took {elapsed:.1f}s to terminate a 5s-budget execution"


def test_active_process_limit_blocks_child_process_creation(sandbox) -> None:
    """Python has no unrestricted child-process creation inside the boundary."""
    _, parsed = _run(
        sandbox,
        "import subprocess, sys\n"
        "def main(payload):\n"
        "    try:\n"
        "        subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "        return {'spawn': 'ALLOWED'}\n"
        "    except OSError as exc:\n"
        "        return {'spawn': 'DENIED:' + type(exc).__name__}\n",
        limits=PythonExecutionLimits(timeout_seconds=45, max_processes=1),
    )
    assert parsed.get("status") == "ok", parsed
    assert parsed["output"]["spawn"].startswith("DENIED")


def test_process_memory_limit_is_enforced(sandbox) -> None:
    """A configured Job Object memory limit is a real limit.

    The allocation is deliberately far above the configured cap, so a
    successful allocation would mean the limit was never applied.
    """
    _, parsed = _run(
        sandbox,
        "def main(payload):\n"
        "    try:\n"
        "        blocks = []\n"
        "        for _ in range(64):\n"
        "            blocks.append(bytearray(16 * 1024 * 1024))\n"
        "        return {'allocated': 'ALLOWED', 'blocks': len(blocks)}\n"
        "    except MemoryError:\n"
        "        return {'allocated': 'DENIED:MemoryError'}\n",
        limits=PythonExecutionLimits(timeout_seconds=60, memory_bytes=128 * 1024 * 1024),
    )
    # Either Python observes the refusal as MemoryError, or the Job Object
    # terminates the process outright. Both are the limit being enforced; an
    # execution that allocated 1GB under a 128MB cap would be neither.
    if parsed.get("status") == "ok":
        assert parsed["output"]["allocated"].startswith("DENIED"), parsed


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def test_no_kortex_secret_reaches_the_execution_environment(sandbox, monkeypatch) -> None:
    """Secrets present in the KORTEX process environment must not cross in."""
    monkeypatch.setenv("KORTEX_DATABASE_URL", "postgresql://kortex:hunter2@db/kortex")
    monkeypatch.setenv("KORTEX_MASTER_KEY", "master-key-material-abc")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_never_be_visible")

    _, parsed = _run(
        sandbox,
        "import os\ndef main(payload):\n    return {'env': dict(os.environ)}\n",
    )
    assert parsed.get("status") == "ok", parsed
    environment = parsed["output"]["env"]

    assert "KORTEX_DATABASE_URL" not in environment
    assert "KORTEX_MASTER_KEY" not in environment
    assert "GITHUB_TOKEN" not in environment
    joined = "|".join(f"{key}={value}" for key, value in environment.items())
    assert "hunter2" not in joined
    assert "master-key-material-abc" not in joined
    assert "ghp_should_never_be_visible" not in joined


def test_action_output_is_separated_from_action_diagnostics(sandbox) -> None:
    """stdout carries only the result document; `print` goes to stderr.

    This is what lets the Gateway treat an unparseable stdout as a real
    protocol failure instead of as "the action printed something".
    """
    result, parsed = _run(
        sandbox,
        "def main(payload):\n    print('DIAGNOSTIC LINE')\n    return {'value': 42}\n",
    )
    assert parsed == {"status": "ok", "output": {"value": 42}}
    assert b"DIAGNOSTIC LINE" in result.stderr  # type: ignore[attr-defined]
    assert b"DIAGNOSTIC LINE" not in result.stdout  # type: ignore[attr-defined]


def test_action_failure_is_reported_as_a_structured_result(sandbox) -> None:
    """A raising action produces a structured error, not a parse failure."""
    _, parsed = _run(
        sandbox,
        "def main(payload):\n    raise ValueError('deliberate failure')\n",
    )
    assert parsed["status"] == "error"
    assert parsed["error_type"] == "ValueError"
    assert "deliberate failure" in parsed["error"]


# ---------------------------------------------------------------------------
# WaitForSingleObject failure (WAIT_FAILED)
# ---------------------------------------------------------------------------
#
# The one deliberate exception to this file's "nothing here is mocked" rule.
# A genuine `WAIT_FAILED` return from `WaitForSingleObject` requires the
# process handle to become invalid *while a wait on it is already in flight*
# -- for example by racing a handle close against a live wait from another
# thread. That is inherently timing-dependent and not something a test can
# reliably force on a real handle without itself becoming flaky. What can be
# tested reliably, end to end, against the real boundary is the *code's own
# decision* when the OS reports that this call could not be performed: only
# `WindowsExecutionBoundary`'s own `WaitForSingleObject` call is replaced
# below. The token, the AppContainer, the Job Object, and the process it
# creates are all real, exactly as everywhere else in this file.


def test_wait_failed_is_never_reported_as_a_successful_or_timed_out_execution(
    sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`WAIT_FAILED` must fail closed, not be read as `WAIT_OBJECT_0`.

    Before this was fixed, `timed_out = waited == WAIT_TIMEOUT` treated any
    value other than `WAIT_TIMEOUT` -- including the failure sentinel
    `WAIT_FAILED` -- as "the process finished normally", and went on to call
    `GetExitCodeProcess` on a process whose state was never actually
    confirmed. That risks reporting an unconfirmed process outcome as a real,
    successful `BoundaryResult`. The fix must instead raise
    `IsolationUnavailableError` -- which the Gateway already reports as a
    `REJECTED` result with no boundary attributed to it, never a
    `SUCCEEDED`/`FAILED`/`TIMEOUT` outcome -- and must still attempt real
    containment (`TerminateJobObject`) on the way out.
    """
    identity, image, root = sandbox
    workspace = ExecutionWorkspace(root / "workspaces", identity)  # type: ignore[arg-type]
    terminate_calls: list[int] = []

    real_terminate_job_object = winapi.kernel32.TerminateJobObject

    def _spy_terminate_job_object(job: object, exit_code: int) -> object:
        # The real containment call still runs -- only counted, never faked --
        # so this test also proves fail-closed WAIT_FAILED handling still
        # terminates the real process tree, not merely that it raises.
        terminate_calls.append(exit_code)
        return real_terminate_job_object(job, exit_code)

    def _fake_wait_for_single_object(handle: object, wait_ms: int) -> int:
        return winapi.WAIT_FAILED

    injected_error_code = 6  # ERROR_INVALID_HANDLE -- a plausible real cause.
    monkeypatch.setattr(winapi.kernel32, "WaitForSingleObject", _fake_wait_for_single_object)
    monkeypatch.setattr(winapi.kernel32, "TerminateJobObject", _spy_terminate_job_object)
    monkeypatch.setattr(winapi, "last_error", lambda: injected_error_code)

    try:
        workspace.write_text("kortex_action.py", "def main(payload):\n    return {'value': 'unreachable'}\n")
        shutil.copy2(_RUNNER_SOURCE, workspace.path / RUNNER_FILENAME)

        import os

        environment = build_environment(
            parent_environment=dict(os.environ),
            workspace_path=str(workspace.path),
            runtime_path=str(image.root),  # type: ignore[attr-defined]
            input_file=str(workspace.path / "input.json"),
            bridge_address=None,
            execution_token=None,
        )
        stdin_payload = json.dumps(
            {"action_path": str(workspace.path / "kortex_action.py"), "entrypoint": "main", "input": {}}
        ).encode("utf-8")

        with pytest.raises(IsolationUnavailableError) as excinfo:
            WindowsExecutionBoundary(identity, image).run(  # type: ignore[arg-type]
                arguments=[str(image.interpreter), "-I", "-B", str(workspace.path / RUNNER_FILENAME)],  # type: ignore[attr-defined]
                working_directory=workspace.path,
                environment=environment,
                stdin_payload=stdin_payload,
                limits=PythonExecutionLimits(timeout_seconds=30),
            )
    finally:
        workspace.dispose()

    # The captured error is the one read at the point of failure, not
    # whatever `TerminateJobObject`/cleanup left behind afterward.
    assert excinfo.value.details["win_error"] == injected_error_code
    assert "outcome could not be confirmed" in str(excinfo.value)
    # Fail-closed still means "contain what we can": the real Job Object was
    # actually terminated, not merely that an exception was raised.
    assert terminate_calls, "WAIT_FAILED must still trigger real Job Object termination"


# ---------------------------------------------------------------------------
# KORTEX database-file denial
# ---------------------------------------------------------------------------


def test_the_kortex_database_file_is_not_readable_from_inside_the_boundary(sandbox) -> None:
    r"""The KORTEX database itself -- not just "some file in the profile".

    `test_host_user_profile_is_not_readable_from_inside_the_boundary` already
    proves the whole user-profile tree is denied, which covers this by
    generalization. This test targets the specific claim directly: it drives
    `kortex.core.db`'s own, real, unmodified default-path resolution
    (`_default_app_data_dir`/`_default_sqlite_url` -- the exact functions
    `DatabaseEngineManager` calls when `KORTEX_DATABASE_URL` is unset) rather
    than reimplementing that logic, creates a real database there with the
    real production schema, and proves it is unreadable from inside the
    boundary.

    `APPDATA` is redirected to a disposable `tmp_path` for this test only.
    The *mechanism* being exercised is still the real one -- this only avoids
    the two problems a literal global-default test would have on a shared
    dev machine: colliding with whatever real local KORTEX installation may
    already be at `%APPDATA%\KORTEX\kortex_local.db`, and (per
    `test_security_bootstrap.py`'s own documented precedent) that shared file
    is already known to accumulate several megabytes of unrelated data with
    nothing in this suite resetting it.

    Deployment modes this does NOT cover, documented rather than silently
    assumed: a deployment where `KORTEX_DATABASE_URL` points at PostgreSQL
    has no local database *file* for a filesystem ACL to protect at all --
    `test_default_deny_network_policy_is_enforced_by_the_kernel` is what
    protects that configuration, by denying the boundary any network path to
    it. An operator who explicitly points `KORTEX_DATABASE_URL` at a SQLite
    file outside the user-profile tree has moved it outside this specific
    guarantee themselves; that is a deployment choice, not a KORTEX default,
    and is out of scope for this test.
    """
    import asyncio
    import os

    from kortex.core.db import DatabaseEngineManager, _default_app_data_dir, _default_sqlite_url

    original_appdata = os.environ.get("APPDATA")
    isolated_root = sandbox[2] / "isolated_appdata"
    os.environ["APPDATA"] = str(isolated_root)
    try:
        # The real production function, not a reimplementation of its logic.
        resolved_app_data_dir = _default_app_data_dir()
        resolved_url = _default_sqlite_url()
        assert resolved_app_data_dir == isolated_root / "KORTEX"
        assert "kortex_local.db" in resolved_url

        db_path = resolved_app_data_dir / "kortex_local.db"

        async def _create_real_database() -> None:
            manager = DatabaseEngineManager(connection_url=resolved_url)
            await manager.connect()
            await manager.create_all_tables()
            await manager.disconnect()

        asyncio.run(_create_real_database())
        assert db_path.is_file(), "the real production schema was not created at the resolved default path"

        # Sanity: the KORTEX-owning account can read it. A denial from inside
        # the boundary is therefore caused by the boundary, not a missing or
        # inaccessible file.
        with open(db_path, "rb") as handle:
            assert handle.read(16), "the created database file is unexpectedly empty"
    finally:
        if original_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = original_appdata

    _, parsed = _run(
        sandbox,
        "def main(payload):\n"
        "    try:\n"
        "        with open(payload['db_path'], 'rb') as handle:\n"
        "            return {'read': 'ALLOWED', 'content': handle.read(16).hex()}\n"
        "    except Exception as exc:\n"
        "        return {'read': 'DENIED', 'error': type(exc).__name__}\n",
        payload={"db_path": str(db_path)},
    )
    assert parsed.get("status") == "ok", parsed
    assert parsed["output"]["read"] == "DENIED"
    assert parsed["output"]["error"] == "PermissionError"
