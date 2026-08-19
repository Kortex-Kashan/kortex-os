"""
KORTEX Knowledge Engine Exception Hierarchy (Milestone M1 base; graph
exceptions added in Milestone M2 per this module's own original roadmap).

All Knowledge Engine exceptions inherit from `KortexError`
(`kortex.core.exceptions`), following the existing KORTEX exception
conventions.

Concrete subclasses are added incrementally as the milestones that need
them land (graph errors in M2, lineage errors in M3, annotation errors in
M4, source-provider errors in M5, trust-promotion errors in M6,
persistence errors in M7, search errors in M8, pack-verification errors in
M9, facade errors in M11), matching the established convention in
`kortex.engines.security.exceptions`.
"""

from __future__ import annotations

from kortex.core.exceptions import KortexError


class KnowledgeEngineError(KortexError):
    """Base exception for all Knowledge Engine errors."""


# -- Knowledge Graph (Milestone M2) ------------------------------------------


class KnowledgeNodeNotFoundError(KnowledgeEngineError):
    """Raised when a graph operation references a node that does not exist
    for the given `tenant_id`. Node lookups are always tenant-scoped, so a
    node that exists under a different tenant is indistinguishable from a
    node that does not exist at all — this is the intended tenant-isolation
    behavior, not an omission."""


class KnowledgeDuplicateNodeError(KnowledgeEngineError):
    """Raised when `add_node` is called with a `node_id` that already
    exists for the given `tenant_id`. Duplicate identity registration is
    always rejected rather than silently overwritten."""


class KnowledgeDuplicateRelationshipError(KnowledgeEngineError):
    """Raised when `add_relationship` is called with a `relationship_id`
    that already exists for the given `tenant_id`."""


class KnowledgeGraphCycleError(KnowledgeEngineError):
    """Raised when adding a relationship would create a cycle within the
    subgraph of that same `relationship_type`. Cycle detection is scoped
    per relationship type: `SUPERSEDES`, `DERIVED_FROM`, and `CONTAINS` are
    inherently hierarchical and a cycle among them is a genuine data error;
    `RELATES_TO` and `REFERENCES` are not hierarchical and legitimately
    form cycles in normal use (e.g. mutual references), so they are never
    subject to this check."""
