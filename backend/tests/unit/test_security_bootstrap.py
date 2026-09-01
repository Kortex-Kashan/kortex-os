"""Unit tests for Milestone M7.1's first-run tenant/administrator bootstrap.

Covers `AuthenticationManager.is_bootstrap_required`/`bootstrap_first_admin`
(the transactional core), `SecurityEngine.bootstrap_create_admin` (the
capability handler, including its dynamic RBAC-permission gathering), the
`kortex.security.bootstrap.create_admin` capability's real registration and
dispatch-level reachability, `Kernel.health_check()`'s `bootstrap_required`
flag, and the fail-closed/concurrency-safety guarantees the M7.1 master
prompt requires.

Every test isolates BOTH `KORTEX_STORAGE_DIR` and `KORTEX_DATABASE_URL` to a
fresh `tmp_path`-scoped SQLite file. This is deliberate and necessary, not
boilerplate: `DatabaseEngineManager` falls back to one fixed, shared,
real `%APPDATA%/KORTEX/kortex_local.db` file (`kortex/core/db.py`'s
`_default_sqlite_url`) whenever `KORTEX_DATABASE_URL` is unset — confirmed
during this milestone to already be several megabytes of accumulated test
data with no existing test file resetting it. `test_kernel_bootstrap.py`'s
own tests get away with this because they never assert a global count; this
file's tests fundamentally must (`is_bootstrap_required` is defined as
"zero principals anywhere"), so genuine per-test database isolation is not
optional here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kortex.api.kernel_bootstrap import build_and_boot_kernel
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.registry.engine import _BOOTSTRAP_EXEMPT_CAPABILITIES
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import (
    AuthenticationError,
    BootstrapClosedError,
    BootstrapValidationError,
)
from kortex.engines.security.models import PermissionRequirement
from kortex.engines.workflow.engine import WorkflowEngine

_BOOTSTRAP_CAPABILITY = "kortex.security.bootstrap.create_admin"
_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32


@pytest.fixture(autouse=True)
def _isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(tmp_path / "bootstrap_storage"))
    monkeypatch.setenv(
        "KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'bootstrap_test.db').as_posix()}"
    )


async def _minimal_security_kernel(tmp_path: Path) -> tuple[Kernel, SecurityEngine]:
    """A bare Kernel with only Security registered — for tests that don't
    need the dynamic-permission-gathering behavior to involve a second
    engine's capabilities."""
    from kortex.engines.storage.engine import StorageEngine

    kernel = Kernel()
    kernel.register_engine(StorageEngine(base_directory=str(tmp_path / "minimal_storage")))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(security_engine)
    await kernel.boot()
    return kernel, security_engine


# =============================================================================
# A. AuthenticationManager.is_bootstrap_required / bootstrap_first_admin
# =============================================================================


