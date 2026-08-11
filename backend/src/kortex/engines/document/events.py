"""Immutable System Event Payload Definitions for KORTEX OS Document Engine.

This module defines all immutable system events emitted by the Document Engine to the Event Engine
in accordance with Section 16 and Milestone 8 of the Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentBaseEvent(BaseModel):
    """Base class for all immutable Document Engine system events."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: f"evt-{datetime.datetime.now(datetime.timezone.utc).timestamp()}")
    event_type: str
    timestamp: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


class DocumentCreatedEvent(DocumentBaseEvent):
    """Dispatched when a new root document entity is registered."""

    event_type: str = "document.created"
    document_id: str
    version_id: str
    title: str
    author_id: str


class DocumentLifecycleTransitionedEvent(DocumentBaseEvent):
    """Dispatched when document state transitions (Draft -> Review, etc.)."""

    event_type: str = "document.lifecycle.transitioned"
    document_id: str
    version_id: str
    from_state: str
    to_state: str


class DocumentPublishedEvent(DocumentBaseEvent):
    """Dispatched when document version transitions to Published (Locks immutability)."""

    event_type: str = "document.published"
    document_id: str
    version_id: str
    published_at: str


class DocumentSupersededEvent(DocumentBaseEvent):
    """Dispatched when a published version is replaced by a newer version."""

    event_type: str = "document.superseded"
    document_id: str
    superseded_version_id: str
    new_version_id: str


class DocumentArchivedEvent(DocumentBaseEvent):
    """Dispatched when document version transitions to Archived."""

    event_type: str = "document.archived"
    document_id: str
    version_id: str


class DocumentOperationStartedEvent(DocumentBaseEvent):
    """Dispatched immediately upon receiving valid OperationRequest."""

    event_type: str = "document.operation.started"
    request_id: str
    profile_id: str


class DocumentOperationCompletedEvent(DocumentBaseEvent):
    """Dispatched when operation output is written to IObjectStore."""

    event_type: str = "document.operation.completed"
    request_id: str
    profile_id: str
    status: str
    execution_time_ms: float


class DocumentOperationFailedEvent(DocumentBaseEvent):
    """Dispatched when operation execution or pipeline stage fails."""

    event_type: str = "document.operation.failed"
    request_id: str
    profile_id: str
    errors: list[str]


class DocumentIntelligenceUpdatedEvent(DocumentBaseEvent):
    """Dispatched when intelligence metadata model is updated."""

    event_type: str = "document.intelligence.updated"
    document_id: str
    version_id: str


class DocumentAdapterRegisteredEvent(DocumentBaseEvent):
    """Dispatched when new document adapter is registered."""

    event_type: str = "document.adapter.registered"
    adapter_id: str
    display_name: str
    vendor: str


__all__ = [
    "DocumentAdapterRegisteredEvent",
    "DocumentArchivedEvent",
    "DocumentBaseEvent",
    "DocumentCreatedEvent",
    "DocumentIntelligenceUpdatedEvent",
    "DocumentLifecycleTransitionedEvent",
    "DocumentOperationCompletedEvent",
    "DocumentOperationFailedEvent",
    "DocumentOperationStartedEvent",
    "DocumentPublishedEvent",
    "DocumentSupersededEvent",
]
