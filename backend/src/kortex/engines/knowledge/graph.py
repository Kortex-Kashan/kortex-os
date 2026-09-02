"""
KORTEX Knowledge Engine — Directed Knowledge Graph (Milestone M2).

Implements `IKnowledgeGraph` (`interfaces.py`) per
`docs/architecture/knowledge_engine_implementation_spec.md` v3.0.0 §8/§11,
adapted to the Chief Architect's redesigned relationship vocabulary
(`DERIVED_FROM`, `SUPERSEDES`, `RELATES_TO`, `CONTAINS`, `REFERENCES`).

In-memory only — storage-agnostic, per the Milestone M2 boundary. Spec §16
also names `ICacheStore`-backed traversal-path caching; that is Storage
Engine integration and belongs to Milestone M7, not here. The committed
`IKnowledgeGraph` Protocol declares synchronous methods (not `async`), so
this implementation is synchronous to match the contract exactly rather
than changing Milestone M1's interface to suit this milestone.

Tenant isolation is a structural invariant: all internal storage and
lookups are keyed by `(tenant_id, node_id)` / `(tenant_id, relationship_id)`
— identical IDs across tenants never collide or leak into each other's
results. A relationship's endpoints are looked up scoped to that same
relationship's own `tenant_id`, so a reference to a node that exists only
under a *different* tenant is indistinguishable from a reference to a node
that does not exist at all (`KnowledgeNodeNotFoundError`) — this is
intentional tenant-isolation behavior, not an omission.

Direction: `find_neighbors` (1-hop) and `traverse` (multi-hop) both follow
outgoing edges only (`source_node_id -> target_node_id`) — one consistent
rule, not an ad hoc split between the two methods.

Known characteristic, not fixed here: `KnowledgeNode`/`KnowledgeRelationship`
are mutable by Milestone M1's own design. If a caller mutates a node or
relationship object *after* passing it to `add_node`/`add_relationship`,
the graph's internal `(tenant_id, id)` index can desynchronize from that
object's own fields, since these are stored by reference, not copied.
Defensively copying on every insert/read would introduce a pattern with no
precedent anywhere else in this codebase and would mean second-guessing
Milestone M1's mutability decision rather than a Milestone M2 concern —
so this is documented rather than silently patched. Callers must treat any
object passed to `add_node`/`add_relationship` as owned by the graph
afterward and not mutate it further.

Relationship integrity / cycle detection (spec §11):
- A self-referencing edge (`source_node_id == target_node_id`) is rejected
  for every relationship type — a node cannot meaningfully relate to,
  reference, contain, derive from, or supersede itself.
- Beyond that, multi-hop cycle detection is scoped per `relationship_type`,
  not graph-wide. `SUPERSEDES`, `DERIVED_FROM`, and `CONTAINS` are
  inherently hierarchical/directional; a cycle among edges of the same one
  of these types is a genuine data error. `RELATES_TO` and `REFERENCES` are
  not hierarchical and legitimately form cycles in ordinary use (e.g.
  mutual references between two documents), so they are never subject to
  the multi-hop check. `traverse` remains cycle-safe (tracks visited nodes)
  regardless, since those two types can and do form cycles.
"""

from __future__ import annotations

from collections import deque

from kortex.engines.knowledge.exceptions import (
    KnowledgeDuplicateNodeError,
    KnowledgeDuplicateRelationshipError,
    KnowledgeGraphCycleError,
    KnowledgeNodeNotFoundError,
)
from kortex.engines.knowledge.models import (
    KnowledgeNode,
    KnowledgeRelationship,
    KnowledgeRelationshipType,
)

_HIERARCHICAL_RELATIONSHIP_TYPES = frozenset(
    {
        KnowledgeRelationshipType.SUPERSEDES,
        KnowledgeRelationshipType.DERIVED_FROM,
        KnowledgeRelationshipType.CONTAINS,
    }
)


