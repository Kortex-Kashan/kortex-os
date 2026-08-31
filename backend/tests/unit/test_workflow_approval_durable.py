"""
KORTEX OS — Phase 5 / Milestone M5.3 Test Suite
Durable Approval Queue, Human Governance System, Concurrency, and Tenancy Verification.

Covers all 27 required verification categories:
1. durable ticket creation
2. retrieval
3. tenant-filtered listing
4. approval
5. rejection
6. sequential double decision
7. concurrent double decision
8. direct role authorization
9. active delegation
10. expired delegation rejection
11. inactive delegation rejection
12. valid Ed25519 signature
13. tampered signature rejection
14. wrong-key rejection
15. expiration sweep
16. expiration event generation
17. workflow failure/compensation after expiration
18. tenant isolation
19. secret sanitization
20. crash/restart recovery
21. approved-ticket recovery
22. rejected/expired recovery
23. transactional rollback
24. outbox atomicity
25. all five capability dispatch paths
26. capability authorization
27. API error mapping
"""

from __future__ import annotations

import asyncio
import http
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.errors import map_exception
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.exceptions import ResourceNotFoundError
from kortex.core.kernel import Kernel
from kortex.core.outbox import OutboxStore
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import (
    AuthorizationDeniedError,
    InvalidSignatureError,
)
from kortex.engines.security.models import (
    PrincipalRecord,
    PrincipalType,
    RolePermissionRecord,
    SecurityPrincipal,
)
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.approval import DurableApprovalManager
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.exceptions import (
    ApprovalConflictError,
    WorkflowApprovalError,
)
from kortex.engines.workflow.models import (
    ApprovalDecision,
    ApprovalDelegation,
    ApprovalRequest,
    ApprovalState,
    CompensationAction,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
)
from kortex.engines.workflow.persistence import (
    ApprovalRequestModel,
    ApprovalStore,
    WorkflowStore,
)

_TEST_MASTER_KEY = b"\xaa" * 32
_TEST_SIGNING_KEY = b"\xbb" * 32


# ============================================================================
# Test Fixtures
# ============================================================================


class ApprovalTestEnvironment:
    """Encapsulates isolated database, stores, and approval manager for unit testing."""

    def __init__(
        self,
        kernel: Kernel,
        db_manager: DatabaseEngineManager,
        data_store: RelationalDataStore,
        approval_store: ApprovalStore,
        outbox_store: OutboxStore,
        approval_manager: DurableApprovalManager,
        security_engine: SecurityEngine,
        local_crypto: LocalCrypto,
    ) -> None:
        self.kernel = kernel
        self.db_manager = db_manager
        self.data_store = data_store
        self.approval_store = approval_store
        self.outbox_store = outbox_store
        self.approval_manager = approval_manager
        self.security_engine = security_engine
        self.local_crypto = local_crypto


@pytest.fixture
async def durable_env(tmp_path: Path) -> AsyncGenerator[ApprovalTestEnvironment, None]:
    """Provide an isolated, in-memory SQLite ApprovalTestEnvironment with full engine wiring."""
    db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager

    storage_dir = tmp_path / f"storage_appr_{uuid4().hex[:8]}"
    storage_engine = StorageEngine(base_directory=str(storage_dir))
    security_engine = SecurityEngine(
        master_key=_TEST_MASTER_KEY,
        signing_private_key=_TEST_SIGNING_KEY,
    )

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    await kernel.boot()

    data_store = storage_engine.data
    approval_store = ApprovalStore(data_store)
    outbox_store = OutboxStore(data_store)
    local_crypto = LocalCrypto()

    approval_manager = DurableApprovalManager(
        data_store=data_store,
        security_engine=security_engine,
        outbox_store=outbox_store,
    )

    env = ApprovalTestEnvironment(
        kernel=kernel,
        db_manager=db_manager,
        data_store=data_store,
        approval_store=approval_store,
        outbox_store=outbox_store,
        approval_manager=approval_manager,
        security_engine=security_engine,
        local_crypto=local_crypto,
    )
    yield env

    await kernel.shutdown()
    if db_manager._engine:
        await db_manager._engine.dispose()


