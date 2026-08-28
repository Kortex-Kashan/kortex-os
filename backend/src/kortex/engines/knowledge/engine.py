"""
KORTEX Knowledge Engine — Core Facade (`KnowledgeEngine`).

Closes the last ratified gap in the Knowledge Engine's three-pillar scope
(`ARCHITECTURE_VERSION_1.0.md` §17: directed graph, search coordinator,
knowledge pack loader — the first two already built in M2/M8) by making
Knowledge Engine a real KORTEX System Engine: a `BaseEngine` subclass with
Kernel lifecycle management and capability registration, matching every
implemented sibling engine (Storage/Workflow/Recipe/Document/Connector/
Security — independently audited during the post-M8 reconciliation review;
all six subclass `BaseEngine`, implement the full lifecycle, and register
capabilities inside `initialize()`).

This module is a pure orchestrator. It never duplicates functionality
already implemented by `graph.py` (M2), `lineage.py` (M3/M6/M7),
`annotations.py` (M4/M7), `sources.py` (M5), `search.py` (M8), or
`packs.py` — every method here resolves the right existing manager(s) and
calls their already-existing public methods.

Capability names are exactly the four named in
`docs/architecture/knowledge_engine_implementation_spec.md` §13 (this
repository's only authoritative source for Knowledge Engine capability
naming) — not guessed: `kortex.knowledge.query.search`,
`kortex.knowledge.graph.traverse`, `kortex.knowledge.pack.load`,
`kortex.knowledge.source.index`. `required_permissions` follows Storage
Engine's own established `<domain>:<read|write>` convention
(`kortex.storage.*` capabilities use `storage:read`/`storage:write`) —
`knowledge:read` for the two read-only capabilities, `knowledge:write` for
the two mutating ones. No central capability-name-constants module exists
anywhere in this codebase (confirmed repository-wide during the
reconciliation audit) — every engine inlines its own capability name
strings directly in `initialize()`, and this module follows that same
established convention rather than inventing a new one.

Security note (load-bearing, resolved by the frozen `IKnowledgeEngine`
Protocol itself, not by anything added here): `create_record()`/
`supersede()` accept a caller-supplied `trust_state` with no actor-type
gate, by intentional Milestone M1/M3 API design (see `lineage.py`'s own
docstring). The post-M8 reconciliation audit flagged this as a
"future exposure risk" that would need a facade-level authorization gate
*if* the facade ever exposed `create_record`/`supersede` directly. It does
not, and must not: `IKnowledgeEngine`'s frozen Protocol (`interfaces.py`,
M1) exposes exactly `query_knowledge`, `index_source`, `load_pack`,
`search` — no method here calls `create_record`/`supersede` with a
caller-supplied `trust_state`. The only path from this facade into
`KnowledgeLineageManager` is `index_source()`, which persists exactly what
`IKnowledgeSourceProvider.ingest()` returns — and that Protocol's own
contract (`interfaces.py:137-141`) constrains every implementation to
produce only `SOURCE_EVIDENCE`-trust-state records. A caller can never
manufacture `HUMAN_CONFIRMED` knowledge through this facade. This
invariant must be preserved by any future change: do not add a facade
method that forwards a caller-supplied `trust_state` into `create_record`/
`supersede` without also adding the actor-type gate `promote()` already
has.

Event integration (spec §14) is implemented following the established
per-engine `_publish_event`/`_emit_event` convention already used by
Connector/Document/Workflow Engine (`await self._kernel.publish_event(
topic=event.event_type, payload=event.model_dump(), sender=self.name)`,
wrapped in a `try`/`except` that only logs) — best-effort, never blocking
or failing the operation the event merely reports on. No second event
mechanism is introduced.

Deliberately descoped, documented rather than silently omitted:
- `indexing.py`/`KnowledgeIndexer` (named in the spec's aspirational folder
  listing): not built. `search.py` (M8) already performs full-text and
  graph search directly over existing components, by deliberate M8 design
  (no separate index layer) — building a redundant indexer now would
  duplicate M8 functionality with no proven need, and reopening the frozen,
  tested M8 search coordinator to use a new indexer is not justified by any
  evidenced defect.
- `diagnostics.py` as a separate file: not created. Every sibling engine
  examined (Storage, Security) implements `IEngineDiagnostics` directly on
  the engine class in `engine.py`, with no separate file — the spec's
  aspirational folder listing does not match any actual sibling's
  convention, and `IEngineDiagnostics` itself is already declared in this
  engine's own frozen `interfaces.py` (M1), matching every sibling's own
  local-Protocol-in-interfaces.py convention.
- `providers/dummy_source.py`: `ReferenceSourceProvider` stays in `sources.py`
  exactly where Milestone M5 built it — moving already-frozen, tested code
  into a new subpackage would be a cosmetic reorganization with no
  functional benefit.
- `ICacheStore` traversal/search-result caching: deliberately deferred, not
  implemented here. No evidence establishes an active performance need (the
  existing 3-hop traversal already meets its 50ms budget uncached); revisit
  only if a concrete, measured performance need arises.
- Cryptographic verification of `KnowledgePack.digital_signature`: see
  `packs.py`'s own module docstring for the full disclosed-boundary
  rationale.

`query_knowledge()` vs. `search()`: no repository evidence anywhere
distinguishes these two frozen `IKnowledgeEngine` methods from one another
— both take a `KnowledgeQuery` and return a `KnowledgeQueryResult` with no
further differentiation in any spec, docstring, or test. Both are
implemented identically here, delegating to the same multi-modal search
coordinator (`search_hybrid`), rather than inventing an unevidenced
distinction between them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.exceptions import KortexError
from kortex.engines.knowledge.annotations import KnowledgeAnnotationManager
from kortex.engines.knowledge.events import (
    KnowledgeBaseEvent,
    KnowledgeNodeIndexedEvent,
    KnowledgePackLoadedEvent,
    KnowledgeQueryExecutedEvent,
)
from kortex.engines.knowledge.exceptions import KnowledgeSourceNotFoundError
from kortex.engines.knowledge.graph import KnowledgeGraph
from kortex.engines.knowledge.interfaces import IEngineDiagnostics, IKnowledgeEngine, IKnowledgeSourceProvider
from kortex.engines.knowledge.lineage import KnowledgeLineageManager
from kortex.engines.knowledge.models import (
    KnowledgeNode,
    KnowledgePack,
    KnowledgeQuery,
    KnowledgeQueryResult,
    KnowledgeRecord,
)
from kortex.engines.knowledge.packs import KnowledgePackManager
from kortex.engines.knowledge.search import KnowledgeSearchEngine
from kortex.engines.knowledge.sources import ReferenceSourceProvider

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.knowledge")

_REGISTERED_CAPABILITIES: List[str] = [
    "kortex.knowledge.query.search",
    "kortex.knowledge.graph.traverse",
    "kortex.knowledge.pack.load",
    "kortex.knowledge.source.index",
]


class KnowledgeEngine(BaseEngine, IKnowledgeEngine, IEngineDiagnostics):
    """KORTEX Knowledge Engine Facade — see module docstring."""

    def __init__(self) -> None:
        super().__init__()
        self._graph = KnowledgeGraph()
        self._lineage_manager: Optional[KnowledgeLineageManager] = None
        self._annotation_manager: Optional[KnowledgeAnnotationManager] = None
        self._search_engine: Optional[KnowledgeSearchEngine] = None
        self._pack_manager: Optional[KnowledgePackManager] = None
        default_provider = ReferenceSourceProvider()
        self._source_providers: Dict[str, IKnowledgeSourceProvider] = {
            default_provider.source_id(): default_provider,
        }
        self._kernel: Optional["Kernel"] = None
        self._metrics: Dict[str, Any] = {
            "sources_indexed": 0,
            "records_ingested": 0,
            "packs_loaded": 0,
            "queries_executed": 0,
        }

    @property
    def name(self) -> str:
        """Unique identifier name for this engine."""
        return "knowledge"

    @property
    def dependencies(self) -> List[str]:
        """Names of prerequisite foundation engines."""
        return ["storage", "registry"]

    @property
    def graph(self) -> KnowledgeGraph:
        """Direct read access to the underlying `KnowledgeGraph` — mirrors
        `StorageEngine`'s own `.data`/`.file`/`.object`/`.cache` accessor
        pattern for a sub-component callers may need directly (e.g. to seed
        nodes/relationships, which no `IKnowledgeEngine` method exposes)."""
        return self._graph

    # -- Lifecycle Implementation --------------------------------------------

    async def initialize(self, kernel: "Kernel") -> None:
        """Wire real managers from the resolved Storage Engine and register
        capabilities with the Kernel."""
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Knowledge Engine...")
        try:
            self._kernel = kernel
            storage_engine = kernel.get_engine("storage")

            data_store = getattr(storage_engine, "data", None)
            if data_store is None:
                raise KortexError("Storage Engine did not provide an IDataStore instance.")
            object_store = getattr(storage_engine, "object", None)
            if object_store is None:
                raise KortexError("Storage Engine did not provide an IObjectStore instance.")

            self._lineage_manager = KnowledgeLineageManager(data_store=data_store)
            self._annotation_manager = KnowledgeAnnotationManager(data_store=data_store)
            self._search_engine = KnowledgeSearchEngine(self._graph, self._lineage_manager)
            self._pack_manager = KnowledgePackManager(object_store=object_store, data_store=data_store)

            await self._lineage_manager.load()
            await self._annotation_manager.load()
            await self._pack_manager.load()

            kernel.register_capability(
                name="kortex.knowledge.query.search",
                description="Execute a multi-modal knowledge search",
                provider=self.name,
                handler=self.search,
                required_permissions=["knowledge:read"],
            )
            kernel.register_capability(
                name="kortex.knowledge.graph.traverse",
                description="Traverse the knowledge graph from a node",
                provider=self.name,
                handler=self.traverse_graph,
                required_permissions=["knowledge:read"],
            )
            kernel.register_capability(
                name="kortex.knowledge.graph.list",
                description="List every knowledge graph node registered for a tenant",
                provider=self.name,
                handler=self.list_nodes,
                required_permissions=["knowledge:read"],
            )
            kernel.register_capability(
                name="kortex.knowledge.pack.load",
                description="Load and verify a .kortex-knowledge pack",
                provider=self.name,
                handler=self.load_pack,
                required_permissions=["knowledge:write"],
            )
            kernel.register_capability(
                name="kortex.knowledge.source.index",
                description="Index a registered knowledge source",
                provider=self.name,
                handler=self.index_source,
                required_permissions=["knowledge:write"],
            )

            self._set_state(EngineState.READY)
            self.logger.info("Knowledge Engine initialized successfully.")
        except Exception as exc:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize Knowledge Engine: %s", exc, exc_info=True)
            raise

    async def start(self) -> None:
        """Start active background services (none: this engine has no
        background loops or listeners)."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Knowledge Engine is RUNNING.")

    async def stop(self) -> None:
        """Gracefully shut down. No background tasks or open resources to
        release — matches `StorageEngine`'s own minimal `stop()` shape."""
        if self._state in (EngineState.STOPPED, EngineState.UNINITIALIZED):
            return
        self._set_state(EngineState.STOPPING)
        self.logger.info("Stopping Knowledge Engine...")
        self._set_state(EngineState.STOPPED)
        self.logger.info("Knowledge Engine stopped cleanly.")

    async def health_check(self) -> Dict[str, Any]:
        """Perform diagnostic health check."""
        return self.health()

    # -- Event emission -------------------------------------------------------

    async def _emit_event(self, event: KnowledgeBaseEvent) -> None:
        """Best-effort event emission, matching Connector/Document/Workflow
        Engine's own `_publish_event`/`_emit_event` convention: never blocks
        or fails the operation the event merely reports on."""
        if self._kernel is None:
            return
        try:
            await self._kernel.publish_event(
                topic=event.event_type,
                payload=event.model_dump(),
                sender=self.name,
            )
        except Exception as exc:
            self.logger.warning("Failed to publish Knowledge Engine event '%s': %s", event.event_type, exc)

    # -- IKnowledgeEngine -------------------------------------------------------

    async def index_source(self, source_id: str, tenant_id: str) -> List[KnowledgeRecord]:
        """Index a registered knowledge source: resolve `source_id`, call
        its `ingest()`, persist every returned record via
        `KnowledgeLineageManager.create_record()`. See module docstring for
        why this can never mint anything but `SOURCE_EVIDENCE` records.

        Raises `KnowledgeSourceNotFoundError` if `source_id` is not
        registered.
        """
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        provider = self._source_providers.get(source_id)
        if provider is None:
            raise KnowledgeSourceNotFoundError(
                f"No knowledge source provider registered for source_id={source_id!r}."
            )

        assert self._lineage_manager is not None
        ingested = await provider.ingest(tenant_id)
        created: List[KnowledgeRecord] = []
        for record in ingested:
            created.append(await self._lineage_manager.create_record(record))

        self._metrics["sources_indexed"] += 1
        self._metrics["records_ingested"] += len(created)
        await self._emit_event(
            KnowledgeNodeIndexedEvent(tenant_id=tenant_id, source_id=source_id, record_count=len(created))
        )
        return created

    async def load_pack(self, pack: KnowledgePack) -> KnowledgePack:
        """Verify and durably register a `.kortex-knowledge` pack — delegates
        entirely to `KnowledgePackManager.load_pack()` (see `packs.py`)."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        assert self._pack_manager is not None
        loaded = await self._pack_manager.load_pack(pack)
        self._metrics["packs_loaded"] += 1
        await self._emit_event(KnowledgePackLoadedEvent(tenant_id=loaded.tenant_id, asset_id=loaded.asset_id))
        return loaded

    async def search(self, query: KnowledgeQuery) -> KnowledgeQueryResult:
        """Execute a multi-modal knowledge search — delegates to
        `KnowledgeSearchEngine.search_hybrid()` (see `search.py`, M8)."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        assert self._search_engine is not None
        result = await self._search_engine.search_hybrid(query)
        self._metrics["queries_executed"] += 1
        await self._emit_event(
            KnowledgeQueryExecutedEvent(
                tenant_id=query.tenant_id, query_id=query.query_id, execution_time_ms=result.execution_time_ms
            )
        )
        return result

    async def query_knowledge(self, query: KnowledgeQuery) -> KnowledgeQueryResult:
        """See module docstring: no evidence distinguishes this from
        `search()` at the facade level; both delegate identically."""
        return await self.search(query)

    # -- Additive facade methods (not on the frozen IKnowledgeEngine Protocol) --

    async def traverse_graph(self, node_id: str, tenant_id: str, max_hops: int) -> List[KnowledgeNode]:
        """Backs the `kortex.knowledge.graph.traverse` capability (spec
        §13). `IKnowledgeEngine`'s frozen Protocol has no `traverse` method
        of its own — purely additive, matching the established pattern of
        additive methods beyond a Protocol's declared surface
        (`graph.py::list_nodes`, `lineage.py::list_current_records`)."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        return self._graph.traverse(node_id, tenant_id, max_hops)

    def list_nodes(self, tenant_id: str) -> List[KnowledgeNode]:
        """Backs the `kortex.knowledge.graph.list` capability (Slice 4.7).

        `kortex.knowledge.query.search`'s handler (`self.search`) expects a
        live `KnowledgeQuery` object but the Kernel dispatcher only ever
        delivers plain, JSON-deserialized dicts as capability parameters —
        confirmed to raise `AttributeError` over the real IPC path. Rather
        than modify that existing, already-registered capability, this
        exposes `KnowledgeGraph.list_nodes` (primitive `str` parameter,
        already immune to that gap — see `traverse_graph` above, which has
        the same property) as the desktop's entity-discovery entry point.
        Purely additive, matching `traverse_graph`'s own established
        pattern of additive methods beyond `IKnowledgeEngine`'s frozen
        Protocol surface."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        return self._graph.list_nodes(tenant_id)

    # -- Common Diagnostics Interface (IEngineDiagnostics) -----------------------

    def health(self) -> Dict[str, Any]:
        """Return diagnostic health checks."""
        return {
            "engine": self.name,
            "status": self._state.value,
            "healthy": self._state in (EngineState.READY, EngineState.RUNNING),
            "lineage_manager_implemented": self._lineage_manager is not None,
            "annotation_manager_implemented": self._annotation_manager is not None,
            "search_engine_implemented": self._search_engine is not None,
            "pack_manager_implemented": self._pack_manager is not None,
        }

    def metrics(self) -> Dict[str, Any]:
        """Return operational runtime metrics."""
        return dict(self._metrics)

    def diagnostics(self) -> Dict[str, Any]:
        """Return detailed technical diagnostics."""
        return {
            "engine": self.name,
            "version": self.version(),
            "state": self._state.value,
            "capabilities": self.capabilities(),
            "metrics": self.metrics(),
            "registered_source_providers": list(self._source_providers.keys()),
        }

    def status(self) -> str:
        """Return current operational state name string."""
        return self._state.value

    def version(self) -> str:
        """Return semantic version string."""
        return "1.0.0"

    def capabilities(self) -> List[str]:
        """Return list of capability strings registered by this engine."""
        return list(_REGISTERED_CAPABILITIES)
