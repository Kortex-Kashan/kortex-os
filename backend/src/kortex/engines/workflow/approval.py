"""
KORTEX Workflow Approval System Abstraction.

Defines the ApprovalRepository and ApprovalProvider interfaces, as well as the
in-memory MemoryApprovalManager for approval ticket tracking, decision processing,
and event generation (Zero UI / notifications / email / WhatsApp).
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable
from uuid import UUID

from kortex.engines.workflow.exceptions import WorkflowApprovalError
from kortex.engines.workflow.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
)

logger = logging.getLogger("kortex.engines.workflow.approval")


@runtime_checkable
class ApprovalRepository(Protocol):
    """Protocol interface for storing and retrieving approval requests."""

    async def save_request(self, request: ApprovalRequest) -> None:
        """Save or update an approval request ticket."""
        ...

    async def get_request(self, request_id: UUID) -> ApprovalRequest | None:
        """Retrieve an approval request ticket by UUID."""
        ...

    async def list_pending_requests(self, role_filter: str | None = None) -> list[ApprovalRequest]:
        """List pending approval requests optioned filtered by required role."""
        ...


@runtime_checkable
class ApprovalProvider(Protocol):
    """Protocol interface for approval request lifecycle management."""

    async def create_request(self, instance_id: UUID, step_id: str, required_role: str) -> ApprovalRequest:
        """Create a new approval request ticket."""
        ...

    async def submit_decision(self, decision: ApprovalDecision) -> ApprovalRequest:
        """Submit an approval decision for a pending ticket."""
        ...


class MemoryApprovalManager(ApprovalRepository, ApprovalProvider):
    """In-memory approval manager implementing ApprovalRepository and ApprovalProvider."""

    def __init__(self) -> None:
        self._requests: dict[UUID, ApprovalRequest] = {}
        self._decisions: dict[UUID, ApprovalDecision] = {}

    async def save_request(self, request: ApprovalRequest) -> None:
        """Save or update an approval request ticket."""
        self._requests[request.id] = request
        logger.debug("Saved approval request '%s' for instance '%s'", request.id, request.instance_id)

    async def get_request(self, request_id: UUID) -> ApprovalRequest | None:
        """Retrieve an approval request ticket by UUID."""
        return self._requests.get(request_id)

    async def list_pending_requests(self, role_filter: str | None = None) -> list[ApprovalRequest]:
        """List pending approval requests."""
        results: list[ApprovalRequest] = []
        for req in self._requests.values():
            if req.state == ApprovalState.PENDING and (role_filter is None or req.required_role == role_filter):
                results.append(req)
        return results

    async def get_request_by_instance(self, instance_id: UUID) -> ApprovalRequest | None:
        """Retrieve the pending approval request for a workflow instance if one exists."""
        for req in self._requests.values():
            if req.instance_id == instance_id and req.state == ApprovalState.PENDING:
                return req
        return None

    async def get_request_by_step(self, instance_id: UUID, step_id: str) -> ApprovalRequest | None:
        """Retrieve the approval request for a specific instance and step."""
        for req in self._requests.values():
            if req.instance_id == instance_id and req.step_id == step_id and req.state == ApprovalState.PENDING:
                return req
        return None

    async def create_request(self, instance_id: UUID, step_id: str, required_role: str) -> ApprovalRequest:
        """Create and register a new pending approval ticket."""
        request = ApprovalRequest(
            instance_id=instance_id,
            step_id=step_id,
            required_role=required_role,
            state=ApprovalState.PENDING,
        )
        await self.save_request(request)
        logger.info(
            "Created approval ticket '%s' for step '%s' (Required Role: '%s')",
            request.id,
            step_id,
            required_role,
        )
        return request

    async def submit_decision(self, decision: ApprovalDecision) -> ApprovalRequest:
        """Submit an approval decision (APPROVED or REJECTED) for a pending ticket.

        Raises:
            WorkflowApprovalError: If request is missing or already decided.
        """
        request = await self.get_request(decision.request_id)
        if not request:
            raise WorkflowApprovalError(f"Approval request ticket '{decision.request_id}' not found.")

        if request.state != ApprovalState.PENDING:
            raise WorkflowApprovalError(
                f"Approval request ticket '{decision.request_id}' is already in state '{request.state.value}'."
            )

        if decision.decision not in (ApprovalState.APPROVED, ApprovalState.REJECTED):
            raise WorkflowApprovalError(f"Invalid decision state '{decision.decision}'. Must be APPROVED or REJECTED.")

        request.state = decision.decision
        self._decisions[decision.request_id] = decision
        await self.save_request(request)

        logger.info(
            "Processed decision '%s' for approval ticket '%s' by approver '%s'",
            decision.decision.value,
            request.id,
            decision.approver_id,
        )
        return request