@pytest.fixture
async def file_backed_durable_env(tmp_path: Path) -> AsyncGenerator[ApprovalTestEnvironment, None]:
    """M6.4-1: a file-backed (not `:memory:`) ApprovalTestEnvironment,
    needed specifically for the genuine-concurrency race tests below.

    `sqlite+aiosqlite:///:memory:` gives every session its own private,
    disconnected in-memory database unless pooled onto a single shared
    connection (SQLAlchemy's `StaticPool`), and two `AsyncSession`s that
    share one physical DBAPI connection cannot each hold an independent,
    truly-concurrent transaction -- interleaving `asyncio.gather`ed writers
    against it produced results inconsistent with either writer's own view
    of what it had done (confirmed by manual reproduction while diagnosing
    this exact test). A real file-backed SQLite database, with each
    `execute_in_transaction` call getting a genuine, independently-lockable
    connection -- the same setup every M6.3 real-concurrency integration
    test already uses -- resolves this: exactly one concurrent writer wins,
    consistently, across repeated runs.
    """
    db_path = (tmp_path / f"kortex_appr_concurrent_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager

    storage_dir = tmp_path / f"storage_appr_concurrent_{uuid4().hex[:8]}"
    storage_engine = StorageEngine(base_directory=str(storage_dir))
    security_engine = SecurityEngine(
        master_key=_TEST_MASTER_KEY,
        signing_private_key=_TEST_SIGNING_KEY,
    )

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    await kernel.boot()

    data_store = storage_engine.data
    approval_store = ApprovalStore(data_store)
    outbox_store = OutboxStore(data_store)
    local_crypto = LocalCrypto()

    approval_manager = DurableApprovalManager(
        data_store=data_store,
        security_engine=security_engine,
        outbox_store=outbox_store,
    )

    env = ApprovalTestEnvironment(
        kernel=kernel,
        db_manager=db_manager,
        data_store=data_store,
        approval_store=approval_store,
        outbox_store=outbox_store,
        approval_manager=approval_manager,
        security_engine=security_engine,
        local_crypto=local_crypto,
    )
    yield env

    await kernel.shutdown()
    if db_manager._engine:
        await db_manager._engine.dispose()


# ============================================================================
# 1. Durable Ticket Creation & 2. Retrieval
# ============================================================================


@pytest.mark.asyncio
async def test_durable_ticket_creation_and_retrieval(durable_env: ApprovalTestEnvironment) -> None:
    """Categories 1 & 2: Verify durable ticket creation and multi-angle retrieval."""
    inst_id = uuid4()
    ticket = await durable_env.approval_manager.create_request(
        instance_id=inst_id,
        step_id="step_approval",
        required_role="FINANCE_MANAGER",
        tenant_id="tenant_alpha",
        timeout_seconds=3600,
        context_snapshot={"invoice_id": "INV-101", "amount": 5000},
        signature_required=True,
    )

    assert ticket.id is not None
    assert ticket.tenant_id == "tenant_alpha"
    assert ticket.instance_id == inst_id
    assert ticket.step_id == "step_approval"
    assert ticket.required_role == "FINANCE_MANAGER"
    assert ticket.state == ApprovalState.PENDING
    assert ticket.signature_required is True
    assert ticket.timeout_at is not None

    # Retrieve by ID
    fetched = await durable_env.approval_manager.get_request(ticket.id, tenant_id="tenant_alpha")
    assert fetched is not None
    assert fetched.id == ticket.id
    assert fetched.context_snapshot.get("invoice_id") == "INV-101"

    # Retrieve by Step
    fetched_step = await durable_env.approval_manager.get_request_by_step(
        inst_id, "step_approval", tenant_id="tenant_alpha"
    )
    assert fetched_step is not None
    assert fetched_step.id == ticket.id

    # Retrieve by Instance
    fetched_inst = await durable_env.approval_manager.get_request_by_instance(
        inst_id, tenant_id="tenant_alpha"
    )
    assert fetched_inst is not None
    assert fetched_inst.id == ticket.id


# ============================================================================
# 3. Tenant-Filtered Listing
# ============================================================================


@pytest.mark.asyncio
async def test_tenant_filtered_listing(durable_env: ApprovalTestEnvironment) -> None:
    """Category 3: Verify list_requests filters strictly by tenant and role."""
    await durable_env.approval_manager.create_request(
        required_role="FINANCE_MANAGER", tenant_id="tenant_1"
    )
    await durable_env.approval_manager.create_request(
        required_role="HR_MANAGER", tenant_id="tenant_1"
    )
    await durable_env.approval_manager.create_request(
        required_role="FINANCE_MANAGER", tenant_id="tenant_2"
    )

    # Tenant 1 should see 2 tickets
    t1_all = await durable_env.approval_manager.list_requests(tenant_id="tenant_1")
    assert len(t1_all) == 2

    # Tenant 1 with role filter
    t1_fin = await durable_env.approval_manager.list_requests(
        tenant_id="tenant_1", role_filter="FINANCE_MANAGER"
    )
    assert len(t1_fin) == 1
    assert t1_fin[0].required_role == "FINANCE_MANAGER"

    # Tenant 2 should see 1 ticket
    t2_all = await durable_env.approval_manager.list_requests(tenant_id="tenant_2")
    assert len(t2_all) == 1
    assert t2_all[0].tenant_id == "tenant_2"


# ============================================================================
# 4. Approval & 5. Rejection Decisions
# ============================================================================


@pytest.mark.asyncio
async def test_approval_and_rejection_decisions(durable_env: ApprovalTestEnvironment) -> None:
    """Categories 4 & 5: Verify valid APPROVED and REJECTED decision submissions."""
    # Test APPROVED
    req_appr = await durable_env.approval_manager.create_request(
        required_role="OPERATIONS", tenant_id="tenant_ops"
    )
    decision_appr = ApprovalDecision(
        request_id=req_appr.id,
        tenant_id="tenant_ops",
        approver_id="ops_user_1",
        decision=ApprovalState.APPROVED,
        reason="Looks good to execute.",
    )
    p_ops = SecurityPrincipal(
        principal_id="ops_user_1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_ops",
        roles=["OPERATIONS"],
    )
    res_appr = await durable_env.approval_manager.submit_decision(
        decision_appr, principal=p_ops, tenant_id="tenant_ops"
    )
    assert res_appr.state == ApprovalState.APPROVED

    persisted_dec = await durable_env.approval_store.get_decision(req_appr.id, tenant_id="tenant_ops")
    assert persisted_dec is not None
    assert persisted_dec.decision == ApprovalState.APPROVED
    assert persisted_dec.approver_id == "ops_user_1"

    # Test REJECTED
    req_rej = await durable_env.approval_manager.create_request(
        required_role="LEGAL", tenant_id="tenant_ops"
    )
    decision_rej = ApprovalDecision(
        request_id=req_rej.id,
        tenant_id="tenant_ops",
        approver_id="legal_user_1",
        decision=ApprovalState.REJECTED,
        reason="Clause 4 violated.",
    )
    p_legal = SecurityPrincipal(
        principal_id="legal_user_1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_ops",
        roles=["LEGAL"],
    )
    res_rej = await durable_env.approval_manager.submit_decision(
        decision_rej, principal=p_legal, tenant_id="tenant_ops"
    )
    assert res_rej.state == ApprovalState.REJECTED

    persisted_rej = await durable_env.approval_store.get_decision(req_rej.id, tenant_id="tenant_ops")
    assert persisted_rej is not None
    assert persisted_rej.decision == ApprovalState.REJECTED
    assert persisted_rej.reason == "Clause 4 violated."


# ============================================================================
# 6. Sequential & 7. Concurrent Double Decisions
# ============================================================================


@pytest.mark.asyncio
async def test_sequential_double_decision_rejection(durable_env: ApprovalTestEnvironment) -> None:
    """Category 6: Verify second sequential decision submission fails closed with Conflict."""
    ticket = await durable_env.approval_manager.create_request(
        required_role="ADMIN", tenant_id="tenant_sec"
    )
    decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_sec",
        approver_id="admin_1",
        decision=ApprovalState.APPROVED,
    )
    p_admin = SecurityPrincipal(
        principal_id="admin_1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_sec",
        roles=["ADMIN"],
    )
    await durable_env.approval_manager.submit_decision(decision, principal=p_admin, tenant_id="tenant_sec")

    # Second decision must raise ApprovalConflictError
    with pytest.raises(ApprovalConflictError, match="already in state 'APPROVED'"):
        await durable_env.approval_manager.submit_decision(decision, principal=p_admin, tenant_id="tenant_sec")


@pytest.mark.asyncio
async def test_concurrent_double_decision_rejection(durable_env: ApprovalTestEnvironment) -> None:
    """Category 7: Verify concurrent competing decisions result in exactly 1 winner and 1 conflict."""
    ticket = await durable_env.approval_manager.create_request(
        required_role="FINANCE", tenant_id="tenant_conc"
    )

    decision_1 = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_conc",
        approver_id="fin_1",
        decision=ApprovalState.APPROVED,
    )
    decision_2 = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_conc",
        approver_id="fin_2",
        decision=ApprovalState.REJECTED,
    )
    p_fin1 = SecurityPrincipal(
        principal_id="fin_1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_conc",
        roles=["FINANCE"],
    )
    p_fin2 = SecurityPrincipal(
        principal_id="fin_2",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_conc",
        roles=["FINANCE"],
    )

    results = await asyncio.gather(
        durable_env.approval_manager.submit_decision(decision_1, principal=p_fin1, tenant_id="tenant_conc"),
        durable_env.approval_manager.submit_decision(decision_2, principal=p_fin2, tenant_id="tenant_conc"),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, ApprovalRequest)]
    conflicts = [r for r in results if isinstance(r, (ApprovalConflictError, WorkflowApprovalError))]

    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(conflicts) == 1, f"Expected exactly 1 conflict, got {len(conflicts)}"


# ============================================================================
# 8. Direct Role Authorization & 9, 10, 11. Delegations
# ============================================================================


@pytest.mark.asyncio
async def test_role_authorization_and_delegation_mechanics(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """Categories 8, 9, 10, 11: Verify role matching, active delegations, and expired/inactive rejection."""
    ticket = await durable_env.approval_manager.create_request(
        required_role="FINANCE_DIRECTOR", tenant_id="tenant_corp"
    )

    # 8. Direct Role Authorization: Principal lacking role is rejected
    unauthorized_principal = SecurityPrincipal(
        principal_id="user_clerk",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_corp",
        roles=["CLERK"],
    )
    decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_corp",
        approver_id="user_clerk",
        decision=ApprovalState.APPROVED,
    )
    with pytest.raises(AuthorizationDeniedError, match="lacks required role"):
        await durable_env.approval_manager.submit_decision(
            decision, principal=unauthorized_principal, tenant_id="tenant_corp"
        )

    # 10. Expired Delegation Rejection
    now = datetime.now(UTC)
    director_principal = SecurityPrincipal(
        principal_id="user_director",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_corp",
        roles=["FINANCE_DIRECTOR"],
    )
    await durable_env.approval_manager.create_delegation(
        delegator_id="user_director",
        delegatee_id="user_clerk",
        role="FINANCE_DIRECTOR",
        valid_from=now - timedelta(days=5),
        valid_until=now - timedelta(days=1),  # Expired
        tenant_id="tenant_corp",
        principal=director_principal,
    )
    with pytest.raises(AuthorizationDeniedError, match="lacks required role"):
        await durable_env.approval_manager.submit_decision(
            decision, principal=unauthorized_principal, tenant_id="tenant_corp"
        )

    # 11. Inactive Delegation Rejection
    inactive_delegation = ApprovalDelegation(
        tenant_id="tenant_corp",
        delegator_id="user_director",
        delegatee_id="user_clerk",
        role="FINANCE_DIRECTOR",
        valid_from=now - timedelta(hours=1),
        valid_until=now + timedelta(hours=1),
        is_active=False,  # Deactivated
    )
    await durable_env.approval_store.save_delegation(inactive_delegation, tenant_id="tenant_corp")
    with pytest.raises(AuthorizationDeniedError, match="lacks required role"):
        await durable_env.approval_manager.submit_decision(
            decision, principal=unauthorized_principal, tenant_id="tenant_corp"
        )

    # 9. Active Delegation Authorization: Valid active delegation authorizes the clerk
    await durable_env.approval_manager.create_delegation(
        delegator_id="user_director",
        delegatee_id="user_clerk",
        role="FINANCE_DIRECTOR",
        valid_from=now - timedelta(hours=1),
        valid_until=now + timedelta(hours=1),  # Currently active
        tenant_id="tenant_corp",
        principal=director_principal,
    )
    approved_ticket = await durable_env.approval_manager.submit_decision(
        decision, principal=unauthorized_principal, tenant_id="tenant_corp"
    )
    assert approved_ticket.state == ApprovalState.APPROVED


