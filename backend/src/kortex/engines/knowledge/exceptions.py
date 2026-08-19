"""
KORTEX Knowledge Engine Exception Hierarchy.

All Knowledge Engine exceptions inherit from `KortexError`
(`kortex.core.exceptions`), following the existing KORTEX exception
conventions. Concrete subclasses are added incrementally as the
functionality that needs them is implemented (graph, lineage, annotation,
source-provider, trust-promotion, persistence-failure normalization,
pack-loading, and facade/source-resolution errors), matching the
established convention in `kortex.engines.security.exceptions`.
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


# -- Knowledge Source Providers (Milestone M5) -------------------------------


class KnowledgeSourceIngestionError(KnowledgeEngineError):
    """Raised by an `IKnowledgeSourceProvider.ingest()` implementation when
    ingestion cannot proceed: a malformed/empty `tenant_id` (fail-closed —
    ingestion never silently returns an empty result for invalid identity
    input, unlike `KnowledgeLineageManager.get_current`'s deliberate
    `Optional`-return contract for a merely *absent* record), or any other
    ingestion-time failure a concrete provider needs to normalize into the
    Knowledge Engine's own exception hierarchy rather than propagating a
    raw, provider-internal exception type."""


# -- Knowledge Trust Promotion (Milestone M6) --------------------------------


class KnowledgePromotionNotAuthorizedError(KnowledgeEngineError):
    """Raised by `promote()` when `actor_type` is not `USER`. Trust-state
    promotion to `HUMAN_CONFIRMED`/`HUMAN_CORRECTED` requires an explicit
    human action — `AGENT` and `SERVICE_PRINCIPAL` actors are always
    denied, regardless of any other argument. Checked before any record
    lookup is performed, so a non-`USER` caller cannot use `promote()` as
    an oracle to distinguish "record doesn't exist" from "denied" — both
    an unknown `record_id` and a legitimate one are rejected identically
    for a non-`USER` actor."""


class KnowledgeInvalidTrustTransitionError(KnowledgeEngineError):
    """Raised by `promote()` when the requested trust-state transition is
    not a valid promotion. Covers two cases: `new_trust_state` is not one
    of the confirmed states (`HUMAN_CONFIRMED`/`HUMAN_CORRECTED`) — this
    method promotes *to* confirmed trust, never to `SOURCE_EVIDENCE`/
    `AI_CANDIDATE`; and the record's *current* trust state is already
    confirmed — `promote()` only applies to an unconfirmed record
    (`SOURCE_EVIDENCE`/`AI_CANDIDATE`), so a repeated or redundant
    promotion attempt on an already-confirmed record is rejected rather
    than silently accepted."""


# -- Persistence Error Normalization ------------------------------------------


class KnowledgePersistenceError(KnowledgeEngineError):
    """Raised when an underlying `IDataStore` operation fails during a
    `KnowledgeLineageManager`, `KnowledgeAnnotationManager`, or
    `KnowledgePackManager` persistence call. Wraps the original exception
    (always available via `__cause__`, using `raise ... from exc` at every
    call site) rather than letting a raw storage-layer exception type
    escape unnormalized — every other Knowledge Engine failure mode is
    already a `KnowledgeEngineError` subclass; this closes the one gap
    where that convention did not hold. Raised only for genuine storage
    failures — a domain-level rejection (e.g. a duplicate identity or an
    invalid transition) is still raised as its own specific
    `KnowledgeEngineError` subclass exactly as before, never wrapped in
    this one, since those are not storage failures."""


# -- Knowledge Pack Loading ----------------------------------------------------


class KnowledgeDuplicatePackError(KnowledgeEngineError):
    """Raised when `load_pack()` is called with an `(tenant_id, asset_id)`
    that has already been loaded. Duplicate identity registration is
    always rejected rather than silently overwritten, mirroring every
    other manager's own duplicate-identity convention
    (`KnowledgeDuplicateNodeError`, `KnowledgeDuplicateAnnotationError`,
    etc.)."""


class KnowledgePackNotFoundError(KnowledgeEngineError):
    """Raised when `load_pack()`'s `pack.storage_key`/`pack.bucket_name`
    does not resolve to an actual object in the configured `IObjectStore`
    (wraps the underlying `ResourceNotFoundError` — see
    `kortex.core.exceptions` — as `__cause__`). Distinct from
    `KnowledgePersistenceError`: this is "the referenced object genuinely
    does not exist," a normal, expected failure mode for a malformed or
    stale `KnowledgePack` reference, not an operational storage failure."""


class KnowledgePackIntegrityError(KnowledgeEngineError):
    """Raised when the object retrieved from storage does not match
    `pack.checksum_sha256` (SHA-256 mismatch) or `pack.size_bytes` (byte
    count mismatch). A corrupted or tampered-with pack must never be
    loaded — this check is the load-bearing integrity gate for the entire
    pack-loading operation and always runs before any durable pack record
    is written."""


class KnowledgeInvalidManifestError(KnowledgeEngineError):
    """Raised when `pack.manifest` is empty. `KnowledgePack.manifest` is an
    untyped `Dict[str, Any]` (Milestone M1 design — no `KortexAssetManifest`
    schema implementation exists anywhere in this codebase to validate
    against, per `models.py`'s own documented rationale), so this is
    deliberately a structural check only (content must exist at all), never
    a semantic/key-schema validation — inventing key-level requirements
    beyond what is actually documented anywhere would be unevidenced
    scope, not a genuine requirement."""


# -- Knowledge Engine Facade ---------------------------------------------------


class KnowledgeSourceNotFoundError(KnowledgeEngineError):
    """Raised by `KnowledgeEngine.index_source()` when `source_id` does not
    match any source provider registered with the facade. Distinct from
    `KnowledgeSourceIngestionError` (Milestone M5), which is raised by a
    provider's own `ingest()` for a malformed *argument* to an otherwise
    correctly-resolved provider; this error means resolution itself
    failed — the requested provider does not exist at all."""
