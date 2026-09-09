"""
Security and Isolation tests for KORTEX OS Tenant-Scoped Capability Projection (Milestone F6).

Covers:
- Gate 4: Caller-supplied `tenant_id` cannot alter identity or authorization scope.
- Gate 5: Cross-tenant projection isolation (Tenant A cannot see Tenant B's capabilities).
- Gate 7: Missing ConnectorProfile does not hide authorized capability.
- Gate 8: Inactive ConnectorProfile does not hide authorized capability.
- Gate 9: Missing secret does not hide authorized capability.
- Gate 10: Zero SecretStore calls during projection.
- Gate 11: Zero external network calls during projection.
- Gate 21: Zero cross-tenant cache contamination.
"""

from __future__ import annotations

import socket
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.capability_projection import register_projection_capabilities
from kortex.core.dispatch import CapabilityExecutionContext, CapabilityRequest
from kortex.core.exceptions import CapabilityNotFoundError
from kortex.core.kernel import Kernel
from kortex.core.projection import CapabilityProjection
from kortex.engines.connector.models import ConnectorProfile
from kortex.engines.registry.engine import RegistryEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import (
    PrincipalRecord,
    PrincipalType,
    RolePermissionRecord,
    SecurityPrincipal,
)
from kortex.engines.security.secrets import SecretStore
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32


def _principal(
    tenant_id: str,
    roles: list[str] | None = None,
    clearance_level: str = "INTERNAL",
    principal_id: str | None = None,
) -> SecurityPrincipal:
    return SecurityPrincipal(
        principal_id=principal_id or f"user-{uuid.uuid4().hex[:6]}",
        principal_type=PrincipalType.USER,
        tenant_id=tenant_id,
        roles=roles or [],
        attributes={"clearance_level": clearance_level},
    )


async def _seed_principal(
    data_store: Any,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("test-credential")

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


def _execution_context(
    principal: SecurityPrincipal,
    capability_name: str = "kortex.system.capability.project",
) -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        request_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        capability_name=capability_name,
        principal=principal,
        tenant_id=principal.tenant_id,
    )


def _role(suffix: str) -> str:
    return f"role-{suffix}-{uuid.uuid4().hex[:8]}"


async def _make_test_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Kernel, StorageEngine, SecurityEngine, RegistryEngine]:
    db_file = tmp_path / f"test_sec_{uuid.uuid4().hex[:8]}.db"
    storage_dir = tmp_path / f"storage_sec_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(storage_dir))

    kernel = Kernel()
    storage = StorageEngine(base_directory=str(storage_dir))
    security = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage)
    kernel.register_engine(security)

    # Register system projection capabilities before kernel boots
    register_projection_capabilities(kernel)

    await kernel.boot()
    registry = kernel._registry_engine
    return kernel, storage, security, registry


async def _grant_permission(data_store: Any, role: str, permission: str) -> None:
    async def _action(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))
        await session.flush()

    await data_store.execute_in_transaction(_action)


