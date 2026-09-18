"""Unit tests for the Python Execution Gateway's *policy* layer.

These tests check what KORTEX decides, not what the operating system enforces.
That split is deliberate and is maintained throughout this milestone's test
evidence: a passing test in this file proves the Gateway asked for the right
containment, and proves nothing at all about whether the containment held. The
OS-level enforcement tests live in
`tests/integration/test_python_execution_boundary.py` and are marked so their
environment dependence is visible.

No test here creates a workspace, spawns a process, or touches the filesystem.
"""

from __future__ import annotations

import platform

import pytest

from kortex.engines.python_exec.exceptions import UnsupportedTrustLevelError
from kortex.engines.python_exec.models import (
    PythonActionVersion,
    PythonBoundaryKind,
    PythonExecutionLimits,
    PythonNetworkPolicy,
    PythonTrustLevel,
)
from kortex.engines.python_exec.policy import (
    ENV_BRIDGE_ADDRESS,
    ENV_EXECUTION_TOKEN,
    SENSITIVE_ENV_KEYS,
    build_environment,
    redact_environment,
    resolve_policy,
)

_IS_WINDOWS = platform.system() == "Windows"


def _version(**overrides: object) -> PythonActionVersion:
    defaults: dict[str, object] = {
        "action_id": "demo-action",
        "tenant_id": "tenant-a",
        "version": 1,
        "source_code": "def main(payload):\n    return payload\n",
        "source_sha256": "0" * 64,
        "trust_level": PythonTrustLevel.TRUSTED,
    }
    defaults.update(overrides)
    return PythonActionVersion(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Trust model
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _IS_WINDOWS, reason="Windows-only trust-model rule.")
def test_windows_untrusted_python_is_rejected_before_any_execution() -> None:
    """Windows Untrusted Python is an MVP non-goal and must fail at policy time.

    Asserting the rejection happens in `resolve_policy` -- not in the boundary
    -- is the point: the Windows boundary must be unreachable with an untrusted
    action, so no workspace is ever created and no process is ever spawned for
    one.
    """
    with pytest.raises(UnsupportedTrustLevelError) as excinfo:
        resolve_policy(_version(trust_level=PythonTrustLevel.UNTRUSTED))

    assert "not supported on Windows" in str(excinfo.value)
    assert excinfo.value.details["action_id"] == "demo-action"


@pytest.mark.skipif(not _IS_WINDOWS, reason="Boundary selection is platform-specific.")
def test_windows_trusted_python_selects_the_windows_boundary() -> None:
    policy = resolve_policy(_version(trust_level=PythonTrustLevel.TRUSTED))
    assert policy.boundary is PythonBoundaryKind.WINDOWS_RESTRICTED_TOKEN_JOB


def test_untrusted_python_never_receives_a_capability_bridge() -> None:
    """An untrusted action gets no bridge even if its metadata asks for one.

    `allowed_capabilities` is cleared *and* `capability_bridge_enabled` is
    False, so an edit that later re-populated the allowlist still would not
    open a bridge.
    """
    if _IS_WINDOWS:
        pytest.skip("Windows rejects untrusted Python outright; covered above.")

    policy = resolve_policy(
        _version(trust_level=PythonTrustLevel.UNTRUSTED, allowed_capabilities=("kortex.knowledge.query.search",))
    )
    assert policy.capability_bridge_enabled is False
    assert policy.allowed_capabilities == ()
    assert policy.permits_capability("kortex.knowledge.query.search") is False


def test_untrusted_python_network_declaration_is_ignored() -> None:
    """An untrusted author cannot grant itself network access."""
    if _IS_WINDOWS:
        pytest.skip("Windows rejects untrusted Python outright; covered above.")

    policy = resolve_policy(
        _version(
            trust_level=PythonTrustLevel.UNTRUSTED,
            network_policy=PythonNetworkPolicy.ALLOW_LIST,
            network_allow_list=("api.example.com",),
        )
    )
    assert policy.network_policy is PythonNetworkPolicy.DENY
    assert policy.network_allow_list == ()


def test_trusted_python_without_declared_capabilities_gets_no_bridge() -> None:
    """The bridge opens only for an action that actually declares capabilities.

    A trusted action that needs no KORTEX access must not have an IPC endpoint
    created for it -- an unused endpoint is still an endpoint.
    """
    policy = resolve_policy(_version(trust_level=PythonTrustLevel.TRUSTED, allowed_capabilities=()))
    assert policy.capability_bridge_enabled is False