# ============================================================================
# 12, 13, 14. Ed25519 Cryptographic Signatures
# ============================================================================


@pytest.mark.asyncio
async def test_cryptographic_signature_verification(durable_env: ApprovalTestEnvironment) -> None:
    """Categories 12, 13, 14: Valid signature, tampered signature, wrong public key, and identity binding."""
    # Generate Ed25519 keypair for approver
    priv_bytes, pub_bytes = durable_env.local_crypto.generate_ed25519_keypair()
    pub_hex = pub_bytes.hex()

    ticket = await durable_env.approval_manager.create_request(
        required_role="CFO", tenant_id="tenant_fin", signature_required=True
    )

    decided_at = datetime.now(UTC)
    approver_id = "cfo_executive"
    canonical_payload = f"{ticket.id}:APPROVED:{approver_id}:{decided_at.isoformat()}".encode()

    # Authoritative CFO principal registered with public key attribute
    cfo_principal = SecurityPrincipal(
        principal_id=approver_id,
        principal_type=PrincipalType.USER,
        tenant_id="tenant_fin",
        roles=["CFO"],
        attributes={"public_key": pub_hex},
    )

    # Sign canonical payload with private key
    sig_bytes = durable_env.local_crypto.sign_ed25519(canonical_payload, priv_bytes)
    sig_hex = sig_bytes.hex()

    # 13. Tampered Signature Rejection: Corrupt one byte of the signature
    corrupted_sig_bytes = bytearray(sig_bytes)
    corrupted_sig_bytes[0] ^= 0xFF
    corrupted_decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_fin",
        approver_id=approver_id,
        decision=ApprovalState.APPROVED,
        signature_hex=bytes(corrupted_sig_bytes).hex(),
        public_key_hex=pub_hex,
        decided_at=decided_at,
    )
    with pytest.raises(InvalidSignatureError, match="verification failed"):
        await durable_env.approval_manager.submit_decision(
            corrupted_decision, principal=cfo_principal, tenant_id="tenant_fin"
        )

    # 14. Wrong-Key Rejection: Signed with key A, validated against key B
    _, other_pub_bytes = durable_env.local_crypto.generate_ed25519_keypair()
    wrong_key_decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_fin",
        approver_id=approver_id,
        decision=ApprovalState.APPROVED,
        signature_hex=sig_hex,
        public_key_hex=other_pub_bytes.hex(),
        decided_at=decided_at,
    )
    with pytest.raises(InvalidSignatureError, match="Provided public key does not match authoritative"):
        await durable_env.approval_manager.submit_decision(
            wrong_key_decision, principal=cfo_principal, tenant_id="tenant_fin"
        )

    # 14b. Key-to-Identity Binding Rejection: Attacker signs payload with their own key and passes own key
    attacker_principal = SecurityPrincipal(
        principal_id="attacker_user",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_fin",
        roles=["CFO"],
        attributes={"public_key": other_pub_bytes.hex()},
    )
    attacker_payload = f"{ticket.id}:APPROVED:attacker_user:{decided_at.isoformat()}".encode()
    attacker_sig = durable_env.local_crypto.sign_ed25519(attacker_payload, _)
    attacker_spoofed_decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_fin",
        approver_id=approver_id,  # Trying to claim cfo_executive
        decision=ApprovalState.APPROVED,
        signature_hex=attacker_sig.hex(),
        public_key_hex=other_pub_bytes.hex(),
        decided_at=decided_at,
    )
    with pytest.raises(AuthorizationDeniedError, match="Approver ID 'cfo_executive' does not match"):
        await durable_env.approval_manager.submit_decision(
            attacker_spoofed_decision, principal=attacker_principal, tenant_id="tenant_fin"
        )

    # 12. Valid Signature: Correct payload, key, and signature passes
    valid_decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_fin",
        approver_id=approver_id,
        decision=ApprovalState.APPROVED,
        signature_hex=sig_hex,
        public_key_hex=pub_hex,
        decided_at=decided_at,
    )
    res = await durable_env.approval_manager.submit_decision(
        valid_decision, principal=cfo_principal, tenant_id="tenant_fin"
    )
    assert res.state == ApprovalState.APPROVED


# ============================================================================
# 15. Expiration Sweep, 16. Outbox Events & 17. Compensation
# ============================================================================


@pytest.mark.asyncio
async def test_expiration_sweep_and_events(durable_env: ApprovalTestEnvironment) -> None:
    """Categories 15 & 16: Expiration sweep transitions timed-out tickets and stages outbox events."""
    # Create ticket that expired 10 seconds ago
    ticket = await durable_env.approval_manager.create_request(
        required_role="MANAGER",
        tenant_id="tenant_exp",
        timeout_seconds=-10,
    )

    expired_list = await durable_env.approval_manager.sweep_expired_requests(tenant_id="tenant_exp")
    assert len(expired_list) == 1
    assert expired_list[0].id == ticket.id
    assert expired_list[0].state == ApprovalState.EXPIRED

    # Check outbox for expiration event
    pending_events = await durable_env.outbox_store.get_pending_events(tenant_id="tenant_exp")
    topics = [e.topic for e in pending_events]
    assert "workflow.approval.expired" in topics


# ============================================================================
# M6.4-1: Expiry Propagation via workflow.approval.decided
# ============================================================================


