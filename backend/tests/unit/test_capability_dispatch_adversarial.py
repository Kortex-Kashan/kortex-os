"""
KORTEX Security Engine — Milestone M8 Adversarial Hardening.

Adversarial test suite for the M5 Kernel Security Boundary /
`CapabilityDispatcher`. Originally run against the unmodified M5/M6
implementation to empirically establish which attacks already failed closed
and which succeeded; Residual #1 (below) was found real and reproducible at
that baseline. Residual #1's two tests were then rewritten in place, as part
of the fix commit, to prove closure instead of demonstrating the exploit —
they remain the regression guard for the vulnerability discovered here, not
a historical artifact.

Residuals under test (see M8 authorization):
  #1 — `Kernel.get_capability(name).handler(...)` bypassed `invoke_capability`
       entirely. FIXED: `CapabilityDescriptor` no longer carries the real
       handler at all (`registry/engine.py`); `CapabilityDispatcher` resolves
       it via a non-public, dispatcher-only path. No new public API was added.
  #2 — Workflow compensation reuses the triggering step's session token; no
       distinct system-internal identity exists. NOT redesigned in M8 — tests
       here only characterize actual behavior when that token is expired
       during compensation.
  #3 — Connector's own decentralized `granted_permissions` check is kept as
       defense-in-depth. Tests here establish precisely which layer (Kernel
       RBAC/ABAC vs. Connector's own check) actually gates each outcome.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.exceptions import KernelStateError, ResourceAlreadyExistsError
from kortex.core.kernel import Kernel
from kortex.engines.connector.base_driver import BaseConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.exceptions import ConnectorSecurityError
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorProfile,
    DriverMetadata,
)
from kortex.engines.registry.engine import _BOOTSTRAP_EXEMPT_CAPABILITIES
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import (
    AuthorizationDeniedError,
    InvalidTokenError,
    TokenExpiredError,
)
from kortex.engines.security.models import (
    PrincipalRecord,
    RolePermissionRecord,
    SecurityPrincipal,
    TokenPayload,
)
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import CompensationAction, WorkflowDefinition, WorkflowState, WorkflowStep

_A_BOOTSTRAP_EXEMPT_CAPABILITY = next(iter(sorted(_BOOTSTRAP_EXEMPT_CAPABILITIES)))

_TEST_MASTER_KEY = b"\xaa" * 32
_TEST_SIGNING_KEY = b"\xbb" * 32


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-adv-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel = Kernel()
    db_file = tmp_path / f"adv_kernel_{uuid.uuid4().hex[:8]}.db"
    kernel._db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_file}")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "adv_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    return kernel, storage_engine, security_engine


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    await kernel.boot()
    return kernel, storage_engine, security_engine


async def _seed_principal(
    data_store: Any,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
    clearance_level: str = "INTERNAL",
    enabled: bool = True,
) -> None:
    credential_hash = PasswordHasher().hash("adv-test-credential")

    async def _action(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type="USER",
                enabled=enabled,
                credential_hash=credential_hash,
                roles=roles or [],
                attributes={"clearance_level": clearance_level},
            )
        )

    await data_store.execute_in_transaction(_action)


async def _set_principal_enabled(data_store: Any, tenant_id: str, principal_id: str, enabled: bool) -> None:
    from sqlalchemy import update

    async def _action(session: AsyncSession) -> None:
        await session.execute(
            update(PrincipalRecord)
            .where(PrincipalRecord.tenant_id == tenant_id, PrincipalRecord.principal_id == principal_id)
            .values(enabled=enabled)
        )

    await data_store.execute_in_transaction(_action)


async def _delete_principal(data_store: Any, tenant_id: str, principal_id: str) -> None:
    from sqlalchemy import delete

    async def _action(session: AsyncSession) -> None:
        await session.execute(
            delete(PrincipalRecord).where(
                PrincipalRecord.tenant_id == tenant_id, PrincipalRecord.principal_id == principal_id
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
            "password": "adv-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _issue_backdated_token(
    security_engine: SecurityEngine, principal: SecurityPrincipal, seconds_ago: int
) -> TokenPayload:
    """Construct a genuinely, validly-signed but ALREADY EXPIRED token —
    simulating a token that was legitimately issued in the past and has
    since expired, without waiting real time or monkeypatching the clock.
    Reuses the exact same signing primitives `issue_token` uses internally.
    """
    manager = security_engine.authentication_manager
    token_id = uuid.uuid4().hex
    issued_at_utc = datetime.now(UTC) - timedelta(seconds=seconds_ago + 1)
    expires_at_utc = issued_at_utc + timedelta(seconds=1)

    payload_bytes = manager._build_signing_payload(
        token_id,
        principal.principal_id,
        principal.principal_type.value,
        principal.tenant_id,
        issued_at_utc,
        expires_at_utc,
    )
    signature = manager._verification_service.sign(
        payload_bytes, manager._signing_private_key, manager._signing_public_key
    )
    return TokenPayload(
        token_id=token_id,
        principal_id=principal.principal_id,
        principal_type=principal.principal_type,
        tenant_id=principal.tenant_id,
        issued_at_utc=issued_at_utc,
        expires_at_utc=expires_at_utc,
        signature=signature.signature,
    )


class _Spy:
    def __init__(self) -> None:
        self.call_count = 0

    async def __call__(self, **kwargs: Any) -> str:
        self.call_count += 1
        return "handler-invoked"


# =============================================================================
# A. RESIDUAL #1 — direct descriptor.handler() bypass
# =============================================================================


@pytest.mark.asyncio
async def test_direct_handler_invocation_no_longer_possible(tmp_path: Path) -> None:
    """CLOSURE PROOF (M8 Residual #1 fix): a caller holding a
    `CapabilityDescriptor` obtained via `kernel.get_capability()` can no
    longer reach the real handler through it at all — the object simply
    does not carry that reference anymore. The SAME unauthorized principal
    remains correctly denied through `kernel.invoke_capability`, exactly as
    before the fix; only the direct-bypass path changed.

    This test previously demonstrated the bypass SUCCEEDING (pre-fix
    baseline). It now proves the opposite and is the regression guard for
    the vulnerability discovered during M8 adversarial hardening.
    """
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(
        name="adv.test.protected",
        description="test",
        provider="test",
        handler=spy,
        required_permissions=["adv.protected.read"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-a")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")  # no roles/permissions
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    # 1. The sanctioned path still correctly denies (no `adv.protected.read`).
    request = CapabilityRequest(
        capability_name="adv.test.protected",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0

    # 2. The descriptor returned by the PUBLIC get_capability() API no
    #    longer has any attribute exposing the real callable.
    descriptor = kernel.get_capability("adv.test.protected")
    assert not hasattr(descriptor, "handler")

    # 3. The sanctioned path, called by an AUTHORIZED principal, still works
    #    end-to-end — the fix did not break legitimate dispatch.
    role = f"role-{tenant_id}"
    await _seed_principal(storage_engine.data, tenant_id, "principal-2", roles=[role])
    await _grant_role_permission(storage_engine.data, role, "adv.protected.read")
    authorized_token = await _issue_token(security_engine, tenant_id, "principal-2")
    authorized_request = CapabilityRequest(
        capability_name="adv.test.protected",
        session_token=authorized_token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(authorized_request)
    assert result == "handler-invoked"
    assert spy.call_count == 1


@pytest.mark.asyncio
async def test_direct_handler_bypass_closed_for_bootstrap_protected_capability(tmp_path: Path) -> None:
    """Same closure proof, but against a REAL production capability
    (`kortex.security.access.authorize`) rather than a synthetic one —
    proves the fix is not an artifact of test-only capability registration."""
    kernel, _storage_engine, _security_engine = await _boot_kernel(tmp_path)
    descriptor = kernel.get_capability("kortex.security.access.authorize")

    # The descriptor for a real production capability carries no handler either.
    assert not hasattr(descriptor, "handler")

    tenant_id = _tenant(tmp_path, "-b")
    from kortex.engines.security.models import PermissionRequirement

    principal = SecurityPrincipal(principal_id="attacker", principal_type="USER", tenant_id=tenant_id, roles=[])
    requirement = PermissionRequirement(capability_name="doc.write", required_permissions=["doc.write"])

    # There is no longer anything callable on the descriptor to invoke this
    # way at all -- confirm the object genuinely has nothing to call.
    with pytest.raises(AttributeError):
        await descriptor.handler(principal, requirement, {"resource_tenant_id": tenant_id})


# =============================================================================
# B. Tampered request parameters / context — permission & classification override attempts
# =============================================================================


@pytest.mark.asyncio
async def test_tampered_parameters_cannot_override_required_permissions(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(
        name="adv.test.perm_override",
        description="test",
        provider="test",
        handler=spy,
        required_permissions=["adv.admin"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-c")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="adv.test.perm_override",
        session_token=token,
        parameters={"required_permissions": []},
        context={"resource_tenant_id": tenant_id, "required_permissions": [], "security_classification": "PUBLIC"},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


@pytest.mark.asyncio
async def test_tampered_context_cannot_override_security_classification(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(
        name="adv.test.classification_override",
        description="test",
        provider="test",
        handler=spy,
        security_classification="RESTRICTED",
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-d")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", clearance_level="PUBLIC")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="adv.test.classification_override",
        session_token=token,
        context={"resource_tenant_id": tenant_id, "security_classification": "PUBLIC"},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


# =============================================================================
# C. Malformed security_classification — full dispatch, not just the unit helper
# =============================================================================


@pytest.mark.asyncio
async def test_malformed_classification_fails_closed_to_restricted_end_to_end(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(
        name="adv.test.malformed_classification",
        description="test",
        provider="test",
        handler=spy,
        security_classification="NOT_A_REAL_LEVEL",
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-e")

    # PUBLIC clearance must be denied (malformed value fails closed to RESTRICTED).
    await _seed_principal(storage_engine.data, tenant_id, "public-principal", clearance_level="PUBLIC")
    public_token = await _issue_token(security_engine, tenant_id, "public-principal")
    request_public = CapabilityRequest(
        capability_name="adv.test.malformed_classification",
        session_token=public_token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request_public)
    assert spy.call_count == 0

    # RESTRICTED clearance must succeed (proves fail-closed-to-RESTRICTED,
    # not fail-closed-to-something-unreachable).
    await _seed_principal(storage_engine.data, tenant_id, "restricted-principal", clearance_level="RESTRICTED")
    restricted_token = await _issue_token(security_engine, tenant_id, "restricted-principal")
    request_restricted = CapabilityRequest(
        capability_name="adv.test.malformed_classification",
        session_token=restricted_token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request_restricted)
    assert result == "handler-invoked"
    assert spy.call_count == 1


# =============================================================================
# D. Malformed / edge-case permission lists
# =============================================================================


@pytest.mark.asyncio
async def test_empty_string_permission_entries_never_match_anything(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(
        name="adv.test.empty_perm_entry",
        description="test",
        provider="test",
        handler=spy,
        required_permissions=[""],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-f")
    role = f"role-{tenant_id}"
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    # Grant everything EXCEPT the empty string itself.
    await _grant_role_permission(storage_engine.data, role, "adv.something.else")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="adv.test.empty_perm_entry",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


@pytest.mark.asyncio
async def test_duplicate_permission_entries_do_not_weaken_enforcement(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(
        name="adv.test.dup_perm",
        description="test",
        provider="test",
        handler=spy,
        required_permissions=["adv.write", "adv.write", "adv.write"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-g")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")  # no roles
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="adv.test.dup_perm", session_token=token, context={"resource_tenant_id": tenant_id}
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


# =============================================================================
# E. Expired session tokens — full dispatch level
# =============================================================================


@pytest.mark.asyncio
async def test_expired_token_denied_at_full_dispatch_level(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(name="adv.test.expired", description="test", provider="test", handler=spy)
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-h")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    principal = SecurityPrincipal(principal_id="principal-1", principal_type="USER", tenant_id=tenant_id)
    expired_token = await _issue_backdated_token(security_engine, principal, seconds_ago=3600)

    request = CapabilityRequest(capability_name="adv.test.expired", session_token=expired_token)
    with pytest.raises(TokenExpiredError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


# =============================================================================
# F. Replayed session tokens against disabled/deleted principals
# =============================================================================


@pytest.mark.asyncio
async def test_token_replay_denied_after_principal_disabled(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(name="adv.test.replay_disabled", description="test", provider="test", handler=spy)
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-i")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    # Token is valid at issuance time.
    request1 = CapabilityRequest(
        capability_name="adv.test.replay_disabled", session_token=token, context={"resource_tenant_id": tenant_id}
    )
    await kernel.invoke_capability(request1)
    assert spy.call_count == 1

    # Principal is disabled; the SAME still-unexpired token is replayed.
    await _set_principal_enabled(storage_engine.data, tenant_id, "principal-1", enabled=False)
    request2 = CapabilityRequest(
        capability_name="adv.test.replay_disabled", session_token=token, context={"resource_tenant_id": tenant_id}
    )
    with pytest.raises(InvalidTokenError):
        await kernel.invoke_capability(request2)
    assert spy.call_count == 1  # unchanged — second call never reached the handler


@pytest.mark.asyncio
async def test_token_replay_denied_after_principal_deleted(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(name="adv.test.replay_deleted", description="test", provider="test", handler=spy)
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-j")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    await _delete_principal(storage_engine.data, tenant_id, "principal-1")
    request = CapabilityRequest(capability_name="adv.test.replay_deleted", session_token=token)
    with pytest.raises(InvalidTokenError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


# =============================================================================
# G/H/I. Tenant mismatch / insufficient RBAC / ABAC denial — self-contained
# =============================================================================


@pytest.mark.asyncio
async def test_tenant_mismatch_denied_full_dispatch(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(name="adv.test.tenant_mismatch", description="test", provider="test", handler=spy)
    await kernel.boot()

    tenant_a = _tenant(tmp_path, "-k1")
    tenant_b = _tenant(tmp_path, "-k2")
    await _seed_principal(storage_engine.data, tenant_a, "principal-1")
    token = await _issue_token(security_engine, tenant_a, "principal-1")

    request = CapabilityRequest(
        capability_name="adv.test.tenant_mismatch", session_token=token, context={"resource_tenant_id": tenant_b}
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


@pytest.mark.asyncio
async def test_insufficient_rbac_permission_denied_full_dispatch(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(
        name="adv.test.insufficient_rbac",
        description="test",
        provider="test",
        handler=spy,
        required_permissions=["adv.needed"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-l")
    role = f"role-{tenant_id}"
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    await _grant_role_permission(storage_engine.data, role, "adv.unrelated")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="adv.test.insufficient_rbac", session_token=token, context={"resource_tenant_id": tenant_id}
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


# =============================================================================
# J. Bootstrap capability near-miss names
# =============================================================================


@pytest.mark.parametrize(
    "near_miss_name",
    [
        "kortex.security.auth.authenticate ",  # trailing space
        " kortex.security.auth.authenticate",  # leading space
        "KORTEX.SECURITY.AUTH.AUTHENTICATE",  # case variation
        "kortex.security.auth.authenticate2",  # suffix
        "kortex.security.auth.authenticat",  # truncated
        "kortex.security.auth.authenticate.extra",  # suffix path segment
    ],
)
def test_bootstrap_exemption_rejects_every_near_miss_name(tmp_path: Path, near_miss_name: str) -> None:
    kernel = Kernel()
    with pytest.raises(ValueError):
        kernel.register_capability(
            name=near_miss_name,
            description="test",
            provider="test",
            handler=lambda: None,
            requires_authentication=False,
        )


# =============================================================================
# K. Duplicate bootstrap registration
# =============================================================================


def test_duplicate_bootstrap_capability_registration_rejected() -> None:
    kernel = Kernel()
    kernel.register_capability(
        name=_A_BOOTSTRAP_EXEMPT_CAPABILITY,
        description="test",
        provider="test",
        handler=lambda: None,
        requires_authentication=False,
    )
    with pytest.raises(ResourceAlreadyExistsError):
        kernel.register_capability(
            name=_A_BOOTSTRAP_EXEMPT_CAPABILITY,
            description="test",
            provider="test",
            handler=lambda: None,
            requires_authentication=False,
        )


# =============================================================================
# L. Post-boot registration attempts
# =============================================================================


@pytest.mark.asyncio
async def test_post_boot_registration_rejected(tmp_path: Path) -> None:
    kernel, _storage_engine, _security_engine = await _boot_kernel(tmp_path)
    with pytest.raises(KernelStateError):
        kernel.register_capability(name="adv.test.too_late", description="test", provider="test", handler=lambda: None)


# =============================================================================
# M. Concurrent capability invocation — no cross-request state leakage
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_dispatch_calls_do_not_leak_identity_between_requests(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(
        name="adv.test.concurrent",
        description="test",
        provider="test",
        handler=spy,
        required_permissions=["adv.concurrent.read"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-m")
    allowed_role = f"allowed-role-{tenant_id}"
    await _grant_role_permission(storage_engine.data, allowed_role, "adv.concurrent.read")

    # 5 authorized principals, 5 unauthorized principals, dispatched concurrently.
    authorized_ids = [f"auth-{i}" for i in range(5)]
    denied_ids = [f"denied-{i}" for i in range(5)]
    for pid in authorized_ids:
        await _seed_principal(storage_engine.data, tenant_id, pid, roles=[allowed_role])
    for pid in denied_ids:
        await _seed_principal(storage_engine.data, tenant_id, pid, roles=[])

    authorized_tokens = [await _issue_token(security_engine, tenant_id, pid) for pid in authorized_ids]
    denied_tokens = [await _issue_token(security_engine, tenant_id, pid) for pid in denied_ids]

    async def _dispatch(token: TokenPayload) -> str:
        request = CapabilityRequest(
            capability_name="adv.test.concurrent", session_token=token, context={"resource_tenant_id": tenant_id}
        )
        try:
            return await kernel.invoke_capability(request)
        except AuthorizationDeniedError:
            return "DENIED"

    results = await asyncio.gather(*[_dispatch(t) for t in authorized_tokens], *[_dispatch(t) for t in denied_tokens])
    authorized_results = results[: len(authorized_tokens)]
    denied_results = results[len(authorized_tokens) :]

    assert all(r == "handler-invoked" for r in authorized_results)
    assert all(r == "DENIED" for r in denied_results)
    assert spy.call_count == len(authorized_tokens)


# =============================================================================
# N. Workflow compensation with an expired triggering token
# =============================================================================


@pytest.mark.asyncio
async def test_workflow_compensation_with_expired_token_fails_closed(tmp_path: Path) -> None:
    """Characterizes Residual #2's actual behavior — NOT a redesign. Proves
    that when the triggering step's session token has expired by the time
    compensation runs, the compensation action fails closed (it is denied
    exactly like any other expired-token dispatch), it does not silently
    succeed, and it does not crash the compensation loop for other actions."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "adv_wf_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow_engine = WorkflowEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(workflow_engine)

    spy = _Spy()
    kernel.register_capability(
        name="adv.test.compensation_target",
        description="test",
        provider="test",
        handler=spy,
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-n")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    principal = SecurityPrincipal(principal_id="principal-1", principal_type="USER", tenant_id=tenant_id)
    expired_token = await _issue_backdated_token(security_engine, principal, seconds_ago=3600)
    token_dict = expired_token.model_dump()
    token_dict["signature"] = token_dict["signature"].hex()

    comp_action = CompensationAction(
        name="rollback",
        capability_name="adv.test.compensation_target",
        parameters={"_authz_context": {"resource_tenant_id": tenant_id}},
    )
    step_succ = WorkflowStep(id="step_succ", name="Succeeds, registers compensation", compensation_action=comp_action)
    step_fail = WorkflowStep(id="step_fail", name="Fails", capability_name="kortex.test.nonexistent.capability")

    wf_def = WorkflowDefinition(id="wf_adv_comp", name="Adversarial Compensation WF", steps=[step_succ, step_fail])
    def_id = workflow_engine.register_definition(wf_def)

    instance = await workflow_engine.start_workflow(def_id, session_token=token_dict)
    for _ in range(600):
        if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED) and not instance.compensation_stack:
            break
        await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.FAILED
    assert spy.call_count == 0  # compensation handler never actually ran — denied before invocation


# =============================================================================
# O/P/Q. Connector's decentralized granted_permissions check vs. Kernel RBAC
# =============================================================================


class _AlwaysSucceedsDriver(BaseConnectorDriver):
    @property
    def metadata(self) -> DriverMetadata:
        return DriverMetadata(
            driver_id="adv-dummy-driver",
            display_name="Adv Dummy",
            vendor="test",
            author="test",
            version="1.0.0",
            description="test",
            supported_actions=[ConnectorActionType.FETCH],
        )

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        return ActionResult(request_id=request.request_id, status="SUCCESS", response_payload={"ok": True})

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return True


async def _build_connector_kernel(
    tmp_path: Path, tenant_id: str
) -> tuple[Kernel, StorageEngine, SecurityEngine, ConnectorEngine]:
    kernel = Kernel()
    db_file = tmp_path / f"adv_conn_{uuid.uuid4().hex[:8]}.db"
    kernel._db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_file}")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "adv_connector_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    connector_engine = ConnectorEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()

    driver = _AlwaysSucceedsDriver()
    connector_engine.register_driver(driver)
    # M6.3-1: the profile must be registered under the same tenant the test's
    # own principal will authenticate as, or `execute_action`'s new
    # principal-derived tenant scoping treats it as not found.
    profile = ConnectorProfile(
        profile_id="adv-profile", tenant_id=tenant_id, name="Adv Profile", driver_id="adv-dummy-driver"
    )
    await connector_engine.profile_manager.register_profile(profile)
    return kernel, storage_engine, security_engine, connector_engine


