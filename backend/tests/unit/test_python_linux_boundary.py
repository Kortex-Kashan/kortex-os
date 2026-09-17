"""Tests for the Linux nsjail boundary's *configuration* and fail-closed path.

These run on any platform and prove two things:

1. The Gateway fails closed when nsjail is unavailable. This is checkable
   anywhere, because it is about what KORTEX does when the dependency is
   missing.
2. The argument vector KORTEX hands nsjail requests the isolation the
   architecture specifies.

They deliberately prove **nothing about enforcement**. Only a Linux host with
nsjail installed can show that the namespaces, seccomp filter and rlimits
actually hold, and no test in this file should ever be cited as evidence that
they do. That gap is recorded in `docs/engines/python-execution.md` and in the
milestone report as ENVIRONMENT DEPENDENT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.engines.python_exec.exceptions import IsolationUnavailableError
from kortex.engines.python_exec.linux_boundary import LinuxExecutionBoundary, resolve_nsjail
from kortex.engines.python_exec.models import (
    PythonExecutionLimits,
    PythonNetworkPolicy,
    PythonTrustLevel,
)
from kortex.engines.python_exec.workspace import RuntimeImage


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeImage:
    root = tmp_path / "runtime"
    root.mkdir()
    interpreter = root / "python3"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    return RuntimeImage(root=root, interpreter=interpreter)


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


def test_missing_nsjail_refuses_execution_rather_than_running_unisolated() -> None:
    """The single most important property of this boundary.

    A fallback to a bare subprocess would execute the code *and report success*,
    leaving the caller believing it was contained. Refusing is the only safe
    outcome, so the absence of the dependency must raise.
    """
    with pytest.raises(IsolationUnavailableError) as excinfo:
        resolve_nsjail("kortex-nsjail-that-does-not-exist")

    assert "refused rather than run unisolated" in str(excinfo.value)
    assert excinfo.value.details["searched"] == "kortex-nsjail-that-does-not-exist"


def test_constructing_the_boundary_fails_closed_when_nsjail_is_absent(runtime: RuntimeImage) -> None:
    """The failure happens at construction, before any workspace or process."""
    with pytest.raises(IsolationUnavailableError):
        LinuxExecutionBoundary(runtime, nsjail_binary="kortex-nsjail-that-does-not-exist")


def test_nsjail_binary_is_resolved_from_the_environment(monkeypatch, tmp_path: Path) -> None:
    """`KORTEX_NSJAIL_BINARY` overrides the PATH lookup.

    Asserted via a *non-existent* override so the test does not depend on any
    real binary: what matters is that the override is the value consulted.
    """
    monkeypatch.setenv("KORTEX_NSJAIL_BINARY", "kortex-env-supplied-nsjail")
    with pytest.raises(IsolationUnavailableError) as excinfo:
        resolve_nsjail()
    assert excinfo.value.details["searched"] == "kortex-env-supplied-nsjail"


# ---------------------------------------------------------------------------
# Requested isolation
# ---------------------------------------------------------------------------


def _vector(runtime: RuntimeImage, tmp_path: Path, **overrides: object) -> list[str]:
    """Build an argument vector without requiring nsjail to be installed."""
    boundary = LinuxExecutionBoundary.__new__(LinuxExecutionBoundary)
    boundary._runtime = runtime
    boundary._nsjail = "/usr/bin/nsjail"
    options: dict[str, object] = {
        "workspace": tmp_path / "ws",
        "interpreter_arguments": [str(runtime.interpreter), "runner.py"],
        "limits": PythonExecutionLimits(
            timeout_seconds=30, memory_bytes=256 * 1024 * 1024, cpu_seconds=15, max_processes=1
        ),
        "trust_level": PythonTrustLevel.UNTRUSTED,
        "network_policy": PythonNetworkPolicy.DENY,
        "environment": {"PATH": str(runtime.root)},
    }
    options.update(overrides)
    return boundary.build_arguments(**options)  # type: ignore[arg-type]


def test_requested_vector_binds_only_the_runtime_and_the_workspace(runtime: RuntimeImage, tmp_path: Path) -> None:
    """Nothing else from the host filesystem is mounted into the jail."""
    vector = _vector(runtime, tmp_path)
    joined = " ".join(vector)

    # Runtime read-only, workspace read-write.
    assert f"--bindmount_ro {runtime.root}:{runtime.root}" in joined
    assert f"--bindmount {tmp_path / 'ws'}:{tmp_path / 'ws'}" in joined
    # No host home, no /etc, no /root, no whole-filesystem bind.
    for forbidden in ("--bindmount /:", "--bindmount /home", "--bindmount /etc", "--bindmount /root"):
        assert forbidden not in joined


def test_requested_vector_denies_network_by_default(runtime: RuntimeImage, tmp_path: Path) -> None:
    vector = _vector(runtime, tmp_path, network_policy=PythonNetworkPolicy.DENY)
    assert "--iface_no_lo" in vector
    assert "--disable_clone_newnet" not in vector


def test_requested_vector_applies_every_configured_limit(runtime: RuntimeImage, tmp_path: Path) -> None:
    vector = _vector(runtime, tmp_path)

    assert vector[vector.index("--rlimit_as") + 1] == "256"
    assert vector[vector.index("--rlimit_cpu") + 1] == "15"
    assert vector[vector.index("--rlimit_nproc") + 1] == "1"
    assert vector[vector.index("--time_limit") + 1] == "30"
    # Privilege reduction: a non-root uid/gid inside the user namespace.
    assert vector[vector.index("--user") + 1] == "65534"
    assert vector[vector.index("--group") + 1] == "65534"


def test_untrusted_python_additionally_requests_a_seccomp_filter(runtime: RuntimeImage, tmp_path: Path) -> None:
    """Untrusted code gets the stricter syscall policy; trusted code does not."""
    untrusted = _vector(runtime, tmp_path, trust_level=PythonTrustLevel.UNTRUSTED)
    assert "--seccomp_string" in untrusted
    policy = untrusted[untrusted.index("--seccomp_string") + 1]
    for syscall in ("ptrace", "mount", "socket", "connect"):
        assert syscall in policy

    trusted = _vector(runtime, tmp_path, trust_level=PythonTrustLevel.TRUSTED)
    assert "--seccomp_string" not in trusted


def test_the_execution_environment_is_passed_explicitly_not_inherited(runtime: RuntimeImage, tmp_path: Path) -> None:
    """Every variable crosses as an explicit `--env`.

    The Linux boundary launches nsjail itself with an empty environment, so
    the KORTEX process environment -- database URLs, secret-store material,
    connector credentials -- is never even visible to the jailer.
    """
    vector = _vector(
        runtime,
        tmp_path,
        environment={"PATH": str(runtime.root), "KORTEX_WORKSPACE": str(tmp_path / "ws")},
    )
    env_values = [vector[index + 1] for index, item in enumerate(vector) if item == "--env"]
    assert f"PATH={runtime.root}" in env_values
    assert f"KORTEX_WORKSPACE={tmp_path / 'ws'}" in env_values


def test_the_interpreter_arguments_come_last_after_a_separator(runtime: RuntimeImage, tmp_path: Path) -> None:
    """`--` separates nsjail's own flags from the command it runs.

    Without it, an argument of the executed command could be parsed as an
    nsjail flag and silently alter the isolation being requested.
    """
    vector = _vector(runtime, tmp_path)
    separator = vector.index("--")
    assert vector[separator + 1 :] == [str(runtime.interpreter), "runner.py"]