@pytest.mark.asyncio
async def test_sweep_publishes_decided_event_with_expired_decision(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """A live `workflow.approval.decided` event, decision=EXPIRED, is
    published on the SAME topic/contract as a human APPROVED/REJECTED
    decision -- not the separate, never-delivered `workflow.approval.expired`
    outbox event. Every value comes from the ticket's own persisted record."""
    manager = DurableApprovalManager(
        data_store=durable_env.data_store,
        security_engine=durable_env.security_engine,
        outbox_store=durable_env.outbox_store,
        event_engine=durable_env.kernel.get_engine("event"),
    )
    ticket = await manager.create_request(
        required_role="MANAGER",
        tenant_id="tenant_exp_evt",
        timeout_seconds=-10,
        correlation_id="corr-expiry-1",
        context_snapshot={"action": "external_execution", "execution_id": "exec-42"},
    )

    received: list[Any] = []
    durable_env.kernel.subscribe_event(
        "workflow.approval.decided", lambda event: received.append(event), subscriber_name="test-spy"
    )

    expired_list = await manager.sweep_expired_requests(tenant_id="tenant_exp_evt")
    assert len(expired_list) == 1

    assert len(received) == 1
    payload = received[0].payload
    assert payload["request_id"] == str(ticket.id)
    assert payload["tenant_id"] == "tenant_exp_evt"
    assert payload["decision"] == "EXPIRED"
    assert payload["correlation_id"] == "corr-expiry-1"
    assert payload["context_snapshot"]["execution_id"] == "exec-42"
    # No human decider exists at sweep time -- no token to mint, none to leak.
    assert "decider_session_token" not in payload


@pytest.mark.asyncio
async def test_sweep_with_no_event_engine_does_not_raise(durable_env: ApprovalTestEnvironment) -> None:
    """`durable_env.approval_manager` is constructed WITHOUT an event_engine
    (the pre-existing fixture default) -- the sweep must still work exactly
    as before, silently skipping publication rather than erroring."""
    ticket = await durable_env.approval_manager.create_request(
        required_role="MANAGER", tenant_id="tenant_no_evt", timeout_seconds=-10
    )
    expired_list = await durable_env.approval_manager.sweep_expired_requests(tenant_id="tenant_no_evt")
    assert len(expired_list) == 1
    assert expired_list[0].id == ticket.id


@pytest.mark.asyncio
async def test_duplicate_sweep_does_not_republish_for_already_expired_ticket(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """A second sweep call over the same (now-EXPIRED) ticket must not
    publish a second event -- `atomic_expire_request`'s state==PENDING
    guard means the ticket is simply absent from the second sweep's result."""
    manager = DurableApprovalManager(
        data_store=durable_env.data_store,
        security_engine=durable_env.security_engine,
        outbox_store=durable_env.outbox_store,
        event_engine=durable_env.kernel.get_engine("event"),
    )
    await manager.create_request(required_role="MANAGER", tenant_id="tenant_dup_sweep", timeout_seconds=-10)

    received: list[Any] = []
    durable_env.kernel.subscribe_event(
        "workflow.approval.decided", lambda event: received.append(event), subscriber_name="test-spy"
    )

    first = await manager.sweep_expired_requests(tenant_id="tenant_dup_sweep")
    second = await manager.sweep_expired_requests(tenant_id="tenant_dup_sweep")

    assert len(first) == 1
    assert len(second) == 0
    assert len(received) == 1


@pytest.mark.asyncio
async def test_concurrent_sweep_workers_expire_and_publish_exactly_once(
    file_backed_durable_env: ApprovalTestEnvironment,
) -> None:
    """Two 'workers' racing to sweep the same expired ticket concurrently
    must transition it exactly once and publish exactly one event -- proven
    against the real DB-level atomic UPDATE-WHERE-status guard, not an
    application-level lock. Uses `file_backed_durable_env` (not `:memory:`)
    for genuine, independently-lockable concurrent transactions -- see that
    fixture's docstring."""
    env = file_backed_durable_env
    manager = DurableApprovalManager(
        data_store=env.data_store,
        security_engine=env.security_engine,
        outbox_store=env.outbox_store,
        event_engine=env.kernel.get_engine("event"),
    )
    await manager.create_request(required_role="MANAGER", tenant_id="tenant_concurrent_sweep", timeout_seconds=-10)

    received: list[Any] = []
    env.kernel.subscribe_event(
        "workflow.approval.decided", lambda event: received.append(event), subscriber_name="test-spy"
    )

    results = await asyncio.gather(
        manager.sweep_expired_requests(tenant_id="tenant_concurrent_sweep"),
        manager.sweep_expired_requests(tenant_id="tenant_concurrent_sweep"),
    )
    total_expired = sum(len(r) for r in results)
    assert total_expired == 1
    assert len(received) == 1


@pytest.mark.asyncio
async def test_approve_wins_race_against_expire_no_event_published_for_loser(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """The reverse ordering of the existing
    `test_adversarial_expiration_race_and_hydration_idempotency` case: a
    ticket is decided (APPROVED) first, then a sweep attempts to expire the
    same, already-timed-out ticket. The sweep must find nothing to expire
    and must not publish a second, conflicting EXPIRED event for a ticket
    that already has a real decision."""
    manager = DurableApprovalManager(
        data_store=durable_env.data_store,
        security_engine=durable_env.security_engine,
        outbox_store=durable_env.outbox_store,
        event_engine=durable_env.kernel.get_engine("event"),
    )
    approver = SecurityPrincipal(
        principal_id="mgr_1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_race2",
        roles=["MANAGER"],
    )
    ticket = await manager.create_request(
        required_role="MANAGER", tenant_id="tenant_race2", timeout_seconds=-5
    )

    received: list[Any] = []
    durable_env.kernel.subscribe_event(
        "workflow.approval.decided", lambda event: received.append(event), subscriber_name="test-spy"
    )

    decision = ApprovalDecision(
        request_id=ticket.id, tenant_id="tenant_race2", approver_id="mgr_1", decision=ApprovalState.APPROVED
    )
    decided = await manager.submit_decision(decision, principal=approver, tenant_id="tenant_race2")
    assert decided.state == ApprovalState.APPROVED

    expired_list = await manager.sweep_expired_requests(tenant_id="tenant_race2")
    assert expired_list == []

    # submit_decision's own outbox staging aside, no EXPIRED event was
    # published by the losing sweep -- only the (unrelated) fact that no
    # spy-visible event fired from the sweep call itself.
    assert all(e.payload.get("decision") != "EXPIRED" for e in received)


async def _run_true_concurrent_decide_vs_expire(
    env: ApprovalTestEnvironment, tenant_id: str, decision_state: ApprovalState
) -> None:
    """Shared driver for the two true-concurrency decide-vs-expire tests
    below: `asyncio.gather` genuinely interleaves `submit_decision` and
    `sweep_expired_requests` against the same already-timed-out ticket, so
    both can pass their own initial PENDING check before either's atomic
    UPDATE actually runs -- exactly the window `atomic_submit_decision` and
    `atomic_expire_request`'s conditional-UPDATE fix (M6.4-1) must close.
    Exactly one of the two must win; the ticket's final persisted state must
    match whichever one did, never a corrupted mix of both. Takes a
    `file_backed_durable_env` (not `:memory:`) -- see that fixture's
    docstring for why genuine concurrency needs a real file-backed DB."""
    manager = DurableApprovalManager(
        data_store=env.data_store,
        security_engine=env.security_engine,
        outbox_store=env.outbox_store,
        event_engine=env.kernel.get_engine("event"),
    )
    approver = SecurityPrincipal(
        principal_id="mgr_concurrent", principal_type=PrincipalType.USER, tenant_id=tenant_id, roles=["MANAGER"]
    )
    ticket = await manager.create_request(required_role="MANAGER", tenant_id=tenant_id, timeout_seconds=-5)
    decision = ApprovalDecision(
        request_id=ticket.id, tenant_id=tenant_id, approver_id="mgr_concurrent", decision=decision_state
    )

    async def _try_decide() -> ApprovalRequest | None:
        try:
            return await manager.submit_decision(decision, principal=approver, tenant_id=tenant_id)
        except ApprovalConflictError:
            return None

    decide_result, expire_result = await asyncio.gather(
        _try_decide(), manager.sweep_expired_requests(tenant_id=tenant_id)
    )

    final = await manager.get_request(ticket.id, tenant_id=tenant_id)
    assert final is not None

    if decide_result is not None:
        # Decide won the race: the ticket carries the real decision, and
        # the concurrent sweep found nothing left to expire.
        assert final.state == decision_state
        assert expire_result == []
    else:
        # Expire won the race: the ticket is EXPIRED, and the concurrent
        # decision was refused rather than silently corrupting it back.
        assert final.state == ApprovalState.EXPIRED
        assert len(expire_result) == 1
        assert expire_result[0].id == ticket.id

    # Whichever side won, exactly one outcome is true -- the two must be
    # mutually exclusive, never both.
    assert (decide_result is not None) != (len(expire_result) == 1)


@pytest.mark.asyncio
async def test_concurrent_approve_vs_expire_exactly_one_wins(
    file_backed_durable_env: ApprovalTestEnvironment,
) -> None:
    await _run_true_concurrent_decide_vs_expire(
        file_backed_durable_env, "tenant_race_approve_expire", ApprovalState.APPROVED
    )


@pytest.mark.asyncio
async def test_concurrent_reject_vs_expire_exactly_one_wins(
    file_backed_durable_env: ApprovalTestEnvironment,
) -> None:
    await _run_true_concurrent_decide_vs_expire(
        file_backed_durable_env, "tenant_race_reject_expire", ApprovalState.REJECTED
    )


# ============================================================================
# 18. Strict Multi-Tenant Isolation
# ============================================================================


@pytest.mark.asyncio
async def test_strict_multi_tenant_isolation(durable_env: ApprovalTestEnvironment) -> None:
    """Category 18: Tenant A tickets are completely invisible and unmodifiable by Tenant B."""
    ticket_a = await durable_env.approval_manager.create_request(
        required_role="SUPERVISOR", tenant_id="tenant_alpha"
    )

    # Tenant B tries to get Tenant A's ticket -> None
    res = await durable_env.approval_manager.get_request(ticket_a.id, tenant_id="tenant_beta")
    assert res is None

    # Tenant B tries to decide Tenant A's ticket -> Fails closed
    decision = ApprovalDecision(
        request_id=ticket_a.id,
        tenant_id="tenant_beta",
        approver_id="intruder_user",
        decision=ApprovalState.APPROVED,
    )
    with pytest.raises(WorkflowApprovalError, match="not found"):
        await durable_env.approval_manager.submit_decision(decision, tenant_id="tenant_beta")


# ============================================================================
# 19. Secret Sanitization
# ============================================================================


@pytest.mark.asyncio
async def test_secret_sanitization_in_snapshots(durable_env: ApprovalTestEnvironment) -> None:
    """Category 19: Nested passwords, session tokens, and keys are scrubbed before persistence."""
    dirty_context = {
        "user_email": "finance@kortex.os",
        "password": "SuperSecretPassword123!",
        "session_token": "tok_live_12345",
        "api_key": "kortex_sec_abcde",
        "nested": {
            "bearer_token": "Bearer xyz",
            "access_token": "acc_123",
            "refresh_token": "ref_456",
            "safe_counter": 42,
        },
        "list_items": [
            {"credential": "cred_val", "public_id": 999},
        ],
    }

    ticket = await durable_env.approval_manager.create_request(
        required_role="AUDITOR",
        tenant_id="tenant_scrub",
        context_snapshot=dirty_context,
    )

    # Read raw JSON from SQLite database row
    async def _action(session: AsyncSession) -> str:
        stmt = select(ApprovalRequestModel.context_snapshot_json).where(
            ApprovalRequestModel.id == str(ticket.id)
        )
        return await session.scalar(stmt) or "{}"

    raw_json = await durable_env.data_store.execute_in_transaction(_action)

    assert "SuperSecretPassword123!" not in raw_json
    assert "tok_live_12345" not in raw_json
    assert "kortex_sec_abcde" not in raw_json
    assert "Bearer xyz" not in raw_json
    assert "acc_123" not in raw_json
    assert "ref_456" not in raw_json
    assert "cred_val" not in raw_json

    assert "finance@kortex.os" in raw_json
    assert "safe_counter" in raw_json
    assert "999" in raw_json


# ============================================================================
# 20, 21, 22. Crash Recovery & 17. Workflow Integration
# ============================================================================


@pytest.mark.asyncio
async def test_crash_recovery_scenarios(tmp_path: Path) -> None:
    """Categories 17, 20, 21, 22: WAITING workflows recover pending, approved, or expired tickets."""
    db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    await db_manager.connect()
    await db_manager.create_all_tables()
    data_store = RelationalDataStore(db_manager)
    workflow_store = WorkflowStore(data_store)
    outbox_store = OutboxStore(data_store)

    appr_mgr = DurableApprovalManager(data_store=data_store, outbox_store=outbox_store)

    engine = WorkflowEngine()
    engine.set_workflow_store(workflow_store)
    engine.set_approval_manager(appr_mgr)

    # Define a workflow with an approval step
    step_1 = WorkflowStep(id="step_prep", name="Prep", handler_capability="noop")
    step_2 = WorkflowStep(
        id="step_gate",
        name="Approval Gate",
        is_approval_step=True,
        required_approval_role="ADMIN",
    )
    step_3 = WorkflowStep(id="step_finalize", name="Finalize", handler_capability="noop")
    wf_def = WorkflowDefinition(
        id="wf_durability_def",
        name="Durable Gate Workflow",
        tenant_id="tenant_rec",
        steps=[step_1, step_2, step_3],
    )
    await workflow_store.save_definition(wf_def, tenant_id="tenant_rec")
    engine.register_definition(wf_def, tenant_id="tenant_rec")

    # 20. Case A: Workflow waiting at approval step recovers and stays WAITING
    inst_pending = WorkflowInstance(
        definition_id=wf_def.id,
        tenant_id="tenant_rec",
        current_step_index=1,
        current_step_id="step_gate",
        state=WorkflowState.WAITING,
        status=WorkflowStatus.WAITING_APPROVAL,
    )
    await workflow_store.save_instance(inst_pending, tenant_id="tenant_rec")
    # Create durable pending ticket
    await appr_mgr.create_request(
        instance_id=inst_pending.id,
        step_id="step_gate",
        required_role="ADMIN",
        tenant_id="tenant_rec",
    )

    recovered = await engine.hydrate_and_recover(tenant_id="tenant_rec")
    rec_p = next((i for i in recovered if i.id == inst_pending.id), None)
    assert rec_p is not None
    assert rec_p.state == WorkflowState.WAITING

    # 21. Case B: Workflow was waiting, but ticket was APPROVED while offline -> resumes
    inst_approved = WorkflowInstance(
        definition_id=wf_def.id,
        tenant_id="tenant_rec",
        current_step_index=1,
        current_step_id="step_gate",
        state=WorkflowState.WAITING,
        status=WorkflowStatus.WAITING_APPROVAL,
    )
    await workflow_store.save_instance(inst_approved, tenant_id="tenant_rec")
    ticket_appr = await appr_mgr.create_request(
        instance_id=inst_approved.id,
        step_id="step_gate",
        required_role="ADMIN",
        tenant_id="tenant_rec",
    )
    p_admin = SecurityPrincipal(
        principal_id="offline_admin",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_rec",
        roles=["ADMIN"],
    )
    # Offline approval
    await appr_mgr.submit_decision(
        ApprovalDecision(
            request_id=ticket_appr.id,
            tenant_id="tenant_rec",
            approver_id="offline_admin",
            decision=ApprovalState.APPROVED,
        ),
        principal=p_admin,
        tenant_id="tenant_rec",
    )

    recovered = await engine.hydrate_and_recover(tenant_id="tenant_rec")
    rec_a = next((i for i in recovered if i.id == inst_approved.id), None)
    assert rec_a is not None
    assert rec_a.current_step_index >= 2  # Advanced past the approval step

    # 22. Case C: Ticket was REJECTED/EXPIRED while offline -> transitions to FAILED
    inst_rej = WorkflowInstance(
        definition_id=wf_def.id,
        tenant_id="tenant_rec",
        current_step_index=1,
        current_step_id="step_gate",
        state=WorkflowState.WAITING,
        status=WorkflowStatus.WAITING_APPROVAL,
    )
    await workflow_store.save_instance(inst_rej, tenant_id="tenant_rec")
    ticket_rej = await appr_mgr.create_request(
        instance_id=inst_rej.id,
        step_id="step_gate",
        required_role="ADMIN",
        tenant_id="tenant_rec",
    )
    await appr_mgr.submit_decision(
        ApprovalDecision(
            request_id=ticket_rej.id,
            tenant_id="tenant_rec",
            approver_id="offline_admin",
            decision=ApprovalState.REJECTED,
        ),
        principal=p_admin,
        tenant_id="tenant_rec",
    )

    recovered = await engine.hydrate_and_recover(tenant_id="tenant_rec")
    rec_r = next((i for i in recovered if i.id == inst_rej.id), None)
    assert rec_r is not None
    assert rec_r.state == WorkflowState.FAILED

    # 17. Expiration Sweep workflow failure & compensation execution
    inst_exp = WorkflowInstance(
        definition_id=wf_def.id,
        tenant_id="tenant_rec",
        current_step_index=1,
        current_step_id="step_gate",
        state=WorkflowState.WAITING,
        status=WorkflowStatus.WAITING_APPROVAL,
    )
    inst_exp.compensation_stack.append(
        CompensationAction(name="rollback_prep", capability_name="noop")
    )
    await workflow_store.save_instance(inst_exp, tenant_id="tenant_rec")
    await appr_mgr.create_request(
        instance_id=inst_exp.id,
        step_id="step_gate",
        required_role="ADMIN",
        tenant_id="tenant_rec",
        timeout_seconds=-5,
    )
    engine._instances[inst_exp.id] = inst_exp

    expired_results = await engine.sweep_expired_approvals(tenant_id="tenant_rec")
    assert len(expired_results) >= 1
    assert inst_exp.state == WorkflowState.FAILED

    await db_manager._engine.dispose()


# ============================================================================
# 23. Transactional Rollback & 24. Outbox Atomicity
# ============================================================================


@pytest.mark.asyncio
async def test_transactional_rollback_and_outbox_atomicity(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """Categories 23 & 24: Failed transactions leave zero partial updates and zero outbox events."""
    ticket = await durable_env.approval_manager.create_request(
        required_role="AUDIT", tenant_id="tenant_atom"
    )

    # Initial outbox events count
    initial_events = await durable_env.outbox_store.get_pending_events(tenant_id="tenant_atom")
    initial_count = len(initial_events)

    # Intentionally submit an invalid decision that triggers an error during processing
    invalid_decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_atom",
        approver_id="bad_user",
        decision=ApprovalState.PENDING,  # PENDING is invalid as decision
    )

    with pytest.raises(WorkflowApprovalError):
        await durable_env.approval_manager.submit_decision(invalid_decision, tenant_id="tenant_atom")

    # Verify ticket state is unchanged
    refetched = await durable_env.approval_manager.get_request(ticket.id, tenant_id="tenant_atom")
    assert refetched is not None
    assert refetched.state == ApprovalState.PENDING

    # Verify outbox event was NOT staged for the failed decision
    after_events = await durable_env.outbox_store.get_pending_events(tenant_id="tenant_atom")
    assert len(after_events) == initial_count


# ============================================================================
# 25, 26. Capability Dispatch Paths & Authorization
# ============================================================================


@pytest.mark.asyncio
async def test_all_five_capability_dispatch_paths(tmp_path: Path) -> None:
    """Categories 25 & 26: Invoke all 5 kortex.workflow.approval.* capabilities through Kernel."""
    kernel = Kernel()
    db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    await db_manager.connect()
    await db_manager.create_all_tables()
    kernel._db_manager = db_manager

    storage_dir = tmp_path / "storage_cap"
    storage_engine = StorageEngine(base_directory=str(storage_dir))
    security_engine = SecurityEngine(
        master_key=_TEST_MASTER_KEY,
        signing_private_key=_TEST_SIGNING_KEY,
    )
    workflow_engine = WorkflowEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(workflow_engine)
    await kernel.boot()

    # Pre-register permissions in RBAC table
    hasher = PasswordHasher()
    async def _seed_rbac(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role="APPROVER_ROLE", permission="approval:write"))
        session.add(RolePermissionRecord(id=str(uuid4()), role="APPROVER_ROLE", permission="approval:read"))
        session.add(RolePermissionRecord(id=str(uuid4()), role="VIEWER_ROLE", permission="approval:read"))
        # Seed test principals
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id="tenant_cap",
                principal_id="user_writer",
                principal_type="USER",
                credential_hash=hasher.hash("pass123"),
                roles=["APPROVER_ROLE", "FINANCE_DEPT"],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id="tenant_cap",
                principal_id="user_reader",
                principal_type="USER",
                credential_hash=hasher.hash("pass123"),
                roles=["VIEWER_ROLE"],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        # M6.2-3: a distinct approver identity, separate from the ticket's
        # own requester ("user_writer") -- self-approval is now denied
        # (see `test_self_approval_denied_even_with_correct_role`), so the
        # capability-dispatch happy path below must decide via a different,
        # equally-permissioned principal, matching real separation-of-duties
        # practice rather than incidentally reusing the requester's own
        # token for convenience.
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id="tenant_cap",
                principal_id="user_approver",
                principal_type="USER",
                credential_hash=hasher.hash("pass123"),
                roles=["APPROVER_ROLE", "FINANCE_DEPT"],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await storage_engine.data.execute_in_transaction(_seed_rbac)

    p_writer = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": "tenant_cap", "principal_id": "user_writer", "password": "pass123"}
    )
    writer_token_payload = await security_engine.authentication_manager.issue_token(p_writer)

    p_approver = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": "tenant_cap", "principal_id": "user_approver", "password": "pass123"}
    )
    approver_token_payload = await security_engine.authentication_manager.issue_token(p_approver)

    p_reader = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": "tenant_cap", "principal_id": "user_reader", "password": "pass123"}
    )
    reader_token_payload = await security_engine.authentication_manager.issue_token(p_reader)

    # 1. Capability: kortex.workflow.approval.create
    req_create = CapabilityRequest(
        capability_name="kortex.workflow.approval.create",
        session_token=writer_token_payload,
        parameters={
            "required_role": "FINANCE_DEPT",
            "tenant_id": "tenant_cap",
        },
        context={"resource_tenant_id": "tenant_cap"},
    )
    res_create = await kernel.invoke_capability(req_create)
    assert res_create is not None
    ticket_id = res_create["id"]

    # 2. Capability: kortex.workflow.approval.list
    req_list = CapabilityRequest(
        capability_name="kortex.workflow.approval.list",
        session_token=reader_token_payload,
        parameters={"tenant_id": "tenant_cap"},
        context={"resource_tenant_id": "tenant_cap"},
    )
    res_list = await kernel.invoke_capability(req_list)
    assert len(res_list) >= 1

    # 3. Capability: kortex.workflow.approval.get
    req_get = CapabilityRequest(
        capability_name="kortex.workflow.approval.get",
        session_token=reader_token_payload,
        parameters={"request_id": ticket_id, "tenant_id": "tenant_cap"},
        context={"resource_tenant_id": "tenant_cap"},
    )
    res_get = await kernel.invoke_capability(req_get)
    assert res_get["id"] == ticket_id

    # 4. Capability Authorization: Reader lacks approval:write -> Denied
    req_unauth_decide = CapabilityRequest(
        capability_name="kortex.workflow.approval.decide",
        session_token=reader_token_payload,
        parameters={
            "request_id": ticket_id,
            "decision": "APPROVED",
            "approver_id": "user_reader",
            "tenant_id": "tenant_cap",
        },
        context={"resource_tenant_id": "tenant_cap"},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(req_unauth_decide)

    # 5. Capability: kortex.workflow.approval.delegate
    now = datetime.now(UTC)
    req_delegate = CapabilityRequest(
        capability_name="kortex.workflow.approval.delegate",
        session_token=writer_token_payload,
        parameters={
            "delegator_id": "user_writer",
            "delegatee_id": "user_deputy",
            "role": "FINANCE_DEPT",
            "valid_from": (now - timedelta(minutes=5)).isoformat(),
            "valid_until": (now + timedelta(hours=2)).isoformat(),
            "tenant_id": "tenant_cap",
        },
        context={"resource_tenant_id": "tenant_cap"},
    )
    res_delegate = await kernel.invoke_capability(req_delegate)
    assert res_delegate["role"] == "FINANCE_DEPT"

    # 6. Capability: kortex.workflow.approval.decide by a different,
    # equally-authorized approver (M6.2-3: the requester, "user_writer",
    # cannot decide the ticket it itself created).
    req_decide = CapabilityRequest(
        capability_name="kortex.workflow.approval.decide",
        session_token=approver_token_payload,
        parameters={
            "request_id": ticket_id,
            "decision": "APPROVED",
            "approver_id": "user_approver",
            "tenant_id": "tenant_cap",
        },
        context={"resource_tenant_id": "tenant_cap"},
    )
    res_decide = await kernel.invoke_capability(req_decide)
    assert res_decide["decision"] == "APPROVED"

    await kernel.shutdown()
    if db_manager._engine:
        await db_manager._engine.dispose()


# ============================================================================
# 27. API Error Mapping
# ============================================================================


def test_api_error_mapping() -> None:
    """Category 27: Verify exact HTTP status mappings for M5.3 approval exceptions."""
    assert map_exception(ApprovalConflictError("conflict")).http_status == http.HTTPStatus.CONFLICT
    assert map_exception(InvalidSignatureError("bad sig")).http_status == http.HTTPStatus.FORBIDDEN
    assert map_exception(ResourceNotFoundError("missing")).http_status == http.HTTPStatus.NOT_FOUND
    assert map_exception(WorkflowApprovalError("bad req")).http_status == http.HTTPStatus.BAD_REQUEST


# ============================================================================
# 28. Comprehensive Adversarial Test Battery (Phases 5-10 Hardening Proofs)
# ============================================================================


@pytest.mark.asyncio
async def test_adversarial_cross_tenant_and_identity_spoofing(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """Phase 5 & 6 Adversarial Audit: Tenant isolation & approver identity spoofing."""
    ticket = await durable_env.approval_manager.create_request(
        required_role="FINANCE", tenant_id="tenant_a"
    )

    # 1. Tenant B user attempts to approve Tenant A ticket
    intruder_principal = SecurityPrincipal(
        principal_id="intruder",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_b",
        roles=["FINANCE"],
    )
    decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_a",
        approver_id="intruder",
        decision=ApprovalState.APPROVED,
    )
    with pytest.raises(AuthorizationDeniedError, match="does not match ticket tenant"):
        await durable_env.approval_manager.submit_decision(
            decision, principal=intruder_principal, tenant_id="tenant_a"
        )

    # 2. Identity Spoofing: User mallory passes approver_id="alice" in request body
    mallory_principal = SecurityPrincipal(
        principal_id="mallory",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_a",
        roles=["FINANCE"],
    )
    spoofed_decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_a",
        approver_id="alice",
        decision=ApprovalState.APPROVED,
    )
    with pytest.raises(AuthorizationDeniedError, match="does not match authenticated principal ID"):
        await durable_env.approval_manager.submit_decision(
            spoofed_decision, principal=mallory_principal, tenant_id="tenant_a"
        )


