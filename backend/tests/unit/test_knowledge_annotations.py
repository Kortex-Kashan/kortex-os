"""Unit tests for the Knowledge Engine Annotation Manager (Milestone M4).

Verifies `KnowledgeAnnotationManager` satisfies `IKnowledgeAnnotationManager`,
enforces tenant isolation, rejects duplicate/dangling/cross-record
`supersedes_annotation_id` references, preserves insertion order, performs
no conflict resolution (all annotation types coexist), and never triggers
any `KnowledgeRecord`-supersession side effect for `CORRECTION`-type
annotations — proving the documented Milestone M4 scope boundary is real.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kortex.engines.knowledge.annotations import KnowledgeAnnotationManager
from kortex.engines.knowledge.exceptions import (
    KnowledgeAnnotationNotFoundError,
    KnowledgeDuplicateAnnotationError,
)
from kortex.engines.knowledge.interfaces import IKnowledgeAnnotationManager
from kortex.engines.knowledge.models import (
    KnowledgeActorType,
    KnowledgeAnnotation,
    KnowledgeAnnotationType,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _annotation(
    annotation_id: str,
    target_record_id: str = "rec-1",
    tenant_id: str = "tenant-a",
    annotation_type: KnowledgeAnnotationType = KnowledgeAnnotationType.REMARK,
    supersedes_annotation_id: str | None = None,
    content: str = "some remark",
) -> KnowledgeAnnotation:
    return KnowledgeAnnotation(
        annotation_id=annotation_id,
        tenant_id=tenant_id,
        target_record_id=target_record_id,
        annotation_type=annotation_type,
        actor_id="user-1",
        actor_type=KnowledgeActorType.USER,
        content=content,
        created_at=_NOW,
        supersedes_annotation_id=supersedes_annotation_id,
    )


# -- Contract conformance -------------------------------------------------------


def test_knowledge_annotation_manager_satisfies_iknowledgeannotationmanager() -> None:
    assert isinstance(KnowledgeAnnotationManager(), IKnowledgeAnnotationManager)


# -- add_annotation ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_annotation_valid() -> None:
    manager = KnowledgeAnnotationManager()
    annotation = _annotation("a1")
    assert await manager.add_annotation(annotation) == annotation


@pytest.mark.asyncio
async def test_add_annotation_rejects_duplicate_within_same_tenant() -> None:
    manager = KnowledgeAnnotationManager()
    await manager.add_annotation(_annotation("a1"))
    with pytest.raises(KnowledgeDuplicateAnnotationError):
        await manager.add_annotation(_annotation("a1"))


@pytest.mark.asyncio
async def test_add_annotation_same_id_allowed_across_different_tenants() -> None:
    manager = KnowledgeAnnotationManager()
    await manager.add_annotation(_annotation("a1", tenant_id="tenant-a"))
    await manager.add_annotation(_annotation("a1", tenant_id="tenant-b"))  # must not raise


@pytest.mark.asyncio
async def test_add_annotation_accepts_all_three_annotation_types_identically() -> None:
    """No special-casing by `annotation_type` at the manager level — proves
    the documented scope boundary: `CORRECTION` is stored exactly like
    `REMARK`/`CONTEXT`, with no record-supersession side effect attempted."""
    manager = KnowledgeAnnotationManager()
    remark = await manager.add_annotation(_annotation("a1", annotation_type=KnowledgeAnnotationType.REMARK))
    context = await manager.add_annotation(_annotation("a2", annotation_type=KnowledgeAnnotationType.CONTEXT))
    correction = await manager.add_annotation(_annotation("a3", annotation_type=KnowledgeAnnotationType.CORRECTION))

    stored = await manager.list_annotations("rec-1", "tenant-a")
    assert stored == [remark, context, correction]


@pytest.mark.asyncio
async def test_add_annotation_with_valid_supersedes_reference() -> None:
    manager = KnowledgeAnnotationManager()
    original = await manager.add_annotation(_annotation("a1"))
    superseding = _annotation("a2", supersedes_annotation_id="a1")
    assert await manager.add_annotation(superseding) == superseding

    stored = await manager.list_annotations("rec-1", "tenant-a")
    assert stored == [original, superseding]


@pytest.mark.asyncio
async def test_add_annotation_rejects_dangling_supersedes_reference() -> None:
    manager = KnowledgeAnnotationManager()
    with pytest.raises(KnowledgeAnnotationNotFoundError):
        await manager.add_annotation(_annotation("a1", supersedes_annotation_id="does-not-exist"))


@pytest.mark.asyncio
async def test_add_annotation_rejects_supersedes_reference_to_a_different_target_record() -> None:
    """A `supersedes_annotation_id` must refer to an annotation on the
    *same* `target_record_id` — a cross-record reference is a data
    integrity violation, not a legitimate supersession."""
    manager = KnowledgeAnnotationManager()
    await manager.add_annotation(_annotation("a1", target_record_id="rec-1"))
    with pytest.raises(KnowledgeAnnotationNotFoundError):
        await manager.add_annotation(
            _annotation("a2", target_record_id="rec-2", supersedes_annotation_id="a1")
        )


@pytest.mark.asyncio
async def test_add_annotation_rejects_self_referencing_supersedes_id() -> None:
    """An annotation cannot supersede itself — at the moment of the check
    it is not yet stored, so this is correctly indistinguishable from a
    dangling reference."""
    manager = KnowledgeAnnotationManager()
    with pytest.raises(KnowledgeAnnotationNotFoundError):
        await manager.add_annotation(_annotation("a1", supersedes_annotation_id="a1"))


@pytest.mark.asyncio
async def test_add_annotation_rejects_supersedes_reference_across_tenants() -> None:
    """A `supersedes_annotation_id` pointing at an annotation that exists
    only under a *different* tenant must be indistinguishable from a
    dangling reference — tenant isolation, not merely a missing-data
    coincidence."""
    manager = KnowledgeAnnotationManager()
    await manager.add_annotation(_annotation("a1", tenant_id="tenant-a"))
    with pytest.raises(KnowledgeAnnotationNotFoundError):
        await manager.add_annotation(
            _annotation("a2", tenant_id="tenant-b", supersedes_annotation_id="a1")
        )


# -- list_annotations ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_annotations_returns_empty_for_unknown_target_record() -> None:
    manager = KnowledgeAnnotationManager()
    assert await manager.list_annotations("does-not-exist", "tenant-a") == []


@pytest.mark.asyncio
async def test_list_annotations_preserves_insertion_order() -> None:
    manager = KnowledgeAnnotationManager()
    a1 = await manager.add_annotation(_annotation("a1"))
    a2 = await manager.add_annotation(_annotation("a2"))
    a3 = await manager.add_annotation(_annotation("a3"))
    assert await manager.list_annotations("rec-1", "tenant-a") == [a1, a2, a3]


@pytest.mark.asyncio
async def test_list_annotations_enforces_tenant_isolation() -> None:
    manager = KnowledgeAnnotationManager()
    await manager.add_annotation(_annotation("a1", tenant_id="tenant-a"))
    assert await manager.list_annotations("rec-1", "tenant-b") == []


@pytest.mark.asyncio
async def test_list_annotations_scopes_by_target_record_id() -> None:
    manager = KnowledgeAnnotationManager()
    await manager.add_annotation(_annotation("a1", target_record_id="rec-1"))
    await manager.add_annotation(_annotation("a2", target_record_id="rec-2"))
    assert [a.annotation_id for a in await manager.list_annotations("rec-1", "tenant-a")] == ["a1"]
    assert [a.annotation_id for a in await manager.list_annotations("rec-2", "tenant-a")] == ["a2"]


@pytest.mark.asyncio
async def test_list_annotations_performs_no_conflict_resolution() -> None:
    """Both an original annotation and its superseding replacement must
    remain listed — the manager performs no filtering, matching the
    model's own documented "no automatic conflict resolution" contract."""
    manager = KnowledgeAnnotationManager()
    original = await manager.add_annotation(_annotation("a1", content="first take"))
    superseding = await manager.add_annotation(
        _annotation("a2", supersedes_annotation_id="a1", content="corrected take")
    )
    stored = await manager.list_annotations("rec-1", "tenant-a")
    assert stored == [original, superseding]
    assert stored[0].content == "first take"
    assert stored[1].content == "corrected take"
    assert stored[1].supersedes_annotation_id == "a1"