async def _dispatch_connector_action(
    kernel: Kernel, tenant_id: str, token: TokenPayload, granted_permissions: Any
) -> Any:
    options: dict[str, Any] = {}
    if granted_permissions is not _UNSET:
        options["granted_permissions"] = granted_permissions
    request = CapabilityRequest(
        capability_name="kortex.connector.action.execute",
        session_token=token,
        parameters={
            "request": ActionRequest(
                request_id=str(uuid.uuid4()),
                profile_id="adv-profile",
                action_type=ConnectorActionType.FETCH,
                options=options,
            )
        },
        context={"resource_tenant_id": tenant_id},
    )
    return await kernel.invoke_capability(request)


_UNSET = object()


@pytest.mark.asyncio
async def test_kernel_authorized_connector_forged_permissions_denied_by_connector(tmp_path: Path) -> None:
    """Kernel RBAC allows (principal has connector:execute); Connector's own
    check is given a present-but-wrong `granted_permissions` list. Connector
    independently denies despite Kernel already having allowed dispatch."""
    tenant_id = _tenant(tmp_path, "-o1")
    kernel, storage_engine, security_engine, _connector = await _build_connector_kernel(tmp_path, tenant_id)
    role = f"role-{tenant_id}"
    await _grant_role_permission(storage_engine.data, role, "connector:execute")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    with pytest.raises(ConnectorSecurityError):
        await _dispatch_connector_action(kernel, tenant_id, token, granted_permissions=["some.forged.permission"])