@pytest.mark.asyncio
async def test_adversarial_delegation_authorization(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """Phase 6 Adversarial Audit: Unauthorized role delegation & boundary checks."""
    now = datetime.now(UTC)

    # 1. Intern attempts to delegate CFO role without possessing it
    intern_principal = SecurityPrincipal(
        principal_id="intern",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_corp",
        roles=["INTERN"],
    )
    with pytest.raises(AuthorizationDeniedError, match="does not possess role 'CFO' to delegate"):
        await durable_env.approval_manager.create_delegation(
            delegator_id="intern",
            delegatee_id="hacker",
            role="CFO",
            valid_from=now,
            valid_until=now + timedelta(hours=1),
            tenant_id="tenant_corp",
            principal=intern_principal,
        )

    # 2. User attempts to delegate on behalf of another user without admin rights
    cfo_principal = SecurityPrincipal(
        principal_id="cfo_user",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_corp",
        roles=["CFO"],
    )
    with pytest.raises(AuthorizationDeniedError, match="cannot delegate on behalf of delegator"):
        await durable_env.approval_manager.create_delegation(
            delegator_id="other_cfo",
            delegatee_id="deputy",
            role="CFO",
            valid_from=now,
            valid_until=now + timedelta(hours=1),
            tenant_id="tenant_corp",
            principal=cfo_principal,
        )


@pytest.mark.asyncio
async def test_adversarial_cryptographic_payload_tampering(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """Phase 7 Adversarial Audit: Payload tampering (decision, timestamp, request_id)."""
    priv_bytes, pub_bytes = durable_env.local_crypto.generate_ed25519_keypair()
    pub_hex = pub_bytes.hex()

    ticket = await durable_env.approval_manager.create_request(
        required_role="EXEC", tenant_id="tenant_crypto", signature_required=True
    )
    approver_id = "exec_1"
    decided_at = datetime.now(UTC)

    exec_principal = SecurityPrincipal(
        principal_id=approver_id,
        principal_type=PrincipalType.USER,
        tenant_id="tenant_crypto",
        roles=["EXEC"],
        attributes={"public_key": pub_hex},
    )

    # Sign payload with APPROVED
    canonical_payload = f"{ticket.id}:APPROVED:{approver_id}:{decided_at.isoformat()}".encode()
    sig_hex = durable_env.local_crypto.sign_ed25519(canonical_payload, priv_bytes).hex()

    # Tamper 1: Tampered Decision (signed APPROVED, submitted as REJECTED)
    tampered_dec = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_crypto",
        approver_id=approver_id,
        decision=ApprovalState.REJECTED,
        signature_hex=sig_hex,
        public_key_hex=pub_hex,
        decided_at=decided_at,
    )
    with pytest.raises(InvalidSignatureError, match="verification failed"):
        await durable_env.approval_manager.submit_decision(
            tampered_dec, principal=exec_principal, tenant_id="tenant_crypto"
        )

    # Tamper 2: Tampered Timestamp (signed at T1, submitted with T2)
    tampered_time_dec = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_crypto",
        approver_id=approver_id,
        decision=ApprovalState.APPROVED,
        signature_hex=sig_hex,
        public_key_hex=pub_hex,
        decided_at=decided_at + timedelta(seconds=1),
    )
    with pytest.raises(InvalidSignatureError, match="verification failed"):
        await durable_env.approval_manager.submit_decision(
            tampered_time_dec, principal=exec_principal, tenant_id="tenant_crypto"
        )


