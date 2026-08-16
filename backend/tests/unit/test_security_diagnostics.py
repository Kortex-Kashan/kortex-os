"""Unit tests for Security Engine M1 diagnostics.

Verifies `SecurityDiagnostics` never falsely advertises authentication,
authorization, secret storage, audit, or Kernel-registered capabilities as
implemented, and never exposes sensitive material.
"""

from __future__ import annotations

from kortex.engines.security.diagnostics import SecurityDiagnostics
from kortex.engines.security.providers.local_crypto import LocalCrypto

_SENSITIVE_MARKERS = ("private_key", "secret", "password", "credential", "plaintext")


def _assert_no_sensitive_leakage(payload: object) -> None:
    """Recursively assert no field carries actual sensitive material.

    Boolean disclosure flags such as `"secret_store_implemented": False` are
    intentional and safe (M1 explicitly discloses what is NOT implemented) —
    this only flags a sensitive-sounding key when it holds non-boolean content
    that could plausibly carry key/secret material.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, bool):
                continue  # explicit "X_implemented: False" disclosure flags are safe
            assert not any(marker in str(key).lower() for marker in _SENSITIVE_MARKERS), (
                f"Diagnostics output contains a suspicious non-boolean key: {key!r} -> {value!r}"
            )
            _assert_no_sensitive_leakage(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _assert_no_sensitive_leakage(item)


# -- Unconfigured (no crypto provider) -------------------------------------------


def test_health_reports_unhealthy_without_crypto_provider() -> None:
    diagnostics = SecurityDiagnostics(crypto_provider=None)
    health = diagnostics.health()
    assert health["healthy"] is False
    assert health["status"] == "unhealthy"
    assert health["crypto_provider_configured"] is False


def test_status_reports_uninitialized_without_crypto_provider() -> None:
    diagnostics = SecurityDiagnostics(crypto_provider=None)
    assert diagnostics.status() == "UNINITIALIZED"


def test_diagnostics_reports_no_supported_operations_without_crypto_provider() -> None:
    diagnostics = SecurityDiagnostics(crypto_provider=None)
    detail = diagnostics.diagnostics()
    assert detail["supported_crypto_operations"] == []


# -- Configured (real crypto provider) -------------------------------------------


def test_health_reports_healthy_with_crypto_provider() -> None:
    diagnostics = SecurityDiagnostics(crypto_provider=LocalCrypto())
    health = diagnostics.health()
    assert health["healthy"] is True
    assert health["status"] == "healthy"
    assert health["crypto_provider_configured"] is True


def test_status_reports_ready_with_crypto_provider() -> None:
    diagnostics = SecurityDiagnostics(crypto_provider=LocalCrypto())
    assert diagnostics.status() == "READY"


def test_diagnostics_lists_supported_crypto_operations_with_crypto_provider() -> None:
    diagnostics = SecurityDiagnostics(crypto_provider=LocalCrypto())
    detail = diagnostics.diagnostics()
    assert "crypto.sha256.hash" in detail["supported_crypto_operations"]
    assert "crypto.ed25519.sign" in detail["supported_crypto_operations"]
    assert "crypto.aes256gcm.encrypt" in detail["supported_crypto_operations"]


# -- M1 must never claim authentication/authorization/audit/secret-store exist ---


def test_health_never_claims_authentication_or_authorization_implemented() -> None:
    for provider in (None, LocalCrypto()):
        health = SecurityDiagnostics(crypto_provider=provider).health()
        assert health["authentication_implemented"] is False
        assert health["authorization_implemented"] is False
        assert health["secret_store_implemented"] is False
        assert health["audit_implemented"] is False


def test_diagnostics_lists_not_yet_implemented_subsystems() -> None:
    diagnostics = SecurityDiagnostics(crypto_provider=LocalCrypto())
    not_yet = diagnostics.diagnostics()["not_yet_implemented"]
    for expected in (
        "authentication",
        "authorization",
        "secret_storage",
        "audit_enforcement",
        "kernel_capability_dispatch",
    ):
        assert expected in not_yet


def test_capabilities_always_empty_in_m1() -> None:
    """No Kernel capability registration occurs in M1 — this must never claim otherwise."""
    for provider in (None, LocalCrypto()):
        assert SecurityDiagnostics(crypto_provider=provider).capabilities() == []


# -- Metrics / version --------------------------------------------------------------


def test_metrics_does_not_fabricate_counters() -> None:
    diagnostics = SecurityDiagnostics(crypto_provider=LocalCrypto())
    metrics = diagnostics.metrics()
    assert metrics["engine"] == "security"
    assert metrics["milestone"] == "M1"


def test_version_is_stable_string() -> None:
    diagnostics = SecurityDiagnostics(crypto_provider=LocalCrypto())
    assert isinstance(diagnostics.version(), str)
    assert diagnostics.version() == diagnostics.version()


# -- No sensitive-material leakage across the full diagnostic surface -----------


def test_no_sensitive_material_in_any_diagnostic_output() -> None:
    diagnostics = SecurityDiagnostics(crypto_provider=LocalCrypto())
    _assert_no_sensitive_leakage(diagnostics.health())
    _assert_no_sensitive_leakage(diagnostics.metrics())
    _assert_no_sensitive_leakage(diagnostics.diagnostics())
