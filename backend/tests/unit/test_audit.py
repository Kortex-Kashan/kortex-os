"""Unit and adversarial tests for KORTEX Security Engine Audit Enforcement Manager (Milestone M6).

Covers:
- `UniversalAuditEntry` model validation, serialization, and immutability.
- `AuditRecord` SQLAlchemy ORM persistence in `security_audit_records`.
- `SecurityBaseEvent` and immutable event payload schemas in `events.py`.
- `AuditManager.compute_state_hash` deterministic hashing.
- `AuditManager.record_audit_entry` & `record_event` with `IDataStore` transactions.
- Event dispatch to `EventEngine` on `kortex.event.security.audit`.
- Subscriber error isolation (event dispatch failure does not abort audit persistence).
- Tenant isolation across audit log queries.
- Pagination, filtering, and limit clamping in `get_audit_entries`.
- `get_audit_entry` cross-tenant retrieval rejection.
- Storage failure normalization into `AuditError`.
- `SecurityEngine` facade integration (`audit_manager` property, diagnostics, `ISecurityEngine` protocol).
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, List, NoReturn, Optional, cast
import uuid


import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.kernel import Kernel
from kortex.engines.event.engine import Event, EventEngine
from kortex.engines.security.audit import AuditManager
from kortex.engines.security.models import RolePermissionRecord
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.events import (
    SecurityAccessDeniedEvent,
    SecurityAccessGrantedEvent,
    SecurityAuditEvent,
    SecurityAuthFailureEvent,
    SecurityAuthSuccessEvent,
    SecuritySecretAccessedEvent,
    SecuritySecretModifiedEvent,
    SecuritySignatureVerifiedEvent,
)
from kortex.engines.security.exceptions import AuditError, SecurityEngineError
from kortex.engines.security.models import (
    AccessDecision,
    AuditRecord,
    CryptographicSignature,
    PermissionRequirement,
    PrincipalRecord,
    PrincipalType,
    SecurityPrincipal,
    UniversalAuditEntry,
)
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.storage.engine import StorageEngine
from argon2 import PasswordHasher

_TEST_MASTER_KEY = b"\x11" * 32
_TEST_SIGNING_KEY = b"\x22" * 32


async def _make_test_env(
    tmp_path: Path, with_event_engine: bool = True
) -> tuple[Kernel, StorageEngine, Optional[EventEngine], AuditManager]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "audit_test_storage"))
    kernel.register_engine(storage_engine)
    await storage_engine.initialize(kernel)
    await storage_engine.start()

    event_engine: Optional[EventEngine] = None
    if with_event_engine:
        event_engine = kernel.get_engine("event")  # type: ignore[assignment]
        await event_engine.initialize(kernel)
        await event_engine.start()

    await kernel.db.connect()
    await kernel.db.create_all_tables()


    audit_mgr = AuditManager(
        data_store=storage_engine.data,
        event_engine=event_engine,
        crypto_provider=LocalCrypto(),
    )
    return kernel, storage_engine, event_engine, audit_mgr


class _FailingDataStore:
    """Simulates database outage for error normalization tests."""

    async def get_session(self) -> Any:
        raise NotImplementedError

    async def execute_in_transaction(self, action: Callable[[AsyncSession], Awaitable[Any]]) -> NoReturn:
        raise RuntimeError("simulated storage failure")


# -- Model & Schema Tests -----------------------------------------------------


def test_universal_audit_entry_immutability_and_defaults() -> None:
    entry = UniversalAuditEntry(
        action="kortex.security.secret.get",
        actor_id="usr_123",
        actor_type="USER",
        tenant_id="tenant_alpha",
        resource_id="secret:kortex/db_pass",
        context={"ip": "127.0.0.1"},
    )
    assert entry.audit_id is not None
    assert isinstance(entry.timestamp_utc, datetime.datetime)
    assert entry.action == "kortex.security.secret.get"
    assert entry.actor_id == "usr_123"
    assert entry.tenant_id == "tenant_alpha"

    # Verify immutability
    with pytest.raises(Exception):
        entry.action = "tampered.action"  # type: ignore[misc]


def test_security_events_immutability() -> None:
    event = SecurityAuditEvent(
        tenant_id="tenant_alpha",
        audit_id="aud_123",
        action="kortex.document.create",
        actor_id="usr_admin",
        actor_type="USER",
    )
    assert event.event_type == "kortex.event.security.audit"
    assert event.event_id.startswith("sec-evt-")

    with pytest.raises(Exception):
        event.actor_id = "tampered"  # type: ignore[misc]

    # Specific event models
    auth_success = SecurityAuthSuccessEvent(tenant_id="t1", principal_id="p1", principal_type="USER")
    assert auth_success.event_type == "kortex.event.security.auth.success"

    auth_fail = SecurityAuthFailureEvent(tenant_id="t1", principal_id="p1", reason="bad_pass")
    assert auth_fail.event_type == "kortex.event.security.auth.failure"

    acc_grant = SecurityAccessGrantedEvent(tenant_id="t1", principal_id="p1", capability_name="cap1", decision_code="ALLOW")
    assert acc_grant.event_type == "kortex.event.security.access.granted"

    acc_deny = SecurityAccessDeniedEvent(tenant_id="t1", principal_id="p1", capability_name="cap1", reason="no_role", decision_code="DENY")
    assert acc_deny.event_type == "kortex.event.security.access.denied"

    sec_acc = SecuritySecretAccessedEvent(tenant_id="t1", secret_handle="sec:key")
    assert sec_acc.event_type == "kortex.event.security.secret.accessed"

    sec_mod = SecuritySecretModifiedEvent(tenant_id="t1", secret_handle="sec:key", operation="PUT")
    assert sec_mod.event_type == "kortex.event.security.secret.modified"

    sig_ver = SecuritySignatureVerifiedEvent(tenant_id="t1", is_valid=True, algorithm="ed25519")
    assert sig_ver.event_type == "kortex.event.security.signature.verified"


def test_compute_state_hash() -> None:
    mgr = AuditManager(data_store=None, crypto_provider=LocalCrypto())  # type: ignore[arg-type]
    assert mgr.compute_state_hash(None) is None

    hash_str = mgr.compute_state_hash("sample text")
    assert isinstance(hash_str, str)
    assert len(hash_str) == 64

    hash_dict1 = mgr.compute_state_hash({"b": 2, "a": 1})
    hash_dict2 = mgr.compute_state_hash({"a": 1, "b": 2})
    assert hash_dict1 == hash_dict2  # Deterministic JSON sort_keys


# -- AuditManager Recording & Event Dispatch Tests ----------------------------


@pytest.mark.asyncio
async def test_record_audit_entry_and_event_publishing(tmp_path: Path) -> None:
    kernel, storage, event_engine, audit_mgr = await _make_test_env(tmp_path, with_event_engine=True)
    assert event_engine is not None

    received_events: List[Event] = []

    async def _on_audit_event(evt: Event) -> None:
        received_events.append(evt)

    event_engine.subscribe("kortex.event.security.audit", _on_audit_event)

    entry = await audit_mgr.record_event(
        action="kortex.security.secret.put",
        actor_id="user_admin",
        actor_type=PrincipalType.USER,
        tenant_id="tenant_1",
        resource_id="secret:kortex/api_key",
        previous_state_hash=None,
        new_state_hash="abc123hash",
        client_ip="192.168.1.50",
        context={"reason": "initial provisioning"},
    )

    assert entry.audit_id is not None
    assert entry.action == "kortex.security.secret.put"
    assert entry.tenant_id == "tenant_1"

    # Verify event published to EventEngine
    assert len(received_events) == 1
    assert received_events[0].topic == "kortex.event.security.audit"
    assert received_events[0].payload["audit_id"] == entry.audit_id
    assert received_events[0].payload["tenant_id"] == "tenant_1"


@pytest.mark.asyncio
async def test_event_subscriber_error_does_not_abort_audit_record(tmp_path: Path) -> None:
    kernel, storage, event_engine, audit_mgr = await _make_test_env(tmp_path, with_event_engine=True)
    assert event_engine is not None

    async def _failing_subscriber(evt: Event) -> NoReturn:
        raise RuntimeError("subscriber exploded")

    event_engine.subscribe("kortex.event.security.audit", _failing_subscriber)

    # Must succeed despite subscriber failure
    entry = await audit_mgr.record_event(
        action="kortex.security.auth.authenticate",
        actor_id="agent_007",
        actor_type=PrincipalType.AGENT,
        tenant_id="tenant_1",
    )

    stored = await audit_mgr.get_audit_entry(entry.audit_id, "tenant_1")
    assert stored is not None
    assert stored.audit_id == entry.audit_id


@pytest.mark.asyncio
async def test_record_audit_validation_failures(tmp_path: Path) -> None:
    kernel, storage, _, audit_mgr = await _make_test_env(tmp_path, with_event_engine=False)

    with pytest.raises(AuditError, match="tenant_id"):
        await audit_mgr.record_audit_entry(
            UniversalAuditEntry(
                action="some.action",
                actor_id="actor_1",
                tenant_id="   ",
            )
        )

    with pytest.raises(AuditError, match="action"):
        await audit_mgr.record_audit_entry(
            UniversalAuditEntry(
                action="   ",
                actor_id="actor_1",
                tenant_id="tenant_1",
            )
        )

    with pytest.raises(AuditError, match="actor_id"):
        await audit_mgr.record_audit_entry(
            UniversalAuditEntry(
                action="some.action",
                actor_id="   ",
                tenant_id="tenant_1",
            )
        )



@pytest.mark.asyncio
async def test_storage_failure_normalizes_to_audit_error() -> None:
    failing_mgr = AuditManager(data_store=_FailingDataStore())  # type: ignore[arg-type]

    with pytest.raises(AuditError, match="Audit storage operation failed"):
        await failing_mgr.record_event(
            action="kortex.test.action",
            actor_id="usr_1",
            actor_type="USER",
            tenant_id="t1",
        )

    with pytest.raises(AuditError, match="Audit storage operation failed"):
        await failing_mgr.get_audit_entries(tenant_id="t1")

    with pytest.raises(AuditError, match="Audit storage operation failed"):
        await failing_mgr.get_audit_entry(audit_id="aud_1", tenant_id="t1")


# -- Tenant Isolation & Query Tests -------------------------------------------


@pytest.mark.asyncio
async def test_audit_tenant_isolation(tmp_path: Path) -> None:
    kernel, storage, _, audit_mgr = await _make_test_env(tmp_path, with_event_engine=False)
    uid = uuid.uuid4().hex
    tenant_a = f"tenant_a_{uid}"
    tenant_b = f"tenant_b_{uid}"

    # Seed entries for Tenant A and Tenant B
    e_a1 = await audit_mgr.record_event("action.1", "user_a", "USER", tenant_a)
    e_a2 = await audit_mgr.record_event("action.2", "user_a", "USER", tenant_a)
    e_b1 = await audit_mgr.record_event("action.1", "user_b", "USER", tenant_b)

    # Query Tenant A
    entries_a = await audit_mgr.get_audit_entries(tenant_a)
    assert len(entries_a) == 2
    assert all(e.tenant_id == tenant_a for e in entries_a)

    # Query Tenant B
    entries_b = await audit_mgr.get_audit_entries(tenant_b)
    assert len(entries_b) == 1
    assert entries_b[0].tenant_id == tenant_b
    assert entries_b[0].audit_id == e_b1.audit_id

    # Cross-tenant get_audit_entry rejected
    assert await audit_mgr.get_audit_entry(e_a1.audit_id, tenant_b) is None
    assert await audit_mgr.get_audit_entry(e_b1.audit_id, tenant_a) is None

    # Valid get_audit_entry succeeds
    found = await audit_mgr.get_audit_entry(e_a1.audit_id, tenant_a)
    assert found is not None
    assert found.audit_id == e_a1.audit_id


@pytest.mark.asyncio
async def test_audit_filtering_and_pagination(tmp_path: Path) -> None:
    kernel, storage, _, audit_mgr = await _make_test_env(tmp_path, with_event_engine=False)
    tenant_corp = f"tenant_corp_{uuid.uuid4().hex}"

    for i in range(15):
        action = "doc.create" if i % 2 == 0 else "doc.delete"
        actor = "admin_1" if i < 10 else "admin_2"
        await audit_mgr.record_event(action, actor, "USER", tenant_corp)

    all_corp = await audit_mgr.get_audit_entries(tenant_corp, limit=50)
    assert len(all_corp) == 15


    # Filter by action
    creates = await audit_mgr.get_audit_entries(tenant_corp, action="doc.create")
    assert len(creates) == 8

    # Filter by actor
    admin2_actions = await audit_mgr.get_audit_entries(tenant_corp, actor_id="admin_2")
    assert len(admin2_actions) == 5

    # Pagination: limit & offset
    page1 = await audit_mgr.get_audit_entries(tenant_corp, limit=5, offset=0)
    page2 = await audit_mgr.get_audit_entries(tenant_corp, limit=5, offset=5)
    assert len(page1) == 5
    assert len(page2) == 5
    page1_ids = {e.audit_id for e in page1}
    page2_ids = {e.audit_id for e in page2}
    assert page1_ids.isdisjoint(page2_ids)



@pytest.mark.asyncio
async def test_query_validation_errors(tmp_path: Path) -> None:
    kernel, storage, _, audit_mgr = await _make_test_env(tmp_path, with_event_engine=False)

    with pytest.raises(AuditError, match="tenant_id"):
        await audit_mgr.get_audit_entries("")

    with pytest.raises(AuditError, match="audit_id and tenant_id"):
        await audit_mgr.get_audit_entry("", "tenant_1")

    with pytest.raises(AuditError, match="audit_id and tenant_id"):
        await audit_mgr.get_audit_entry("aud_1", "")


# -- SecurityEngine Integration Tests -----------------------------------------


@pytest.mark.asyncio
async def test_security_engine_audit_and_diagnostics(tmp_path: Path) -> None:
    kernel = Kernel()
    storage = StorageEngine(base_directory=str(tmp_path / "sec_diag_storage"))
    sec_engine = SecurityEngine(
        master_key=_TEST_MASTER_KEY,
        signing_private_key=_TEST_SIGNING_KEY,
    )

    kernel.register_engine(storage)
    kernel.register_engine(sec_engine)

    await storage.initialize(kernel)
    await storage.start()
    event = kernel.get_engine("event")
    await event.initialize(kernel)
    await event.start()
    await kernel.db.connect()
    await kernel.db.create_all_tables()


    # Before initialize, property access raises
    uninit_engine = SecurityEngine()
    with pytest.raises(SecurityEngineError, match="AuditManager is not initialized"):
        _ = uninit_engine.audit_manager

    await sec_engine.initialize(kernel)
    await sec_engine.start()

    # Health and diagnostics
    health = sec_engine.health()
    assert health["audit_implemented"] is True
    assert health["secret_store_implemented"] is True
    assert health["authentication_implemented"] is True
    assert health["authorization_implemented"] is True

    diag = sec_engine.diagnostics()
    assert diag["version"] == "0.6.0-m6"
    assert sec_engine.version() == "0.6.0-m6"

    # Record audit via facade property
    entry = await sec_engine.audit_manager.record_event(
        action="kortex.system.test",
        actor_id="sys_actor",
        actor_type="SYSTEM_ENGINE",
        tenant_id="default",
    )
    assert entry.audit_id is not None


@pytest.mark.asyncio
async def test_signature_verify_capability_and_facade(tmp_path: Path) -> None:
    kernel = Kernel()
    storage = StorageEngine(base_directory=str(tmp_path / "sec_sig_storage"))
    sec_engine = SecurityEngine(
        master_key=_TEST_MASTER_KEY,
        signing_private_key=_TEST_SIGNING_KEY,
    )

    kernel.register_engine(storage)
    kernel.register_engine(sec_engine)

    await storage.initialize(kernel)
    await storage.start()
    await kernel.db.connect()
    await kernel.db.create_all_tables()

    await sec_engine.initialize(kernel)
    await sec_engine.start()

    # Generate keypair and sign data
    crypto = LocalCrypto()
    priv, pub = crypto.generate_ed25519_keypair()
    payload = b"Immutable platform payload"
    sig_bytes = crypto.sign_ed25519(payload, priv)

    sig_obj = CryptographicSignature(
        algorithm="ed25519",
        signature=sig_bytes,
        public_key=pub,
    )

    # 1. Facade ISecurityEngine.verify_signature
    assert await sec_engine.verify_signature(payload, sig_obj) is True

    # Bad payload
    assert await sec_engine.verify_signature(b"Tampered payload", sig_obj) is False

    # 2. Capability handler `kortex.security.signature.verify`
    verify_handler = kernel._registry_engine.get_raw_handler_for_testing("kortex.security.signature.verify")
    assert verify_handler is not None

    # Call with CryptographicSignature
    res1 = await verify_handler(data=payload, signature=sig_obj)
    assert res1 is True

    # Call with dict
    sig_dict = {
        "algorithm": "ed25519",
        "signature": sig_bytes.hex(),
        "public_key": pub.hex(),
    }
    res2 = await verify_handler(data="Immutable platform payload", signature=sig_dict)
    assert res2 is True

    # Call with raw bytes & pubkey
    res3 = await verify_handler(data=payload, signature=sig_bytes, public_key=pub)
    assert res3 is True

    # Tampered / invalid calls (fail-closed)
    assert await verify_handler(data=payload, signature=b"bad_signature", public_key=pub) is False
    assert await verify_handler(data=payload, signature=sig_bytes, public_key=None) is False
    assert await verify_handler(data=None, signature=sig_obj) is False


# -- Audit Wiring: authenticate/authorize actually generate audit records ----


async def _seed_auth_principal(
    storage_engine: StorageEngine, tenant_id: str, principal_id: str, password: str, roles: list[str] | None = None
) -> None:
    credential_hash = PasswordHasher().hash(password)

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

    await storage_engine.data.execute_in_transaction(_action)


async def _grant_role_permission_for_audit_test(storage_engine: StorageEngine, role: str, permission: str) -> None:
    async def _action(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))

    await storage_engine.data.execute_in_transaction(_action)


async def _boot_kernel_with_security_and_event(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "audit_wiring_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    await kernel.boot()
    return kernel, storage_engine, security_engine


@pytest.mark.asyncio
async def test_authenticate_success_records_audit_entry_and_publishes_event(tmp_path: Path) -> None:
    kernel, storage, security_engine = await _boot_kernel_with_security_and_event(tmp_path)
    tenant_id = f"tenant-auth-ok-{uuid.uuid4().hex}"
    await _seed_auth_principal(storage, tenant_id, "principal-1", "correct-password")

    received: List[Event] = []
    event_engine = cast(EventEngine, kernel.get_engine("event"))
    event_engine.subscribe("kortex.event.security.auth.success", lambda evt: received.append(evt))

    principal = await security_engine.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": "principal-1", "password": "correct-password"}
    )
    assert principal.principal_id == "principal-1"

    entries = await security_engine.audit_manager.get_audit_entries(tenant_id)
    assert len(entries) == 1
    assert entries[0].action == "kortex.security.auth.authenticate"
    assert entries[0].actor_id == "principal-1"
    assert entries[0].actor_type == "HUMAN"  # PrincipalType.USER -> frozen vocabulary
    assert entries[0].context["result"] == "success"

    assert len(received) == 1
    assert received[0].payload["principal_id"] == "principal-1"


@pytest.mark.asyncio
async def test_authenticate_failure_records_audit_entry_and_publishes_event(tmp_path: Path) -> None:
    kernel, storage, security_engine = await _boot_kernel_with_security_and_event(tmp_path)
    tenant_id = f"tenant-auth-fail-{uuid.uuid4().hex}"
    await _seed_auth_principal(storage, tenant_id, "principal-1", "correct-password")

    received: List[Event] = []
    event_engine = cast(EventEngine, kernel.get_engine("event"))
    event_engine.subscribe("kortex.event.security.auth.failure", lambda evt: received.append(evt))

    with pytest.raises(Exception):
        await security_engine.authenticate(
            {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": "principal-1", "password": "wrong-password"}
        )

    entries = await security_engine.audit_manager.get_audit_entries(tenant_id)
    assert len(entries) == 1
    assert entries[0].context["result"] == "failure"
    assert len(received) == 1


@pytest.mark.asyncio
async def test_authorize_capability_dispatch_records_audit_entry_on_grant_and_deny(tmp_path: Path) -> None:
    """Exercises the actual registered capability handler for
    `kortex.security.access.authorize` (now `self.authorize`, not the raw
    `AuthorizationEngine.authorize`), proving the real dispatch path is
    audited, not merely the facade method in isolation."""
    kernel, storage, security_engine = await _boot_kernel_with_security_and_event(tmp_path)
    role = f"role-{uuid.uuid4().hex}"
    await _grant_role_permission_for_audit_test(storage, role, "document.write")
    raw_handler = kernel._registry_engine.get_raw_handler_for_testing("kortex.security.access.authorize")

    tenant_id = f"tenant-authz-{uuid.uuid4().hex}"
    principal = SecurityPrincipal(
        principal_id="p1", principal_type=PrincipalType.USER, tenant_id=tenant_id, roles=[role],
        attributes={"clearance_level": "INTERNAL"},
    )
    allowed_req = PermissionRequirement(capability_name="document.write", required_permissions=["document.write"])
    denied_req = PermissionRequirement(capability_name="document.delete", required_permissions=["document.delete"])

    decision_allow = await raw_handler(principal, allowed_req, {"resource_tenant_id": tenant_id})
    decision_deny = await raw_handler(principal, denied_req, {"resource_tenant_id": tenant_id})
    assert decision_allow.is_allowed is True
    assert decision_deny.is_allowed is False

    entries = await security_engine.audit_manager.get_audit_entries(tenant_id, limit=10)
    actions = {e.action for e in entries}
    assert actions == {"document.write", "document.delete"}
    for entry in entries:
        assert entry.actor_id == "p1"
        assert entry.actor_type == "HUMAN"


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed_credentials", ["not-a-dict", None, 12345, ["a", "list"]])
async def test_authenticate_facade_fails_closed_for_non_dict_credentials(
    tmp_path: Path, malformed_credentials: Any
) -> None:
    """Regression guard: the M6 audit-context extraction in `authenticate()`
    must not itself crash (e.g. `AttributeError` from calling `.get()` on a
    non-dict) ahead of `AuthenticationManager.authenticate`'s own required
    fail-closed `AuthenticationError` for malformed credential shapes."""
    kernel, _storage, security_engine = await _boot_kernel_with_security_and_event(tmp_path)

    from kortex.engines.security.exceptions import AuthenticationError

    with pytest.raises(AuthenticationError):
        await security_engine.authenticate(malformed_credentials)


@pytest.mark.asyncio
async def test_audit_persistence_failure_does_not_break_authenticate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An audit-store outage must not become a fail-closed lockout of
    authentication — audit recording is best-effort (Milestone M6 design
    decision, not a frozen mandate)."""
    kernel, storage, security_engine = await _boot_kernel_with_security_and_event(tmp_path)
    tenant_id = f"tenant-auditfail-{uuid.uuid4().hex}"
    await _seed_auth_principal(storage, tenant_id, "principal-1", "correct-password")

    async def _broken_record_event(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(security_engine.audit_manager, "record_event", _broken_record_event)

    principal = await security_engine.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": "principal-1", "password": "correct-password"}
    )
    assert principal.principal_id == "principal-1"