@pytest.mark.asyncio
async def test_bootstrap_required_on_fresh_database(tmp_path: Path) -> None:
    kernel, security_engine = await _minimal_security_kernel(tmp_path)
    try:
        assert await security_engine.authentication_manager.is_bootstrap_required() is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_bootstrap_creates_authenticatable_admin_and_closes_bootstrap(tmp_path: Path) -> None:
    kernel, security_engine = await _minimal_security_kernel(tmp_path)
    try:
        await security_engine.authentication_manager.bootstrap_first_admin(
            tenant_id="acme",
            principal_id="owner",
            password="correct horse battery staple",
            roles=["admin"],
            permissions=["security:read"],
        )

        assert await security_engine.authentication_manager.is_bootstrap_required() is False

        principal = await security_engine.authentication_manager.authenticate(
            {
                "principal_type": "USER",
                "tenant_id": "acme",
                "principal_id": "owner",
                "password": "correct horse battery staple",
            }
        )
        assert principal.principal_id == "owner"
        assert principal.tenant_id == "acme"
        assert principal.roles == ["admin"]
        assert principal.attributes.get("clearance_level") == "RESTRICTED"
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_bootstrap_grants_the_supplied_permissions_to_the_role(tmp_path: Path) -> None:
    kernel, security_engine = await _minimal_security_kernel(tmp_path)
    try:
        await security_engine.authentication_manager.bootstrap_first_admin(
            tenant_id="acme",
            principal_id="owner",
            password="correct horse battery staple",
            roles=["admin"],
            permissions=["security:read", "workflow:start"],
        )
        principal = await security_engine.authentication_manager.authenticate(
            {
                "principal_type": "USER",
                "tenant_id": "acme",
                "principal_id": "owner",
                "password": "correct horse battery staple",
            }
        )
        decision = await security_engine.authorization_engine.evaluate_rbac(
            principal,
            PermissionRequirement(capability_name="test.capability", required_permissions=["workflow:start"]),
        )
        assert decision.is_allowed is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_second_bootstrap_attempt_is_rejected(tmp_path: Path) -> None:
    kernel, security_engine = await _minimal_security_kernel(tmp_path)
    try:
        await security_engine.authentication_manager.bootstrap_first_admin(
            tenant_id="acme", principal_id="owner", password="correct horse battery staple",
            roles=["admin"], permissions=[],
        )
        with pytest.raises(BootstrapClosedError):
            await security_engine.authentication_manager.bootstrap_first_admin(
                tenant_id="other-corp", principal_id="second-admin", password="another-strong-password",
                roles=["admin"], permissions=[],
            )
        # The rejected second attempt must not have created anything.
        with pytest.raises(AuthenticationError):
            await security_engine.authentication_manager.authenticate(
                {
                    "principal_type": "USER",
                    "tenant_id": "other-corp",
                    "principal_id": "second-admin",
                    "password": "another-strong-password",
                }
            )
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_concurrent_bootstrap_attempts_only_one_wins(tmp_path: Path) -> None:
    """Two genuinely concurrent bootstrap calls with different identities —
    proving the fixed-sentinel mutex (not just the sequential count check)
    is what prevents a second admin from ever being created."""
    kernel, security_engine = await _minimal_security_kernel(tmp_path)
    try:
        results = await asyncio.gather(
            security_engine.authentication_manager.bootstrap_first_admin(
                tenant_id="tenant-a", principal_id="admin-a", password="password-number-one",
                roles=["admin"], permissions=[],
            ),
            security_engine.authentication_manager.bootstrap_first_admin(
                tenant_id="tenant-b", principal_id="admin-b", password="password-number-two",
                roles=["admin"], permissions=[],
            ),
            return_exceptions=True,
        )
        successes = [r for r in results if r is None]
        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(successes) == 1, f"expected exactly one winner, got {results}"
        assert len(failures) == 1
        assert await security_engine.authentication_manager.is_bootstrap_required() is False
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tenant_id,principal_id,password",
    [
        ("", "owner", "correct horse battery staple"),
        ("acme", "", "correct horse battery staple"),
        ("acme", "owner", "short"),
        ("acme", "owner", ""),
    ],
)
async def test_bootstrap_rejects_invalid_input_without_creating_anything(
    tmp_path: Path, tenant_id: str, principal_id: str, password: str
) -> None:
    kernel, security_engine = await _minimal_security_kernel(tmp_path)
    try:
        with pytest.raises(BootstrapValidationError):
            await security_engine.authentication_manager.bootstrap_first_admin(
                tenant_id=tenant_id, principal_id=principal_id, password=password,
                roles=["admin"], permissions=[],
            )
        # Validation failure must never close bootstrap.
        assert await security_engine.authentication_manager.is_bootstrap_required() is True
    finally:
        await kernel.shutdown()


# =============================================================================
# B. SecurityEngine.bootstrap_create_admin — dynamic permission gathering
# =============================================================================


@pytest.mark.asyncio
async def test_bootstrap_create_admin_grants_every_currently_registered_permission(tmp_path: Path) -> None:
    """The bootstrap-created admin must be able to exercise a capability
    from a *different* engine (Workflow) with zero manual RBAC setup —
    proving `_list_capabilities`-driven gathering actually reaches other
    engines' registrations, not just Security's own four."""
    kernel = Kernel()
    from kortex.engines.storage.engine import StorageEngine

    kernel.register_engine(StorageEngine(base_directory=str(tmp_path / "storage")))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(security_engine)
    kernel.register_engine(WorkflowEngine())
    await kernel.boot()
    try:
        result = await security_engine.bootstrap_create_admin(
            tenant_id="acme", principal_id="owner", password="correct horse battery staple"
        )
        assert result == {"created": True, "tenant_id": "acme", "principal_id": "owner"}

        request = CapabilityRequest(
            capability_name="kortex.security.auth.authenticate",
            parameters={
                "credentials": {
                    "principal_type": "USER",
                    "tenant_id": "acme",
                    "principal_id": "owner",
                    "password": "correct horse battery staple",
                }
            },
        )
        principal = await kernel.invoke_capability(request)
        token = await security_engine.authentication_manager.issue_token(principal)

        list_request = CapabilityRequest(
            capability_name="kortex.workflow.definition.list",
            session_token=token,
            context={"resource_tenant_id": "acme"},
        )
        # Must not raise AuthorizationDeniedError — the bootstrap admin was
        # granted `workflow:read` dynamically, without any manual RolePermissionRecord setup.
        outcome = await kernel.invoke_capability(list_request)
        assert outcome == []
    finally:
        await kernel.shutdown()