@pytest.mark.asyncio
async def test_adversarial_expiration_race_and_hydration_idempotency(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """Phase 9 & 10 Adversarial Audit: Expiration vs decision races and hydrate_and_recover idempotency."""
    ticket = await durable_env.approval_manager.create_request(
        required_role="LEAD",
        tenant_id="tenant_race",
        timeout_seconds=-5,  # already expired
    )

    # 1. Sweep expires the ticket
    expired = await durable_env.approval_manager.sweep_expired_requests(tenant_id="tenant_race")
    assert len(expired) == 1
    assert expired[0].id == ticket.id
    assert expired[0].state == ApprovalState.EXPIRED

    # 2. Race: Attempt to submit decision after expiration -> must fail with Conflict
    lead_principal = SecurityPrincipal(
        principal_id="lead_1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_race",
        roles=["LEAD"],
    )
    decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_race",
        approver_id="lead_1",
        decision=ApprovalState.APPROVED,
    )
    with pytest.raises(ApprovalConflictError, match="already in state 'EXPIRED'"):
        await durable_env.approval_manager.submit_decision(
            decision, principal=lead_principal, tenant_id="tenant_race"
        )


# ============================================================================
# M6.2-3: Requester Identity, Self-Approval Prevention, Actor-Type Attribution
# ============================================================================


