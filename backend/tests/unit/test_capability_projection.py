"""
Unit tests for KORTEX OS Tenant-Scoped Capability Projection (Milestone F6).

Covers:
- Gate 1: Authorized tenant receives authorized capabilities.
- Gate 2: Unauthorized capabilities are omitted from projection.
- Gate 3: Tenant identity is derived strictly from SecurityPrincipal / CapabilityExecutionContext.
- Gate 6: Global registry existence is respected.
- Gate 15: Read-only metadata is preserved.
- Gate 16: Idempotency metadata is preserved.
- Gate 17: Parameters schema is preserved verbatim.
- Gate 18: Returns schema is preserved verbatim.
- Gate 19: Global capability search remains unchanged and unaffected.
- Gate 22: Registry unregistration reflected dynamically on subsequent projection.
- Gate 23: Capability metadata changes reflected dynamically on subsequent projection.
- Gate 24: Permission changes reflected dynamically on subsequent projection.
- Authority Reuse Regression Test: proves F6 delegates to SecurityEngine rather than reimplementing RBAC/ABAC.
- Single capability lookup: fails closed with CapabilityNotFoundError on unauthorized/absent access.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.dispatch import CapabilityExecutionContext
from kortex.core.exceptions import CapabilityNotFoundError
from kortex.core.kernel import Kernel
from kortex.core.projection import CapabilityProjection
from kortex.engines.registry.engine import RegistryEngine
from kortex.engines.security.authorization import AuthorizationEngine
from kortex.engines.security.models import (
    ClassificationLevel,
    PrincipalType,
    RolePermissionRecord,
    SecurityPrincipal,
)
from kortex.engines.storage.engine import StorageEngine


def _principal(
    tenant_id: str = "tenant-a",
    roles: list[str] | None = None,
    clearance_level: str = "INTERNAL",
) -> SecurityPrincipal:
    return SecurityPrincipal(
        principal_id=f"user-{uuid.uuid4().hex[:6]}",
        principal_type=PrincipalType.USER,
        tenant_id=tenant_id,
        roles=roles or [],
        attributes={"clearance_level": clearance_level},
    )


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
) -> tuple[Kernel, StorageEngine, AuthorizationEngine, RegistryEngine]:
    db_file = tmp_path / f"test_{uuid.uuid4().hex[:8]}.db"
    storage_dir = tmp_path / f"storage_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(storage_dir))

    kernel = Kernel()
    storage = StorageEngine(base_directory=str(storage_dir))
    kernel.register_engine(storage)
    await storage.initialize(kernel)
    await storage.start()
    await kernel.db.connect()
    await kernel.db.create_all_tables()
    auth_engine = AuthorizationEngine(data_store=storage.data)
    registry = kernel._registry_engine
    return kernel, storage, auth_engine, registry


async def _grant_permission(data_store: Any, role: str, permission: str) -> None:
    async def _action(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))
        await session.flush()

    await data_store.execute_in_transaction(_action)


@pytest.mark.asyncio
async def test_gate_1_and_2_authorized_capabilities_included_unauthorized_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel, storage, auth_engine, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        registry.register_capability(
            name="kortex.finance.invoice.read",
            description="Read invoice data",
            provider="finance",
            required_permissions=["finance:read"],
            requires_authentication=True,
        )
        registry.register_capability(
            name="kortex.finance.invoice.delete",
            description="Delete invoice data",
            provider="finance",
            required_permissions=["finance:delete"],
            requires_authentication=True,
        )
        registry.register_capability(
            name="kortex.security.auth.authenticate",
            description="Authenticate caller credentials",
            provider="security",
            requires_authentication=False,
        )

        role_finance = _role("finance-reader")
        await _grant_permission(storage.data, role_finance, "finance:read")

        principal = _principal("tenant-alpha", roles=[role_finance])
        context = _execution_context(principal)

        class MockSecurityEngine:
            authorization_engine = auth_engine

        projection = CapabilityProjection(
            registry_engine=registry,
            security_engine=MockSecurityEngine(),
        )

        projected = await projection.project_capabilities(context)
        projected_names = {c.name for c in projected}

        # Authorized capability and unauthenticated capability must appear
        assert "kortex.finance.invoice.read" in projected_names
        assert "kortex.security.auth.authenticate" in projected_names
        # Unauthorized capability must be strictly omitted
        assert "kortex.finance.invoice.delete" not in projected_names
    finally:
        await kernel.db.disconnect()
        await storage.stop()


@pytest.mark.asyncio
async def test_gate_3_tenant_identity_derived_from_principal_and_execution_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel, storage, auth_engine, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        registry.register_capability(
            name="kortex.operations.asset.read",
            description="Read operations asset",
            provider="operations",
            required_permissions=["operations:read"],
            requires_authentication=True,
        )

        role_ops = _role("ops")
        await _grant_permission(storage.data, role_ops, "operations:read")

        # Principal for tenant-A
        principal_a = _principal("tenant-A", roles=[role_ops])
        context_a = _execution_context(principal_a)

        class MockSecurityEngine:
            authorization_engine = auth_engine

        projection = CapabilityProjection(
            registry_engine=registry,
            security_engine=MockSecurityEngine(),
        )

        # Works with execution_context
        projected_from_context = await projection.project_capabilities(context_a)
        assert any(c.name == "kortex.operations.asset.read" for c in projected_from_context)

        # Works with direct SecurityPrincipal
        projected_from_principal = await projection.project_capabilities(principal_a)
        assert any(c.name == "kortex.operations.asset.read" for c in projected_from_principal)
    finally:
        await kernel.db.disconnect()
        await storage.stop()


@pytest.mark.asyncio
async def test_gate_6_global_existence_respected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel, storage, auth_engine, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        role_super = _role("super")
        await _grant_permission(storage.data, role_super, "super:read")

        principal = _principal("tenant-a", roles=[role_super])
        context = _execution_context(principal)

        class MockSecurityEngine:
            authorization_engine = auth_engine

        projection = CapabilityProjection(
            registry_engine=registry,
            security_engine=MockSecurityEngine(),
        )

        projected = await projection.project_capabilities(context)
        assert not any(c.name == "kortex.phantom.nonexistent.capability" for c in projected)
    finally:
        await kernel.db.disconnect()
        await storage.stop()


@pytest.mark.asyncio
async def test_gate_15_to_18_metadata_schemas_and_flags_preserved_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel, storage, auth_engine, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        test_params = {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"],
        }
        test_returns = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
        }

        registry.register_capability(
            name="kortex.finance.invoice.get",
            description="Fetch invoice with exact schema",
            provider="finance",
            required_permissions=["finance:read"],
            requires_authentication=True,
            is_read_only=True,
            is_idempotent=True,
            parameters_schema=test_params,
            returns_schema=test_returns,
        )

        role = _role("finance")
        await _grant_permission(storage.data, role, "finance:read")
        principal = _principal("tenant-test", roles=[role])
        context = _execution_context(principal)

        class MockSecurityEngine:
            authorization_engine = auth_engine

        projection = CapabilityProjection(
            registry_engine=registry,
            security_engine=MockSecurityEngine(),
        )

        projected = await projection.project_capabilities(context)
        match = next(c for c in projected if c.name == "kortex.finance.invoice.get")

        # Gate 15: is_read_only preserved
        assert match.is_read_only is True
        # Gate 16: is_idempotent preserved
        assert match.is_idempotent is True
        # Gate 17: parameters_schema preserved
        assert match.parameters_schema == test_params
        # Gate 18: returns_schema preserved
        assert match.returns_schema == test_returns
        # Canonical name segments
        assert match.owner_domain == "finance"
        assert match.resource_type == "invoice"
        assert match.action == "get"
    finally:
        await kernel.db.disconnect()
        await storage.stop()


@pytest.mark.asyncio
async def test_gate_19_global_capability_search_remains_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel, storage, auth_engine, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        registry.register_capability(
            name="kortex.admin.system.purge",
            description="Administrative purge capability",
            provider="admin",
            required_permissions=["admin:purge"],
            requires_authentication=True,
        )

        # Global administrative search returns everything matching criteria, ignoring tenant permissions
        global_results = kernel.search_capabilities(keyword="purge")
        assert any(c.name == "kortex.admin.system.purge" for c in global_results)

        # In contrast, projection for unprivileged tenant omits it
        unprivileged = _principal("tenant-unprivileged", roles=[])
        context = _execution_context(unprivileged)

        class MockSecurityEngine:
            authorization_engine = auth_engine

        projection = CapabilityProjection(
            registry_engine=registry,
            security_engine=MockSecurityEngine(),
        )
        projected = await projection.project_capabilities(context, keyword="purge")
        assert projected == []
    finally:
        await kernel.db.disconnect()
        await storage.stop()


@pytest.mark.asyncio
async def test_gate_22_to_24_dynamic_reflection_of_unregistration_metadata_and_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel, storage, auth_engine, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        cap_name = "kortex.dynamic.item.process"
        role = _role("dynamic")

        registry.register_capability(
            name=cap_name,
            description="Dynamic test capability initial description",
            provider="dynamic",
            required_permissions=["dynamic:perm"],
            requires_authentication=True,
        )

        principal = _principal("tenant-dyn", roles=[role])
        context = _execution_context(principal)

        class MockSecurityEngine:
            authorization_engine = auth_engine

        projection = CapabilityProjection(
            registry_engine=registry,
            security_engine=MockSecurityEngine(),
        )

        # 1. Initially without permission granted, omitted
        proj_1 = await projection.project_capabilities(context)
        assert not any(c.name == cap_name for c in proj_1)

        # Gate 24: Permission changes reflected dynamically
        await _grant_permission(storage.data, role, "dynamic:perm")
        proj_2 = await projection.project_capabilities(context)
        assert any(c.name == cap_name for c in proj_2)

        # Gate 23: Metadata changes reflected dynamically
        # Directly update description in registry
        registry._capabilities[cap_name] = registry._capabilities[cap_name].model_copy(
            update={"description": "Updated dynamic description"}
        )
        proj_3 = await projection.project_capabilities(context)
        matched = next(c for c in proj_3 if c.name == cap_name)
        assert matched.description == "Updated dynamic description"

        # Gate 22: Unregistration reflected dynamically
        del registry._capabilities[cap_name]
        registry._stores.get("capability", {}).pop(cap_name, None)
        proj_4 = await projection.project_capabilities(context)
        assert not any(c.name == cap_name for c in proj_4)
    finally:
        await kernel.db.disconnect()
        await storage.stop()


@pytest.mark.asyncio
async def test_authority_reuse_regression_proves_delegation_to_security_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test proving F6 delegates to SecurityEngine's authorization authority
    rather than reimplementing an independent RBAC/ABAC engine."""
    kernel, storage, auth_engine, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        registry.register_capability(
            name="kortex.audit.log.read",
            description="Read audit log",
            provider="audit",
            required_permissions=["audit:read"],
            requires_authentication=True,
        )

        principal = _principal("tenant-audit", roles=[_role("auditor")])
        context = _execution_context(principal)

        # Spy on authorization_engine.authorize
        real_authorize = auth_engine.authorize
        spy_authorize = AsyncMock(side_effect=real_authorize)
        auth_engine.authorize = spy_authorize  # type: ignore[method-assign]

        class MockSecurityEngine:
            authorization_engine = auth_engine

        projection = CapabilityProjection(
            registry_engine=registry,
            security_engine=MockSecurityEngine(),
        )

        await projection.project_capabilities(context)

        # Verify that SecurityEngine's authorize method was called directly
        assert spy_authorize.called, "CapabilityProjection must invoke SecurityEngine authorization authority"
        call_args = spy_authorize.call_args[0]
        # First arg is principal, second is PermissionRequirement, third is context
        assert call_args[0].principal_id == principal.principal_id
        assert call_args[1].capability_name == "kortex.audit.log.read"
        assert call_args[2] == {"resource_tenant_id": principal.tenant_id}
    finally:
        await kernel.db.disconnect()
        await storage.stop()


