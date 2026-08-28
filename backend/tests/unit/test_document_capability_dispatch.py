"""Slice 4.7 regression coverage: `kortex.document.adapter.list` and
`kortex.document.template.list` through the real Kernel Capability
Enforcement Boundary (`kortex.core.dispatch`).

Mirrors `test_connector_capability_dispatch.py` / `test_workflow_capability_dispatch.py`
/ `test_marketplace_capability_dispatch.py` / `test_ai_capability_dispatch.py`'s
established bootstrap/seeding pattern (real, unmodified Storage + Security
Engines; no mocks on the security decision path) but drives it against the
real, production `DocumentEngine` — proving the three states Slice 4.7
requires end to end:

    no token                          -> AuthenticationError  (401)
    valid token, missing permission   -> AuthorizationDeniedError (403)
    valid token, "document:read"      -> real data (200)

Unlike Connector/Workflow/Marketplace's genuinely empty starting
registries, `DocumentEngine.initialize()` auto-discovers real in-package
reference adapters and `TemplateLibrary` ships pre-seeded with real
standard templates — so the "authorized success" case here asserts on
real, non-empty data rather than an empty list, which is the actual
production behavior for this engine.
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
from kortex.engines.document.engine import DocumentEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_MASTER_KEY = b"\xbb" * 32
_TEST_SIGNING_KEY = b"\xcc" * 32
_TEST_ROLE = "document-dispatch-test-role"


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-document-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "document_dispatch_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(DocumentEngine())
    return kernel, storage_engine, security_engine


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    await kernel.boot()
    return kernel, storage_engine, security_engine


async def _seed_principal(
    data_store: IDataStore,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("document-dispatch-test-credential")

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
            "password": "document-dispatch-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.mark.parametrize("capability_name", ["kortex.document.adapter.list", "kortex.document.template.list"])
@pytest.mark.asyncio
async def test_no_token_is_denied_authentication(tmp_path: Path, capability_name: str) -> None:
    kernel, _storage, _security = await _boot_kernel(tmp_path)

    request = CapabilityRequest(capability_name=capability_name, session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.parametrize("capability_name", ["kortex.document.adapter.list", "kortex.document.template.list"])
@pytest.mark.asyncio
async def test_authenticated_without_document_read_permission_is_denied_authorization(
    tmp_path: Path, capability_name: str
) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    # Principal has no roles at all -> RBAC denies for lack of any granted permission.
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=capability_name,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.parametrize("capability_name", ["kortex.document.adapter.list", "kortex.document.template.list"])
@pytest.mark.asyncio
async def test_authenticated_with_document_read_permission_returns_real_data(
    tmp_path: Path, capability_name: str
) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "document:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=capability_name,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    # Real, pre-seeded data (reference adapters / standard templates) --
    # never an empty list for this engine, unlike Connector/Workflow/Marketplace.
    assert len(result) > 0
