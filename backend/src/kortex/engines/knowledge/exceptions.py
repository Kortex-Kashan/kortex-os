"""
KORTEX Knowledge Engine Exception Hierarchy (Milestone M1 base; graph
exceptions added in Milestone M2, lineage exceptions added in Milestone M3,
annotation exceptions added in Milestone M4, per this module's own
original roadmap).

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


# -- Knowledge Lineage (Milestone M3) ----------------------------------------


class KnowledgeRecordNotFoundError(KnowledgeEngineError):
    """Raised when `get_lineage`/`supersede` reference a `record_id` that
    was never created for the given `tenant_id`. Distinct from
    `get_current`'s contract: `get_current` returns `None` for a missing
    record (its own declared `Optional[KnowledgeRecord]` return type makes
    that a normal, non-exceptional outcome), while every other lineage
    operation treats a wholly unknown `record_id` as an error."""


class KnowledgeLineageConsistencyError(KnowledgeEngineError):
    """Raised when a lineage operation would violate the "exactly one
    version chain, exactly one CURRENT version" invariant for a given
    `(tenant_id, record_id)`. Covers two cases: `create_record` called for
    a `record_id` that already has a version chain (an initial version can
    only ever be created once — later versions arrive via `supersede`, not
    `create_record`); and `supersede` called with a `new_version` whose
    `parent_version_id` does not match the actual current version's
    `version_id` (a stale or concurrent-modification supersession attempt)."""


class KnowledgePromotionNotEnforcedError(KnowledgeEngineError):
    """Raised unconditionally by `promote()` in Milestone M3.

    Trust-state promotion is deliberately not implemented here: `promote()`
    exists only so `KnowledgeLineageManager` structurally satisfies
    `IKnowledgeRecordManager`. Milestone M6 delivers the real state
    transition together with its `USER`-only actor-type enforcement as a
    single unit — Milestone M3 must never provide a working, unenforced
    transition in the interim, which would create a window where any actor
    type could promote a record to `HUMAN_CONFIRMED`/`HUMAN_CORRECTED`.
    """


# -- Knowledge Annotations (Milestone M4) ------------------------------------


class KnowledgeDuplicateAnnotationError(KnowledgeEngineError):
    """Raised when `add_annotation` is called with an `annotation_id` that
    already exists for the given `tenant_id`. Duplicate identity
    registration is always rejected rather than silently overwritten,
    mirroring M2's `KnowledgeDuplicateNodeError`/`KnowledgeDuplicateRelationshipError`."""


class KnowledgeAnnotationNotFoundError(KnowledgeEngineError):
    """Raised when `add_annotation` is given a `supersedes_annotation_id`
    that does not reference an existing annotation attached to the same
    `(tenant_id, target_record_id)`. A dangling reference, or one pointing
    at an annotation attached to a *different* record or a *different*
    tenant, is rejected at write time — mirroring M2's endpoint-existence
    check on `add_relationship` and M3's identity-match check on
    `supersede`."""