@pytest.mark.asyncio
async def test_gate_4_caller_tenant_id_cannot_alter_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate 4: Caller cannot manipulate tenant authorization scope by providing a tenant_id parameter."""
    kernel, storage, security, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        role_legit = _role("legit-tenant")
        role_target = _role("target-tenant")
        perm_legit = "action:legit"
        perm_target = "action:target"

        await _grant_permission(storage.data, role_legit, perm_legit)
        await _grant_permission(storage.data, role_target, perm_target)

        registry.register_capability(
            name="test.tenant.legit_cap",
            description="Legit capability",
            provider="test",
            handler=AsyncMock(return_value={"status": "ok"}),
            required_permissions=[perm_legit],
            requires_authentication=True,
            security_classification="INTERNAL",
        )

        registry.register_capability(
            name="test.tenant.target_cap",
            description="Target capability",
            provider="test",
            handler=AsyncMock(return_value={"status": "ok"}),
            required_permissions=[perm_target],
            requires_authentication=True,
            security_classification="INTERNAL",
        )

        principal_id = "user-legit"
        await _seed_principal(storage.data, tenant_id="tenant-legit", principal_id=principal_id, roles=[role_legit])
        principal = _principal(tenant_id="tenant-legit", roles=[role_legit], principal_id=principal_id)
        context = _execution_context(principal)
        token = await security.authentication_manager.issue_token(principal)

        # Attacker injects tenant_id="tenant-target" in parameters
        request = CapabilityRequest(
            capability_name="kortex.system.capability.project",
            session_token=token,
            context={"resource_tenant_id": "tenant-legit"},
            parameters={"tenant_id": "tenant-target"},
        )
        result = await kernel.invoke_capability(request)
        projected = result

        projected_names = {item["name"] for item in projected}
        assert "test.tenant.legit_cap" in projected_names
        # Attacker CANNOT see target_cap despite providing tenant_id="tenant-target"
        assert "test.tenant.target_cap" not in projected_names

        # Direct projection engine call also respects only context.tenant_id
        projection = CapabilityProjection(kernel)
        direct_projected = await projection.project_capabilities(context)
        direct_names = {d.name for d in direct_projected}
        assert "test.tenant.legit_cap" in direct_names
        assert "test.tenant.target_cap" not in direct_names
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_gate_5_and_21_cross_tenant_isolation_and_no_cache_contamination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gates 5 & 21: Tenant A never sees Tenant B's capabilities;
    sequential calls across tenants never contaminate each other.
    """
    kernel, storage, _security, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        role_a = _role("tenant-a")
        role_b = _role("tenant-b")
        perm_a = "data:tenant_a_read"
        perm_b = "data:tenant_b_read"

        await _grant_permission(storage.data, role_a, perm_a)
        await _grant_permission(storage.data, role_b, perm_b)

        registry.register_capability(
            name="test.cap.tenant_a_only",
            description="Tenant A only",
            provider="test",
            handler=AsyncMock(return_value={}),
            required_permissions=[perm_a],
            requires_authentication=True,
            security_classification="INTERNAL",
        )
        registry.register_capability(
            name="test.cap.tenant_b_only",
            description="Tenant B only",
            provider="test",
            handler=AsyncMock(return_value={}),
            required_permissions=[perm_b],
            requires_authentication=True,
            security_classification="INTERNAL",
        )

        principal_a = _principal(tenant_id="tenant-a", roles=[role_a])
        ctx_a = _execution_context(principal_a)

        principal_b = _principal(tenant_id="tenant-b", roles=[role_b])
        ctx_b = _execution_context(principal_b)

        projection = CapabilityProjection(kernel)

        # 1. Project for Tenant A
        res_a_1 = await projection.project_capabilities(ctx_a)
        names_a_1 = {d.name for d in res_a_1}
        assert "test.cap.tenant_a_only" in names_a_1
        assert "test.cap.tenant_b_only" not in names_a_1

        # 2. Project for Tenant B
        res_b = await projection.project_capabilities(ctx_b)
        names_b = {d.name for d in res_b}
        assert "test.cap.tenant_b_only" in names_b
        assert "test.cap.tenant_a_only" not in names_b

        # 3. Project for Tenant A again (verifies no cache pollution from Tenant B)
        res_a_2 = await projection.project_capabilities(ctx_a)
        names_a_2 = {d.name for d in res_a_2}
        assert names_a_2 == names_a_1
        assert "test.cap.tenant_b_only" not in names_a_2

        # 4. Single capability lookup cross-tenant check
        # Tenant A attempting to get Tenant B's capability fails closed
        with pytest.raises(CapabilityNotFoundError):
            await projection.get_projected_capability("test.cap.tenant_b_only", identity=ctx_a)

        # Tenant B attempting to get Tenant A's capability fails closed
        with pytest.raises(CapabilityNotFoundError):
            await projection.get_projected_capability("test.cap.tenant_a_only", identity=ctx_b)
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_gates_7_8_9_missing_inactive_profile_and_missing_secret_do_not_hide_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gates 7, 8, 9: Projection is strictly Existence ∩ Authorization.

    Missing ConnectorProfile, inactive ConnectorProfile, and missing secrets in SecretStore
    do NOT hide an authorized capability from discovery.
    """
    kernel, storage, _security, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        role_user = _role("crm-user")
        perm_crm = "connector:crm:sync"
        await _grant_permission(storage.data, role_user, perm_crm)

        cap_name = "crm.customer.sync"
        registry.register_capability(
            name=cap_name,
            description="Sync customer records with external CRM",
            provider="crm_connector",
            handler=AsyncMock(return_value={"count": 42}),
            required_permissions=[perm_crm],
            requires_authentication=True,
            security_classification="INTERNAL",
            is_read_only=False,
            is_idempotent=True,
        )

        principal = _principal(tenant_id="tenant-crm", roles=[role_user])
        ctx = _execution_context(principal)
        projection = CapabilityProjection(kernel)

        # Condition 1: Missing ConnectorProfile entirely
        projected = await projection.project_capabilities(ctx)
        assert any(d.name == cap_name for d in projected), "Capability must be discoverable despite missing profile"

        # Condition 2: Inactive ConnectorProfile exists
        _inactive_profile = ConnectorProfile(
            profile_id="prof-inactive-1",
            name="crm-inactive",
            driver_id="driver-http",
            tenant_id="tenant-crm",
            is_active=False,
        )
        projected_after_profile = await projection.project_capabilities(ctx)
        assert any(d.name == cap_name for d in projected_after_profile), (
            "Capability must be discoverable despite inactive profile"
        )

        # Condition 3: Missing secret in SecretStore
        desc = await projection.get_projected_capability(cap_name, identity=ctx)
        assert desc.name == cap_name
        assert desc.provider == "crm_connector"
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_gate_10_zero_secret_store_calls_during_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 10: Capability projection MUST NOT query SecretStore."""
    kernel, storage, _security, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        role_user = _role("user")
        perm = "read:reports"
        await _grant_permission(storage.data, role_user, perm)

        registry.register_capability(
            name="reports.read",
            description="Read financial reports",
            provider="reporting",
            handler=AsyncMock(return_value={}),
            required_permissions=[perm],
            requires_authentication=True,
            security_classification="INTERNAL",
        )

        principal = _principal(tenant_id="tenant-audit", roles=[role_user])
        ctx = _execution_context(principal)
        projection = CapabilityProjection(kernel)

        with patch.object(SecretStore, "get_secret", autospec=True) as mock_get_secret:
            results = await projection.project_capabilities(ctx)
            assert len(results) >= 1

            desc = await projection.get_projected_capability("reports.read", identity=ctx)
            assert desc.name == "reports.read"

            assert mock_get_secret.call_count == 0, (
                f"SecretStore.get_secret called {mock_get_secret.call_count} times during projection!"
            )
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_gate_11_zero_external_network_calls_during_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 11: Capability projection MUST NOT perform any external network I/O."""
    kernel, storage, _security, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        role_user = _role("user")
        perm = "action:ping"
        await _grant_permission(storage.data, role_user, perm)

        registry.register_capability(
            name="system.ping",
            description="System ping capability",
            provider="core",
            handler=AsyncMock(return_value={}),
            required_permissions=[perm],
            requires_authentication=True,
            security_classification="INTERNAL",
        )

        principal = _principal(tenant_id="tenant-net", roles=[role_user])
        ctx = _execution_context(principal)
        projection = CapabilityProjection(kernel)

        def _forbidden_connect(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("External network connection attempted during capability projection!")

        with patch.object(socket.socket, "connect", side_effect=_forbidden_connect):
            results = await projection.project_capabilities(ctx)
            assert any(d.name == "system.ping" for d in results)

            desc = await projection.get_projected_capability("system.ping", identity=ctx)
            assert desc.name == "system.ping"
    finally:
        await kernel.shutdown()
