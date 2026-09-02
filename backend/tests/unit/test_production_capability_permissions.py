"""
Unit tests proving that the RBAC `required_permissions` metadata now declared
on production capabilities (Security, Workflow, Connector, Recipe, Storage,
Document engines) is real and enforced through `Kernel.invoke_capability`,
not merely present on the descriptor.

This does not re-prove the generic enforcement mechanism itself (that is
`test_capability_dispatch.py`'s job, against synthetic capabilities) — it
proves the specific production capability -> permission mapping introduced
in this milestone is wired correctly and actually gates execution.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.document.engine import DocumentEngine
from kortex.engines.recipe.engine import RecipeEngine
from kortex.engines.registry.engine import _BOOTSTRAP_EXEMPT_CAPABILITIES
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.engine import WorkflowEngine

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32

# The exact production capability -> permission mapping implemented by this
# milestone. Kept as an explicit literal here (not re-derived from source) so
# a future accidental change to any engine's registration call is caught as
# a real test failure rather than silently drifting.
_EXPECTED_PERMISSIONS: dict[str, list[str] | None] = {
    "kortex.security.auth.authenticate": None,
    "kortex.security.access.authorize": ["security:read"],
    "kortex.security.secret.get": ["security:read"],
    "kortex.security.secret.put": ["security:secret:write"],  # M7.3
    "kortex.security.signature.verify": ["security:read"],
    "kortex.security.bootstrap.create_admin": None,
    "kortex.workflow.instance.start": ["workflow:start"],
    "kortex.workflow.instance.approve": ["workflow:approve"],
    "kortex.workflow.instance.cancel": ["workflow:cancel"],
    "kortex.workflow.state.get": ["workflow:read"],
    "kortex.connector.action.execute": ["connector:execute"],
    "kortex.connector.driver.register": ["connector:write"],
    "kortex.connector.driver.list": ["connector:read"],
    "kortex.connector.profile.get": ["connector:read"],
    "kortex.connector.profile.register": ["connector:write"],  # M7.3
    "kortex.connector.profile.list": ["connector:read"],  # M7.3
    "kortex.connector.profile.delete": ["connector:write"],  # M7.3
    "kortex.recipe.load": ["recipe:write"],
    "kortex.recipe.validate": ["recipe:read"],
    "kortex.recipe.compile": ["recipe:read"],
    "kortex.recipe.install": ["recipe:write"],
    "kortex.recipe.remove": ["recipe:write"],
    "kortex.recipe.upgrade": ["recipe:write"],
    "kortex.recipe.package": ["recipe:write"],
    "kortex.recipe.search": ["recipe:read"],
    "kortex.recipe.list": ["recipe:read"],
    "kortex.recipe.info": ["recipe:read"],
    "kortex.storage.data.session": ["storage:read"],
    "kortex.storage.file.store": ["storage:write"],
    "kortex.storage.object.put": ["storage:write"],
    "kortex.storage.cache.set": ["storage:write"],
    "kortex.document.operation.execute": ["document:execute"],
    "kortex.document.lifecycle.transition": ["document:write"],
    "kortex.document.template.bind": ["document:write"],
    "kortex.document.preview.generate": ["document:read"],
    "kortex.document.adapter.list": ["document:read"],
    "kortex.document.intelligence.analyze": ["document:read"],
    "kortex.document.recommendation.get": ["document:read"],
    "kortex.document.adapter.register": ["document:write"],
}


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-prodperm-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


async def _build_full_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "prodperm_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(WorkflowEngine())
    kernel.register_engine(ConnectorEngine())
    kernel.register_engine(RecipeEngine())
    kernel.register_engine(DocumentEngine())
    await kernel.boot()
    return kernel, storage_engine, security_engine


async def _seed_principal(
    data_store: Any,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("prodperm-test-credential")

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


async def _grant_role_permission(data_store: Any, role: str, permission: str) -> None:
    async def _action(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))

    await data_store.execute_in_transaction(_action)


async def _issue_token(security_engine: SecurityEngine, tenant_id: str, principal_id: str) -> TokenPayload:
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": "prodperm-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


class _Spy:
    def __init__(self) -> None:
        self.call_count = 0

    async def __call__(self, **kwargs: Any) -> str:
        self.call_count += 1
        return "handler-invoked"


# -- Matrix: every registered production capability carries the intended
#    required_permissions / requires_authentication metadata ----------------


@pytest.mark.asyncio
async def test_production_capability_permission_matrix(tmp_path: Path) -> None:
    kernel, _storage_engine, _security_engine = await _build_full_kernel(tmp_path)

    descriptors = {d.name: d for d in kernel.list_capabilities()}
    checked = 0
    for name, expected_permissions in _EXPECTED_PERMISSIONS.items():
        assert name in descriptors, f"expected capability '{name}' to be registered"
        descriptor = descriptors[name]
        assert descriptor.required_permissions == expected_permissions, (
            f"{name}: expected required_permissions={expected_permissions}, got {descriptor.required_permissions}"
        )
        checked += 1
    assert checked == len(_EXPECTED_PERMISSIONS)

    # -- E. Bootstrap exemption remains limited to the fixed allowlist -------
    for name, descriptor in descriptors.items():
        if name in _BOOTSTRAP_EXEMPT_CAPABILITIES:
            assert descriptor.requires_authentication is False
        else:
            assert descriptor.requires_authentication is True, (
                f"{name} must require authentication; only {sorted(_BOOTSTRAP_EXEMPT_CAPABILITIES)} may be exempt"
            )


# -- A. Authenticated + authorized (has workflow:start) succeeds ------------


@pytest.mark.asyncio
async def test_workflow_start_succeeds_with_required_permission(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _build_full_kernel(tmp_path)
    spy = _Spy()
    kernel._registry_engine.set_raw_handler_for_testing("kortex.workflow.instance.start", spy)

    tenant_id = _tenant(tmp_path, "-a")
    role = f"workflow-starter-{tenant_id}"
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    await _grant_role_permission(storage_engine.data, role, "workflow:start")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.workflow.instance.start",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert result == "handler-invoked"
    assert spy.call_count == 1


# -- B. Authenticated + unauthorized (missing workflow:start) is denied -----


@pytest.mark.asyncio
async def test_workflow_start_denied_without_required_permission(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _build_full_kernel(tmp_path)
    spy = _Spy()
    kernel._registry_engine.set_raw_handler_for_testing("kortex.workflow.instance.start", spy)

    tenant_id = _tenant(tmp_path, "-b")
    role = f"workflow-starter-{tenant_id}"
    # Principal has a role, but that role is never granted workflow:start.
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    await _grant_role_permission(storage_engine.data, role, "workflow:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.workflow.instance.start",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


# -- C. Unauthenticated invocation of a protected production capability -----


@pytest.mark.asyncio
async def test_workflow_start_denied_without_authentication(tmp_path: Path) -> None:
    kernel, _storage_engine, _security_engine = await _build_full_kernel(tmp_path)
    spy = _Spy()
    kernel._registry_engine.set_raw_handler_for_testing("kortex.workflow.instance.start", spy)

    request = CapabilityRequest(capability_name="kortex.workflow.instance.start", session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


# -- D. Correct RBAC permission but invalid tenant context is still denied --


@pytest.mark.asyncio
async def test_workflow_start_denied_on_tenant_mismatch_despite_valid_permission(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _build_full_kernel(tmp_path)
    spy = _Spy()
    kernel._registry_engine.set_raw_handler_for_testing("kortex.workflow.instance.start", spy)

    tenant_id = _tenant(tmp_path, "-c")
    role = f"workflow-starter-{tenant_id}"
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    await _grant_role_permission(storage_engine.data, role, "workflow:start")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    # RBAC permission is present and correct; ABAC tenant context is wrong.
    request = CapabilityRequest(
        capability_name="kortex.workflow.instance.start",
        session_token=token,
        context={"resource_tenant_id": _tenant(tmp_path, "-other")},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0

    # Same principal, missing resource_tenant_id entirely -> still denied
    # (M4's fail-closed missing-tenant rule, unaffected by this milestone).
    request_no_context = CapabilityRequest(
        capability_name="kortex.workflow.instance.start",
        session_token=token,
        context={},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request_no_context)
    assert spy.call_count == 0


# -- F. required_permissions is descriptor-authoritative for a real --------
#      production capability, not merely for a synthetic test one ----------


@pytest.mark.asyncio
async def test_storage_cache_set_permission_not_overridable_by_request(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _build_full_kernel(tmp_path)
    spy = _Spy()
    kernel._registry_engine.set_raw_handler_for_testing("kortex.storage.cache.set", spy)

    tenant_id = _tenant(tmp_path, "-f")
    # No roles/permissions granted at all.
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.storage.cache.set",
        session_token=token,
        parameters={"required_permissions": [], "key": "k", "value": "v"},
        context={"resource_tenant_id": tenant_id, "required_permissions": []},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0

    # Granting the real, descriptor-declared permission (storage:write) to a
    # second principal is what actually makes an otherwise-identical request
    # succeed -- proving the denial above was governed by the descriptor's
    # own required_permissions, not by anything request-supplied.
    cache_role = f"cache-writer-role-{tenant_id}"
    await _seed_principal(storage_engine.data, tenant_id, "principal-2", roles=[cache_role])
    await _grant_role_permission(storage_engine.data, cache_role, "storage:write")
    token2 = await _issue_token(security_engine, tenant_id, "principal-2")
    request2 = CapabilityRequest(
        capability_name="kortex.storage.cache.set",
        session_token=token2,
        parameters={"key": "k", "value": "v"},
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request2)
    assert result == "handler-invoked"
    assert spy.call_count == 1