class KnowledgeGraph:
    """In-memory, tenant-scoped directed Knowledge Graph (spec §8)."""

    def __init__(self) -> None:
        self._nodes: dict[tuple[str, str], KnowledgeNode] = {}
        self._relationships: dict[tuple[str, str], KnowledgeRelationship] = {}
        # Outgoing-edge index: (tenant_id, source_node_id) -> [relationship, ...]
        self._outgoing: dict[tuple[str, str], list[KnowledgeRelationship]] = {}

    def list_nodes(self, tenant_id: str) -> list[KnowledgeNode]:
        """Return every node registered for `tenant_id`. Added in Milestone
        M8 so a search coordinator can enumerate candidate nodes without
        reaching into `_nodes` directly — purely additive; `IKnowledgeGraph`'s
        frozen Protocol (M1) is unchanged and does not declare this method.
        Synchronous, matching every other method on this class. Returns an
        empty list for an unknown/empty tenant — not an error, consistent
        with `find_neighbors`'s own empty-result convention for an isolated
        node."""
        return [node for (t, _node_id), node in self._nodes.items() if t == tenant_id]

    def add_node(self, node: KnowledgeNode) -> KnowledgeNode:
        """Add a node to the graph. Raises `KnowledgeDuplicateNodeError` if
        `(tenant_id, node_id)` already exists — duplicates are always
        rejected, never silently overwritten."""
        key = (node.tenant_id, node.node_id)
        if key in self._nodes:
            raise KnowledgeDuplicateNodeError(f"Node '{node.node_id}' already exists for tenant '{node.tenant_id}'.")
        self._nodes[key] = node
        return node

    def add_relationship(self, relationship: KnowledgeRelationship) -> KnowledgeRelationship:
        """Add a directed relationship between two existing nodes.

        Raises:
            KnowledgeDuplicateRelationshipError: `relationship_id` already
                exists for this tenant.
            KnowledgeNodeNotFoundError: either endpoint does not exist for
                this tenant (including a cross-tenant reference, which is
                indistinguishable from "does not exist").
            KnowledgeGraphCycleError: the edge is a self-loop, or (for
                `SUPERSEDES`/`DERIVED_FROM`/`CONTAINS`) would close a cycle
                within the subgraph of that same relationship type.
        """
        tenant_id = relationship.tenant_id
        rel_key = (tenant_id, relationship.relationship_id)
        if rel_key in self._relationships:
            raise KnowledgeDuplicateRelationshipError(
                f"Relationship '{relationship.relationship_id}' already exists for tenant '{tenant_id}'."
            )

        source_key = (tenant_id, relationship.source_node_id)
        if source_key not in self._nodes:
            raise KnowledgeNodeNotFoundError(
                f"Source node '{relationship.source_node_id}' not found for tenant '{tenant_id}'."
            )
        target_key = (tenant_id, relationship.target_node_id)
        if target_key not in self._nodes:
            raise KnowledgeNodeNotFoundError(
                f"Target node '{relationship.target_node_id}' not found for tenant '{tenant_id}'."
            )

        if relationship.source_node_id == relationship.target_node_id:
            raise KnowledgeGraphCycleError(
                f"Relationship '{relationship.relationship_id}' is a self-loop on "
                f"'{relationship.source_node_id}', which is invalid for any relationship type."
            )

        if relationship.relationship_type in _HIERARCHICAL_RELATIONSHIP_TYPES and self._would_create_cycle(
            tenant_id, relationship
        ):
            raise KnowledgeGraphCycleError(
                f"Adding relationship '{relationship.relationship_id}' "
                f"({relationship.relationship_type.value}) from "
                f"'{relationship.source_node_id}' to '{relationship.target_node_id}' "
                f"would create a cycle for tenant '{tenant_id}'."
            )

        self._relationships[rel_key] = relationship
        self._outgoing.setdefault((tenant_id, relationship.source_node_id), []).append(relationship)
        return relationship

    def _would_create_cycle(self, tenant_id: str, new_relationship: KnowledgeRelationship) -> bool:
        """True if `new_relationship` would close a cycle within the
        subgraph of its own `relationship_type`: true exactly when the
        proposed target can already reach the proposed source by following
        existing same-type outgoing edges."""
        target = new_relationship.target_node_id
        source = new_relationship.source_node_id

        visited: set[str] = {target}
        queue: deque[str] = deque([target])
        while queue:
            current = queue.popleft()
            for edge in self._outgoing.get((tenant_id, current), []):
                if edge.relationship_type != new_relationship.relationship_type:
                    continue
                if edge.target_node_id == source:
                    return True
                if edge.target_node_id not in visited:
                    visited.add(edge.target_node_id)
                    queue.append(edge.target_node_id)
        return False

    def find_neighbors(self, node_id: str, tenant_id: str) -> list[KnowledgeNode]:
        """Return each distinct immediate outgoing-neighbor node of
        `node_id` exactly once, scoped to `tenant_id`. If multiple distinct
        relationships (different `relationship_id`, same or different
        `relationship_type`) connect `node_id` to the same target, that
        target is still a single neighbor and appears once — consistent
        with `traverse`, which deduplicates via its visited-node set.
        Raises `KnowledgeNodeNotFoundError` if `node_id` does not exist for
        `tenant_id`."""
        if (tenant_id, node_id) not in self._nodes:
            raise KnowledgeNodeNotFoundError(f"Node '{node_id}' not found for tenant '{tenant_id}'.")
        seen: set[str] = set()
        neighbors: list[KnowledgeNode] = []
        for edge in self._outgoing.get((tenant_id, node_id), []):
            if edge.target_node_id in seen:
                continue
            seen.add(edge.target_node_id)
            neighbors.append(self._nodes[(tenant_id, edge.target_node_id)])
        return neighbors

    def traverse(self, node_id: str, tenant_id: str, max_hops: int) -> list[KnowledgeNode]:
        """Breadth-first traversal up to `max_hops` outgoing hops from
        `node_id`, scoped to `tenant_id`. Cycle-safe (never revisits a
        node) since `RELATES_TO`/`REFERENCES` edges may legitimately form
        cycles.

        Raises:
            ValueError: `max_hops` is negative.
            KnowledgeNodeNotFoundError: `node_id` does not exist for `tenant_id`.
        """
        if max_hops < 0:
            raise ValueError("max_hops must be a non-negative integer.")
        if (tenant_id, node_id) not in self._nodes:
            raise KnowledgeNodeNotFoundError(f"Node '{node_id}' not found for tenant '{tenant_id}'.")

        visited: set[str] = {node_id}
        result: list[KnowledgeNode] = []
        frontier: list[str] = [node_id]
        hops = 0
        while frontier and hops < max_hops:
            next_frontier: list[str] = []
            for current in frontier:
                for edge in self._outgoing.get((tenant_id, current), []):
                    neighbor_id = edge.target_node_id
                    if neighbor_id in visited:
                        continue
                    visited.add(neighbor_id)
                    result.append(self._nodes[(tenant_id, neighbor_id)])
                    next_frontier.append(neighbor_id)
            frontier = next_frontier
            hops += 1
        return result