@pytest.mark.asyncio
async def test_kernel_denied_connector_forged_permissions_never_reaches_connector(tmp_path: Path) -> None:
    """Kernel RBAC denies (principal lacks connector:execute); Connector's
    own check is forged to CLAIM the correct permission. Kernel denies
    before Connector's handler — and therefore Connector's check — ever runs."""
    tenant_id = _tenant(tmp_path, "-o2")
    kernel, storage_engine, security_engine, _connector = await _build_connector_kernel(tmp_path, tenant_id)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[])  # no connector:execute
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    with pytest.raises(AuthorizationDeniedError):
        await _dispatch_connector_action(
            kernel, tenant_id, token, granted_permissions=["kortex.connector.action.execute"]
        )


@pytest.mark.asyncio
async def test_mismatched_permission_set_denied_by_connector(tmp_path: Path) -> None:
    tenant_id = _tenant(tmp_path, "-o3")
    kernel, storage_engine, security_engine, _connector = await _build_connector_kernel(tmp_path, tenant_id)
    role = f"role-{tenant_id}"
    await _grant_role_permission(storage_engine.data, role, "connector:execute")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    with pytest.raises(ConnectorSecurityError):
        await _dispatch_connector_action(
            kernel, tenant_id, token, granted_permissions=["kortex.connector.other.action"]
        )


