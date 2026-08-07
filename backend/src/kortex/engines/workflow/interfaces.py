"""
KORTEX Workflow Engine Interfaces & Protocols.

Defines protocol interfaces for scheduler providers (ISchedulerProvider) and step execution handlers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from uuid import UUID

from kortex.engines.workflow.models import (
    ApprovalDecision,
    ApprovalRequest,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowResult,
)


@runtime_checkable
class ISchedulerProvider(Protocol):
    """Scheduler provider protocol interface for registering workflow delayed or cron jobs.

    Note: Implementation is out of scope for Phase 2; this interface serves as the contract point.
    """

    async def schedule_workflow(
        self,
        definition_name: str,
        cron_expression_or_delay_seconds: Any,
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Schedule a workflow for execution."""
        ...

    async def cancel_scheduled_workflow(self, schedule_id: str) -> bool:
        """Cancel a scheduled workflow job."""
        ...

    async def list_scheduled_workflows(self) -> List[Dict[str, Any]]:
        """List active scheduled workflow jobs."""
        ...


@runtime_checkable
class IWorkflowExecutor(Protocol):
    """Protocol interface for workflow execution engine facade."""

    async def execute_workflow(
        self,
        definition: WorkflowDefinition,
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowResult:
        """Execute a workflow definition to completion."""
        ...

    async def pause_workflow(self, instance_id: UUID) -> WorkflowInstance:
        """Pause a running workflow instance."""
        ...

    async def resume_workflow(self, instance_id: UUID) -> WorkflowInstance:
        """Resume a paused or approved workflow instance."""
        ...

    async def cancel_workflow(self, instance_id: UUID, reason: str = "") -> WorkflowInstance:
        """Cancel a running or waiting workflow instance."""
        ...
