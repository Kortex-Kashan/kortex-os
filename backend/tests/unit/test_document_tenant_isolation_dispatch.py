"""M7.4-W1 adversarial regression coverage: proves the tenant-derivation fix
to `DocumentEngine.execute_profile`/`.transition_lifecycle`/`.list_profiles`
through the real Kernel Capability Enforcement Boundary (`kortex.core.dispatch`).

Prior to M7.4, `execute_profile` trusted `OperationRequest.binding_context.
tenant_id` as caller-supplied data with no cross-check against the
authenticated caller's real tenant, and `transition_lifecycle` accepted no
tenant identifier at all (always operating against
`DocumentLifecycleManager`'s own `tenant_id="default"` fallback) — a caller
holding the coarse `document:execute`/`document:write` permission could
reach or mutate any tenant's operation profile or document lineage by
supplying that tenant's id (or simply by relying on every caller silently
sharing the "default" partition). Mirrors
`test_connector_tenant_isolation_dispatch.py`'s exact M6.3-1 pattern and
`test_connector_profile_capabilities.py`'s M7.3 pattern:

    tenant B cannot execute an operation profile it does not own, even
    while spoofing tenant A's id in the request payload

    tenant B cannot transition a document version it does not own, given
    only its document_id/version_id

    kortex.document.profile.list (M7.4-W2) returns only the caller's own
    tenant's profiles

    same-tenant access continues to work correctly after the fix
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
from kortex.engines.document.exceptions import DocumentLifecycleError, DocumentProfileNotFoundError
from kortex.engines.document.models import DocumentLifecycleState, DocumentOperationProfile
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_MASTER_KEY = b"\x99" * 32
_TEST_SIGNING_KEY = b"\xaa" * 32


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-doc-isolation-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, DocumentEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "document_isolation_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    document_engine = DocumentEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(document_engine)
    return kernel, storage_engine, security_engine, document_engine


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, DocumentEngine]:
    kernel, storage_engine, security_engine, document_engine = _build_kernel(tmp_path)
    await kernel.boot()
    return kernel, storage_engine, security_engine, document_engine


async def _seed_principal(
    data_store: IDataStore, tenant_id: str, principal_id: str, roles: list[str] | None = None
) -> None:
    credential_hash = PasswordHasher().hash("document-isolation-test-credential")

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
            "password": "document-isolation-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _authorized_token(
    storage_engine: StorageEngine,
    security_engine: SecurityEngine,
    tenant_id: str,
    principal_id: str,
    permission: str,
) -> TokenPayload:
    role = f"role-{uuid.uuid4().hex[:8]}"
    await _seed_principal(storage_engine.data, tenant_id, principal_id, roles=[role])
    await _grant_role_permission(storage_engine.data, role, permission)
    return await _issue_token(security_engine, tenant_id, principal_id)


def _profile(profile_id: str) -> DocumentOperationProfile:
    """A minimal profile with no adapter_pipeline -- `execute_profile` completes it
    trivially (no real adapter execution needed), which is exactly what a
    tenant-isolation test needs and nothing more."""
    return DocumentOperationProfile(
        id=profile_id,
        name="Isolation Test Profile",
        namespace="kortex.test",
        version="1.0.0",
        description="Minimal profile for tenant-isolation testing.",
        business_operation="test.isolation",
    )


# -- W1: execute_profile cross-tenant -----------------------------------------


@pytest.mark.asyncio
async def test_execute_profile_succeeds_for_the_owning_tenant(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, document_engine = await _boot_kernel(tmp_path)
    tenant_a = _tenant(tmp_path, "-a")
    await document_engine.profile_manager.register_profile(_profile("iso-profile-a"), tenant_id=tenant_a)

    token_a = await _authorized_token(storage_engine, security_engine, tenant_a, "principal-a", "document:execute")
    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.document.operation.execute",
            session_token=token_a,
            context={"resource_tenant_id": tenant_a},
            parameters={
                "profile_id": "iso-profile-a",
                "request": {
                    "request_id": "req-1",
                    "profile_id": "iso-profile-a",
                    "binding_context": {"context_id": "ctx-1"},
                },
            },
        )
    )
    assert result.status == "COMPLETED"


@pytest.mark.asyncio
async def test_execute_profile_cross_tenant_attempt_fails_closed_even_with_spoofed_tenant_id(
    tmp_path: Path,
) -> None:
    """Tenant B, holding valid `document:execute`, cannot execute tenant A's
    profile by guessing its profile_id and claiming tenant A's id in the
    request payload -- `principal.tenant_id` (tenant B, real) is
    authoritative over `binding_context.tenant_id` (tenant A, spoofed)."""
    kernel, storage_engine, security_engine, document_engine = await _boot_kernel(tmp_path)
    tenant_a = _tenant(tmp_path, "-a")
    tenant_b = _tenant(tmp_path, "-b")
    await document_engine.profile_manager.register_profile(_profile("shared-guessable-profile-id"), tenant_id=tenant_a)

    token_b = await _authorized_token(storage_engine, security_engine, tenant_b, "principal-b", "document:execute")
    with pytest.raises(DocumentProfileNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.document.operation.execute",
                session_token=token_b,
                context={"resource_tenant_id": tenant_b},
                parameters={
                    "profile_id": "shared-guessable-profile-id",
                    "request": {
                        "request_id": "req-2",
                        "profile_id": "shared-guessable-profile-id",
                        # An attacker-controlled request payload claiming tenant A's id.
                        "binding_context": {"context_id": "ctx-attack", "tenant_id": tenant_a},
                    },
                },
            )
        )


# -- W1: transition_lifecycle cross-tenant ------------------------------------


@pytest.mark.asyncio
async def test_transition_lifecycle_succeeds_for_the_owning_tenant(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, document_engine = await _boot_kernel(tmp_path)
    tenant_a = _tenant(tmp_path, "-a")
    version = await document_engine.lifecycle_manager.create_version(
        title="Isolation Test Document", author_id="principal-a", tenant_id=tenant_a
    )

    token_a = await _authorized_token(storage_engine, security_engine, tenant_a, "principal-a", "document:write")
    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.document.lifecycle.transition",
            session_token=token_a,
            context={"resource_tenant_id": tenant_a},
            parameters={
                "document_id": version.document_id,
                "version_id": version.version_id,
                "target_state": DocumentLifecycleState.REVIEW.value,
            },
        )
    )
    assert result.lifecycle_state == DocumentLifecycleState.REVIEW


@pytest.mark.asyncio
async def test_transition_lifecycle_cross_tenant_attempt_fails_closed(tmp_path: Path) -> None:
    """Tenant B, holding valid `document:write`, cannot transition tenant A's
    document version given only its document_id/version_id -- before the
    M7.4-W1 fix this handler accepted no tenant identifier at all and always
    operated against the manager's own tenant="default" fallback."""
    kernel, storage_engine, security_engine, document_engine = await _boot_kernel(tmp_path)
    tenant_a = _tenant(tmp_path, "-a")
    tenant_b = _tenant(tmp_path, "-b")
    version = await document_engine.lifecycle_manager.create_version(
        title="Isolation Test Document", author_id="principal-a", tenant_id=tenant_a
    )

    token_b = await _authorized_token(storage_engine, security_engine, tenant_b, "principal-b", "document:write")
    with pytest.raises(DocumentLifecycleError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.document.lifecycle.transition",
                session_token=token_b,
                context={"resource_tenant_id": tenant_b},
                parameters={
                    "document_id": version.document_id,
                    "version_id": version.version_id,
                    "target_state": DocumentLifecycleState.REVIEW.value,
                },
            )
        )


# -- W2: profile.list ----------------------------------------------------------


@pytest.mark.asyncio
async def test_list_profiles_no_token_is_denied_authentication(tmp_path: Path) -> None:
    kernel, _storage, _security, _document = await _boot_kernel(tmp_path)

    request = CapabilityRequest(capability_name="kortex.document.profile.list", session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_list_profiles_without_document_read_permission_is_denied(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _document = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.document.profile.list",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_list_profiles_returns_only_the_callers_tenant(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, document_engine = await _boot_kernel(tmp_path)
    tenant_a = _tenant(tmp_path, "-a")
    tenant_b = _tenant(tmp_path, "-b")
    await document_engine.profile_manager.register_profile(_profile("profile-a"), tenant_id=tenant_a)
    await document_engine.profile_manager.register_profile(_profile("profile-b"), tenant_id=tenant_b)

    token_a = await _authorized_token(storage_engine, security_engine, tenant_a, "reader-a", "document:read")
    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.document.profile.list",
            session_token=token_a,
            context={"resource_tenant_id": tenant_a},
        )
    )

    assert [p.id for p in result] == ["profile-a"]
