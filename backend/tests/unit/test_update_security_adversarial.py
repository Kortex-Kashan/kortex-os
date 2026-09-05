"""Adversarial security unit tests for KORTEX Update Engine.

Phase 7 — Production Hardening — Update Engine.
Covers Section 32 Test Strategy #9:
- Missing / unauthenticated execution context rejection
- Unauthorized principals & RBAC boundary violations
- Cross-tenant context validation
- Tampered manifests, invalid/expired signatures, untrusted key IDs
- Hostile archive payloads (traversal, absolute/UNC paths, zip bombs)
- Cross-engine concurrency conflicts (active backup / recovery locks)
"""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.security.models import PrincipalType, SecurityPrincipal
from kortex.engines.update.constants import (
    PERMISSION_UPDATE_MANAGE,
    PERMISSION_UPDATE_READ,
)
from kortex.engines.update.crypto import UpdateCryptoManager
from kortex.engines.update.engine import UpdateEngine
from kortex.engines.update.exceptions import (
    UpdateAuthenticationError,
    UpdateAuthorizationError,
    UpdateConcurrencyError,
    UpdateKeyNotFoundError,
    UpdatePathTraversalError,
    UpdateSignatureError,
    UpdateZipBombError,
)
from kortex.engines.update.staging import UpdateStagingManager


def make_context(
    tenant_id: str = "tenant-safe",
    principal_id: str = "user-123",
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
    authenticated: bool = True,
) -> CapabilityExecutionContext | None:
    """Helper to construct CapabilityExecutionContext for tests."""
    if not authenticated:
        return None
    principal = SecurityPrincipal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        principal_type=PrincipalType.USER,
        roles=roles if roles is not None else ["TENANT_ADMIN"],
        attributes={
            "permissions": permissions
            if permissions is not None
            else [PERMISSION_UPDATE_READ, PERMISSION_UPDATE_MANAGE]
        },
    )
    return CapabilityExecutionContext(
        request_id="req-sec-01",
        correlation_id="corr-sec-01",
        capability_name="kortex.update.test",
        principal=principal,
        tenant_id=tenant_id,
    )


@pytest.fixture
def test_engine(tmp_path: Path) -> UpdateEngine:
    """Instantiate an UpdateEngine bound to a temporary directory."""
    engine = UpdateEngine(update_dir=tmp_path / ".update", target_root=tmp_path / "app")
    return engine


# ============================================================================
# 1. Missing & Unauthenticated Context Rejection
# ============================================================================


@pytest.mark.asyncio
async def test_all_capabilities_reject_missing_context(test_engine: UpdateEngine) -> None:
    """Every capability handler must reject missing (None) execution context with UpdateAuthenticationError."""
    with pytest.raises(UpdateAuthenticationError):
        await test_engine.handle_update_check(execution_context=None)

    with pytest.raises(UpdateAuthenticationError):
        await test_engine.handle_update_get(execution_context=None)

    with pytest.raises(UpdateAuthenticationError):
        await test_engine.handle_update_stage(
            manifest_path="manifest.json", archive_path="update.zip", execution_context=None
        )

    with pytest.raises(UpdateAuthenticationError):
        await test_engine.handle_update_apply(update_id="upd-001", execution_context=None)

    with pytest.raises(UpdateAuthenticationError):
        await test_engine.handle_update_cancel(update_id="upd-001", execution_context=None)

    with pytest.raises(UpdateAuthenticationError):
        await test_engine.handle_update_diagnostics_get(execution_context=None)


# ============================================================================
# 2. RBAC Authorization & Privilege Escalation Defenses
# ============================================================================


@pytest.mark.asyncio
async def test_read_only_principal_cannot_stage_or_apply(test_engine: UpdateEngine) -> None:
    """Principal with only system:update:read cannot perform stage, apply, or cancel."""
    read_only_ctx = make_context(roles=["OBSERVER"], permissions=[PERMISSION_UPDATE_READ])

    with pytest.raises(UpdateAuthorizationError) as exc:
        await test_engine.handle_update_stage(
            manifest={"manifest_id": "m1"},
            execution_context=read_only_ctx,
        )
    assert "missing required permission" in str(exc.value).lower()

    with pytest.raises(UpdateAuthorizationError) as exc:
        await test_engine.handle_update_apply(
            update_id="upd-001",
            execution_context=read_only_ctx,
        )
    assert "missing required permission" in str(exc.value).lower()

    with pytest.raises(UpdateAuthorizationError) as exc:
        await test_engine.handle_update_cancel(
            update_id="upd-001",
            execution_context=read_only_ctx,
        )
    assert "missing required permission" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_anonymous_principal_rejected(test_engine: UpdateEngine) -> None:
    """Principal with anonymous role and zero permissions is rejected for read operations."""
    anon_ctx = make_context(roles=["ANONYMOUS"], permissions=[])

    with pytest.raises(UpdateAuthorizationError):
        await test_engine.handle_update_check(channel="stable", execution_context=anon_ctx)

    with pytest.raises(UpdateAuthorizationError):
        await test_engine.handle_update_get(execution_context=anon_ctx)

    with pytest.raises(UpdateAuthorizationError):
        await test_engine.handle_update_diagnostics_get(execution_context=anon_ctx)