def test_trusted_python_capability_allowlist_is_exact_match_only() -> None:
    policy = resolve_policy(
        _version(
            trust_level=PythonTrustLevel.TRUSTED,
            allowed_capabilities=("kortex.knowledge.query.search",),
        )
    )
    assert policy.permits_capability("kortex.knowledge.query.search") is True
    # No prefix widening, no namespace widening, no case folding.
    assert policy.permits_capability("kortex.knowledge.query") is False
    assert policy.permits_capability("kortex.knowledge.query.search.extra") is False
    assert policy.permits_capability("KORTEX.KNOWLEDGE.QUERY.SEARCH") is False
    assert policy.permits_capability("kortex.security.secret.get") is False


def test_capability_allowlist_rejects_wildcards_at_construction() -> None:
    """A pattern is not an allowlist entry. Rejected when the version is built."""
    with pytest.raises(ValueError, match="Invalid capability allowlist entry"):
        _version(allowed_capabilities=("kortex.security.*",))

    with pytest.raises(ValueError, match="Invalid capability allowlist entry"):
        _version(allowed_capabilities=("os.system",))


def test_neither_trust_level_permits_unrestricted_subprocesses() -> None:
    policy = resolve_policy(_version(trust_level=PythonTrustLevel.TRUSTED))
    assert policy.allow_subprocesses is False


# ---------------------------------------------------------------------------
# Environment sanitization
# ---------------------------------------------------------------------------


def test_environment_is_built_from_an_allowlist_not_by_pruning_secrets() -> None:
    """A secret nobody anticipated must be absent by construction.

    The parent environment here carries exactly the kinds of values KORTEX's
    own process holds -- a database URL, a secret-store passphrase, connector
    and MCP credentials, an agent private key. None may survive into the
    execution's environment, and the test deliberately includes a variable
    (`TOTALLY_NEW_SECRET_2027`) that no blocklist could know about.
    """
    parent = {
        "SystemRoot": r"C:\Windows",
        "windir": r"C:\Windows",
        "LOCALAPPDATA": r"C:\Users\test\AppData\Local",
        "LANG": "en_US.UTF-8",
        "KORTEX_DATABASE_URL": "postgresql://kortex:hunter2@db/kortex",
        "KORTEX_MASTER_KEY": "master-key-material",
        "GITHUB_TOKEN": "ghp_connector_credential",
        "MCP_API_KEY": "mcp-credential",
        "AGENT_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----",
        "AWS_SECRET_ACCESS_KEY": "aws-secret",
        "TOTALLY_NEW_SECRET_2027": "a secret no blocklist anticipated",
    }

    environment = build_environment(
        parent_environment=parent,
        workspace_path="/ws",
        runtime_path="/runtime",
        input_file="/ws/input.json",
        bridge_address=None,
        execution_token=None,
    )

    leaked = {
        "KORTEX_DATABASE_URL",
        "KORTEX_MASTER_KEY",
        "GITHUB_TOKEN",
        "MCP_API_KEY",
        "AGENT_PRIVATE_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "TOTALLY_NEW_SECRET_2027",
    }
    assert leaked.isdisjoint(environment)
    # And no secret *value* survives under a different name.
    assert "hunter2" not in "".join(environment.values())
    assert "master-key-material" not in "".join(environment.values())


def test_environment_allowlist_matching_is_case_insensitive() -> None:
    """The base environment allowlist matches keys case-insensitively.

    `os.environ` upper-cases every key on Windows, so a case-sensitive
    allowlist silently matched nothing for `SystemRoot`/`windir` during
    implementation -- an allowlist that quietly matches nothing looks
    identical to a working one, so this pins the behaviour. The matching is
    not Windows-specific, so it is asserted on POSIX too against that
    platform's own allowlist.
    """
    # The allowlist itself is platform-specific (`_WINDOWS_BASE_ENV_KEYS` vs
    # `_POSIX_BASE_ENV_KEYS`), so the fixture has to be too -- asserting the
    # Windows keys on POSIX would only prove that `SystemRoot` is correctly
    # absent there, not that matching is case-insensitive. Both branches pin
    # the same invariant against their own platform's allowlist: a key whose
    # case differs from the allowlist entry is still matched, and the parent's
    # own spelling is what survives.
    if _IS_WINDOWS:
        parent_environment = {"SYSTEMROOT": r"C:\Windows", "WINDIR": r"C:\Windows"}
    else:
        parent_environment = {"lang": "en_US.UTF-8", "lc_all": "en_US.UTF-8"}

    environment = build_environment(
        parent_environment=parent_environment,
        workspace_path="/ws",
        runtime_path="/runtime",
        input_file="/ws/input.json",
        bridge_address=None,
        execution_token=None,
    )

    if _IS_WINDOWS:
        assert environment.get("SYSTEMROOT") == r"C:\Windows"
        assert environment.get("WINDIR") == r"C:\Windows"
    else:
        assert environment.get("lang") == "en_US.UTF-8"
        assert environment.get("lc_all") == "en_US.UTF-8"