@pytest.mark.asyncio
async def test_empty_permission_list_denied_by_connector(tmp_path: Path) -> None:
    tenant_id = _tenant(tmp_path, "-o4")
    kernel, storage_engine, security_engine, _connector = await _build_connector_kernel(tmp_path, tenant_id)
    role = f"role-{tenant_id}"
    await _grant_role_permission(storage_engine.data, role, "connector:execute")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    with pytest.raises(ConnectorSecurityError):
        await _dispatch_connector_action(kernel, tenant_id, token, granted_permissions=[])


@pytest.mark.asyncio
async def test_malformed_type_permission_set_silently_skips_connector_check(tmp_path: Path) -> None:
    """DISCOVERY: `granted_permissions` of a non-list/set/tuple type (or the
    key being absent entirely) causes Connector's own `isinstance` guard to
    silently SKIP its check rather than denying — Connector's decentralized
    check is opt-in, not mandatory. Kernel RBAC (`connector:execute`) is the
    ONLY gate in this scenario. This is not a Kernel bypass (Kernel still
    independently required and enforced `connector:execute`), but it means
    Connector's "defense-in-depth" contributes nothing unless the caller
    happens to supply a list/set/tuple."""
    tenant_id = _tenant(tmp_path, "-o5")
    kernel, storage_engine, security_engine, _connector = await _build_connector_kernel(tmp_path, tenant_id)
    role = f"role-{tenant_id}"
    await _grant_role_permission(storage_engine.data, role, "connector:execute")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    # Malformed type (a string, not a list/set/tuple) -> Connector's isinstance check
    # is skipped entirely -> execution proceeds (gated only by Kernel RBAC above).
    result = await _dispatch_connector_action(kernel, tenant_id, token, granted_permissions="not-a-list")
    assert result.status == "SUCCESS"


