"""Immutable System Event Payload Definitions for KORTEX OS Connector Engine.

This module defines immutable system event payload schemas emitted by the Connector Engine
across action execution lifecycle stages and driver registration events.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConnectorBaseEvent(BaseModel):
    """Base class for all immutable Connector Engine system event payloads."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: f"evt-{uuid.uuid4().hex}")
    event_type: str
    timestamp: str = Field(default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat())


class ConnectorActionStartedEvent(ConnectorBaseEvent):
    """Dispatched when a connector action execution is initiated."""

    event_type: Literal["connector.action.started"] = "connector.action.started"
    request_id: str
    profile_id: str
    action_type: str
    correlation_id: str | None = None


class ConnectorActionCompletedEvent(ConnectorBaseEvent):
    """Dispatched when a connector action successfully completes execution."""

    event_type: Literal["connector.action.completed"] = "connector.action.completed"
    request_id: str
    profile_id: str
    action_type: str
    status: str
    execution_time_ms: float
    correlation_id: str | None = None


class ConnectorActionFailedEvent(ConnectorBaseEvent):
    """Dispatched when a connector action execution fails or is rejected."""

    event_type: Literal["connector.action.failed"] = "connector.action.failed"
    request_id: str
    profile_id: str
    action_type: str
    error_message: str
    execution_time_ms: float
    correlation_id: str | None = None


class ConnectorDriverRegisteredEvent(ConnectorBaseEvent):
    """Dispatched when a connector driver plugin is registered in the engine registry."""

    event_type: Literal["connector.driver.registered"] = "connector.driver.registered"
    driver_id: str
    driver_name: str
    version: str
    supported_actions: tuple[str, ...] = Field(default_factory=tuple)


__all__ = [
    "ConnectorActionCompletedEvent",
    "ConnectorActionFailedEvent",
    "ConnectorActionStartedEvent",
    "ConnectorBaseEvent",
    "ConnectorDriverRegisteredEvent",
]
