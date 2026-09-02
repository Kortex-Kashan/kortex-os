"""
KORTEX Knowledge Engine — Domain Event Payloads.

Immutable event payloads emitted by `KnowledgeEngine` (`engine.py`) through
the Kernel's Event Engine, matching the canonical capability set named in
`docs/architecture/knowledge_engine_implementation_spec.md` §14 exactly
(`KnowledgeNodeIndexedEvent`/`knowledge.node.indexed`,
`KnowledgePackLoadedEvent`/`knowledge.pack.loaded`,
`KnowledgeQueryExecutedEvent`/`knowledge.query.executed`).

Follows the established per-engine local base-event convention already used
by `kortex.engines.connector.events.ConnectorBaseEvent` and
`kortex.engines.security.events.SecurityBaseEvent` (frozen Pydantic model,
`event_id`/`event_type`/`tenant_id`/`timestamp`, each concrete subclass
pinning `event_type` to a `Literal` default) — no shared base event class is
imported, matching every sibling engine's own local declaration.

Emission itself (`KnowledgeEngine._emit_event`) always goes through
`Kernel.publish_event(topic=event.event_type, payload=event.model_dump(),
sender=self.name)`, best-effort or in a `try`/`except` that only logs a
failure — never a second event-bus mechanism, and never something that
blocks or fails the operation the event merely reports on. This mirrors
Connector/Document/Workflow Engine's own `_publish_event`/`_emit_event`
helpers exactly.

`KnowledgeNodeIndexedEvent`'s name (`node.indexed`, per the spec) refers to
`index_source()`'s output, which is a list of `KnowledgeRecord`s, not
`KnowledgeNode`s — those are a deliberately distinct, load-bearing concept
in this codebase (see `models.py`'s own docstring on `KnowledgeRecord`). The
event's *name* is preserved exactly as the approved spec names it (renaming
it would be an unauthorized deviation from an approved document); its
*payload* reflects what `index_source()` actually produces (a source id and
a count of ingested records), not a fabricated node identifier.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseEvent(BaseModel):
    """Base class for all immutable Knowledge Engine system event payloads."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: f"evt-{uuid.uuid4().hex}")
    event_type: str
    tenant_id: str = Field(..., min_length=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KnowledgeNodeIndexedEvent(KnowledgeBaseEvent):
    """Emitted after `KnowledgeEngine.index_source()` successfully persists
    every record a source provider's `ingest()` returned."""

    event_type: Literal["knowledge.node.indexed"] = "knowledge.node.indexed"
    source_id: str = Field(..., min_length=1)
    record_count: int = Field(..., ge=0)


class KnowledgePackLoadedEvent(KnowledgeBaseEvent):
    """Emitted after `KnowledgeEngine.load_pack()` successfully verifies and
    durably registers a `KnowledgePack`."""

    event_type: Literal["knowledge.pack.loaded"] = "knowledge.pack.loaded"
    asset_id: str = Field(..., min_length=1)


class KnowledgeQueryExecutedEvent(KnowledgeBaseEvent):
    """Emitted after `KnowledgeEngine.search()`/`query_knowledge()`
    completes (success or not — the query itself executed)."""

    event_type: Literal["knowledge.query.executed"] = "knowledge.query.executed"
    query_id: str = Field(..., min_length=1)
    execution_time_ms: float = Field(..., ge=0.0)