def test_path_points_only_at_the_provisioned_runtime() -> None:
    """The execution must not resolve arbitrary host executables by name."""
    environment = build_environment(
        parent_environment={"PATH": r"C:\Windows\System32;C:\tools;C:\Users\me\bin"},
        workspace_path="/ws",
        runtime_path="/kortex/runtime",
        input_file="/ws/input.json",
        bridge_address=None,
        execution_token=None,
    )
    assert environment["PATH"] == "/kortex/runtime"


def test_temporary_directories_are_redirected_into_the_workspace() -> None:
    environment = build_environment(
        parent_environment={},
        workspace_path="/ws/exec-abc",
        runtime_path="/runtime",
        input_file="/ws/exec-abc/input.json",
        bridge_address=None,
        execution_token=None,
    )
    assert environment["TEMP"] == "/ws/exec-abc"
    assert environment["TMP"] == "/ws/exec-abc"
    assert environment["TMPDIR"] == "/ws/exec-abc"


def test_bridge_variables_appear_only_when_both_are_supplied() -> None:
    """A half-configured bridge must never reach the execution's environment."""
    without = build_environment(
        parent_environment={},
        workspace_path="/ws",
        runtime_path="/runtime",
        input_file="/ws/input.json",
        bridge_address=r"\\.\pipe\kortex-test",
        execution_token=None,
    )
    assert ENV_BRIDGE_ADDRESS not in without
    assert ENV_EXECUTION_TOKEN not in without

    with_both = build_environment(
        parent_environment={},
        workspace_path="/ws",
        runtime_path="/runtime",
        input_file="/ws/input.json",
        bridge_address=r"\\.\pipe\kortex-test",
        execution_token="secret-execution-token",
    )
    assert with_both[ENV_BRIDGE_ADDRESS] == r"\\.\pipe\kortex-test"
    assert with_both[ENV_EXECUTION_TOKEN] == "secret-execution-token"


def test_redaction_hides_the_execution_token_from_diagnostics() -> None:
    """Anywhere an environment reaches a log or audit context, the token is gone."""
    environment = build_environment(
        parent_environment={},
        workspace_path="/ws",
        runtime_path="/runtime",
        input_file="/ws/input.json",
        bridge_address=r"\\.\pipe\kortex-test",
        execution_token="secret-execution-token",
    )
    redacted = redact_environment(environment)

    assert redacted[ENV_EXECUTION_TOKEN] == "[REDACTED]"
    assert "secret-execution-token" not in "".join(redacted.values())
    # The bridge address is not itself a credential and stays legible.
    assert redacted[ENV_BRIDGE_ADDRESS] == r"\\.\pipe\kortex-test"
    assert ENV_EXECUTION_TOKEN in SENSITIVE_ENV_KEYS


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_unset_limits_stay_unset_rather_than_being_invented() -> None:
    """`None` means "not configured" and must not become a silent default.

    A boundary applies a limit only when one is configured, so a limit that
    appears in the enforced set is always a real one.
    """
    limits = PythonExecutionLimits(memory_bytes=None, cpu_seconds=None, max_processes=None)
    policy = resolve_policy(_version(limits=limits))

    assert policy.limits.memory_bytes is None
    assert policy.limits.cpu_seconds is None
    assert policy.limits.max_processes is None
    # The termination budget is never optional.
    assert policy.limits.timeout_seconds > 0


def test_timeout_is_always_bounded() -> None:
    with pytest.raises(ValueError, match="less than or equal to 3600"):
        PythonExecutionLimits(timeout_seconds=99999)
    with pytest.raises(ValueError, match="greater than 0"):
        PythonExecutionLimits(timeout_seconds=0)
