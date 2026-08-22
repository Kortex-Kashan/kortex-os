"""Immutable system event payload definitions for the KORTEX OS AI Orchestration Engine.

No publishing logic lives here. Event emission (via `Kernel.publish_event`,
the same mechanism Connector Engine uses in
`kortex.engines.connector.engine.ConnectorEngine._publish_event`) is a later
milestone's responsibility, once the engine facade exists.

Event type naming: these use the short `ai.<entity>.<action>` form already
present in `ai_orchestration_engine_implementation_spec.md` section 16
(e.g. `ai.generation.started`), matching the convention actually
implemented by Connector Engine (`connector.action.started`,
`connector.driver.registered` — see `kortex.engines.connector.events`),
where `event.event_type` is passed directly as the Kernel publish topic
with no additional prefix. `docs/architecture/event_bus.md` describes a
longer `kortex.event.<domain>.<entity>.<action>` topic format, but no
shipped engine follows it; the short form is the actual, working
convention and is what this module follows. This is a documented
discrepancy between the architecture doc and the real implementation, not
a defect introduced here.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AIBaseEvent(BaseModel):
    """Base class for all immutable AI Orchestration Engine system event payloads."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: f"evt-{uuid.uuid4().hex}")
    event_type: str
    timestamp: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


class AIGenerationStartedEvent(AIBaseEvent):
    """Dispatched when an AI generation request is initiated."""

    event_type: Literal["ai.generation.started"] = "ai.generation.started"
    request_id: str
    tenant_id: str
    conversation_id: str


class AIGenerationCompletedEvent(AIBaseEvent):
    """Dispatched when an AI generation request successfully completes."""

    event_type: Literal["ai.generation.completed"] = "ai.generation.completed"
    request_id: str
    tenant_id: str
    conversation_id: str
    execution_time_ms: float


class AIToolInvokedEvent(AIBaseEvent):
    """Dispatched when an AI-requested tool/capability invocation occurs.

    Payload is intentionally minimal pending Milestone 5's `ToolDefinition`
    contract — this only records that an invocation happened, not its full
    shape.
    """

    event_type: Literal["ai.tool.invoked"] = "ai.tool.invoked"
    request_id: str
    tenant_id: str
    tool_name: str


class AgentTaskCompletedEvent(AIBaseEvent):
    """Dispatched when an agent orchestration task completes.

    Payload is intentionally minimal pending Milestone 6's `AgentTask`
    contract.
    """

    event_type: Literal["ai.agent.completed"] = "ai.agent.completed"
    task_id: str
    tenant_id: str
    status: str


__all__ = [
    "AIBaseEvent",
    "AIGenerationCompletedEvent",
    "AIGenerationStartedEvent",
    "AIToolInvokedEvent",
    "AgentTaskCompletedEvent",
]
