"""Unit tests for the Knowledge Engine Persistence Layer (Milestone M7).

Verifies `KnowledgeLineageManager` and `KnowledgeAnnotationManager` remain
100% backward compatible with no `data_store` (Milestone M3/M4 behavior
unchanged), and that when a `data_store` is provided: mutations durably
persist and survive reconstruction (`load()`), tenant isolation holds
across a reload, and a persistence failure leaves the in-memory state
completely untouched (no divergence between memory and store). Also
proves the documented architectural boundary: `KnowledgeGraph` (Milestone
M2) deliberately receives no `data_store` parameter in this milestone.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from kortex.core.db import DatabaseEngineManager
from kortex.engines.knowledge.annotations import KnowledgeAnnotationManager
from kortex.engines.knowledge.graph import KnowledgeGraph
from kortex.engines.knowledge.lineage import KnowledgeLineageManager
from kortex.engines.knowledge.models import (
    KnowledgeActorType,
    KnowledgeAnnotation,
    KnowledgeAnnotationType,
    KnowledgeRecord,
    KnowledgeRecordStatus,
    KnowledgeRecordType,
    KnowledgeTrustState,
)
from kortex.engines.knowledge.exceptions import KnowledgePersistenceError
from kortex.engines.knowledge.persistence import KnowledgeRecordRow
from kortex.engines.storage.stores.data_store import RelationalDataStore

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _build_data_store(tmp_path: Path, name: str = "m7") -> RelationalDataStore:
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{tmp_path}/{name}.db")
    await db_manager.create_all_tables()
    return RelationalDataStore(db_manager)


class _FailingDataStore:
    """Simulates an `IDataStore` operational failure — mirrors the
    established `_FailingDataStore` pattern already used in
    `test_capability_dispatch.py`."""

    async def get_session(self) -> Any:  # pragma: no cover - not exercised by these tests
        raise AssertionError("get_session should not be called directly")

    async def execute_in_transaction(self, action: Any) -> Any:
        raise RuntimeError("simulated storage failure")


def _record(
    record_id: str,
    version_id: str,
    tenant_id: str = "tenant-a",
    parent_version_id: str | None = None,
    status: KnowledgeRecordStatus = KnowledgeRecordStatus.CURRENT,
    trust_state: KnowledgeTrustState = KnowledgeTrustState.AI_CANDIDATE,
    successor_version_id: str | None = None,
) -> KnowledgeRecord:
    return KnowledgeRecord(
        record_id=record_id,
        tenant_id=tenant_id,
        version_id=version_id,
        parent_version_id=parent_version_id,
        record_type=KnowledgeRecordType.FACT,
        trust_state=trust_state,
        created_by="user-1",
        created_by_type=KnowledgeActorType.USER,
        created_at=_NOW,
        status=status,
        successor_version_id=successor_version_id,
    )


def _annotation(
    annotation_id: str,
    target_record_id: str = "rec-1",
    tenant_id: str = "tenant-a",
    supersedes_annotation_id: str | None = None,
    content: str = "a remark",
) -> KnowledgeAnnotation:
    return KnowledgeAnnotation(
        annotation_id=annotation_id,
        tenant_id=tenant_id,
        target_record_id=target_record_id,
        annotation_type=KnowledgeAnnotationType.REMARK,
        actor_id="user-1",
        actor_type=KnowledgeActorType.USER,
        content=content,
        created_at=_NOW,
        supersedes_annotation_id=supersedes_annotation_id,
    )


# -- Backward compatibility: no data_store behaves exactly as M3/M4 ---------------


@pytest.mark.asyncio
async def test_lineage_manager_with_no_data_store_is_unaffected() -> None:
    manager = KnowledgeLineageManager()
    await manager.load()  # must be a safe no-op
    record = await manager.create_record(_record("rec-1", "v1"))
    assert record.trust_state == KnowledgeTrustState.AI_CANDIDATE
    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None


@pytest.mark.asyncio
async def test_annotation_manager_with_no_data_store_is_unaffected() -> None:
    manager = KnowledgeAnnotationManager()
    await manager.load()  # must be a safe no-op
    annotation = await manager.add_annotation(_annotation("a1"))
    assert annotation.annotation_id == "a1"
    listed = await manager.list_annotations("rec-1", "tenant-a")
    assert listed == [annotation]


# -- KnowledgeLineageManager: persist + reload -------------------------------------


@pytest.mark.asyncio
async def test_create_record_persists_and_survives_reload(tmp_path: Path) -> None:
    data_store = await _build_data_store(tmp_path)
    manager = KnowledgeLineageManager(data_store=data_store)
    original = await manager.create_record(_record("rec-1", "v1"))

    reloaded_manager = KnowledgeLineageManager(data_store=data_store)
    await reloaded_manager.load()

    current = await reloaded_manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.record_id == original.record_id
    assert current.version_id == original.version_id
    assert current.trust_state == original.trust_state
    assert current.status == KnowledgeRecordStatus.CURRENT


@pytest.mark.asyncio
async def test_supersede_persists_and_survives_reload(tmp_path: Path) -> None:
    data_store = await _build_data_store(tmp_path, "supersede")
    manager = KnowledgeLineageManager(data_store=data_store)
    await manager.create_record(_record("rec-1", "v1"))
    await manager.supersede("rec-1", "tenant-a", _record("rec-1", "v2", parent_version_id="v1"))

    reloaded_manager = KnowledgeLineageManager(data_store=data_store)
    await reloaded_manager.load()

    lineage = await reloaded_manager.get_lineage("rec-1", "tenant-a")
    assert [v.version_id for v in lineage] == ["v1", "v2"]
    assert lineage[0].status == KnowledgeRecordStatus.SUPERSEDED
    assert lineage[0].successor_version_id == "v2"
    assert lineage[1].status == KnowledgeRecordStatus.CURRENT

    current = await reloaded_manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.version_id == "v2"


@pytest.mark.asyncio
async def test_promote_persists_and_survives_reload(tmp_path: Path) -> None:
    data_store = await _build_data_store(tmp_path, "promote")
    manager = KnowledgeLineageManager(data_store=data_store)
    await manager.create_record(_record("rec-1", "v1", trust_state=KnowledgeTrustState.AI_CANDIDATE))
    await manager.promote("rec-1", "tenant-a", "actor-1", KnowledgeActorType.USER, KnowledgeTrustState.HUMAN_CONFIRMED)

    reloaded_manager = KnowledgeLineageManager(data_store=data_store)
    await reloaded_manager.load()

    current = await reloaded_manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.trust_state == KnowledgeTrustState.HUMAN_CONFIRMED


@pytest.mark.asyncio
async def test_lineage_tenant_isolation_preserved_across_reload(tmp_path: Path) -> None:
    data_store = await _build_data_store(tmp_path, "tenant_iso")
    manager = KnowledgeLineageManager(data_store=data_store)
    await manager.create_record(_record("rec-1", "v1", tenant_id="tenant-a"))
    await manager.create_record(_record("rec-1", "v1", tenant_id="tenant-b"))

    reloaded_manager = KnowledgeLineageManager(data_store=data_store)
    await reloaded_manager.load()

    current_a = await reloaded_manager.get_current("rec-1", "tenant-a")
    current_b = await reloaded_manager.get_current("rec-1", "tenant-b")
    assert current_a is not None and current_a.tenant_id == "tenant-a"
    assert current_b is not None and current_b.tenant_id == "tenant-b"


# -- KnowledgeLineageManager: persistence failure causes no partial mutation ------


@pytest.mark.asyncio
async def test_create_record_persistence_failure_leaves_no_partial_state() -> None:
    manager = KnowledgeLineageManager(data_store=_FailingDataStore())
    with pytest.raises(KnowledgePersistenceError) as exc_info:
        await manager.create_record(_record("rec-1", "v1"))
    assert isinstance(exc_info.value.__cause__, RuntimeError)

    # No in-memory state whatsoever -- the failed durable write must have
    # prevented the in-memory mutation from happening at all.
    assert await manager.get_current("rec-1", "tenant-a") is None
    assert manager._versions == {}
    assert manager._current_version_id == {}


@pytest.mark.asyncio
async def test_supersede_persistence_failure_leaves_no_partial_state(tmp_path: Path) -> None:
    data_store = await _build_data_store(tmp_path, "supersede_fail")
    manager = KnowledgeLineageManager(data_store=data_store)
    await manager.create_record(_record("rec-1", "v1"))

    manager._data_store = _FailingDataStore()  # simulate a subsequent storage outage
    with pytest.raises(KnowledgePersistenceError) as exc_info:
        await manager.supersede("rec-1", "tenant-a", _record("rec-1", "v2", parent_version_id="v1"))
    assert isinstance(exc_info.value.__cause__, RuntimeError)

    # The original current version must be completely unaffected by the failed attempt.
    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.version_id == "v1"
    assert current.status == KnowledgeRecordStatus.CURRENT
    lineage = await manager.get_lineage("rec-1", "tenant-a")
    assert [v.version_id for v in lineage] == ["v1"]


@pytest.mark.asyncio
async def test_promote_persistence_failure_leaves_no_partial_state(tmp_path: Path) -> None:
    data_store = await _build_data_store(tmp_path, "promote_fail")
    manager = KnowledgeLineageManager(data_store=data_store)
    await manager.create_record(_record("rec-1", "v1", trust_state=KnowledgeTrustState.AI_CANDIDATE))

    manager._data_store = _FailingDataStore()
    with pytest.raises(KnowledgePersistenceError) as exc_info:
        await manager.promote(
            "rec-1", "tenant-a", "actor-1", KnowledgeActorType.USER, KnowledgeTrustState.HUMAN_CONFIRMED
        )
    assert isinstance(exc_info.value.__cause__, RuntimeError)

    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.trust_state == KnowledgeTrustState.AI_CANDIDATE  # unchanged by the failed promotion


# -- KnowledgeAnnotationManager: persist + reload ----------------------------------


@pytest.mark.asyncio
async def test_add_annotation_persists_and_survives_reload(tmp_path: Path) -> None:
    data_store = await _build_data_store(tmp_path, "annotations")
    manager = KnowledgeAnnotationManager(data_store=data_store)
    await manager.add_annotation(_annotation("a1"))

    reloaded_manager = KnowledgeAnnotationManager(data_store=data_store)
    await reloaded_manager.load()

    listed = await reloaded_manager.list_annotations("rec-1", "tenant-a")
    assert len(listed) == 1
    assert listed[0].annotation_id == "a1"
    assert listed[0].content == "a remark"


@pytest.mark.asyncio
async def test_list_annotations_preserves_insertion_order_after_reload(tmp_path: Path) -> None:
    data_store = await _build_data_store(tmp_path, "annotation_order")
    manager = KnowledgeAnnotationManager(data_store=data_store)
    await manager.add_annotation(_annotation("a1", content="first"))
    await manager.add_annotation(_annotation("a2", content="second"))
    await manager.add_annotation(_annotation("a3", content="third"))

    reloaded_manager = KnowledgeAnnotationManager(data_store=data_store)
    await reloaded_manager.load()

    listed = await reloaded_manager.list_annotations("rec-1", "tenant-a")
    assert [a.annotation_id for a in listed] == ["a1", "a2", "a3"]


@pytest.mark.asyncio
async def test_annotation_tenant_isolation_preserved_across_reload(tmp_path: Path) -> None:
    data_store = await _build_data_store(tmp_path, "annotation_tenant_iso")
    manager = KnowledgeAnnotationManager(data_store=data_store)
    await manager.add_annotation(_annotation("a1", tenant_id="tenant-a"))
    await manager.add_annotation(_annotation("a1", tenant_id="tenant-b"))  # same id, different tenant

    reloaded_manager = KnowledgeAnnotationManager(data_store=data_store)
    await reloaded_manager.load()

    listed_a = await reloaded_manager.list_annotations("rec-1", "tenant-a")
    listed_b = await reloaded_manager.list_annotations("rec-1", "tenant-b")
    assert [a.tenant_id for a in listed_a] == ["tenant-a"]
    assert [a.tenant_id for a in listed_b] == ["tenant-b"]


@pytest.mark.asyncio
async def test_add_annotation_persistence_failure_leaves_no_partial_state() -> None:
    manager = KnowledgeAnnotationManager(data_store=_FailingDataStore())
    with pytest.raises(KnowledgePersistenceError) as exc_info:
        await manager.add_annotation(_annotation("a1"))
    assert isinstance(exc_info.value.__cause__, RuntimeError)

    assert await manager.list_annotations("rec-1", "tenant-a") == []
    assert manager._annotations == {}
    assert manager._by_target == {}


# -- Documented architectural boundary: KnowledgeGraph is NOT persisted in M7 -----


def test_knowledge_graph_constructor_has_no_data_store_parameter_by_design() -> None:
    """`IKnowledgeGraph`'s Protocol (frozen since M1) declares its methods
    synchronous; `IDataStore.execute_in_transaction` is only awaitable.
    Milestone M7 therefore deliberately does NOT add persistence to
    `KnowledgeGraph` — this test proves that boundary is real and
    intentional, not an oversight."""
    signature = inspect.signature(KnowledgeGraph.__init__)
    assert list(signature.parameters) == ["self"]


# -- Concurrency: adversarial-audit findings, reproduced and fixed ----------------


@pytest.mark.asyncio
async def test_concurrent_create_record_for_same_identity_does_not_corrupt_current_pointer(
    tmp_path: Path,
) -> None:
    """Regression guard for a real defect found during this milestone's
    adversarial audit: persisting introduces a genuine `await` suspension
    point inside `create_record` where none existed in Milestone M3/M6
    (nothing inside it ever actually suspended without a `data_store`).
    Two concurrent `create_record()` calls for the same
    `(tenant_id, record_id)` could both pass the "does not already exist"
    check before either persisted — reproduced with exactly this shape
    before the `asyncio.Lock` fix, leaving two rows both marked CURRENT in
    the durable store. Exactly one call must succeed; the durable store
    must end with exactly one CURRENT row."""
    data_store = await _build_data_store(tmp_path, "race_create")
    manager = KnowledgeLineageManager(data_store=data_store)

    results = await asyncio.gather(
        manager.create_record(_record("rec-1", "v1")),
        manager.create_record(_record("rec-1", "v2")),
        return_exceptions=True,
    )
    successes = [r for r in results if isinstance(r, KnowledgeRecord)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1

    async def _action(session: Any) -> list[Any]:
        result = await session.execute(select(KnowledgeRecordRow))
        return list(result.scalars().all())

    rows = await data_store.execute_in_transaction(_action)
    current_rows = [r for r in rows if r.status == KnowledgeRecordStatus.CURRENT.value]
    assert len(current_rows) == 1  # never two CURRENT rows for the same identity


@pytest.mark.asyncio
async def test_concurrent_add_annotation_for_same_identity_does_not_duplicate(tmp_path: Path) -> None:
    """Same class of finding as the lineage race above, for
    `KnowledgeAnnotationManager.add_annotation`: two concurrent calls for
    the same `(tenant_id, annotation_id)` must not both succeed."""
    data_store = await _build_data_store(tmp_path, "race_annotation")
    manager = KnowledgeAnnotationManager(data_store=data_store)

    results = await asyncio.gather(
        manager.add_annotation(_annotation("a1", content="first")),
        manager.add_annotation(_annotation("a1", content="second")),
        return_exceptions=True,
    )
    successes = [r for r in results if isinstance(r, KnowledgeAnnotation)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1

    listed = await manager.list_annotations("rec-1", "tenant-a")
    assert len(listed) == 1
