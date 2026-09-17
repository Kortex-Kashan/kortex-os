"""Security policy for the KORTEX Python Execution Gateway.

Everything in this module is a *decision*, never an enforcement mechanism:
`policy.py` decides what an execution is allowed to be, and the platform
boundary modules (`windows_boundary.py`, `linux_boundary.py`) are the only
code that actually enforces it against the operating system.

Keeping the two apart is what makes the honesty requirement checkable: a
boundary that cannot enforce a decision made here must raise
`IsolationUnavailableError` rather than quietly downgrading the policy, and
a test can assert that by inspecting the policy and the boundary separately.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass

from kortex.engines.python_exec.exceptions import UnsupportedTrustLevelError
from kortex.engines.python_exec.models import (
    PythonActionVersion,
    PythonBoundaryKind,
    PythonExecutionLimits,
    PythonNetworkPolicy,
    PythonTrustLevel,
)

# The only environment variables an execution ever inherits. Everything else
# in the parent environment is dropped -- this is an allowlist, not a
# denylist, precisely so a newly-introduced KORTEX secret in the backend's
# own environment (a database URL, a SecretStore passphrase, a connector
# credential, an agent private key) can never reach Python by default simply
# because nobody remembered to add it to a blocklist.
#
# `SystemRoot` and `windir` are required for Windows DLL resolution.
# `LOCALAPPDATA` is required by the AppContainer runtime itself: a container's
# profile lives under `%LOCALAPPDATA%\Packages\<container>`, and creating a
# process under an AppContainer identity with this variable absent fails with
# ERROR_ENVVAR_NOT_FOUND (203) before the process ever starts -- verified
# empirically during this milestone by minimising the environment until only
# this variable remained load-bearing. It is a path, not a credential.
# `PATH` is deliberately absent -- the boundary sets a minimal one pointing
# only at the provisioned runtime.
_WINDOWS_BASE_ENV_KEYS = frozenset(
    {"SystemRoot", "windir", "LOCALAPPDATA", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}
)
_POSIX_BASE_ENV_KEYS = frozenset({"LANG", "LC_ALL"})

# Environment variables the Gateway itself sets inside the workspace. Listed
# here (rather than inlined at the call site) so the complete set of what
# Python can observe is readable in one place.
ENV_WORKSPACE = "KORTEX_WORKSPACE"
ENV_INPUT_FILE = "KORTEX_INPUT_FILE"
ENV_BRIDGE_ADDRESS = "KORTEX_BRIDGE_ADDRESS"
ENV_EXECUTION_TOKEN = "KORTEX_EXECUTION_TOKEN"  # noqa: S105 - a variable NAME, not a secret

# Never logged, never persisted, never included in a result payload. Any
# code path that serializes an environment must filter these out.
SENSITIVE_ENV_KEYS = frozenset({ENV_EXECUTION_TOKEN})


@dataclass(frozen=True)
class ExecutionPolicy:
    """The fully-resolved, enforceable decision for one execution."""

    trust_level: PythonTrustLevel
    boundary: PythonBoundaryKind
    limits: PythonExecutionLimits
    network_policy: PythonNetworkPolicy
    network_allow_list: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    capability_bridge_enabled: bool
    allow_subprocesses: bool

    def permits_capability(self, capability_name: str) -> bool:
        """Exact-match allowlist check. No prefixes, no wildcards, no
        normalization -- the name either is in the declared allowlist or it
        is not."""
        return self.capability_bridge_enabled and capability_name in self.allowed_capabilities


def current_platform_boundary() -> PythonBoundaryKind:
    """The governed boundary this host provides.

    Raises `UnsupportedTrustLevelError` on any platform KORTEX has no
    implemented boundary for, rather than falling back to an unguarded
    `subprocess.run` -- there is no "no boundary" execution mode.
    """
    system = platform.system()
    if system == "Windows":
        return PythonBoundaryKind.WINDOWS_RESTRICTED_TOKEN_JOB
    if system == "Linux":
        return PythonBoundaryKind.LINUX_NSJAIL
    raise UnsupportedTrustLevelError(
        f"KORTEX provides no governed Python execution boundary on platform {system!r}.",
        details={"platform": system},
    )


def resolve_policy(version: PythonActionVersion) -> ExecutionPolicy:
    """Resolve an Action version into the enforceable policy for one execution.

    This is the single place where the frozen trust-model rules become code:

    * Windows Untrusted Python is rejected here, before any workspace is
      created or any process is spawned. It is an MVP non-goal, and the
      rejection is a policy decision rather than a boundary failure so that
      it is impossible to reach the Windows boundary with an untrusted
      action at all.
    * Untrusted Python gets no capability bridge, ever -- not an empty
      allowlist that a later edit could widen, but `capability_bridge_
      enabled=False`, so the bridge address is never even placed in its
      environment.
    * Network is default-deny for both trust levels. A Trusted action may
      declare an allow list; an Untrusted action's declaration is ignored
      and forced back to DENY, so an untrusted author cannot grant itself
      network access by editing its own action metadata.
    """
    boundary = current_platform_boundary()

    is_windows_boundary = boundary is PythonBoundaryKind.WINDOWS_RESTRICTED_TOKEN_JOB
    if version.trust_level is PythonTrustLevel.UNTRUSTED and is_windows_boundary:
        raise UnsupportedTrustLevelError(
            "Untrusted Python execution is not supported on Windows in this release. "
            "The Windows boundary is a governed trusted-Python execution boundary, "
            "not a malicious-code sandbox.",
            details={"action_id": version.action_id, "version": version.version},
        )

    is_trusted = version.trust_level is PythonTrustLevel.TRUSTED

    if is_trusted:
        network_policy = version.network_policy
        network_allow_list = version.network_allow_list if network_policy is PythonNetworkPolicy.ALLOW_LIST else ()
        allowed_capabilities = version.allowed_capabilities
    else:
        # An untrusted action's own declarations are not inputs to its
        # containment -- they are ignored outright.
        network_policy = PythonNetworkPolicy.DENY
        network_allow_list = ()
        allowed_capabilities = ()

    return ExecutionPolicy(
        trust_level=version.trust_level,
        boundary=boundary,
        limits=version.limits,
        network_policy=network_policy,
        network_allow_list=network_allow_list,
        allowed_capabilities=allowed_capabilities,
        capability_bridge_enabled=is_trusted and bool(allowed_capabilities),
        # Neither trust level gets unrestricted child-process creation. The
        # Job Object / nsjail process limit is the enforcement; this flag
        # only records whether more than the Python process itself is
        # permitted at all.
        allow_subprocesses=False,
    )


def build_environment(
    *,
    parent_environment: dict[str, str],
    workspace_path: str,
    runtime_path: str,
    input_file: str,
    bridge_address: str | None,
    execution_token: str | None,
) -> dict[str, str]:
    """Build the complete, sanitized environment for one execution.

    Constructed additively from an allowlist of the parent environment plus
    the Gateway's own variables -- the parent environment is never copied
    and then pruned, so a secret that nobody anticipated is absent by
    construction rather than by successful filtering.
    """
    allowed_keys = _WINDOWS_BASE_ENV_KEYS if platform.system() == "Windows" else _POSIX_BASE_ENV_KEYS
    # Matched case-insensitively, preserving the parent's own spelling.
    # `os.environ` upper-cases every key on Windows, so a case-sensitive
    # comparison silently drops `SystemRoot` and `windir` -- an allowlist that
    # quietly matches nothing is indistinguishable from a working one until
    # something downstream needs the variable.
    normalized = {key.casefold(): key for key in allowed_keys}
    environment = {key: value for key, value in parent_environment.items() if key.casefold() in normalized}

    # A minimal PATH pointing only at the provisioned runtime: the execution
    # must not be able to resolve arbitrary host executables by name.
    environment["PATH"] = runtime_path
    environment["PYTHONIOENCODING"] = "utf-8"
    # No user site-packages, no PYTHONPATH inheritance, no .pth processing
    # beyond the provisioned runtime, and no bytecode written into the
    # (ephemeral, ACL-controlled) workspace.
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONHASHSEED"] = "0"

    # TEMP/TMP point inside the workspace so any library that writes a
    # temporary file writes it somewhere the workspace teardown reclaims,
    # never into a shared host temp directory.
    environment["TEMP"] = workspace_path
    environment["TMP"] = workspace_path
    environment["TMPDIR"] = workspace_path

    environment[ENV_WORKSPACE] = workspace_path
    environment[ENV_INPUT_FILE] = input_file

    if bridge_address is not None and execution_token is not None:
        environment[ENV_BRIDGE_ADDRESS] = bridge_address
        environment[ENV_EXECUTION_TOKEN] = execution_token

    return environment


def redact_environment(environment: dict[str, str]) -> dict[str, str]:
    """Return `environment` with every sensitive value replaced by a marker.

    Used anywhere an environment would otherwise reach a log line, an audit
    context, or a diagnostics payload.
    """
    return {key: ("[REDACTED]" if key in SENSITIVE_ENV_KEYS else value) for key, value in environment.items()}


__all__ = [
    "ENV_BRIDGE_ADDRESS",
    "ENV_EXECUTION_TOKEN",
    "ENV_INPUT_FILE",
    "ENV_WORKSPACE",
    "SENSITIVE_ENV_KEYS",
    "ExecutionPolicy",
    "build_environment",
    "current_platform_boundary",
    "redact_environment",
    "resolve_policy",
]
