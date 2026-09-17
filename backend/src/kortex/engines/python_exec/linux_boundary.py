"""The Linux Python execution boundary, built on nsjail.

Linux supports **both** trust levels. Nsjail is the isolation dependency;
KORTEX owns the Gateway. Nothing here re-implements orchestration, scheduling,
or authorization -- nsjail is invoked as a subprocess with an explicit argument
vector and nothing more.

The boundary fails closed. If the nsjail binary is absent, not executable, or
rejects the requested configuration, `IsolationUnavailableError` is raised and
**no Python runs**. There is deliberately no "degraded" path that falls back to
a bare `subprocess.run`: a fallback that silently executes unisolated code is
worse than an outright failure, because the caller believes it was contained.

Untrusted executions additionally never receive a bridge address or an
execution token in their environment (decided in `policy.resolve_policy`), so
the KORTEX capability IPC endpoint is not merely unauthorized for them -- it is
unreachable and unknown.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from kortex.engines.python_exec.exceptions import IsolationUnavailableError
from kortex.engines.python_exec.models import (
    BoundaryResult,
    PythonExecutionLimits,
    PythonNetworkPolicy,
    PythonTrustLevel,
)
from kortex.engines.python_exec.workspace import RuntimeImage

logger = logging.getLogger("kortex.engine.python_exec.linux")

DEFAULT_NSJAIL_BINARY = "nsjail"


def resolve_nsjail(binary: str | None = None) -> str:
    """Locate the nsjail binary, or fail closed.

    Resolution order is explicit argument, then `KORTEX_NSJAIL_BINARY`, then
    `PATH`. The resolved path is verified to be executable here so that a
    misconfiguration surfaces as a clear isolation failure rather than as a
    confusing `FileNotFoundError` from deep inside process creation.
    """
    candidate = binary or os.environ.get("KORTEX_NSJAIL_BINARY") or DEFAULT_NSJAIL_BINARY
    resolved = shutil.which(candidate)
    if resolved is None:
        raise IsolationUnavailableError(
            "The nsjail isolation binary required by the Linux Python execution boundary "
            "was not found. Python execution is refused rather than run unisolated.",
            details={"searched": candidate},
        )
    if not os.access(resolved, os.X_OK):
        raise IsolationUnavailableError("The nsjail binary is present but not executable.", details={"path": resolved})
    return resolved


class LinuxExecutionBoundary:
    """Runs one Python execution inside nsjail."""

    def __init__(self, runtime: RuntimeImage, *, nsjail_binary: str | None = None) -> None:
        self._runtime = runtime
        self._nsjail = resolve_nsjail(nsjail_binary)

    def build_arguments(
        self,
        *,
        workspace: Path,
        interpreter_arguments: list[str],
        limits: PythonExecutionLimits,
        trust_level: PythonTrustLevel,
        network_policy: PythonNetworkPolicy,
        environment: dict[str, str],
    ) -> list[str]:
        """Build the complete nsjail argument vector.

        Exposed as its own method, separate from `run`, specifically so tests
        can assert on the *exact* isolation flags KORTEX requests without
        needing a Linux host with nsjail installed. A test that asserts on this
        vector is checking KORTEX's configuration decision; only a test that
        actually runs nsjail can check enforcement, and the two are reported
        separately in this milestone's test evidence.
        """
        arguments = [
            self._nsjail,
            "--mode",
            "o",  # run once, then exit
            "--quiet",
            "--iface_no_lo" if network_policy is PythonNetworkPolicy.DENY else "--disable_clone_newnet",
            # Namespaces: a fresh mount/pid/ipc/uts/user namespace per execution.
            "--disable_clone_newcgroup",
            "--rlimit_as",
            str(max(1, (limits.memory_bytes or 0) // (1024 * 1024)) if limits.memory_bytes else "max"),
            "--rlimit_cpu",
            str(limits.cpu_seconds) if limits.cpu_seconds else "max",
            "--rlimit_nofile",
            "256",
            "--rlimit_fsize",
            str(max(1, limits.max_output_bytes // (1024 * 1024))),
            "--time_limit",
            str(int(limits.timeout_seconds)),
            "--max_cpus",
            "1",
            # Privilege reduction: no new privileges, and a non-root uid/gid
            # mapped inside the user namespace.
            "--nice_level",
            "0",
            "--user",
            "65534",
            "--group",
            "65534",
            "--cwd",
            str(workspace),
            # Read-only bind of the KORTEX-owned runtime image, read-write bind
            # of this execution's workspace, and nothing else from the host.
            "--bindmount_ro",
            f"{self._runtime.root}:{self._runtime.root}",
            "--bindmount",
            f"{workspace}:{workspace}",
            "--bindmount_ro",
            "/lib:/lib",
            "--bindmount_ro",
            "/lib64:/lib64",
            "--bindmount_ro",
            "/usr/lib:/usr/lib",
        ]

        if limits.max_processes is not None:
            arguments += ["--rlimit_nproc", str(limits.max_processes)]

        # Untrusted code gets the stricter seccomp policy: no new processes, no
        # ptrace, no mount, no network syscalls at all.
        if trust_level is PythonTrustLevel.UNTRUSTED:
            arguments += [
                "--seccomp_string",
                "ERRNO(1) { ptrace, process_vm_readv, process_vm_writev, mount, umount2, "
                "socket, socketpair, connect, bind, listen, accept, accept4 }",
            ]

        for key, value in sorted(environment.items()):
            arguments += ["--env", f"{key}={value}"]

        arguments.append("--")
        arguments.extend(interpreter_arguments)
        return arguments

    def run(
        self,
        *,
        arguments: list[str],
        working_directory: Path,
        environment: dict[str, str],
        stdin_payload: bytes,
        limits: PythonExecutionLimits,
        trust_level: PythonTrustLevel,
        network_policy: PythonNetworkPolicy,
    ) -> BoundaryResult:
        """Execute under nsjail and collect the structured result."""
        vector = self.build_arguments(
            workspace=working_directory,
            interpreter_arguments=arguments,
            limits=limits,
            trust_level=trust_level,
            network_policy=network_policy,
            environment=environment,
        )
        started = time.monotonic()
        timed_out = False
        stdout = stderr = b""

        # `env={}` rather than inheriting: nsjail itself is launched with an
        # empty environment, and the execution's own environment is passed
        # explicitly through `--env` above. The KORTEX process environment --
        # which holds database URLs, secret-store material and connector
        # credentials -- is therefore never even visible to the jailer.
        process = subprocess.Popen(  # noqa: S603 - explicit argv, shell=False, no user-supplied string
            vector,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(working_directory),
            env={},
            shell=False,
        )
        try:
            stdout, stderr = process.communicate(
                input=stdin_payload,
                # A small grace period beyond nsjail's own `--time_limit`, so
                # the in-jail timeout is the one that normally fires and this
                # is only a backstop against nsjail itself hanging.
                timeout=limits.timeout_seconds + 5.0,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            stdout, stderr = process.communicate()

        stdout_truncated = len(stdout) > limits.max_output_bytes
        stderr_truncated = len(stderr) > limits.max_output_bytes

        return BoundaryResult(
            exit_code=None if timed_out else process.returncode,
            stdout=stdout[: limits.max_output_bytes],
            stderr=stderr[: limits.max_output_bytes],
            timed_out=timed_out,
            duration_ms=(time.monotonic() - started) * 1000,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )


__all__ = ["DEFAULT_NSJAIL_BINARY", "LinuxExecutionBoundary", "resolve_nsjail"]
