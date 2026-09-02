"""KORTEX Platform Security — Capability Identity Propagation: Workflow
approval-impersonation regression, exercised through the real Kernel
capability-dispatch path.

Confirmed adjacent finding (not the same code path as the nested-token
vulnerability, but the same class of defect): `DurableApprovalManager.
submit_decision` used to resolve "who is deciding" by loading a
`SecurityPrincipal` *by the caller-supplied `decision.approver_id` string*
whenever no principal was already supplied — never checking that the
actual, dispatcher-authenticated caller *is* that named approver. Any
authenticated principal could submit a decision as any named `approver_id`
who happened to hold the required role.

Fixed by removing that lookup-by-name fallback entirely (see
`approval.py::submit_decision`) and by `decide_approval_request` (the
capability handler) now sourcing `principal` exclusively from the
dispatcher-injected `CapabilityExecutionContext` — never `None`, never
independently re-derived. `principal.principal_id != decision.approver_id`
is therefore a meaningful check again, not a tautology.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.engine import WorkflowEngine
from tests.unit.test_workflow_capability_dispatch import (
    _TEST_MASTER_KEY,
    _TEST_SIGNING_KEY,
    _grant_role_permission,
    _issue_token,
    _seed_principal,
    _tenant,
)

_CAPABILITY = "kortex.workflow.approval.decide"


def _build_and_boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, WorkflowEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "approval_impersonation_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow_engine = WorkflowEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(workflow_engine)
    return kernel, storage_engine, security_engine, workflow_engine


@pytest.mark.asyncio
async def test_authenticated_caller_cannot_impersonate_a_different_approver_id(tmp_path: Path) -> None:
    """Tenant A: principal-caller is authenticated and holds
    `approval:write`, but does NOT hold the ticket's required role
    ("SUPERVISOR"). principal-real-approver DOES hold it. principal-caller
    submits a decision claiming `approver_id="principal-real-approver"` —
    i.e., attempts to decide *as* someone else. This must be denied: the
    authenticated caller is principal-caller, not principal-real-approver,
    and no delegation exists between them."""
    kernel, storage_engine, security_engine, workflow_engine = _build_and_boot_kernel(tmp_path)
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-impersonate")
    caller_role = "role-caller"
    await _grant_role_permission(storage_engine.data, caller_role, "approval:write")
    await _seed_principal(storage_engine.data, tenant_id, "principal-caller", roles=[caller_role])
    # The real approver exists and genuinely holds SUPERVISOR — but never
    # authenticates or delegates anything to principal-caller.
    await _seed_principal(storage_engine.data, tenant_id, "principal-real-approver", roles=["SUPERVISOR"])
    caller_token = await _issue_token(security_engine, tenant_id, "principal-caller")

    ticket = await workflow_engine._approval_manager.create_request(
        required_role="SUPERVISOR", tenant_id=tenant_id, signature_required=False
    )

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=caller_token,
        parameters={
            "request_id": str(ticket.id),
            "decision": "APPROVED",
            "approver_id": "principal-real-approver",  # impersonation attempt
            "tenant_id": tenant_id,
        },
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_authenticated_caller_deciding_as_themselves_with_required_role_succeeds(tmp_path: Path) -> None:
    """Control case: the authenticated caller genuinely IS the named
    approver and genuinely holds the required role — must succeed. Proves
    the fix rejects impersonation specifically, not all decisions."""
    kernel, storage_engine, security_engine, workflow_engine = _build_and_boot_kernel(tmp_path)
    await kernel.boot()

    tenant_id = _tenant(tmp_path, "-legit")
    role = "role-supervisor"
    await _grant_role_permission(storage_engine.data, role, "approval:write")
    await _seed_principal(storage_engine.data, tenant_id, "principal-approver", roles=[role, "SUPERVISOR"])
    token = await _issue_token(security_engine, tenant_id, "principal-approver")

    ticket = await workflow_engine._approval_manager.create_request(
        required_role="SUPERVISOR", tenant_id=tenant_id, signature_required=False
    )

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        parameters={
            "request_id": str(ticket.id),
            "decision": "APPROVED",
            "approver_id": "principal-approver",  # the caller's own real identity
            "tenant_id": tenant_id,
        },
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)
    assert result["state"] == "APPROVED"
