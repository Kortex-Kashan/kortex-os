"""Tests for `AuthenticationManager.change_password`/`request_password_reset`/
`reset_password` (Phase A).

Mirrors `test_authentication_manager.py`'s exact fixture pattern.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from kortex.core.kernel import Kernel
from kortex.engines.security.auth import AuthenticationManager
from kortex.engines.security.exceptions import AuthenticationError, PasswordPolicyError, PasswordResetError
from kortex.engines.security.models import PasswordResetTokenRecord, PrincipalType
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.storage.engine import StorageEngine

_TEST_SIGNING_KEY = b"\x66" * 32


def _tenant(tmp_path: Path) -> str:
    return f"tenant-pw-{tmp_path.name}-{uuid.uuid4().hex[:8]}"


async def _make_manager(tmp_path: Path) -> tuple[Kernel, StorageEngine, AuthenticationManager]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "password_test"))
    kernel.register_engine(storage_engine)
    await storage_engine.initialize(kernel)
    await storage_engine.start()
    await kernel.db.connect()
    await kernel.db.create_all_tables()
    manager = AuthenticationManager(
        data_store=storage_engine.data, crypto_provider=LocalCrypto(), signing_private_key=_TEST_SIGNING_KEY
    )
    return kernel, storage_engine, manager


# -- change_password ----------------------------------------------------------


@pytest.mark.asyncio
async def test_change_password_succeeds_and_new_password_authenticates(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)
    await manager.register_principal(tenant_id=tenant, principal_id="user", password="old-password", roles=["member"])

    await manager.change_password(
        tenant_id=tenant,
        principal_id="user",
        principal_type=PrincipalType.USER.value,
        current_password="old-password",
        new_password="new-password-123",
    )

    principal = await manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant,
            "principal_id": "user",
            "password": "new-password-123",
        }
    )
    assert principal.principal_id == "user"

    with pytest.raises(AuthenticationError):
        await manager.authenticate(
            {"principal_type": "USER", "tenant_id": tenant, "principal_id": "user", "password": "old-password"}
        )


@pytest.mark.asyncio
async def test_change_password_wrong_current_password_denied_generic(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)
    await manager.register_principal(tenant_id=tenant, principal_id="user", password="old-password", roles=["member"])

    with pytest.raises(AuthenticationError) as excinfo:
        await manager.change_password(
            tenant_id=tenant,
            principal_id="user",
            principal_type=PrincipalType.USER.value,
            current_password="totally-wrong",
            new_password="new-password-123",
        )
    assert "old-password" not in str(excinfo.value)
    assert "totally-wrong" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_change_password_rejects_short_new_password(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)
    await manager.register_principal(tenant_id=tenant, principal_id="user", password="old-password", roles=["member"])

    with pytest.raises(PasswordPolicyError):
        await manager.change_password(
            tenant_id=tenant,
            principal_id="user",
            principal_type=PrincipalType.USER.value,
            current_password="old-password",
            new_password="short",
        )


@pytest.mark.asyncio
async def test_change_password_unknown_principal_denied_generic(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)

    with pytest.raises(AuthenticationError):
        await manager.change_password(
            tenant_id=tenant,
            principal_id="does-not-exist",
            principal_type=PrincipalType.USER.value,
            current_password="anything",
            new_password="new-password-123",
        )


# -- request_password_reset / reset_password ----------------------------------


@pytest.mark.asyncio
async def test_password_reset_full_round_trip(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)
    email = f"{uuid.uuid4().hex}@example.com"
    await manager.register_principal(
        tenant_id=tenant, principal_id="user", password="old-password", roles=["member"], email=email
    )

    token = await manager.request_password_reset(email)
    assert token is not None

    await manager.reset_password(token=token, new_password="brand-new-password")

    principal = await manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant, "principal_id": "user", "password": "brand-new-password"}
    )
    assert principal.principal_id == "user"

    with pytest.raises(AuthenticationError):
        await manager.authenticate(
            {"principal_type": "USER", "tenant_id": tenant, "principal_id": "user", "password": "old-password"}
        )


@pytest.mark.asyncio
async def test_request_password_reset_unknown_email_returns_none_no_exception(tmp_path: Path) -> None:
    """Enumeration resistance: an unmatched email is not an error — the
    caller (`SecurityEngine`) presents the identical generic response either
    way."""
    _kernel, _storage, manager = await _make_manager(tmp_path)

    result = await manager.request_password_reset(f"{uuid.uuid4().hex}@nowhere.example")
    assert result is None


@pytest.mark.asyncio
async def test_reset_password_rejects_unknown_token(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)

    with pytest.raises(PasswordResetError):
        await manager.reset_password(token="not-a-real-token", new_password="brand-new-password")


@pytest.mark.asyncio
async def test_reset_password_token_is_single_use(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)
    email = f"{uuid.uuid4().hex}@example.com"
    await manager.register_principal(
        tenant_id=tenant, principal_id="user", password="old-password", roles=["member"], email=email
    )
    token = await manager.request_password_reset(email)
    assert token is not None

    await manager.reset_password(token=token, new_password="first-new-password")

    with pytest.raises(PasswordResetError):
        await manager.reset_password(token=token, new_password="second-new-password")


@pytest.mark.asyncio
async def test_reset_password_expired_token_rejected(tmp_path: Path) -> None:
    _kernel, storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)
    email = f"{uuid.uuid4().hex}@example.com"
    await manager.register_principal(
        tenant_id=tenant, principal_id="user", password="old-password", roles=["member"], email=email
    )
    token = await manager.request_password_reset(email)
    assert token is not None

    # Force the just-issued token into the past, simulating expiry without
    # waiting on a real clock (mirrors `test_verify_token_expired_denied`'s
    # own technique in `test_authentication_manager.py`).
    async def _expire(session):
        stmt = select(PasswordResetTokenRecord).where(PasswordResetTokenRecord.tenant_id == tenant)
        record = (await session.execute(stmt)).scalar_one()
        record.expires_at_utc = datetime.now(UTC) - timedelta(minutes=1)

    await storage.data.execute_in_transaction(_expire)

    with pytest.raises(PasswordResetError):
        await manager.reset_password(token=token, new_password="brand-new-password")


@pytest.mark.asyncio
async def test_reset_password_rejects_short_new_password(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)
    email = f"{uuid.uuid4().hex}@example.com"
    await manager.register_principal(
        tenant_id=tenant, principal_id="user", password="old-password", roles=["member"], email=email
    )
    token = await manager.request_password_reset(email)
    assert token is not None

    with pytest.raises(PasswordPolicyError):
        await manager.reset_password(token=token, new_password="short")


@pytest.mark.asyncio
async def test_reset_password_disabled_principal_rejected(tmp_path: Path) -> None:
    _kernel, storage, manager = await _make_manager(tmp_path)
    tenant = _tenant(tmp_path)
    email = f"{uuid.uuid4().hex}@example.com"
    await manager.register_principal(
        tenant_id=tenant, principal_id="user", password="old-password", roles=["member"], email=email
    )
    token = await manager.request_password_reset(email)
    assert token is not None

    from kortex.engines.security.models import PrincipalRecord

    async def _disable(session):
        stmt = select(PrincipalRecord).where(
            PrincipalRecord.tenant_id == tenant, PrincipalRecord.principal_id == "user"
        )
        record = (await session.execute(stmt)).scalar_one()
        record.enabled = False

    await storage.data.execute_in_transaction(_disable)

    with pytest.raises(PasswordResetError):
        await manager.reset_password(token=token, new_password="brand-new-password")
