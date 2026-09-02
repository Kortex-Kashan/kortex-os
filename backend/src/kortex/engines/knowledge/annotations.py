"""
KORTEX Knowledge Engine — Annotation Management (Milestone M4).

Implements `IKnowledgeAnnotationManager` (`interfaces.py`) for
`KnowledgeAnnotation` storage and retrieval: non-destructive human
remarks/corrections/context notes attached to `KnowledgeRecord`s.

Tenant-scoped, in-memory by default; optionally durable (Milestone M7) via
`IDataStore` (`kortex.engines.storage`), passed as an optional `data_store`
constructor argument — omitting it preserves Milestone M4's original,
purely in-memory behavior exactly, so every existing caller/test
constructing `KnowledgeAnnotationManager()` with no arguments is
unaffected. `IKnowledgeAnnotationManager`'s methods are declared `async`
in the committed Protocol, which is precisely what makes real,
transactional persistence possible here with no contract change at all.

When `data_store` is provided, `add_annotation` persists via
`persistence.KnowledgeAnnotationRow` *before* the in-memory dictionaries
are touched: if the durable write raises, the in-memory state is left
exactly as it was before the call. `load()` performs the reverse
direction — reading every row back into memory, ordered by
`KnowledgeAnnotationRow.insertion_sequence` (a `time.monotonic_ns()` value
stamped at persist time, not the domain `KnowledgeAnnotation.created_at`
field, which two annotations from the same caller can share at typical
timestamp resolution, and not the row's own UUID `id`, which sorts
arbitrarily) so `list_annotations`'s documented insertion order is
preserved across a reload — and must be called explicitly after
construction, since `__init__` cannot itself be `async`.

Milestone M7 concurrency finding (reproduced and fixed during this
milestone's adversarial audit): persisting introduces a genuine `await`
suspension point inside `add_annotation` where none existed in Milestone
M4 (its check-then-act sequence was implicitly atomic with no
`data_store`, since nothing inside it ever actually suspended). Two
concurrent calls for the same `(tenant_id, annotation_id)` could otherwise
both pass the duplicate check before either persisted. An `asyncio.Lock`
serializes each call's full check-through-persist-through-memory-write
sequence per manager instance, restoring that atomicity.

Scope boundary (deliberate, evidence-based — not an oversight):
`models.py`'s own `KnowledgeAnnotation` docstring states that a
`CORRECTION`-type annotation "triggers creation of a new superseding
`KnowledgeRecord` version." That auto-triggering is NOT implemented here.
The committed `IKnowledgeAnnotationManager.add_annotation(annotation)`
signature accepts only a `KnowledgeAnnotation` — a free-text `content: str`
plus identity/actor fields — which carries none of the structured data
(`record_type`, `trust_state`, `created_by_type`, `content: Dict[str, Any]`,
etc.) that `IKnowledgeRecordManager.supersede()` requires to construct a
new `KnowledgeRecord` version. Wiring `add_annotation` directly to
`supersede()` would require either changing the frozen Milestone M1
`IKnowledgeAnnotationManager`/`KnowledgeAnnotation` contracts (out of
scope — M1-M3 are frozen) or inventing a new, uncommitted cross-manager
composition mechanism (speculative architecture with no precedent
anywhere in this repository). This manager therefore stores every
annotation — including `CORRECTION`-type ones — as the non-destructive,
coexisting record the model docstring itself also confirms ("Conflicting
remarks on the same record simply coexist; no automatic conflict
resolution is performed by the domain model"). Actually wiring
`CORRECTION` to real record supersession is left to whichever future
milestone extends the committed interfaces to carry that capability.

Persistence error normalization (closure hardening, found during the post-M8
reconciliation audit): `add_annotation`/`load` now go through a private
`_execute_in_transaction` helper that wraps any non-`KnowledgeEngineError`
storage failure in `KnowledgePersistenceError` (original exception preserved
as `__cause__`), matching `KnowledgeLineageManager`'s identical fix and
closing the same established-convention gap.

This manager never validates that `target_record_id` refers to an
existing `KnowledgeRecord`. Like `KnowledgeGraph` and
`KnowledgeLineageManager`, it is a standalone, storage-agnostic component
with no constructor-time dependency on any other engine's manager
(consistent with both of those managers' own `__init__(self) -> None`
signatures) — cross-manager referential validation between annotations
and the records they target is a composition-root concern belonging to a
later facade milestone, not to M4.

Tenant isolation is a structural invariant: internal storage is keyed by
`(tenant_id, annotation_id)`, matching the pattern established by M2/M3.
`(tenant_id, annotation_id)` duplicates are rejected, mirroring M2's
duplicate-node/-relationship precedent. A `supersedes_annotation_id` that
is provided must reference an existing annotation attached to the *same*
`(tenant_id, target_record_id)` — this is basic referential integrity,
mirroring M2's requirement that a relationship's endpoints must exist and
M3's requirement that `new_version`'s identity match the call's own
record/tenant — a dangling, cross-record, or cross-tenant supersession
reference is rejected, not silently accepted.

`list_annotations` returns every annotation attached to `target_record_id`
— including ones with a non-null `supersedes_annotation_id` — in
insertion order. No filtering or conflict-resolution is performed, per the
model's own documented characteristic; callers that want only "the
latest" annotation must interpret the `supersedes_annotation_id` chain
themselves. An unknown `target_record_id` yields an empty list, not an
error — this manager has no internal notion of "record exists" to
validate against (it does not store records), so a `target_record_id`
with zero annotations is indistinguishable from one that happens to be
unrecognized, and both are normal, non-exceptional outcomes.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.knowledge.exceptions import (
    KnowledgeAnnotationNotFoundError,
    KnowledgeDuplicateAnnotationError,
    KnowledgeEngineError,
    KnowledgePersistenceError,
)
from kortex.engines.knowledge.models import KnowledgeActorType, KnowledgeAnnotation, KnowledgeAnnotationType
from kortex.engines.knowledge.persistence import KnowledgeAnnotationRow
from kortex.engines.storage.interfaces import IDataStore


class KnowledgeAnnotationManager:
    """Tenant-scoped annotation manager (Milestone M4), optionally durable
    via `IDataStore` (Milestone M7)."""

    def __init__(self, data_store: IDataStore | None = None) -> None:
        self._annotations: dict[tuple[str, str], KnowledgeAnnotation] = {}
        # (tenant_id, target_record_id) -> [annotation_id, ...] in insertion order.
        self._by_target: dict[tuple[str, str], list[str]] = {}
        self._data_store = data_store
        # See module docstring's Milestone M7 concurrency finding.
        self._lock = asyncio.Lock()

    async def _execute_in_transaction(self, action: Any) -> Any:
        """Run `action` via the configured `IDataStore`, normalizing any
        non-`KnowledgeEngineError` failure into `KnowledgePersistenceError`
        (original exception preserved as `__cause__`)."""
        assert self._data_store is not None
        try:
            return await self._data_store.execute_in_transaction(action)
        except KnowledgeEngineError:
            raise
        except Exception as exc:
            raise KnowledgePersistenceError(f"Knowledge annotation persistence operation failed: {exc}") from exc

    async def load(self) -> None:
        """Hydrate in-memory state from the configured `IDataStore`
        (Milestone M7). No-op if no `data_store` was provided at
        construction — pure in-memory mode, identical to Milestone M4's
        original behavior. Must be called explicitly after construction
        (`__init__` cannot itself `await`)."""
        if self._data_store is None:
            return

        async def _action(session: AsyncSession) -> list[KnowledgeAnnotationRow]:
            result = await session.execute(
                select(KnowledgeAnnotationRow).order_by(KnowledgeAnnotationRow.insertion_sequence)
            )
            return list(result.scalars().all())

        async with self._lock:
            rows = await self._execute_in_transaction(_action)
            for row in rows:
                annotation = KnowledgeAnnotation(
                    annotation_id=row.annotation_id,
                    tenant_id=row.tenant_id,
                    target_record_id=row.target_record_id,
                    annotation_type=KnowledgeAnnotationType(row.annotation_type),
                    actor_id=row.actor_id,
                    actor_type=KnowledgeActorType(row.actor_type),
                    content=row.content,
                    created_at=row.annotation_created_at,
                    supersedes_annotation_id=row.supersedes_annotation_id,
                )
                self._annotations[(row.tenant_id, row.annotation_id)] = annotation
                target_key = (row.tenant_id, row.target_record_id)
                self._by_target.setdefault(target_key, []).append(row.annotation_id)

    async def _persist_annotation(self, annotation: KnowledgeAnnotation) -> None:
        """Durably insert one new row. No-op if no `data_store` was
        configured. Raises on failure — never silently swallowed, since
        the caller relies on it to decide whether the in-memory mutation
        may proceed."""
        if self._data_store is None:
            return

        async def _action(session: AsyncSession) -> None:
            session.add(
                KnowledgeAnnotationRow(
                    id=str(uuid.uuid4()),
                    tenant_id=annotation.tenant_id,
                    annotation_id=annotation.annotation_id,
                    target_record_id=annotation.target_record_id,
                    annotation_type=annotation.annotation_type.value,
                    actor_id=annotation.actor_id,
                    actor_type=annotation.actor_type.value,
                    content=annotation.content,
                    annotation_created_at=annotation.created_at,
                    supersedes_annotation_id=annotation.supersedes_annotation_id,
                    insertion_sequence=time.monotonic_ns(),
                )
            )

        await self._execute_in_transaction(_action)

    async def add_annotation(self, annotation: KnowledgeAnnotation) -> KnowledgeAnnotation:
        """Attach a new, non-destructive annotation to a `KnowledgeRecord`.

        Raises:
            KnowledgeDuplicateAnnotationError: `(tenant_id, annotation_id)`
                already exists.
            KnowledgeAnnotationNotFoundError: `supersedes_annotation_id` is
                set but does not reference an existing annotation attached
                to the same `(tenant_id, target_record_id)`.
        """
        async with self._lock:
            key = (annotation.tenant_id, annotation.annotation_id)
            if key in self._annotations:
                raise KnowledgeDuplicateAnnotationError(
                    f"Annotation '{annotation.annotation_id}' already exists for tenant '{annotation.tenant_id}'."
                )

            if annotation.supersedes_annotation_id is not None:
                superseded_key = (annotation.tenant_id, annotation.supersedes_annotation_id)
                superseded = self._annotations.get(superseded_key)
                if superseded is None or superseded.target_record_id != annotation.target_record_id:
                    raise KnowledgeAnnotationNotFoundError(
                        f"supersedes_annotation_id={annotation.supersedes_annotation_id!r} does not "
                        f"reference an existing annotation on target_record_id="
                        f"{annotation.target_record_id!r} for tenant '{annotation.tenant_id}'."
                    )

            await self._persist_annotation(annotation)

            self._annotations[key] = annotation
            target_key = (annotation.tenant_id, annotation.target_record_id)
            self._by_target.setdefault(target_key, []).append(annotation.annotation_id)
            return annotation

    async def list_annotations(self, target_record_id: str, tenant_id: str) -> list[KnowledgeAnnotation]:
        """Return every annotation attached to `target_record_id`, scoped to
        `tenant_id`, in insertion order. Returns an empty list if none
        exist — an unrecognized `target_record_id` is not an error here,
        since this manager never validates `target_record_id` against any
        stored `KnowledgeRecord`."""
        target_key = (tenant_id, target_record_id)
        annotation_ids = self._by_target.get(target_key, [])
        return [self._annotations[(tenant_id, annotation_id)] for annotation_id in annotation_ids]
