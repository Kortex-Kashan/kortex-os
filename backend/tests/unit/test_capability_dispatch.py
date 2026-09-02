"""
Unit tests for the Kernel Capability Enforcement Boundary
(`kortex.core.dispatch.CapabilityDispatcher` / `Kernel.invoke_capability`).

Proves the central invariant: a production capability request cannot reach
a capability handler without passing through the canonical Kernel-mediated
authentication + authorization boundary. Reuses M1-M4's unmodified
`AuthenticationManager`/`AuthorizationEngine`/RBAC/ABAC exactly as built —
no decision logic is duplicated or re-implemented here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest, _safe_classification
from kortex.core.exceptions import CapabilityNotFoundError, KernelStateError, ResourceNotFoundError
from kortex.core.kernel import Kernel
from kortex.engines.registry.engine import _BOOTSTRAP_EXEMPT_CAPABILITIES
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError, SecurityEngineError
from kortex.engines.security.models import (
    ClassificationLevel,
    PrincipalRecord,
    RolePermissionRecord,
    TokenPayload,
)
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\x11" * 32
_TEST_SIGNING_KEY = b"\x22" * 32
_TEST_ROLE = "dispatch-test-role"


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-dispatch-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    """Construct (but do not boot) a Kernel with real, unmodified Storage +
    Security Engines. Callers that need to register their own test
    capability must do so before calling `kernel.boot()` — capability
    registration is only permitted at `CREATED`/`BOOTING`."""
    kernel = Kernel()
    # M5-A8: explicit isolated in-memory DB, matching the established pattern
    # in `test_capability_dispatch_adversarial.py`.
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "dispatch_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    return kernel, storage_engine, security_engine


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    """`_build_kernel` plus an immediate boot, for tests that register no
    test-specific capability of their own."""
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    await kernel.boot()
    return kernel, storage_engine, security_engine


async def _seed_principal(
    data_store: Any,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
    clearance_level: str = "INTERNAL",
) -> None:
    """Insert a `PrincipalRecord` directly via `IDataStore` — matching
    `test_authentication_manager.py`'s established seeding convention."""
    credential_hash = PasswordHasher().hash("dispatch-test-credential")

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
                attributes={"clearance_level": clearance_level},
            )
        )

    await data_store.execute_in_transaction(_action)


async def _grant_role_permission(data_store: Any, role: str, permission: str) -> None:
    async def _action(session: AsyncSession) -> None:
        from sqlalchemy import select

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
            "password": "dispatch-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


class _CallCountingHandler:
    """Spy handler proving `handler.call_count == 0` on denial — not merely
    that an exception was raised, per the required test rigor."""

    def __init__(self) -> None:
        self.call_count = 0
        self.received: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> str:
        self.call_count += 1
        self.received.append(kwargs)
        return "handler-invoked"


class _FailingDataStore:
    """Simulates an `IDataStore` operational failure inside RBAC evaluation."""

    async def get_session(self) -> Any:  # pragma: no cover - not exercised by these tests
        raise AssertionError("get_session should not be called")

    async def execute_in_transaction(self, action: Any) -> Any:
        raise RuntimeError("simulated storage failure")


# -- 1. Authorized capability executes successfully -------------------------


