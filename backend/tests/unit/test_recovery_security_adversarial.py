"""Adversarial and security tests for Recovery Engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.recovery.crypto import RecoveryCryptoManager
from kortex.engines.recovery.engine import RecoveryEngine
from kortex.engines.recovery.exceptions import (
    RecoveryAuthenticationError,
    RecoveryAuthorizationError,
    RecoverySecurityError,
)
from kortex.engines.recovery.models import RecoveryConfig
from kortex.engines.security.models import PrincipalType, SecurityPrincipal


def make_context(
    tenant_id: str = "primary",
    principal_id: str = "admin",
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
) -> CapabilityExecutionContext:
    principal = SecurityPrincipal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        principal_type=PrincipalType.USER,
        roles=roles if roles is not None else ["TENANT_ADMIN"],
        attributes={"permissions": permissions or []},
    )
    return CapabilityExecutionContext(
        request_id="req-123",
        correlation_id="corr-123",
        capability_name="kortex.recovery.test",
        principal=principal,
        tenant_id=tenant_id,
    )


@pytest.mark.asyncio
async def test_missing_execution_context_fail_closed(tmp_path: Path) -> None:
    """CRITICAL SECURITY: Any capability invoked without execution context MUST fail closed."""
    engine = RecoveryEngine(
        config=RecoveryConfig(staging_directory=str(tmp_path / "staging")),
        crypto_manager=RecoveryCryptoManager(key=b"\x01" * 32),
    )
    await engine.initialize()

    with pytest.raises(RecoveryAuthenticationError, match=r"Missing trusted execution context"):
        await engine.handle_recovery_create(backup_id="bck-123", execution_context=None)

    with pytest.raises(RecoveryAuthenticationError, match=r"Missing trusted execution context"):
        await engine.handle_recovery_list(execution_context=None)

    with pytest.raises(RecoveryAuthenticationError, match=r"Missing trusted execution context"):
        await engine.handle_recovery_get(recovery_id="rec-123", execution_context=None)

    with pytest.raises(RecoveryAuthenticationError, match=r"Missing trusted execution context"):
        await engine.handle_recovery_verify(backup_id="bck-123", execution_context=None)

    with pytest.raises(RecoveryAuthenticationError, match=r"Missing trusted execution context"):
        await engine.handle_recovery_delete(recovery_id="rec-123", execution_context=None)


@pytest.mark.asyncio
async def test_unauthorized_principal_rejected(tmp_path: Path) -> None:
    """Verify principal without recovery permissions is rejected with RecoveryAuthorizationError."""
    engine = RecoveryEngine(
        config=RecoveryConfig(staging_directory=str(tmp_path / "staging")),
        crypto_manager=RecoveryCryptoManager(key=b"\x02" * 32),
    )
    await engine.initialize()

    # Context with no permissions and viewer role
    unauthorized_ctx = make_context(
        tenant_id="primary-tenant",
        principal_id="user-nobody",
        roles=["viewer"],
        permissions=["some:other:permission"],
    )

    with pytest.raises(RecoveryAuthorizationError, match=r"Unauthorized principal"):
        await engine.handle_recovery_create(backup_id="bck-123", execution_context=unauthorized_ctx)


@pytest.mark.asyncio
async def test_tenant_spoofing_prevented(tmp_path: Path) -> None:
    """CRITICAL SECURITY: Caller-supplied tenant_id cannot override execution context tenant."""
    engine = RecoveryEngine(
        config=RecoveryConfig(staging_directory=str(tmp_path / "staging")),
        crypto_manager=RecoveryCryptoManager(key=b"\x03" * 32),
    )
    await engine.initialize()

    trusted_ctx = make_context(
        tenant_id="tenant-alpha",
        principal_id="admin-alpha",
    )

    effective_tenant = engine._resolve_authoritative_tenant(trusted_ctx, caller_tenant="tenant-beta")
    assert effective_tenant == "tenant-alpha"


@pytest.mark.asyncio
async def test_caller_migration_bypass_rejected(tmp_path: Path) -> None:
    """Verify caller cannot supply arbitrary migration override parameters."""
    engine = RecoveryEngine(
        config=RecoveryConfig(staging_directory=str(tmp_path / "staging")),
        crypto_manager=RecoveryCryptoManager(key=b"\x04" * 32),
    )
    await engine.initialize()

    ctx = make_context(tenant_id="primary", principal_id="admin")

    with pytest.raises(RecoverySecurityError, match=r"Caller migration bypass prohibited"):
        await engine.handle_recovery_create(
            backup_id="bck-123",
            execution_context=ctx,
            allow_forward_migration=True,
        )


@pytest.mark.asyncio
async def test_adversarial_path_traversal_in_identifiers(tmp_path: Path) -> None:
    """Verify malicious path traversal payloads in recovery_id are rejected."""
    engine = RecoveryEngine(
        config=RecoveryConfig(staging_directory=str(tmp_path / "staging")),
        crypto_manager=RecoveryCryptoManager(key=b"\x05" * 32),
    )
    await engine.initialize()

    ctx = make_context(tenant_id="primary", principal_id="admin")

    malicious_ids = [
        "../../etc/shadow",
        "..\\..\\Windows\\System32\\calc.exe",
        "/root/backup",
        "nested/../../escape",
    ]

    for bad_id in malicious_ids:
        with pytest.raises(RecoverySecurityError):
            await engine.handle_recovery_get(recovery_id=bad_id, execution_context=ctx)


def test_secret_leakage_prevention(tmp_path: Path) -> None:
    """Verify diagnostics and string representations never contain raw cryptographic keys."""
    secret_key = b"SUPER_SECRET_RECOVERY_KEY_BYTES_32"
    crypto = RecoveryCryptoManager(key=secret_key, key_id="sec-check")
    engine = RecoveryEngine(
        config=RecoveryConfig(staging_directory=str(tmp_path / "staging")),
        crypto_manager=crypto,
    )

    diag_json = engine.get_diagnostics().model_dump_json()
    assert "SUPER_SECRET" not in diag_json
    assert secret_key.hex() not in diag_json

    health_json = json.dumps(engine.health())
    assert "SUPER_SECRET" not in health_json
    assert secret_key.hex() not in health_json