# =============================================================================
# C. Capability registration / dispatch-level reachability
# =============================================================================


def test_bootstrap_capability_is_in_the_exempt_allowlist() -> None:
    assert _BOOTSTRAP_CAPABILITY in _BOOTSTRAP_EXEMPT_CAPABILITIES


@pytest.mark.asyncio
async def test_bootstrap_capability_registers_unauthenticated_on_production_boot_path(tmp_path: Path) -> None:
    kernel = await build_and_boot_kernel()
    try:
        descriptor = kernel.get_capability(_BOOTSTRAP_CAPABILITY)
        assert descriptor.provider == "security"
        assert descriptor.requires_authentication is False
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_bootstrap_capability_reachable_via_kernel_dispatch_with_no_session_token(tmp_path: Path) -> None:
    kernel = await build_and_boot_kernel()
    try:
        request = CapabilityRequest(
            capability_name=_BOOTSTRAP_CAPABILITY,
            parameters={"tenant_id": "acme", "principal_id": "owner", "password": "correct horse battery staple"},
        )
        result = await kernel.invoke_capability(request)
        assert result["created"] is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_bootstrap_capability_fails_closed_via_dispatch_once_already_bootstrapped(tmp_path: Path) -> None:
    kernel = await build_and_boot_kernel()
    try:
        first = CapabilityRequest(
            capability_name=_BOOTSTRAP_CAPABILITY,
            parameters={"tenant_id": "acme", "principal_id": "owner", "password": "correct horse battery staple"},
        )
        await kernel.invoke_capability(first)

        second = CapabilityRequest(
            capability_name=_BOOTSTRAP_CAPABILITY,
            parameters={"tenant_id": "other", "principal_id": "intruder", "password": "another-strong-password"},
        )
        with pytest.raises(BootstrapClosedError):
            await kernel.invoke_capability(second)
    finally:
        await kernel.shutdown()


# =============================================================================
# D. Kernel.health_check() bootstrap_required flag
# =============================================================================


@pytest.mark.asyncio
async def test_health_check_reports_bootstrap_required_true_on_fresh_install(tmp_path: Path) -> None:
    kernel = await build_and_boot_kernel()
    try:
        report = await kernel.health_check()
        assert report["bootstrap_required"] is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_health_check_reports_bootstrap_required_false_after_bootstrap(tmp_path: Path) -> None:
    kernel = await build_and_boot_kernel()
    try:
        request = CapabilityRequest(
            capability_name=_BOOTSTRAP_CAPABILITY,
            parameters={"tenant_id": "acme", "principal_id": "owner", "password": "correct horse battery staple"},
        )
        await kernel.invoke_capability(request)

        report = await kernel.health_check()
        assert report["bootstrap_required"] is False
    finally:
        await kernel.shutdown()


# =============================================================================
# E. Tenant isolation — the bootstrap-lock sentinel never leaks
# =============================================================================


@pytest.mark.asyncio
async def test_bootstrap_lock_sentinel_can_never_authenticate(tmp_path: Path) -> None:
    kernel, security_engine = await _minimal_security_kernel(tmp_path)
    try:
        await security_engine.authentication_manager.bootstrap_first_admin(
            tenant_id="acme", principal_id="owner", password="correct horse battery staple",
            roles=["admin"], permissions=[],
        )
        with pytest.raises(AuthenticationError):
            await security_engine.authentication_manager.authenticate(
                {
                    "principal_type": "SERVICE_PRINCIPAL",
                    "tenant_id": "__system__",
                    "principal_id": "__bootstrap_lock__",
                    "credential": "anything",
                }
            )
    finally:
        await kernel.shutdown()
