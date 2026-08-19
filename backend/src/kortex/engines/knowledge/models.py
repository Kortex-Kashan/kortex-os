"""
KORTEX Knowledge Engine Domain Models (Milestone M1 — redesigned scope).

Pydantic v2 domain models for the Knowledge Engine, covering both the
original graph-primitive contracts (per
`docs/architecture/knowledge_engine_implementation_spec.md` v3.0.0 §5) and
the organizational-memory contracts (versioned records, annotations, trust
state) added by the Chief Architect's redesigned M1 scope. The redesigned
scope materially exceeds v3.0.0's own §1/§5 and is expected to be captured
in a future formal spec amendment; until then, this module and the
Chief Architect's M1 instructions are the authoritative reference.

`KnowledgeRelationship`, `KnowledgeRecord`, and `KnowledgePack` locally
mirror the shape of `UniversalRelationship` / `UniversalAsset`
(`docs/architecture/shared_domain_models.md` §13 / §7) because no shared
implementation of those models exists anywhere in this codebase — this
follows the same convention already established by Security Engine's local
`UniversalAuditEntry` (`kortex.engines.security.models`). `KnowledgePack.manifest`
is an untyped dict for the same reason: `KortexAssetManifest` has no code
implementation to import, and validating its schema is out of scope for
Milestone M1.

`tenant_id` is present on every domain entity that logically requires
tenant ownership, per the frozen tenant-isolation requirement in
`docs/architecture/multi_tenant_architecture.md` §8 — this is a
cross-document requirement, not a field named in the Knowledge Engine
spec's own §5 list.

`KnowledgeRelationshipType` is limited to exactly the five values the Chief
Architect designated for the redesigned scope (`DERIVED_FROM`, `SUPERSEDES`,
`RELATES_TO`, `CONTAINS`, `REFERENCES`) — this supersedes the original five
values (`DEPENDS_ON`, `PARENTS`, `DERIVED_FROM`, `SUPERSEDES`, `LINKS_TO`)
used in the first M1 approval pass. `DERIVED_FROM` and `SUPERSEDES` are
reused for both structural graph edges and record-lineage edges
(provenance and supersession, respectively) — no separate lineage
relationship vocabulary is introduced.

`KnowledgeActorType` is a **local** enum (`USER`/`SERVICE_PRINCIPAL`/`AGENT`)
whose values are identical to Security Engine's real `PrincipalType`
(`kortex.engines.security.models.PrincipalType`). It is declared locally
rather than imported directly. One precedent does exist elsewhere in this
repository for a peer engine importing a Security Engine model directly —
`kortex.engines.workflow.engine` imports `TokenPayload` — but that import
exists because Workflow must pass that exact object through the shared
capability-dispatch path (`CapabilityRequest.session_token`) for Security
Engine's own `verify_token()` to cryptographically verify it; type identity
is functionally required there. No such interop point exists for
`KnowledgeActorType` — nothing in Security Engine ever receives or
type-checks a Knowledge Engine actor-type value — so only value-level
alignment is required here, not import-time coupling. Alignment with
Security's real vocabulary is verified by test
(`test_knowledge_actor_type_values_align_with_security_principal_type`),
not by import.

`KnowledgeQuery` and `KnowledgeQueryResult` are frozen (`ConfigDict(frozen=True)`)
— they represent point-in-time request/result snapshots, matching the
precedent set by Security Engine's `AccessDecision`/`CryptographicSignature`/
`UniversalAuditEntry`. `KnowledgeRecord` and `KnowledgeAnnotation` are also
frozen: a `KnowledgeRecord` is an immutable version snapshot (mirroring
Document Engine's `DocumentVersionRecord` immutability — a version is never
mutated in place, only superseded by a new version), and a
`KnowledgeAnnotation` is a non-destructive, point-in-time remark (mirroring
Workflow Engine's `ApprovalDecision` — an annotation is never edited, only
superseded by a new annotation via `supersedes_annotation_id`).
`KnowledgeNode`, `KnowledgeRelationship`, and `KnowledgePack` remain
mutable — their mutation semantics belong to later milestones (graph
updates in M2, pack ingestion in M9), not to M1.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeRelationshipType(str, Enum):
    """Semantic relationship types for Knowledge Graph edges (redesigned M1 scope)."""

    DERIVED_FROM = "DERIVED_FROM"
    SUPERSEDES = "SUPERSEDES"
    RELATES_TO = "RELATES_TO"
    CONTAINS = "CONTAINS"
    REFERENCES = "REFERENCES"


class KnowledgeActorType(str, Enum):
    """Actor identity type, value-aligned with Security Engine's `PrincipalType`
    (`kortex.engines.security.models.PrincipalType`). Declared locally per the
    established cross-engine convention; alignment is verified by test.
    """

    USER = "USER"
    SERVICE_PRINCIPAL = "SERVICE_PRINCIPAL"
    AGENT = "AGENT"


class KnowledgeRecordType(str, Enum):
    """Discriminator for what kind of organizational claim a `KnowledgeRecord` represents."""

    FACT = "FACT"
    DECISION = "DECISION"
    PROCEDURE = "PROCEDURE"
    HISTORICAL_STATE = "HISTORICAL_STATE"


class KnowledgeTrustState(str, Enum):
    """Trust/authority state of a `KnowledgeRecord`.

    Only `HUMAN_CONFIRMED` and `HUMAN_CORRECTED` may be treated as current
    organizational truth. `AI_CANDIDATE` must never silently become
    authoritative — promotion always requires an explicit, human (`USER`
    actor type) action. Promotion enforcement itself is Milestone M6 scope;
    M1 only establishes this as a domain value.
    """

    SOURCE_EVIDENCE = "SOURCE_EVIDENCE"
    AI_CANDIDATE = "AI_CANDIDATE"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
    HUMAN_CORRECTED = "HUMAN_CORRECTED"


class KnowledgeClassification(str, Enum):
    """Security classification level, mirroring Document Engine's local
    `SecurityClassification` (`kortex.engines.document.models`) — no shared
    implementation exists to import, per the established convention.
    """

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class KnowledgeRecordStatus(str, Enum):
    """Lifecycle status of a `KnowledgeRecord` version."""

    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"
    DEPRECATED = "DEPRECATED"


class KnowledgeAnnotationType(str, Enum):
    """Type of a human `KnowledgeAnnotation`."""

    REMARK = "REMARK"
    CORRECTION = "CORRECTION"
    CONTEXT = "CONTEXT"


class KnowledgeNode(BaseModel):
    """A single entity node in the Knowledge Graph (spec §5, §8).

    Mutable — node property updates are a Milestone M2 (`graph.py`) concern.
    """

    node_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    entity_type: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    properties: Dict[str, Any] = Field(default_factory=dict)
    vector_embedding: Optional[List[float]] = None


class KnowledgeRelationship(BaseModel):
    """A directed, weighted semantic edge between two Knowledge Graph nodes
    (spec §5, §8). Locally mirrors `UniversalRelationship`
    (`shared_domain_models.md` §13); no shared implementation exists to import.

    Mutable — relationship integrity/cycle-detection enforcement is a
    Milestone M2 (`graph.py`) concern, not a model-level constraint.
    """

    relationship_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    source_node_id: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    relationship_type: KnowledgeRelationshipType
    weight: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeRecord(BaseModel):
    """The single versioned unit of organizational knowledge — a fact,
    decision, procedure, or historical state, discriminated by `record_type`
    rather than as four separate models. Locally mirrors the shape of
    Document Engine's `DocumentVersionRecord` immutable-version pattern.

    Frozen — a specific version is an immutable snapshot; it is never
    mutated in place, only superseded by a new version carrying
    `parent_version_id` (Milestone M3 behavior — not implemented here).

    Distinct from `KnowledgeNode`: a node is a thing/entity in the graph; a
    record is a versioned, trust-state-aware claim about organizational
    knowledge. This distinction is load-bearing and must not be collapsed.

    Provenance is intentionally optional (no required "source" field) —
    manually authored organizational knowledge with no external source must
    remain representable.
    """

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    version_id: str = Field(..., min_length=1)
    parent_version_id: Optional[str] = None
    lineage_path: List[str] = Field(default_factory=list)
    record_type: KnowledgeRecordType
    content: Dict[str, Any] = Field(default_factory=dict)
    trust_state: KnowledgeTrustState
    classification: KnowledgeClassification = KnowledgeClassification.INTERNAL
    created_by: str = Field(..., min_length=1)
    created_by_type: KnowledgeActorType
    created_at: datetime
    status: KnowledgeRecordStatus = KnowledgeRecordStatus.CURRENT
    successor_version_id: Optional[str] = None


class KnowledgeAnnotation(BaseModel):
    """A human remark, correction, or context note attached to a
    `KnowledgeRecord`. Locally mirrors Workflow Engine's non-destructive
    `ApprovalDecision` pattern: an annotation is never edited or deleted,
    only superseded by a new annotation via `supersedes_annotation_id`.

    Frozen — a point-in-time record of what an actor said, when.

    Whether an annotation modifies knowledge or merely annotates it is
    determined by `annotation_type`: `REMARK`/`CONTEXT` are pure,
    non-destructive additions; `CORRECTION` triggers creation of a new
    superseding `KnowledgeRecord` version (Milestone M4 behavior — not
    implemented here). Conflicting remarks on the same record simply
    coexist; no automatic conflict resolution is performed by the domain
    model.
    """

    model_config = ConfigDict(frozen=True)

    annotation_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    target_record_id: str = Field(..., min_length=1)
    annotation_type: KnowledgeAnnotationType
    actor_id: str = Field(..., min_length=1)
    actor_type: KnowledgeActorType
    content: str = Field(..., min_length=1)
    created_at: datetime
    supersedes_annotation_id: Optional[str] = None


class KnowledgePack(BaseModel):
    """A declarative `.kortex-knowledge` package definition (spec §5, §7).

    Locally mirrors `UniversalAsset` (`shared_domain_models.md` §7); no shared
    implementation exists to import.

    Mutable — pack ingestion/verification is a Milestone M6 (`packs.py`) concern.
    """

    asset_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    manifest: Dict[str, Any] = Field(default_factory=dict)
    checksum_sha256: str = Field(..., min_length=1)
    digital_signature: Optional[str] = None
    size_bytes: int = Field(..., ge=0)
    mime_type: str = Field(..., min_length=1)
    storage_key: str = Field(..., min_length=1)
    bucket_name: str = "knowledge"


class KnowledgeQuery(BaseModel):
    """An immutable knowledge search request (spec §5, extended for the
    redesigned M1 scope).

    Frozen — represents a point-in-time request snapshot, not a mutable
    working object.

    `trust_states` defaults to excluding unverified content
    (`SOURCE_EVIDENCE`/`AI_CANDIDATE`) so that a query never surfaces
    unconfirmed knowledge as current truth unless explicitly requested.
    `as_of` establishes the contract for point-in-time/historical
    resolution; the actual lineage-walk resolution logic is Milestone M3/M8
    behavior, not implemented here.
    """

    model_config = ConfigDict(frozen=True)

    query_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    query_text: str = Field(..., min_length=1)
    filters: Dict[str, Any] = Field(default_factory=dict)
    entity_types: List[str] = Field(default_factory=list)
    max_results: Optional[int] = None
    trust_states: List[KnowledgeTrustState] = Field(
        default_factory=lambda: [KnowledgeTrustState.HUMAN_CONFIRMED, KnowledgeTrustState.HUMAN_CORRECTED]
    )
    as_of: Optional[datetime] = None


class KnowledgeQueryResult(BaseModel):
    """An immutable knowledge search result (spec §5, extended for the
    redesigned M1 scope).

    Frozen — represents a point-in-time executed-query record.
    """

    model_config = ConfigDict(frozen=True)

    query_id: str = Field(..., min_length=1)
    matching_nodes: List[KnowledgeNode] = Field(default_factory=list)
    graph_relationships: List[KnowledgeRelationship] = Field(default_factory=list)
    matching_records: List[KnowledgeRecord] = Field(default_factory=list)
    execution_time_ms: float = Field(..., ge=0.0)
