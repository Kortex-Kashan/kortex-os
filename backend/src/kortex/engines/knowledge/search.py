"""
KORTEX Knowledge Engine — Search Coordinator (Milestone M8).

Implements `IKnowledgeSearchEngine` (`interfaces.py:144-158`, declared
since M1, unimplemented until now): `search_text`, `search_graph`,
`search_hybrid`, each `async (query: KnowledgeQuery) -> KnowledgeQueryResult`.

A pure read-side coordinator over the already-built `KnowledgeGraph`
(Milestone M2) and `KnowledgeLineageManager` (Milestone M3/M7), injected
via the constructor (the Protocol's own method shape — query in, result
out, no manager arguments — is the only way this composes: the concrete
class must hold the references). Mirrors the coordinator-over-existing-
components principle already used by the Kernel's `CapabilityDispatcher`
(unrelated Security-Engine milestone, same repository) and by Connector/
Workflow Engine's own orchestration-over-registries style: this module
never re-implements graph traversal, lineage resolution, persistence,
annotation management, or source-provider behavior — it only calls the
existing public methods that already do those things.

Locked architectural decisions for this milestone (Chief Architect,
authorizing this implementation):
- NO vector-embedding similarity ranking, indexes, or retrieval — the
  existence of `KnowledgeNode.vector_embedding` does not authorize it here.
- NO `ICacheStore` integration of any kind (traversal-path caching,
  search-result caching, invalidation) — `graph.py`'s own Milestone M2
  docstring attributed this to Milestone M7, which (verified this
  session) did not deliver it; M8 does not silently absorb that residual.
- Traversal depth (`KnowledgeQuery` has no `max_hops`/`depth` field, frozen
  since M1): `_DEFAULT_TRAVERSAL_MAX_HOPS = 3` below is not a guess — it is
  the *only* hop-count that appears anywhere in this repository's own
  evidence trail: the frozen spec's §16 performance budget ("Graph
  traversal queries <= 50ms for 3-hop neighbor lookups") and `graph.py`'s
  own already-passing `test_traverse_three_hop_meets_performance_budget`
  test, which exercises exactly `max_hops=3`. This constant is an
  M8-internal implementation detail, not a new contract — it is not
  exposed on any model or Protocol, and `KnowledgeQuery` itself is
  unmodified.

Empty `query_text`: `KnowledgeQuery.query_text` already has
`Field(..., min_length=1)` (frozen, M1) — an empty string can never reach
this module at all, since `KnowledgeQuery` construction itself raises a
`pydantic.ValidationError` first. This module relies on that existing
validation rather than re-implementing an empty-text check.

`max_results`: `KnowledgeQuery.max_results` (frozen, M1) carries no
`ge=0`/similar constraint — any `int`, including zero or negative, is a
valid value at the model layer. Since no repository evidence defines what
a negative `max_results` should mean, this module treats every
non-positive value (`<= 0`) identically to `0` — "return no results" —
the simplest, deterministic reading of "a result-count cap," and
explicitly avoids Python's surprising negative-slice semantics
(`list[:-1]` would otherwise drop the *last* element, not "return
everything" or "return nothing").

Determinism: neither `IKnowledgeGraph` nor `IKnowledgeRecordManager`
defines a result ordering, and this module does not rely on dict/insertion
iteration order (explicitly disclaimed as an accident waiting to happen).
`matching_records` is sorted by `record_id`; `matching_nodes` is sorted by
`node_id` — simple, stable, fully deterministic tiebreakers, not a
relevance-ranking system (none is introduced).

Trust-state filtering is the load-bearing security invariant this module
inherits from `KnowledgeQuery.trust_states`'s own frozen default
(excludes `SOURCE_EVIDENCE`/`AI_CANDIDATE`) — `search_text`/`search_hybrid`
apply it to every candidate's *resolved* trust state (the `as_of` version's
trust state when `as_of` is set, not the current version's) before it can
ever enter a result. Tenant isolation is structural: every enumeration
call is scoped to `query.tenant_id`.

Disclosed scope gap (found during this milestone's own implementation, not
hidden): `search_graph`/`search_hybrid` cannot populate
`KnowledgeQueryResult.graph_relationships` with real `KnowledgeRelationship`
objects. Neither `find_neighbors` nor `traverse` (the only authorized,
already-existing `KnowledgeGraph` methods this module may call) returns
relationship objects, only nodes — and this milestone's authorized file
scope adds exactly one method to `graph.py` (`list_nodes`), not a second
one for relationship enumeration. Reaching into `KnowledgeGraph._relationships`/
`_outgoing` directly would violate the explicit "do not access private
storage structures directly" requirement. `graph_relationships` is
therefore always `[]` in this milestone's results — a deliberate,
disclosed limitation, not a silent omission; closing it would require a
third additive method (or a Protocol change) outside this milestone's
authorized scope.

`search_hybrid` reuses `search_text()`/`search_graph()` verbatim — it
re-applies no filtering, re-caps no `max_results`, and re-deduplicates
nothing itself; each sub-search's own result is already fully processed
(filtered, ordered, capped) by the time `search_hybrid` merges them.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import List, Optional

from kortex.engines.knowledge.graph import KnowledgeGraph
from kortex.engines.knowledge.lineage import KnowledgeLineageManager
from kortex.engines.knowledge.models import (
    KnowledgeNode,
    KnowledgeQuery,
    KnowledgeQueryResult,
    KnowledgeRecord,
)

_DEFAULT_TRAVERSAL_MAX_HOPS = 3


def _clamp_max_results(count: Optional[int]) -> Optional[int]:
    """`None` means unlimited (the model's own default/contract). Any
    concrete value <= 0 means "return nothing" — see module docstring."""
    if count is None:
        return None
    return max(count, 0)


def _record_matches_text(record: KnowledgeRecord, query_text: str) -> bool:
    """Case-insensitive substring match over the record's textual content
    (`record_type` and `content`, stringified). No full-text index/BM25 is
    introduced — in-memory scan only, matching the no-new-infrastructure
    posture already established by every prior Knowledge Engine milestone."""
    haystack = f"{record.record_type.value} {record.content}".lower()
    return query_text.lower() in haystack


def _node_matches_text(node: KnowledgeNode, query_text: str) -> bool:
    """Case-insensitive substring match over the node's `label`."""
    return query_text.lower() in node.label.lower()


class KnowledgeSearchEngine:
    """Multi-modal search coordinator (Milestone M8). Holds references to
    an already-constructed `KnowledgeGraph` and `KnowledgeLineageManager`
    — it owns neither and persists nothing itself."""

    def __init__(self, graph: KnowledgeGraph, lineage_manager: KnowledgeLineageManager) -> None:
        self._graph = graph
        self._lineage_manager = lineage_manager

    async def _resolve_as_of(self, current_record: KnowledgeRecord, as_of: datetime) -> Optional[KnowledgeRecord]:
        """Resolve the version of `current_record` that was current as of
        `as_of`, by walking `get_lineage()`'s existing chain — never a
        second lineage implementation. Returns `None` if the record did
        not yet exist as of that timestamp (every version's `created_at`
        is after it).

        Deliberately evaluates every version and keeps whichever qualifying
        one has the latest `created_at`, rather than assuming
        `get_lineage()`'s structural (parent-link) chain order matches
        `created_at` order and stopping at the first version whose
        `created_at` exceeds `as_of` — nothing in the frozen
        `KnowledgeRecord` model or `supersede()`'s contract requires a
        caller to supply strictly increasing timestamps along a chain, so
        this does not assume it.
        """
        lineage = await self._lineage_manager.get_lineage(current_record.record_id, current_record.tenant_id)
        applicable: Optional[KnowledgeRecord] = None
        for version in lineage:
            if version.created_at <= as_of and (applicable is None or version.created_at > applicable.created_at):
                applicable = version
        return applicable

    async def search_text(self, query: KnowledgeQuery) -> KnowledgeQueryResult:
        """Full-text search over the tenant's current `KnowledgeRecord`s,
        resolved to `query.as_of` when set, filtered by
        `query.trust_states`/`query.entity_types`/`query.query_text`,
        capped by `query.max_results`, deterministically ordered by
        `record_id`."""
        start = time.perf_counter()

        current_records = await self._lineage_manager.list_current_records(query.tenant_id)

        resolved: List[KnowledgeRecord] = []
        for record in current_records:
            if query.as_of is not None:
                version = await self._resolve_as_of(record, query.as_of)
                if version is None:
                    continue
            else:
                version = record
            resolved.append(version)

        matches = [r for r in resolved if r.trust_state in query.trust_states]
        if query.entity_types:
            matches = [r for r in matches if r.record_type.value in query.entity_types]
        matches = [r for r in matches if _record_matches_text(r, query.query_text)]

        matches.sort(key=lambda r: r.record_id)
        capped = _clamp_max_results(query.max_results)
        if capped is not None:
            matches = matches[:capped]

        elapsed_ms = (time.perf_counter() - start) * 1000
        return KnowledgeQueryResult(
            query_id=query.query_id,
            matching_records=matches,
            execution_time_ms=elapsed_ms,
        )

    async def search_graph(self, query: KnowledgeQuery) -> KnowledgeQueryResult:
        """Graph-traversal search: find seed nodes matching
        `query.query_text`/`query.entity_types`, then traverse from each
        via the existing `traverse()` (see module docstring for the
        evidenced `_DEFAULT_TRAVERSAL_MAX_HOPS` default), deduplicated,
        capped by `query.max_results`, deterministically ordered by
        `node_id`. `graph_relationships` is always `[]` — see module
        docstring's disclosed scope gap."""
        start = time.perf_counter()

        all_nodes = self._graph.list_nodes(query.tenant_id)
        seeds = [
            node
            for node in all_nodes
            if (not query.entity_types or node.entity_type in query.entity_types)
            and _node_matches_text(node, query.query_text)
        ]

        seen_ids: set = set()
        matched_nodes: List[KnowledgeNode] = []
        for seed in seeds:
            if seed.node_id not in seen_ids:
                seen_ids.add(seed.node_id)
                matched_nodes.append(seed)
            for neighbor in self._graph.traverse(seed.node_id, query.tenant_id, max_hops=_DEFAULT_TRAVERSAL_MAX_HOPS):
                if neighbor.node_id not in seen_ids:
                    seen_ids.add(neighbor.node_id)
                    matched_nodes.append(neighbor)

        matched_nodes.sort(key=lambda n: n.node_id)
        capped = _clamp_max_results(query.max_results)
        if capped is not None:
            matched_nodes = matched_nodes[:capped]

        elapsed_ms = (time.perf_counter() - start) * 1000
        return KnowledgeQueryResult(
            query_id=query.query_id,
            matching_nodes=matched_nodes,
            graph_relationships=[],
            execution_time_ms=elapsed_ms,
        )

    async def search_hybrid(self, query: KnowledgeQuery) -> KnowledgeQueryResult:
        """Combined multi-modal search: reuses `search_text()` and
        `search_graph()` verbatim, merging their already fully-processed
        results into one `KnowledgeQueryResult`. Re-applies no filtering,
        re-caps no `max_results`, re-deduplicates nothing itself."""
        start = time.perf_counter()

        text_result = await self.search_text(query)
        graph_result = await self.search_graph(query)

        elapsed_ms = (time.perf_counter() - start) * 1000
        return KnowledgeQueryResult(
            query_id=query.query_id,
            matching_nodes=graph_result.matching_nodes,
            graph_relationships=graph_result.graph_relationships,
            matching_records=text_result.matching_records,
            execution_time_ms=elapsed_ms,
        )
