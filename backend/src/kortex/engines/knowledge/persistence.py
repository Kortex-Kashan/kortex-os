"""
KORTEX Knowledge Engine — Persistence Layer (Milestone M7).

SQLAlchemy ORM models for durable storage of `KnowledgeRecord`
(`KnowledgeLineageManager`, Milestone M3) and `KnowledgeAnnotation`
(`KnowledgeAnnotationManager`, Milestone M4), via the existing
`StorageEngine` (`IDataStore`) — following the exact pattern already
established by Security Engine's `PrincipalRecord`/`RolePermissionRecord`
(`kortex.engines.security.models`): inherit `core.db.BaseModel`, relying on
the existing `Base.metadata.create_all()` boot path (`DatabaseEngineManager
.create_all_tables()`) for table creation. No new persistence mechanism,
no Alembic migration, no direct filesystem/database access outside
`IDataStore.execute_in_transaction` — zero direct SQL connections are ever
opened by this module itself.

Scope boundary (deliberate, evidence-based, reported rather than silently
worked around): `KnowledgeGraph` (Milestone M2) is NOT persisted here.
`IKnowledgeGraph`'s Protocol (frozen since Milestone M1) declares
`add_node`/`add_relationship`/`find_neighbors`/`traverse` as *synchronous*
methods; `IDataStore.execute_in_transaction` is only awaitable, so a
synchronous method cannot call it directly without either fragile
sync-over-async bridging or a non-transactional, best-effort write that
could not guarantee "no partial mutation after failure." Changing
`IKnowledgeGraph` to `async def` would be a Milestone M1/M2 contract
change outside Milestone M7's authority. `KnowledgeLineageManager` and
`KnowledgeAnnotationManager`, by contrast, already declare every mutating
method `async def` in their own frozen Protocols
(`IKnowledgeRecordManager`/`IKnowledgeAnnotationManager`), so real,
transactional persistence composes with their existing signatures with no
contract change at all — that is exactly what this module provides.

Every ORM row stores the domain enum/UUID/datetime fields as plain
strings (`.value` for enums, ISO-compatible via SQLAlchemy's native
`DateTime` support) — the domain models (`models.py`, frozen since M1) are
never modified; this module only mirrors their shape for durability.

`KnowledgePackRow` (added for the pack-loader closure work) follows the
identical pattern for `KnowledgePack` (Milestone M1 domain model, loading/
verification implemented in `packs.py`/`KnowledgePackManager`) — same
`core.db.BaseModel` inheritance, same tenant-scoped unique-identity
convention as the two rows above.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel


class KnowledgeRecordRow(BaseModel):
    """Durable row for one `KnowledgeRecord` version (Milestone M3 domain
    model, Milestone M7 persistence). Identity is
    `(tenant_id, record_id, version_id)` — matching
    `KnowledgeLineageManager`'s own in-memory keying exactly."""

    __tablename__ = "knowledge_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "record_id", "version_id", name="uq_knowledge_records_identity"),
    )

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    record_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_version_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    lineage_path: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    record_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    trust_state: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(64), nullable=False)
    record_created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    successor_version_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class KnowledgeAnnotationRow(BaseModel):
    """Durable row for one `KnowledgeAnnotation` (Milestone M4 domain
    model, Milestone M7 persistence). Identity is
    `(tenant_id, annotation_id)` — matching `KnowledgeAnnotationManager`'s
    own in-memory keying exactly."""

    __tablename__ = "knowledge_annotations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "annotation_id", name="uq_knowledge_annotations_identity"),
    )

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    annotation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_record_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    annotation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    annotation_created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supersedes_annotation_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Strictly-increasing per-process sequence number (`time.monotonic_ns()`
    # at insert time), NOT the row's own UUID `id` or its `created_at`
    # audit column — a UUID sorts arbitrarily, and two annotations added
    # in rapid succession can share the same wall-clock `created_at` at
    # typical timestamp resolution. `list_annotations`'s documented
    # insertion-order contract (Milestone M4) requires a reload (Milestone
    # M7) to reconstruct that exact order, so this column exists solely to
    # make that ordering reconstructible — it carries no domain meaning.
    # Caveat, accepted as out of scope: `time.monotonic_ns()`'s epoch is
    # arbitrary per-process, so values are only meaningfully ordered
    # within the single process that wrote them — consistent with this
    # project's established local-first, single-process desktop
    # architecture; no evidence anywhere assigns multi-process concurrent
    # writers to the same store to this milestone.
    insertion_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class KnowledgePackRow(BaseModel):
    """Durable row for one verified, loaded `KnowledgePack` (`models.py`,
    Milestone M1 domain model; loading/verification implemented in
    `packs.py`). Identity is `(tenant_id, asset_id)` — matching
    `KnowledgePackManager`'s own in-memory keying exactly, and mirroring
    `KnowledgeRecordRow`/`KnowledgeAnnotationRow`'s established pattern:
    inherits `core.db.BaseModel`, no new persistence mechanism."""

    __tablename__ = "knowledge_packs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "asset_id", name="uq_knowledge_packs_identity"),
    )

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(255), nullable=False)
    manifest: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    digital_signature: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    bucket_name: Mapped[str] = mapped_column(String(255), nullable=False)
    loaded_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