@pytest.mark.asyncio
async def test_authorized_capability_executes_successfully(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(
        name="dispatch.test.allowed",
        description="test",
        provider="test",
        handler=handler,
        required_permissions=["dispatch.read"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "dispatch.read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="dispatch.test.allowed",
        session_token=token,
        parameters={"value": 1},
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert result == "handler-invoked"
    assert handler.call_count == 1
    assert handler.received == [{"value": 1}]


# -- 2. Unauthorized capability (RBAC deny) is denied ------------------------


@pytest.mark.asyncio
async def test_unauthorized_capability_denied_by_rbac(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(
        name="dispatch.test.rbac_denied",
        description="test",
        provider="test",
        handler=handler,
        required_permissions=["dispatch.write"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path)
    # Principal has no roles at all -> RBAC_NO_ROLES.
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="dispatch.test.rbac_denied",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)

    # -- 3. Unauthorized handler call count remains exactly zero ------------
    assert handler.call_count == 0


# -- 4. Missing session token is denied --------------------------------------


@pytest.mark.asyncio
async def test_missing_session_token_denied(tmp_path: Path) -> None:
    kernel, _storage_engine, _security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(name="dispatch.test.needs_token", description="test", provider="test", handler=handler)
    await kernel.boot()

    request = CapabilityRequest(capability_name="dispatch.test.needs_token", session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)
    assert handler.call_count == 0


# -- 5. Authentication failure is denied -------------------------------------


@pytest.mark.asyncio
async def test_authentication_failure_denied(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(name="dispatch.test.auth_fail", description="test", provider="test", handler=handler)
    await kernel.boot()

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")
    # Tampering with a signed field breaks the Ed25519 signature check
    # itself, which raises `InvalidSignatureError` — a `SecurityEngineError`
    # subclass, but (per `security/exceptions.py`) NOT an `AuthenticationError`
    # subclass. Assert the true common base, not the narrower one.
    tampered = token.model_copy(update={"principal_id": "someone-else"})

    request = CapabilityRequest(capability_name="dispatch.test.auth_fail", session_token=tampered)
    with pytest.raises(SecurityEngineError):
        await kernel.invoke_capability(request)
    assert handler.call_count == 0


# -- 6. Authorization failure (ABAC classification) is denied ---------------


@pytest.mark.asyncio
async def test_authorization_failure_denied_by_abac_classification(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(
        name="dispatch.test.classified",
        description="test",
        provider="test",
        handler=handler,
        security_classification="RESTRICTED",
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path)
    # PUBLIC clearance, capability requires RESTRICTED.
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", clearance_level="PUBLIC")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="dispatch.test.classified",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert handler.call_count == 0


# -- 7. Authorization engine (storage) failure fails closed -----------------


@pytest.mark.asyncio
async def test_authorization_engine_storage_failure_fails_closed() -> None:
    """A genuine `IDataStore` operational failure during RBAC evaluation
    must propagate as `SecurityEngineError`, never resolve to allow or a
    misleading deny. Tested directly against `AuthorizationEngine` (a
    failing data store can't also issue a genuine token for a full
    `Kernel.invoke_capability` round trip, since token issuance/verification
    share the same `IDataStore`) — `kortex.core.dispatch` never catches or
    reinterprets this exception, so proving it here is equivalent to
    proving the dispatcher's own fail-closed behavior for this path.
    """
    from kortex.engines.security.authorization import AuthorizationEngine
    from kortex.engines.security.models import PermissionRequirement, SecurityPrincipal

    authorization_engine = AuthorizationEngine(data_store=_FailingDataStore())
    principal = SecurityPrincipal(principal_id="p", principal_type="USER", tenant_id="t")
    requirement = PermissionRequirement(capability_name="x", required_permissions=["x.read"])
    with pytest.raises(SecurityEngineError):
        await authorization_engine.authorize_strict(principal, requirement, {"resource_tenant_id": "t"})


# -- 8. Cross-tenant request is denied ---------------------------------------


@pytest.mark.asyncio
async def test_cross_tenant_request_denied(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(name="dispatch.test.tenant_scoped", description="test", provider="test", handler=handler)
    await kernel.boot()

    tenant_a = _tenant(tmp_path, "-a")
    await _seed_principal(storage_engine.data, tenant_a, "principal-1")
    token = await _issue_token(security_engine, tenant_a, "principal-1")

    request = CapabilityRequest(
        capability_name="dispatch.test.tenant_scoped",
        session_token=token,
        context={"resource_tenant_id": _tenant(tmp_path, "-b")},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert handler.call_count == 0


# -- 9. Missing resource_tenant_id is denied ---------------------------------


@pytest.mark.asyncio
async def test_missing_resource_tenant_id_denied(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(name="dispatch.test.no_context", description="test", provider="test", handler=handler)
    await kernel.boot()

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(capability_name="dispatch.test.no_context", session_token=token, context={})
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert handler.call_count == 0


# -- 10 & 11. required_permissions come exclusively from the descriptor -----


@pytest.mark.asyncio
async def test_required_permissions_sourced_from_descriptor_not_request(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(
        name="dispatch.test.real_requirement",
        description="test",
        provider="test",
        handler=handler,
        required_permissions=["dispatch.admin"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path)
    # Principal has NO roles/permissions granted at all.
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    # Attempt to smuggle a weaker/empty permission requirement through both
    # untrusted channels the dispatcher accepts (parameters and context).
    # Neither can influence what `PermissionRequirement.required_permissions`
    # actually is — that is built exclusively from the resolved descriptor.
    request = CapabilityRequest(
        capability_name="dispatch.test.real_requirement",
        session_token=token,
        parameters={"required_permissions": [], "security_classification": "PUBLIC"},
        context={"resource_tenant_id": tenant_id, "required_permissions": [], "security_classification": "PUBLIC"},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert handler.call_count == 0


# -- 12. Capability not found propagates -------------------------------------


@pytest.mark.asyncio
async def test_capability_not_found_propagates(tmp_path: Path) -> None:
    kernel, _storage_engine, _security_engine = await _boot_kernel(tmp_path)
    request = CapabilityRequest(capability_name="dispatch.test.does_not_exist")
    with pytest.raises(CapabilityNotFoundError):
        await kernel.invoke_capability(request)


# -- 13. access.authorize remains independently callable (M4 regression) ----


@pytest.mark.asyncio
async def test_access_authorize_capability_remains_independently_callable(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "doc.write")
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": "principal-1",
            "password": "dispatch-test-credential",
        }
    )

    from kortex.engines.security.models import PermissionRequirement

    raw_handler = kernel._registry_engine.get_raw_handler_for_testing("kortex.security.access.authorize")
    requirement = PermissionRequirement(capability_name="doc.write", required_permissions=["doc.write"])
    decision = await raw_handler(principal, requirement, {"resource_tenant_id": tenant_id})
    assert decision.is_allowed is True

    # Calling this capability directly never itself authorizes execution of
    # anything else — it is a standalone Security Engine service, not the
    # enforcement mechanism (that is `Kernel.invoke_capability`).


# -- 14. Authentication happens before authorization -------------------------


@pytest.mark.asyncio
async def test_authentication_precedes_authorization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel, _storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(name="dispatch.test.order", description="test", provider="test", handler=handler)
    await kernel.boot()

    authorize_calls: list[int] = []
    real_authorize_strict = security_engine.authorization_engine.authorize_strict

    async def _spy_authorize_strict(*args: Any, **kwargs: Any) -> Any:
        authorize_calls.append(1)
        return await real_authorize_strict(*args, **kwargs)

    monkeypatch.setattr(security_engine.authorization_engine, "authorize_strict", _spy_authorize_strict)

    # No token supplied -> authentication fails before authorization is ever reached.
    request = CapabilityRequest(capability_name="dispatch.test.order", session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)

    assert authorize_calls == []
    assert handler.call_count == 0


# -- 15. Security Engine unavailable fails closed ----------------------------


@pytest.mark.asyncio
async def test_security_engine_unavailable_fails_closed(tmp_path: Path) -> None:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "no_security_storage"))
    kernel.register_engine(storage_engine)
    # Deliberately no SecurityEngine registered at all.

    handler = _CallCountingHandler()
    kernel.register_capability(name="dispatch.test.no_security", description="test", provider="test", handler=handler)
    await kernel.boot()

    now = datetime.now(UTC)
    request = CapabilityRequest(
        capability_name="dispatch.test.no_security",
        session_token=TokenPayload(
            token_id="t",
            principal_id="p",
            principal_type="USER",
            tenant_id="t",
            issued_at_utc=now,
            expires_at_utc=now,
        ),
    )
    # Security Engine isn't registered at all -> Kernel.get_engine("security")
    # raises ResourceNotFoundError (Kernel's own missing-engine error, not a
    # Security-Engine-specific exception, since Security Engine literally
    # doesn't exist to raise one) -- still fails closed either way.
    with pytest.raises(ResourceNotFoundError):
        await kernel.invoke_capability(request)
    assert handler.call_count == 0


# -- 16. Registration after boot is rejected ---------------------------------


@pytest.mark.asyncio
async def test_registration_after_boot_rejected(tmp_path: Path) -> None:
    kernel, _storage_engine, _security_engine = await _boot_kernel(tmp_path)
    with pytest.raises(KernelStateError):
        kernel.register_capability(
            name="dispatch.test.too_late", description="test", provider="test", handler=lambda: None
        )


# -- 17. requires_authentication=False cannot become a generic bypass -------


@pytest.mark.asyncio
async def test_requires_authentication_false_reserved_for_bootstrap_capability(tmp_path: Path) -> None:
    kernel = Kernel()
    # Any other capability name attempting the bootstrap carve-out is rejected.
    with pytest.raises(ValueError):
        kernel.register_capability(
            name="dispatch.test.fake_bootstrap",
            description="test",
            provider="test",
            handler=lambda: None,
            requires_authentication=False,
        )
    # Every genuinely exempt capability name in the fixed allowlist is accepted.
    for exempt_name in _BOOTSTRAP_EXEMPT_CAPABILITIES:
        descriptor = kernel.register_capability(
            name=exempt_name,
            description="test",
            provider="test",
            handler=lambda: None,
            requires_authentication=False,
        )
        assert descriptor.requires_authentication is False


# -- 18. Invalid security_classification defaults to RESTRICTED -------------


def test_invalid_classification_defaults_to_restricted() -> None:
    assert _safe_classification("NOT_A_REAL_LEVEL") is ClassificationLevel.RESTRICTED
    assert _safe_classification("") is ClassificationLevel.RESTRICTED
    # Valid values are parsed correctly, not blanket-overridden.
    assert _safe_classification("PUBLIC") is ClassificationLevel.PUBLIC
    assert _safe_classification("INTERNAL") is ClassificationLevel.INTERNAL


# -- 19. parameters/context are independent channels, never cross-extracted -


@pytest.mark.asyncio
async def test_parameters_and_context_are_never_cross_extracted(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(name="dispatch.test.channels", description="test", provider="test", handler=handler)
    await kernel.boot()

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    # A "_authz_context"-shaped key placed in `parameters` (as workflow
    # steps do before evaluator.py strips it) is NOT special to the
    # dispatcher itself -- it is just another parameter forwarded verbatim
    # to the handler. Extraction is entirely `workflow/evaluator.py`'s own
    # convention, not something `CapabilityDispatcher` performs.
    request = CapabilityRequest(
        capability_name="dispatch.test.channels",
        session_token=token,
        parameters={"_authz_context": {"resource_tenant_id": "attacker-supplied-tenant"}, "real_arg": 42},
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert result == "handler-invoked"
    assert handler.received == [{"_authz_context": {"resource_tenant_id": "attacker-supplied-tenant"}, "real_arg": 42}]
    # The real ABAC decision used `context`, not the value embedded in
    # `parameters` -- proven by the call succeeding at all, since
    # `principal`'s real tenant is `tenant_id`, not "attacker-supplied-tenant".


# -- 20. Malformed session token is rejected by CapabilityRequest itself ----


def test_malformed_session_token_rejected_by_model_validation() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CapabilityRequest(capability_name="dispatch.test.malformed", session_token={"not": "a valid token shape"})


# -- 21. Audit/security hook: dispatch produces an audit trail (Milestone M5) -----


@pytest.mark.asyncio
async def test_successful_dispatch_records_authentication_and_authorization_audit_entries(tmp_path: Path) -> None:
    """Every dispatched invocation must be observable through Security
    Engine's existing Milestone M6 `AuditManager` — not merely enforced.
    Proves both the token-verification step and the authorization decision
    are recorded, with the correct outcome, for a successful invocation."""
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(
        name="dispatch.test.audited_allow",
        description="test",
        provider="test",
        handler=handler,
        required_permissions=["dispatch.audit_read"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "dispatch.audit_read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="dispatch.test.audited_allow",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)
    assert result == "handler-invoked"

    auth_entries = await security_engine.audit_manager.get_audit_entries(
        tenant_id=tenant_id, action="kortex.kernel.dispatch.authenticate"
    )
    assert len(auth_entries) == 1
    assert auth_entries[0].actor_id == "principal-1"
    assert auth_entries[0].context["result"] == "success"

    authz_entries = await security_engine.audit_manager.get_audit_entries(
        tenant_id=tenant_id, action="dispatch.test.audited_allow"
    )
    assert len(authz_entries) == 1
    assert authz_entries[0].actor_id == "principal-1"
    assert authz_entries[0].context["is_allowed"] is True


@pytest.mark.asyncio
async def test_denied_dispatch_still_records_authorization_audit_entry(tmp_path: Path) -> None:
    """An RBAC denial must still be recorded to the audit trail — a
    security-relevant decision is auditable regardless of outcome, per the
    architecture spec's "all security events, access grants, denials"
    mandate. The handler must never execute either way."""
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(
        name="dispatch.test.audited_deny",
        description="test",
        provider="test",
        handler=handler,
        required_permissions=["dispatch.admin"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="dispatch.test.audited_deny",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)
    assert handler.call_count == 0

    authz_entries = await security_engine.audit_manager.get_audit_entries(
        tenant_id=tenant_id, action="dispatch.test.audited_deny"
    )
    assert len(authz_entries) == 1
    assert authz_entries[0].context["is_allowed"] is False


@pytest.mark.asyncio
async def test_authentication_failure_records_audit_entry_using_claimed_identity(tmp_path: Path) -> None:
    """A failed token verification must still be recorded, using the
    *claimed* (unverified) identity from the presented token — the only
    identity available before verification succeeds."""
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(
        name="dispatch.test.audited_auth_fail", description="test", provider="test", handler=handler
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")
    tampered = token.model_copy(update={"principal_id": "someone-else"})

    request = CapabilityRequest(capability_name="dispatch.test.audited_auth_fail", session_token=tampered)
    with pytest.raises(SecurityEngineError):
        await kernel.invoke_capability(request)
    assert handler.call_count == 0

    auth_entries = await security_engine.audit_manager.get_audit_entries(
        tenant_id=tenant_id, action="kortex.kernel.dispatch.authenticate"
    )
    assert len(auth_entries) == 1
    assert auth_entries[0].actor_id == "someone-else"  # claimed, unverified identity
    assert auth_entries[0].context["result"] == "failure"


@pytest.mark.asyncio
async def test_dispatch_publishes_typed_security_events(tmp_path: Path) -> None:
    """The dispatch boundary must publish the same typed
    `SecurityAuthSuccessEvent`/`SecurityAccessGrantedEvent` signals Security
    Engine's own audited capabilities already publish — proving the audit
    hook is wired through `EventEngine`, not only the durable audit log."""
    from kortex.engines.security.events import SecurityAccessGrantedEvent, SecurityAuthSuccessEvent

    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    handler = _CallCountingHandler()
    kernel.register_capability(
        name="dispatch.test.audited_events",
        description="test",
        provider="test",
        handler=handler,
        required_permissions=["dispatch.audit_events"],
    )
    await kernel.boot()

    received: list[Any] = []
    kernel.subscribe_event(SecurityAuthSuccessEvent.model_fields["event_type"].default, lambda e: received.append(e))
    kernel.subscribe_event(SecurityAccessGrantedEvent.model_fields["event_type"].default, lambda e: received.append(e))

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "dispatch.audit_events")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="dispatch.test.audited_events",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    await kernel.invoke_capability(request)

    received_topics = {event.topic for event in received}
    assert "kortex.event.security.auth.success" in received_topics
    assert "kortex.event.security.access.granted" in received_topics


# -- 19. M7.2: dict-to-Pydantic-model parameter coercion ---------------------
#
# Every real caller (Tauri/Rust IPC, the FastAPI HTTP boundary) delivers
# `request.parameters` as plain JSON-deserialized data — never a live Python
# object. Before M7.2, a handler whose signature declared a `BaseModel`-typed
# parameter crashed on its own first attribute access, confirmed for
# `kortex.ai.response.generate`/`orchestrate_agent`/`resume_agent`/
# `invoke_tool` and already independently documented for
# `kortex.knowledge.query.search` (`test_knowledge_capability_dispatch.py`).
# These tests exercise the fix (`_coerce_model_parameters`) directly through
# real dispatch, with synthetic capabilities, independent of any specific
# engine.


class _SamplePayload(BaseModel):
    name: str = Field(min_length=1)
    count: int = 0


class _RecordingModelHandler:
    """Spy handler with a `BaseModel`-typed parameter — records exactly what
    it received (object identity, not just equality) so a test can prove
    coercion produced a real instance without silently double-coercing an
    already-correct one."""

    def __init__(self) -> None:
        self.received: list[Any] = []

    async def __call__(self, payload: _SamplePayload) -> str:
        self.received.append(payload)
        return "ok"


class _RecordingOptionalModelHandler:
    def __init__(self) -> None:
        self.received: list[Any] = []

    async def __call__(self, payload: _SamplePayload | None = None) -> str:
        self.received.append(payload)
        return "ok"


class _RecordingListModelHandler:
    def __init__(self) -> None:
        self.received: list[Any] = []

    async def __call__(self, items: list[_SamplePayload]) -> str:
        self.received.append(items)
        return "ok"


class _RecordingDictHandler:
    """Control case: a plain `dict[str, Any]` parameter (not a `BaseModel`)
    must never be coerced/replaced — proves the fix is narrowly scoped."""

    def __init__(self) -> None:
        self.received: list[Any] = []

    async def __call__(self, raw: dict[str, Any]) -> str:
        self.received.append(raw)
        return "ok"


async def _authorized_request(tmp_path: Path, capability_name: str, handler: Any, parameters: dict[str, Any]) -> Any:
    """Shared setup for the coercion tests below: build+boot a kernel with
    one synthetic capability, seed an authorized principal, dispatch once,
    and return the raw result."""
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    kernel.register_capability(
        name=capability_name,
        description="test",
        provider="test",
        handler=handler,
        required_permissions=["dispatch.coerce"],
    )
    await kernel.boot()

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "dispatch.coerce")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=capability_name,
        session_token=token,
        parameters=parameters,
        context={"resource_tenant_id": tenant_id},
    )
    return await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_dict_parameter_coerced_into_declared_model_type(tmp_path: Path) -> None:
    handler = _RecordingModelHandler()
    await _authorized_request(
        tmp_path, "dispatch.test.coerce.model", handler, {"payload": {"name": "alice", "count": 3}}
    )

    assert len(handler.received) == 1
    assert isinstance(handler.received[0], _SamplePayload)
    assert handler.received[0].name == "alice"
    assert handler.received[0].count == 3


@pytest.mark.asyncio
async def test_already_live_instance_is_never_recoerced(tmp_path: Path) -> None:
    """A caller that already constructed the real model (e.g. a same-process
    test, or a future non-JSON caller) must see the identical object pass
    through untouched — proves coercion is a no-op for already-correct
    input, not a blind re-validate-everything step."""
    handler = _RecordingModelHandler()
    original = _SamplePayload(name="bob", count=7)

    await _authorized_request(tmp_path, "dispatch.test.coerce.identity", handler, {"payload": original})

    assert len(handler.received) == 1
    assert handler.received[0] is original


@pytest.mark.asyncio
async def test_optional_model_parameter_dict_is_coerced(tmp_path: Path) -> None:
    handler = _RecordingOptionalModelHandler()
    await _authorized_request(
        tmp_path, "dispatch.test.coerce.optional", handler, {"payload": {"name": "carol", "count": 1}}
    )

    assert isinstance(handler.received[0], _SamplePayload)
    assert handler.received[0].name == "carol"


@pytest.mark.asyncio
async def test_optional_model_parameter_none_is_left_alone(tmp_path: Path) -> None:
    handler = _RecordingOptionalModelHandler()
    await _authorized_request(tmp_path, "dispatch.test.coerce.optional_none", handler, {"payload": None})

    assert handler.received == [None]


@pytest.mark.asyncio
async def test_list_of_model_parameter_is_coerced_element_wise(tmp_path: Path) -> None:
    handler = _RecordingListModelHandler()
    await _authorized_request(
        tmp_path,
        "dispatch.test.coerce.list",
        handler,
        {"items": [{"name": "d1", "count": 1}, {"name": "d2", "count": 2}]},
    )

    assert len(handler.received) == 1
    items = handler.received[0]
    assert all(isinstance(item, _SamplePayload) for item in items)
    assert [item.name for item in items] == ["d1", "d2"]


@pytest.mark.asyncio
async def test_list_of_model_parameter_preserves_already_live_instances(tmp_path: Path) -> None:
    handler = _RecordingListModelHandler()
    already_live = _SamplePayload(name="e1", count=9)

    await _authorized_request(
        tmp_path,
        "dispatch.test.coerce.list_mixed",
        handler,
        {"items": [already_live, {"name": "e2", "count": 2}]},
    )

    items = handler.received[0]
    assert items[0] is already_live
    assert isinstance(items[1], _SamplePayload) and items[1] is not already_live


@pytest.mark.asyncio
async def test_malformed_dict_still_fails_validation_not_silently_accepted(tmp_path: Path) -> None:
    """The fix solves the transport-shape problem; it must never weaken
    Pydantic's own field validation — a dict missing a required field (or
    with a value outside a declared constraint) must still raise."""
    handler = _RecordingModelHandler()

    with pytest.raises(Exception):  # noqa: B017 - real Pydantic ValidationError, propagated unmodified
        await _authorized_request(
            tmp_path, "dispatch.test.coerce.invalid", handler, {"payload": {"name": "", "count": 1}}
        )

    assert handler.received == []


@pytest.mark.asyncio
async def test_plain_dict_typed_parameter_is_never_coerced(tmp_path: Path) -> None:
    """A parameter genuinely typed `dict[str, Any]` (not a BaseModel) must
    stay a plain dict — proves the fix cannot mis-fire on a parameter that
    is supposed to remain a raw mapping (e.g. `ToolCall.arguments`)."""
    handler = _RecordingDictHandler()
    await _authorized_request(tmp_path, "dispatch.test.coerce.rawdict", handler, {"raw": {"anything": "goes"}})

    assert handler.received == [{"anything": "goes"}]
    assert type(handler.received[0]) is dict