@pytest.mark.asyncio
async def test_create_request_records_requester_principal_identity(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """A ticket created by a verified principal must persist that principal's
    identity, roundtripping correctly through the real database."""
    ai_principal = SecurityPrincipal(
        principal_id="kortex-ai-system",
        principal_type=PrincipalType.AGENT,
        tenant_id="tenant_ai",
        roles=["AI_SYSTEM_ACTOR"],
    )

    ticket = await durable_env.approval_manager.create_request(
        instance_id=None,
        step_id="task-123",
        required_role="ai_approver",
        tenant_id="tenant_ai",
        context_snapshot={"action": "ai_tool_invocation", "task_id": "task-123"},
        principal=ai_principal,
        correlation_id="task-123",
        action_fingerprint="deadbeef",
    )

    assert ticket.requester_principal_id == "kortex-ai-system"
    assert ticket.requester_principal_type == "AGENT"
    assert ticket.correlation_id == "task-123"
    assert ticket.action_fingerprint == "deadbeef"

    fetched = await durable_env.approval_manager.get_request(ticket.id, tenant_id="tenant_ai")
    assert fetched is not None
    assert fetched.requester_principal_id == "kortex-ai-system"
    assert fetched.requester_principal_type == "AGENT"
    assert fetched.correlation_id == "task-123"
    assert fetched.action_fingerprint == "deadbeef"


@pytest.mark.asyncio
async def test_ticket_with_no_requester_recorded_has_null_requester_fields(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """Backward-compatible default: a ticket created without a principal
    (existing callers that never supplied one) must not fabricate a
    requester identity."""
    ticket = await durable_env.approval_manager.create_request(
        required_role="FINANCE_MANAGER", tenant_id="tenant_legacy"
    )
    assert ticket.requester_principal_id is None
    assert ticket.requester_principal_type is None


@pytest.mark.asyncio
async def test_self_approval_denied_even_with_correct_role(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """SECURITY (M6.2-3): a principal must never be able to decide a ticket
    it itself requested -- even if it happens to also hold the ticket's own
    `required_role`. This is the defense-in-depth layer on top of the
    primary control (role-scoping, which is an operational/configuration
    concern this test cannot exercise directly)."""
    requester = SecurityPrincipal(
        principal_id="kortex-ai-system",
        principal_type=PrincipalType.AGENT,
        tenant_id="tenant_self_appr",
        # Deliberately holds the ticket's own required_role, to prove the
        # requester-identity check is what actually blocks this -- not RBAC
        # or role-scoping, which would otherwise mask the gap this test
        # exists to close.
        roles=["ai_approver"],
    )

    ticket = await durable_env.approval_manager.create_request(
        required_role="ai_approver",
        tenant_id="tenant_self_appr",
        principal=requester,
    )

    decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_self_appr",
        approver_id="kortex-ai-system",
        decision=ApprovalState.APPROVED,
    )

    with pytest.raises(AuthorizationDeniedError, match="cannot decide an approval ticket it itself requested"):
        await durable_env.approval_manager.submit_decision(
            decision, principal=requester, tenant_id="tenant_self_appr"
        )

    # The ticket must remain PENDING -- the denied attempt must not have
    # partially mutated its state.
    still_pending = await durable_env.approval_manager.get_request(ticket.id, tenant_id="tenant_self_appr")
    assert still_pending is not None
    assert still_pending.state == ApprovalState.PENDING


@pytest.mark.asyncio
async def test_different_principal_with_role_can_still_decide(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """Regression guard: the self-approval check must not block a
    DIFFERENT, correctly-roled principal from deciding a ticket it did not
    request."""
    requester = SecurityPrincipal(
        principal_id="kortex-ai-system",
        principal_type=PrincipalType.AGENT,
        tenant_id="tenant_self_appr_2",
        roles=["AI_SYSTEM_ACTOR"],
    )
    decider = SecurityPrincipal(
        principal_id="human_reviewer_1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_self_appr_2",
        roles=["ai_approver"],
    )

    ticket = await durable_env.approval_manager.create_request(
        required_role="ai_approver",
        tenant_id="tenant_self_appr_2",
        principal=requester,
    )

    decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_self_appr_2",
        approver_id="human_reviewer_1",
        decision=ApprovalState.APPROVED,
    )
    result = await durable_env.approval_manager.submit_decision(
        decision, principal=decider, tenant_id="tenant_self_appr_2"
    )
    assert result.state == ApprovalState.APPROVED


@pytest.mark.asyncio
async def test_actor_type_derived_from_principal_type_for_ai_agent(
    durable_env: ApprovalTestEnvironment,
) -> None:
    """M6.2-3: the audit trail must label an AI-originated ticket/decision
    `AI_AGENT`, not the previous hardcoded `HUMAN` fallback."""
    ai_principal = SecurityPrincipal(
        principal_id="kortex-ai-system",
        principal_type=PrincipalType.AGENT,
        tenant_id="tenant_actor_type",
        roles=["ai_approver"],
    )
    other_ai_principal = SecurityPrincipal(
        principal_id="kortex-ai-system-2",
        principal_type=PrincipalType.AGENT,
        tenant_id="tenant_actor_type",
        roles=["ai_approver"],
    )

    ticket = await durable_env.approval_manager.create_request(
        required_role="ai_approver",
        tenant_id="tenant_actor_type",
        principal=ai_principal,
    )
    decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_actor_type",
        approver_id="kortex-ai-system-2",
        decision=ApprovalState.APPROVED,
    )
    await durable_env.approval_manager.submit_decision(
        decision, principal=other_ai_principal, tenant_id="tenant_actor_type"
    )

    entries = await durable_env.security_engine.audit_manager.get_audit_entries(tenant_id="tenant_actor_type")
    create_entries = [e for e in entries if e.action == "kortex.workflow.approval.create"]
    decide_entries = [e for e in entries if e.action == "kortex.workflow.approval.decide"]
    assert len(create_entries) == 1
    assert create_entries[0].actor_type == "AI_AGENT"
    assert len(decide_entries) == 1
    assert decide_entries[0].actor_type == "AI_AGENT"


@pytest.mark.asyncio
async def test_actor_type_still_human_for_user_principal(durable_env: ApprovalTestEnvironment) -> None:
    """Regression guard: a human (`USER`) principal's create/decide audit
    entries are unaffected by the M6.2-3 fix."""
    human_principal = SecurityPrincipal(
        principal_id="human_requester_1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_actor_type_human",
        roles=["ai_approver"],
    )

    ticket = await durable_env.approval_manager.create_request(
        required_role="ai_approver",
        tenant_id="tenant_actor_type_human",
        principal=human_principal,
    )
    decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_actor_type_human",
        approver_id="human_requester_1",
        decision=ApprovalState.REJECTED,
    )
    # A human CAN decide their own request today (self-approval prevention
    # only fires when the ticket recorded a requester and this decider is
    # that same requester -- which is exactly the case here, so this must
    # actually be denied too; use a different decider to isolate the
    # actor_type assertion from the self-approval control).
    other_human = SecurityPrincipal(
        principal_id="human_reviewer_2",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_actor_type_human",
        roles=["ai_approver"],
    )
    decision = ApprovalDecision(
        request_id=ticket.id,
        tenant_id="tenant_actor_type_human",
        approver_id="human_reviewer_2",
        decision=ApprovalState.REJECTED,
    )
    await durable_env.approval_manager.submit_decision(
        decision, principal=other_human, tenant_id="tenant_actor_type_human"
    )

    entries = await durable_env.security_engine.audit_manager.get_audit_entries(
        tenant_id="tenant_actor_type_human"
    )
    create_entries = [e for e in entries if e.action == "kortex.workflow.approval.create"]
    decide_entries = [e for e in entries if e.action == "kortex.workflow.approval.decide"]
    assert len(create_entries) == 1
    assert create_entries[0].actor_type == "HUMAN"
    assert len(decide_entries) == 1
    assert decide_entries[0].actor_type == "HUMAN"
