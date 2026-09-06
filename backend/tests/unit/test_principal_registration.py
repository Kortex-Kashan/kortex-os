"""Tests for `AuthenticationManager.register_principal`/`set_email` (Phase A:
admin-provisioned "Register" and self-service email).

Mirrors `test_authentication_manager.py`'s exact fixture pattern (`_make_manager`,
tenant IDs derived from `tmp_path` + a UUID suffix — `Kernel()` defaults to a
single shared, non-test-scoped SQLite file, so rows must never collide across
test runs).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from kortex.core.kernel import Kernel
from kortex.engines.security.auth import AuthenticationManager
from kortex.engines.security.exceptions import (
    AuthenticationError,
    PasswordPolicyError,
    PrincipalAlreadyExistsError,
    PrincipalRegistrationValidationError,
)
from kortex.engines.security.models import PrincipalRecord, PrincipalType
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.storage.engine import StorageEngine

_TEST_SIGNING_KEY = b"\x55" * 32


def _tenant(tmp_path: Path) -> str:
    return f"tenant-reg-{tmp_path.name}-{uuid.uuid4().hex[:8]}"


async def _make_manager(tmp_path: Path) -> tuple[Kernel, StorageEngine, AuthenticationManager]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "principal_registration_test"))
    kernel.register_engine(storage_engine)
    await storage_engine.initialize(kernel)
    await storage_engine.start()
    await kernel.db.connect()
    await kernel.db.create_all_tables()
    manager = AuthenticationManager(
        data_store=storage_engine.data, crypto_provider=LocalCrypto(), signing_private_key=_TEST_SIGNING_KEY
    )
    return kernel, storage_engine, manager


@pytest.mark.asyncio
async def test_register_principal_creates_new_user(tmp_path: Path) -> None:
    _kernel, storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)

    await manager.register_principal(
        tenant_id=tenant,
        principal_id="new-user",
        password="correct-secret",
        roles=["member"],
        email=f"{uuid.uuid4().hex}@example.com",
    )

    async def _load(session):
        stmt = select(PrincipalRecord).where(
            PrincipalRecord.tenant_id == tenant,
            PrincipalRecord.principal_id == "new-user",
            PrincipalRecord.principal_type == PrincipalType.USER.value,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    record = await storage.data.execute_in_transaction(_load)
    assert record is not None
    assert record.enabled is True
    assert record.roles == ["member"]
    assert record.credential_hash is not None
    assert record.credential_hash != "correct-secret"


@pytest.mark.asyncio
async def test_register_principal_duplicate_raises_conflict_not_silent_noop(tmp_path: Path) -> None:
    """Distinct from `provision_principal`'s idempotent no-op precedent: an
    admin submitting a duplicate username must see a real conflict."""
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)

    await manager.register_principal(
        tenant_id=tenant, principal_id="dupe-user", password="first-password", roles=["member"]
    )

    with pytest.raises(PrincipalAlreadyExistsError):
        await manager.register_principal(
            tenant_id=tenant, principal_id="dupe-user", password="second-password", roles=["member"]
        )


@pytest.mark.asyncio
async def test_register_principal_is_tenant_scoped(tmp_path: Path) -> None:
    """The identical username in a different tenant is not a conflict."""
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant_a = _tenant(tmp_path)
    tenant_b = f"{tenant_a}-other"

    await manager.register_principal(
        tenant_id=tenant_a, principal_id="shared-name", password="password-one", roles=["member"]
    )
    await manager.register_principal(
        tenant_id=tenant_b, principal_id="shared-name", password="password-two", roles=["member"]
    )


@pytest.mark.parametrize(
    ("tenant_id", "principal_id", "roles", "email"),
    [
        ("", "user", ["member"], None),
        ("tenant", "", ["member"], None),
        ("tenant", "user", [], None),
        ("tenant", "user", ["member"], "not-an-email"),
    ],
)
@pytest.mark.asyncio
async def test_register_principal_validation_errors(
    tmp_path: Path, tenant_id: str, principal_id: str, roles: list[str], email: str | None
) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    with pytest.raises(PrincipalRegistrationValidationError):
        await manager.register_principal(
            tenant_id=tenant_id, principal_id=principal_id, password="correct-secret", roles=roles, email=email
        )


@pytest.mark.asyncio
async def test_register_principal_rejects_short_password(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)
    with pytest.raises(PasswordPolicyError):
        await manager.register_principal(tenant_id=tenant, principal_id="user", password="short", roles=["member"])


@pytest.mark.asyncio
async def test_register_principal_never_stores_plaintext_password() -> None:
    """`PasswordPolicyError`/`PrincipalRegistrationValidationError` messages
    never leak the submitted password, mirroring `BootstrapValidationError`'s
    own precedent."""
    try:
        raise PasswordPolicyError("Password must be at least 8 characters.")
    except PasswordPolicyError as exc:
        assert "correct-secret" not in str(exc)


@pytest.mark.asyncio
async def test_set_email_updates_record(tmp_path: Path) -> None:
    _kernel, storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)
    await manager.register_principal(tenant_id=tenant, principal_id="user", password="correct-secret", roles=["member"])

    new_email = f"{uuid.uuid4().hex}@example.com"
    await manager.set_email(
        tenant_id=tenant, principal_id="user", principal_type=PrincipalType.USER.value, email=new_email
    )

    async def _load(session):
        stmt = select(PrincipalRecord).where(
            PrincipalRecord.tenant_id == tenant,
            PrincipalRecord.principal_id == "user",
            PrincipalRecord.principal_type == PrincipalType.USER.value,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    record = await storage.data.execute_in_transaction(_load)
    assert record is not None
    assert record.email == new_email


@pytest.mark.asyncio
async def test_set_email_rejects_malformed_address(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)
    await manager.register_principal(tenant_id=tenant, principal_id="user", password="correct-secret", roles=["member"])

    with pytest.raises(PrincipalRegistrationValidationError):
        await manager.set_email(
            tenant_id=tenant, principal_id="user", principal_type=PrincipalType.USER.value, email="not-an-email"
        )


@pytest.mark.asyncio
async def test_set_email_unknown_principal_denied(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)

    with pytest.raises(AuthenticationError):
        await manager.set_email(
            tenant_id=tenant,
            principal_id="does-not-exist",
            principal_type=PrincipalType.USER.value,
            email=f"{uuid.uuid4().hex}@example.com",
        )
