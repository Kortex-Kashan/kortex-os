"""M7.3-W3 regression coverage: `kortex.security.secret.put` through the real
Kernel Capability Enforcement Boundary (`kortex.core.dispatch`).

Mirrors `test_security_engine.py`'s established bootstrap/seeding pattern
(real, unmodified Storage + Security Engines) and `test_connector_profile_
capabilities.py`'s tenant-spoofing-resistance style, proving:

    no token                              -> AuthenticationError  (401)
    valid token, missing permission       -> AuthorizationDeniedError (403)
    valid token, "security:secret:write"  -> put succeeds, round-trips via get_secret

    a caller-supplied tenant_id is never trusted once a principal exists
    (the one new-capability-specific hardening this milestone adds -- the
    pre-existing `kortex.security.secret.get` capability does not yet do
    this, a known, pre-existing, out-of-scope gap, see engine.py's own
    docstring on `put_secret_capability`)

    the plaintext value never appears in the capability response, the
    persisted audit entry, or the published SecuritySecretModifiedEvent
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_MASTER_KEY = b"\x77" * 32
_TEST_SIGNING_KEY = b"\x88" * 32
_CAPABILITY = "kortex.security.secret.put"
_PLAINTEXT = "correct-horse-battery-staple-super-secret-api-key"  # nosec - test fixture, not a real secret


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-secret-put-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "secret_put_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    await kernel.boot()
    return kernel, storage_engine, security_engine


async def _seed_principal(
    data_store: IDataStore, tenant_id: str, principal_id: str, roles: list[str] | None = None
) -> None:
    credential_hash = PasswordHasher().hash("secret-put-test-credential")

    async def _action(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type="USER",
                enabled=True,
                credential_hash=credential_hash,
                roles=roles or [],
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await data_store.execute_in_transaction(_action)


async def _grant_role_permission(data_store: IDataStore, role: str, permission: str) -> None:
    async def _action(session: AsyncSession) -> None:
        existing = await session.scalar(
            select(RolePermissionRecord).where(
                RolePermissionRecord.role == role,
                RolePermissionRecord.permission == permission,
            )
        )
        if existing is None:
            session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))

    await data_store.execute_in_transaction(_action)


async def _issue_token(security_engine: SecurityEngine, tenant_id: str, principal_id: str) -> TokenPayload:
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": "secret-put-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _authorized_token(
    storage_engine: StorageEngine,
    security_engine: SecurityEngine,
    tenant_id: str,
    principal_id: str,
) -> TokenPayload:
    role = f"role-{uuid.uuid4().hex[:8]}"
    await _seed_principal(storage_engine.data, tenant_id, principal_id, roles=[role])
    await _grant_role_permission(storage_engine.data, role, "security:secret:write")
    return await _issue_token(security_engine, tenant_id, principal_id)


@pytest.mark.asyncio
async def test_no_token_is_denied_authentication(tmp_path: Path) -> None:
    kernel, _storage, _security = await _boot_kernel(tmp_path)

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=None,
        parameters={"secret_handle": "h1", "plaintext": _PLAINTEXT},
    )
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_without_secret_write_permission_is_denied(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
        parameters={"secret_handle": "h1", "plaintext": _PLAINTEXT},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_having_only_secret_read_permission_is_not_sufficient(tmp_path: Path) -> None:
    """`security:read` (which already gates `secret.get`) must not also
    authorize `secret.put` -- write access to arbitrary tenant secrets is a
    deliberately distinct, more sensitive permission (see engine.py's
    `_CANONICAL_CAPABILITY_PERMISSIONS` comment)."""
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    role = f"role-{uuid.uuid4().hex[:8]}"
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    await _grant_role_permission(storage_engine.data, role, "security:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
        parameters={"secret_handle": "h1", "plaintext": _PLAINTEXT},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_put_then_get_round_trip_and_response_never_contains_plaintext(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    token = await _authorized_token(storage_engine, security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
        parameters={"secret_handle": "connector/api-key", "plaintext": _PLAINTEXT},
    )
    result = await kernel.invoke_capability(request)

    assert result["secret_handle"] == "connector/api-key"
    assert result["tenant_id"] == tenant_id
    assert _PLAINTEXT not in str(result)

    resolved = await security_engine.get_secret("connector/api-key", tenant_id)
    assert resolved == _PLAINTEXT


@pytest.mark.asyncio
async def test_ignores_caller_supplied_tenant_id_when_principal_present(tmp_path: Path) -> None:
    """A caller-supplied `tenant_id` parameter must never be trusted once a
    verified principal exists -- the principal's own tenant always wins,
    the same M6.3-1 precedence rule every other M7.3 capability follows."""
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    token = await _authorized_token(storage_engine, security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
        parameters={
            "secret_handle": "h1",
            "plaintext": _PLAINTEXT,
            "tenant_id": "someone-elses-tenant",
        },
    )
    result = await kernel.invoke_capability(request)

    assert result["tenant_id"] == tenant_id
    resolved = await security_engine.get_secret("h1", tenant_id)
    assert resolved == _PLAINTEXT


@pytest.mark.asyncio
async def test_publishes_secret_modified_event_without_plaintext(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    token = await _authorized_token(storage_engine, security_engine, tenant_id, "principal-1")

    received: list[object] = []

    def _event_handler(event: object) -> None:
        if event.topic.startswith("kortex.event.security.secret"):  # type: ignore[attr-defined]
            received.append(event)

    kernel.subscribe_event("*", _event_handler)

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
        parameters={"secret_handle": "h1", "plaintext": _PLAINTEXT},
    )
    await kernel.invoke_capability(request)

    assert len(received) == 1
    event = received[0]
    assert event.topic == "kortex.event.security.secret.modified"
    assert event.payload["secret_handle"] == "h1"
    assert event.payload["operation"] == "PUT"
    assert event.payload["tenant_id"] == tenant_id
    assert _PLAINTEXT not in str(event.payload)


@pytest.mark.asyncio
async def test_audit_entry_records_operation_without_plaintext(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    token = await _authorized_token(storage_engine, security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
        parameters={"secret_handle": "h1", "plaintext": _PLAINTEXT},
    )
    await kernel.invoke_capability(request)

    entries = await security_engine.audit_manager.get_audit_entries(tenant_id=tenant_id, limit=50)
    # Two entries legitimately share `action == _CAPABILITY`: the generic
    # RBAC decision `SecurityEngine.authorize()` itself records for every
    # capability call (`resource_id=None`, `context={"decision_code": ...}`)
    # and this capability's own explicit record (`resource_id=secret_handle`)
    # — filtering on `resource_id` isolates the one this test cares about.
    matching = [e for e in entries if e.action == _CAPABILITY and e.resource_id == "h1"]
    assert len(matching) == 1
    assert matching[0].resource_id == "h1"
    assert _PLAINTEXT not in str(matching[0].context)