@pytest.mark.asyncio
async def test_missing_permission_key_silently_skips_connector_check(tmp_path: Path) -> None:
    """Same discovery as above, but for the key being absent entirely
    (the realistic production shape — nothing in this codebase's production
    call paths populates `options.granted_permissions` today)."""
    tenant_id = _tenant(tmp_path, "-o6")
    kernel, storage_engine, security_engine, _connector = await _build_connector_kernel(tmp_path, tenant_id)
    role = f"role-{tenant_id}"
    await _grant_role_permission(storage_engine.data, role, "connector:execute")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    result = await _dispatch_connector_action(kernel, tenant_id, token, granted_permissions=_UNSET)
    assert result.status == "SUCCESS"


# =============================================================================
# F. Identity/role/tenant-authority forgery via caller-supplied data
# =============================================================================


@pytest.mark.asyncio
async def test_forged_identity_and_role_claims_in_context_and_parameters_are_ignored(tmp_path: Path) -> None:
    """An attacker holding a valid but unprivileged token cannot smuggle a
    privileged identity/role by embedding fake `principal_id`/`roles`
    claims in `parameters` or `context`. The only identity the dispatcher
    ever trusts comes from `AuthenticationManager.verify_token()`, and the
    only roles ever consulted come from a server-side DB lookup keyed on
    that verified `principal_id` -- never from caller-supplied data."""
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(
        name="adv.test.forge_identity",
        description="test",
        provider="test",
        handler=spy,
        required_permissions=["adv.forge.protected"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-fi")
    victim_role = f"role-{tenant_id}-victim"
    await _grant_role_permission(storage_engine.data, victim_role, "adv.forge.protected")
    await _seed_principal(storage_engine.data, tenant_id, "principal-victim", roles=[victim_role])
    await _seed_principal(storage_engine.data, tenant_id, "principal-attacker")  # zero roles/permissions
    attacker_token = await _issue_token(security_engine, tenant_id, "principal-attacker")

    request = CapabilityRequest(
        capability_name="adv.test.forge_identity",
        session_token=attacker_token,
        parameters={"principal_id": "principal-victim", "roles": [victim_role], "actor_id": "principal-victim"},
        context={
            "resource_tenant_id": tenant_id,
            "principal_id": "principal-victim",
            "roles": [victim_role],
            "identity": "principal-victim",
        },
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


@pytest.mark.asyncio
async def test_forged_tenant_authority_via_parameters_is_ignored(tmp_path: Path) -> None:
    """`resource_tenant_id` supplied in `parameters` (not `context`) must
    have zero effect on the ABAC tenant check -- only `context` is ever
    consulted for authorization; `parameters` reaches only the handler,
    never the security decision. Must be denied exactly like a wholly
    missing tenant context, not silently accepted from the wrong field."""
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(name="adv.test.forge_tenant", description="test", provider="test", handler=spy)
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-ft")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="adv.test.forge_tenant",
        session_token=token,
        parameters={"resource_tenant_id": tenant_id},
        context={},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert spy.call_count == 0


@pytest.mark.asyncio
async def test_repeated_invocation_is_deterministic(tmp_path: Path) -> None:
    """The same request, dispatched twice through the sanctioned path, must
    produce the same authorization outcome both times and invoke the
    handler exactly once per call -- `CapabilityDispatcher` holds no
    per-request instance state (see its own class docstring), so no hidden
    state can accumulate across calls."""
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    spy = _Spy()
    kernel.register_capability(
        name="adv.test.repeatable",
        description="test",
        provider="test",
        handler=spy,
        required_permissions=["adv.repeatable.read"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-rep")
    role = f"role-{tenant_id}"
    await _grant_role_permission(storage_engine.data, role, "adv.repeatable.read")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[role])
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="adv.test.repeatable",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    result1 = await kernel.invoke_capability(request)
    result2 = await kernel.invoke_capability(request)
    assert result1 == result2 == "handler-invoked"
    assert spy.call_count == 2


@pytest.mark.asyncio
async def test_handler_receives_only_exploded_parameters_never_request_or_context(tmp_path: Path) -> None:
    """The handler is invoked as `handler(**request.parameters)` -- it never
    receives the `CapabilityRequest` object, the verified `session_token`,
    or `context` itself, so it structurally cannot inspect or silently
    replace the security identity/context that authorized its own
    invocation (Core Invariant #12)."""
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    received: dict[str, Any] = {}

    async def handler(**kwargs: Any) -> str:
        received.update(kwargs)
        return "ok"

    kernel.register_capability(name="adv.test.no_context_leak", description="test", provider="test", handler=handler)
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-ncl")
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="adv.test.no_context_leak",
        session_token=token,
        parameters={"value": 42},
        context={"resource_tenant_id": tenant_id, "secret_marker": "should-never-reach-handler"},
    )
    await kernel.invoke_capability(request)

    assert received == {"value": 42}
    assert "session_token" not in received
    assert "context" not in received
    assert "secret_marker" not in received
