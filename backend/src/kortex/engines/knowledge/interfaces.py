"""
KORTEX Knowledge Engine Abstract Interfaces & Protocols (Milestone M1 —
redesigned scope).

Defines the Protocol contracts for the Knowledge Engine, covering both the
original graph-primitive contracts per
`docs/architecture/knowledge_engine_implementation_spec.md` v3.0.0 (§4) and
the organizational-memory contracts (`IKnowledgeRecordManager`,
`IKnowledgeAnnotationManager`) added by the Chief Architect's redesigned M1
scope.

`IKnowledgeGraph` is implemented in `graph.py`.
`IKnowledgeRecordManager` is implemented in `lineage.py` — its `promote()`
contract expresses the domain operation only; the actor-type enforcement
restricting promotion to `USER`-type actors is implemented there too, in
`promote()` itself.
`IKnowledgeAnnotationManager` is implemented in `annotations.py`.
`IKnowledgeSourceProvider` is implemented in `sources.py`.
`IKnowledgeSearchEngine` is implemented in `search.py`.
`IKnowledgeEngine` is implemented by the `KnowledgeEngine` facade
(`engine.py`), a `BaseEngine` subclass registering `kortex.knowledge.*`
capabilities with the Kernel.
`IEngineDiagnostics` is implemented directly on `KnowledgeEngine`
(`engine.py`) — no separate `diagnostics.py` file, matching every sibling
engine's own convention of implementing this Protocol directly on the
engine class rather than in a dedicated file.

None of these Protocols carry implementation logic in Milestone M1 — they
are forward contracts only, following the same convention already
established by Security Engine's `interfaces.py` (M1 declared
`ISecretStore` / `IAuthorizationEngine` / `ISecurityEngine` ahead of their
M2 / M4 / M6 implementations).

`IEngineDiagnostics` intentionally mirrors every other engine's local copy
of this Protocol exactly rather than importing a shared one — this
codebase's established convention is for each engine to declare its own
local copy rather than import a common definition.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kortex.engines.knowledge.models import (
    KnowledgeActorType,
    KnowledgeAnnotation,
    KnowledgeNode,
    KnowledgePack,
    KnowledgeQuery,
    KnowledgeQueryResult,
    KnowledgeRecord,
    KnowledgeRelationship,
    KnowledgeTrustState,
)


@runtime_checkable
class IKnowledgeGraph(Protocol):
    """Directed Knowledge Graph interface. Implemented as of Milestone M2 (`graph.py`)."""

    def add_node(self, node: KnowledgeNode) -> KnowledgeNode:
        """Add a node to the graph."""
        ...

    def add_relationship(self, relationship: KnowledgeRelationship) -> KnowledgeRelationship:
        """Add a directed relationship between two existing nodes."""
        ...

    def find_neighbors(self, node_id: str, tenant_id: str) -> list[KnowledgeNode]:
        """Return the immediate neighbor nodes of `node_id`, scoped to `tenant_id`."""
        ...

    def traverse(self, node_id: str, tenant_id: str, max_hops: int) -> list[KnowledgeNode]:
        """Traverse up to `max_hops` from `node_id`, scoped to `tenant_id`."""
        ...


@runtime_checkable
class IKnowledgeRecordManager(Protocol):
    """Versioned record lineage and supersession interface. Implemented as
    of Milestone M3 (`lineage.py`).
    """

    async def create_record(self, record: KnowledgeRecord) -> KnowledgeRecord:
        """Create a new `KnowledgeRecord` version."""
        ...

    async def get_current(self, record_id: str, tenant_id: str) -> KnowledgeRecord | None:
        """Return the current version of `record_id`, scoped to `tenant_id`."""
        ...

    async def get_lineage(self, record_id: str, tenant_id: str) -> list[KnowledgeRecord]:
        """Return the full ordered version history of `record_id`, scoped to `tenant_id`."""
        ...

    async def supersede(self, record_id: str, tenant_id: str, new_version: KnowledgeRecord) -> KnowledgeRecord:
        """Atomically supersede the current version of `record_id` with `new_version`."""
        ...

    async def promote(
        self,
        record_id: str,
        tenant_id: str,
        actor_id: str,
        actor_type: KnowledgeActorType,
        new_trust_state: KnowledgeTrustState,
    ) -> KnowledgeRecord:
        """Promote a record's trust state (e.g. `AI_CANDIDATE` to `HUMAN_CONFIRMED`).

        This contract expresses the domain operation only. Enforcing that
        promotion to `HUMAN_CONFIRMED`/`HUMAN_CORRECTED` requires a `USER`
        `actor_type` is Milestone M6 behavior, not implemented here.
        """
        ...


@runtime_checkable
class IKnowledgeAnnotationManager(Protocol):
    """Human remark/correction/context annotation interface. Implemented as
    of Milestone M4 (`annotations.py`).
    """

    async def add_annotation(self, annotation: KnowledgeAnnotation) -> KnowledgeAnnotation:
        """Attach a new, non-destructive annotation to a `KnowledgeRecord`."""
        ...

    async def list_annotations(self, target_record_id: str, tenant_id: str) -> list[KnowledgeAnnotation]:
        """Return all annotations attached to `target_record_id`, scoped to `tenant_id`."""
        ...


@runtime_checkable
class IKnowledgeSourceProvider(Protocol):
    """Abstract data source ingestion interface. Implemented as of Milestone M5 (`sources.py`)."""

    def source_id(self) -> str:
        """Return the unique identifier of this source provider."""
        ...

    async def ingest(self, tenant_id: str) -> list[KnowledgeRecord]:
        """Ingest entities from this source into `SOURCE_EVIDENCE`-trust-state
        `KnowledgeRecord`s (not raw `KnowledgeNode`s — ingestion produces
        evidence-level assertions, per the redesigned M1 scope)."""
        ...


@runtime_checkable
class IKnowledgeSearchEngine(Protocol):
    """Multi-modal search coordinator interface. Implemented as of Milestone M8 (`search.py`)."""

    async def search_text(self, query: KnowledgeQuery) -> KnowledgeQueryResult:
        """Execute a full-text search."""
        ...

    async def search_graph(self, query: KnowledgeQuery) -> KnowledgeQueryResult:
        """Execute a graph-traversal search."""
        ...

    async def search_hybrid(self, query: KnowledgeQuery) -> KnowledgeQueryResult:
        """Execute a combined multi-modal search."""
        ...


@runtime_checkable
class IKnowledgeEngine(Protocol):
    """Primary Knowledge Engine facade protocol. Implemented by the
    `KnowledgeEngine` facade (`engine.py`)."""

    async def query_knowledge(self, query: KnowledgeQuery) -> KnowledgeQueryResult:
        """Execute a knowledge query."""
        ...

    async def index_source(self, source_id: str, tenant_id: str) -> list[KnowledgeRecord]:
        """Index a registered knowledge source, delegating to that source's
        `IKnowledgeSourceProvider.ingest()`. Returns the same
        `SOURCE_EVIDENCE`-trust-state `KnowledgeRecord`s `ingest()` produces
        — this facade method wraps the provider call, so its return type
        must match what it wraps."""
        ...

    async def load_pack(self, pack: KnowledgePack) -> KnowledgePack:
        """Load and verify a `.kortex-knowledge` pack."""
        ...

    async def search(self, query: KnowledgeQuery) -> KnowledgeQueryResult:
        """Execute a multi-modal knowledge search."""
        ...


@runtime_checkable
class IEngineDiagnostics(Protocol):
    """Standardized diagnostics interface exposed by all KORTEX System Engines."""

    def health(self) -> dict[str, Any]:
        """Return operational health status and diagnostic checks."""
        ...

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and throughput metrics."""
        ...

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics and system environment details."""
        ...

    def status(self) -> str:
        """Return current engine state name string."""
        ...

    def version(self) -> str:
        """Return semantic version string of the engine."""
        ...

    def capabilities(self) -> list[str]:
        """Return list of capability strings registered by the engine."""
        ...