@pytest.mark.asyncio
async def test_single_capability_lookup_get_projected_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves get_projected_capability fails closed with CapabilityNotFoundError on unauthorized
    or missing capability without disclosing sensitive metadata."""
    kernel, storage, auth_engine, registry = await _make_test_env(tmp_path, monkeypatch)
    try:
        registry.register_capability(
            name="kortex.secret.vault.open",
            description="Top secret vault",
            provider="vault",
            required_permissions=["vault:open"],
            requires_authentication=True,
            security_classification=ClassificationLevel.RESTRICTED.value,
            parameters_schema={"type": "object", "properties": {"combo": {"type": "string"}}},
        )

        role_vault = _role("vault-master")
        await _grant_permission(storage.data, role_vault, "vault:open")

        authorized_principal = _principal("tenant-v", roles=[role_vault], clearance_level="RESTRICTED")
        unauthorized_principal = _principal("tenant-v", roles=[], clearance_level="PUBLIC")

        class MockSecurityEngine:
            authorization_engine = auth_engine

        projection = CapabilityProjection(
            registry_engine=registry,
            security_engine=MockSecurityEngine(),
        )

        # 1. Authorized principal retrieves descriptor
        desc = await projection.get_projected_capability("kortex.secret.vault.open", authorized_principal)
        assert desc.name == "kortex.secret.vault.open"
        assert desc.parameters_schema == {"type": "object", "properties": {"combo": {"type": "string"}}}

        # 2. Unauthorized principal gets CapabilityNotFoundError (standard not-found, no metadata leaked)
        with pytest.raises(
            CapabilityNotFoundError,
            match=re.escape("Capability 'kortex.secret.vault.open' not found."),
        ):
            await projection.get_projected_capability("kortex.secret.vault.open", unauthorized_principal)

        # 3. Nonexistent capability raises CapabilityNotFoundError
        with pytest.raises(
            CapabilityNotFoundError,
            match=re.escape("Capability 'kortex.does.not.exist' not found."),
        ):
            await projection.get_projected_capability("kortex.does.not.exist", authorized_principal)
    finally:
        await kernel.db.disconnect()
        await storage.stop()