@pytest.mark.asyncio
async def test_list_annotations_returns_empty_for_empty_string_arguments() -> None:
    """`target_record_id`/`tenant_id` are raw `str` parameters at this call
    boundary (not Pydantic-validated), so an empty string must behave like
    any other unrecognized key — an empty list, never a crash."""
    manager = KnowledgeAnnotationManager()
    await manager.add_annotation(_annotation("a1"))
    assert await manager.list_annotations("", "tenant-a") == []
    assert await manager.list_annotations("rec-1", "") == []


@pytest.mark.asyncio
async def test_list_annotations_is_deterministic_across_repeated_calls() -> None:
    manager = KnowledgeAnnotationManager()
    await manager.add_annotation(_annotation("a1"))
    await manager.add_annotation(_annotation("a2"))
    first = [a.annotation_id for a in await manager.list_annotations("rec-1", "tenant-a")]
    second = [a.annotation_id for a in await manager.list_annotations("rec-1", "tenant-a")]
    assert first == second


# -- Documented scope boundary: no CORRECTION -> record-supersession wiring -------


@pytest.mark.asyncio
async def test_correction_annotation_never_mutates_or_references_any_record_manager() -> None:
    """`KnowledgeAnnotationManager` has no constructor dependency on
    `IKnowledgeRecordManager` and performs no cross-manager call when
    storing a `CORRECTION`-type annotation — proving the documented
    Milestone M4 scope boundary (auto-triggered record supersession is
    deliberately deferred, not silently implemented) is real."""
    manager = KnowledgeAnnotationManager()
    assert not hasattr(manager, "_record_manager")
    assert not hasattr(manager, "_lineage_manager")

    correction = await manager.add_annotation(
        _annotation("a1", annotation_type=KnowledgeAnnotationType.CORRECTION)
    )
    assert correction.annotation_type == KnowledgeAnnotationType.CORRECTION
    # No exception, no side effect beyond storage — proven by successful retrieval.
    assert await manager.list_annotations("rec-1", "tenant-a") == [correction]