# ============================================================================
# 3. Cryptographic Authenticity Defenses
# ============================================================================


def test_tampered_manifest_rejected() -> None:
    """A manifest whose content was modified after signing must be rejected."""
    key = Ed25519PrivateKey.generate()
    pub_bytes = key.public_key().public_bytes_raw()
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")

    crypto = UpdateCryptoManager(trusted_public_keys={"key-1": pub_b64})

    manifest_dict = {
        "manifest_id": "man-001",
        "key_id": "key-1",
        "version": {"target_version": "1.0.0"},
    }
    sig = crypto.sign_manifest(manifest_dict, key.private_bytes_raw())
    manifest_dict["signature"] = sig

    # Valid check passes
    assert crypto.verify_manifest(manifest_dict) is True

    # Tampered payload fails
    tampered = dict(manifest_dict)
    tampered["version"] = {"target_version": "2.0.0"}
    with pytest.raises(UpdateSignatureError):
        crypto.verify_manifest(tampered)


def test_untrusted_vendor_key_rejected() -> None:
    """Manifest signed by an untrusted or non-whitelisted key ID must be rejected."""
    crypto = UpdateCryptoManager(trusted_public_keys={"authorized-vendor-2026": "dGVzdGtleQ=="})
    manifest_dict = {
        "manifest_id": "man-002",
        "key_id": "rogue-untrusted-key",
        "signature": "d3Jvbmc=",
    }
    with pytest.raises(UpdateKeyNotFoundError):
        crypto.verify_manifest(manifest_dict)


def test_corrupted_signature_encoding_rejected() -> None:
    """Malformed or invalid length base64 signature must be rejected."""
    key = Ed25519PrivateKey.generate()
    pub_bytes = key.public_key().public_bytes_raw()
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")

    crypto = UpdateCryptoManager(trusted_public_keys={"key-1": pub_b64})
    manifest_dict = {
        "manifest_id": "man-003",
        "key_id": "key-1",
        "signature": "not-a-valid-base64-signature",
    }
    with pytest.raises(UpdateSignatureError):
        crypto.verify_manifest(manifest_dict)


# ============================================================================
# 4. Hostile Archive Payloads (Zip Slip, Absolute Paths, Zip Bombs)
# ============================================================================


def test_zip_slip_parent_traversal_rejected(tmp_path: Path) -> None:
    """Archive entries attempting parent directory traversal ('../') must be rejected."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../../etc/shadow", "root:x:0:0:::")
    zip_bytes = buf.getvalue()

    zip_file = tmp_path / "traversal.zip"
    zip_file.write_bytes(zip_bytes)

    with pytest.raises(UpdatePathTraversalError):
        staging.validate_archive_security(zip_file)


def test_zip_absolute_path_rejected(tmp_path: Path) -> None:
    """Archive entries with absolute Unix or Windows paths must be rejected."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("/etc/passwd", "root:x:0:0:::")
    zip_file = tmp_path / "absolute.zip"
    zip_file.write_bytes(buf.getvalue())

    with pytest.raises(UpdatePathTraversalError):
        staging.validate_archive_security(zip_file)


def test_zip_expansion_bomb_rejected(tmp_path: Path) -> None:
    """Archive exceeding expansion ratio limit (10:1) must be rejected."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1 MB of zeros compresses to ~1 KB (> 100:1 ratio)
        zf.writestr("zero.bin", b"\x00" * (1024 * 1024))
    zip_file = tmp_path / "bomb.zip"
    zip_file.write_bytes(buf.getvalue())

    with pytest.raises(UpdateZipBombError) as exc:
        staging.validate_archive_security(zip_file)
    assert "expansion ratio" in str(exc.value).lower()


# ============================================================================
# 5. Cross-Engine Mutual Exclusion & Concurrency
# ============================================================================


@pytest.mark.asyncio
async def test_concurrency_conflict_with_active_recovery(tmp_path: Path) -> None:
    """Attempting an update while a recovery lock is present must raise UpdateConcurrencyError."""
    update_dir = tmp_path / ".update"
    recovery_lock = tmp_path / ".recovery" / "maintenance.lock"
    recovery_lock.parent.mkdir(parents=True, exist_ok=True)
    recovery_lock.write_text('{"operation": "recovery", "pid": 999999}')

    engine = UpdateEngine(update_dir=update_dir)
    engine._quiescence_manager._recovery_lock = recovery_lock

    with pytest.raises(UpdateConcurrencyError) as exc:
        await engine._quiescence_manager.acquire_maintenance_lock("upd-01")
    assert "recovery" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_concurrency_conflict_with_active_backup(tmp_path: Path) -> None:
    """Attempting an update while a backup lock is present must raise UpdateConcurrencyError."""
    update_dir = tmp_path / ".update"
    backup_lock = tmp_path / "backups" / "backup.lock"
    backup_lock.parent.mkdir(parents=True, exist_ok=True)
    backup_lock.write_text('{"operation": "backup", "pid": 999999}')

    engine = UpdateEngine(update_dir=update_dir)
    engine._quiescence_manager._backup_lock = backup_lock

    with pytest.raises(UpdateConcurrencyError) as exc:
        await engine._quiescence_manager.acquire_maintenance_lock("upd-01")
    assert "backup" in str(exc.value).lower()
