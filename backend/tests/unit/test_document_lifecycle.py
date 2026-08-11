"""Unit tests for Document Lifecycle, Versioning, and Lineage Manager (Milestone 2).

Target: 100% pass rate, 100% line coverage for lifecycle.py.
"""

from __future__ import annotations

import pytest

from kortex.engines.document.exceptions import DocumentLifecycleError
from kortex.engines.document.lifecycle import DocumentLifecycleManager
from kortex.engines.document.models import DocumentLifecycleState, SecurityClassification, SecurityMetadata


@pytest.mark.asyncio
async def test_create_root_version() -> None:
    """Test creating a root document version."""
    mgr = DocumentLifecycleManager()
    sec = SecurityMetadata(classification=SecurityClassification.CONFIDENTIAL)

    version = await mgr.create_version(
        title="Annual Financial Report",
        author_id="finance_lead",
        security_metadata=sec,
    )

    assert version.metadata.title == "Annual Financial Report"
    assert version.metadata.author_id == "finance_lead"
    assert version.metadata.lifecycle_state == DocumentLifecycleState.DRAFT
    assert version.metadata.is_immutable is False
    assert version.metadata.parent_version_id is None
    assert len(version.metadata.lineage_path) == 1
    assert version.metadata.lineage_path[0] == version.version_id
    assert version.version_number == "1.0.0"
    assert version.metadata.security_metadata.classification == SecurityClassification.CONFIDENTIAL


@pytest.mark.asyncio
async def test_create_child_version() -> None:
    """Test creating child versions derived from a parent version."""
    mgr = DocumentLifecycleManager()
    root = await mgr.create_version(title="Root Doc", author_id="user1")

    child = await mgr.create_child_version(
        parent_version_id=root.version_id,
        author_id="user2",
    )

    assert child.document_id == root.document_id
    assert child.parent_version_id == root.version_id
    assert child.metadata.title == "Root Doc"
    assert child.metadata.lineage_path == [root.version_id, child.version_id]
    assert child.version_number == "1.0.1"

    latest = await mgr.get_latest_version(root.document_id)
    assert latest.version_id == child.version_id


@pytest.mark.asyncio
async def test_valid_lifecycle_transitions() -> None:
    """Test valid lifecycle state transitions."""
    mgr = DocumentLifecycleManager()
    v1 = await mgr.create_version(title="Doc 1")
    doc_id = v1.document_id
    ver_id = v1.version_id

    # DRAFT -> REVIEW
    m1 = await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.REVIEW)
    assert m1.lifecycle_state == DocumentLifecycleState.REVIEW
    assert m1.is_immutable is False

    # REVIEW -> DRAFT
    m2 = await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.DRAFT)
    assert m2.lifecycle_state == DocumentLifecycleState.DRAFT

    # DRAFT -> PUBLISHED
    m3 = await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.PUBLISHED)
    assert m3.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert m3.is_immutable is True
    assert m3.published_at is not None

    # PUBLISHED -> SUPERSEDED
    m4 = await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.SUPERSEDED)
    assert m4.lifecycle_state == DocumentLifecycleState.SUPERSEDED
    assert m4.is_immutable is True

    # SUPERSEDED -> ARCHIVED
    m5 = await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.ARCHIVED)
    assert m5.lifecycle_state == DocumentLifecycleState.ARCHIVED

    # ARCHIVED -> LOGICAL_DELETE
    m6 = await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.LOGICAL_DELETE)
    assert m6.lifecycle_state == DocumentLifecycleState.LOGICAL_DELETE


@pytest.mark.asyncio
async def test_invalid_lifecycle_transitions() -> None:
    """Test invalid lifecycle transitions raise DocumentLifecycleError."""
    mgr = DocumentLifecycleManager()
    v1 = await mgr.create_version(title="Doc Invalid")
    doc_id = v1.document_id
    ver_id = v1.version_id

    # Invalid: DRAFT -> SUPERSEDED
    with pytest.raises(DocumentLifecycleError, match="Invalid lifecycle transition"):
        await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.SUPERSEDED)

    # Invalid: DRAFT -> ARCHIVED
    with pytest.raises(DocumentLifecycleError, match="Invalid lifecycle transition"):
        await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.ARCHIVED)

    # Transition to PUBLISHED first
    await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.PUBLISHED)

    # Invalid: PUBLISHED -> DRAFT
    with pytest.raises(DocumentLifecycleError, match="Invalid lifecycle transition"):
        await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.DRAFT)

    # Invalid: PUBLISHED -> REVIEW
    with pytest.raises(DocumentLifecycleError, match="Invalid lifecycle transition"):
        await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.REVIEW)

    # Transition to ARCHIVED
    await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.ARCHIVED)

    # Invalid: ARCHIVED -> PUBLISHED
    with pytest.raises(DocumentLifecycleError, match="Invalid lifecycle transition"):
        await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.PUBLISHED)

    # Transition to LOGICAL_DELETE
    await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.LOGICAL_DELETE)

    # Invalid: LOGICAL_DELETE -> any state
    with pytest.raises(DocumentLifecycleError, match="Invalid lifecycle transition"):
        await mgr.transition_state(doc_id, ver_id, DocumentLifecycleState.DRAFT)


@pytest.mark.asyncio
async def test_immutable_published_documents() -> None:
    """Test that published documents are marked immutable and check immutability status."""
    mgr = DocumentLifecycleManager()
    v1 = await mgr.create_version(title="Doc Immutability")

    assert await mgr.is_immutable(v1.document_id, v1.version_id) is False

    await mgr.transition_state(v1.document_id, v1.version_id, DocumentLifecycleState.PUBLISHED)

    assert await mgr.is_immutable(v1.document_id, v1.version_id) is True


@pytest.mark.asyncio
async def test_parent_auto_superseded_on_child_published() -> None:
    """Test that when a child version is published, its published parent is marked SUPERSEDED."""
    mgr = DocumentLifecycleManager()
    v1 = await mgr.create_version(title="Doc Parent")

    # Publish parent version
    await mgr.transition_state(v1.document_id, v1.version_id, DocumentLifecycleState.PUBLISHED)

    # Create child version
    v2 = await mgr.create_child_version(parent_version_id=v1.version_id)

    # Publish child version
    await mgr.transition_state(v2.document_id, v2.version_id, DocumentLifecycleState.PUBLISHED)

    # Verify parent version state auto-updated to SUPERSEDED
    parent_meta = await mgr.get_version(v1.document_id, v1.version_id)
    assert parent_meta.lifecycle_state == DocumentLifecycleState.SUPERSEDED

    child_meta = await mgr.get_version(v2.document_id, v2.version_id)
    assert child_meta.lifecycle_state == DocumentLifecycleState.PUBLISHED


@pytest.mark.asyncio
async def test_lineage_retrieval() -> None:
    """Test line-by-line lineage chain retrieval."""
    mgr = DocumentLifecycleManager()
    v1 = await mgr.create_version(title="V1")
    v2 = await mgr.create_child_version(parent_version_id=v1.version_id, title="V2")
    v3 = await mgr.create_child_version(parent_version_id=v2.version_id, title="V3")

    lineage = await mgr.get_lineage(v1.document_id)
    assert len(lineage) == 3
    assert [m.version_id for m in lineage] == [v1.version_id, v2.version_id, v3.version_id]
    assert [m.title for m in lineage] == ["V1", "V2", "V3"]


@pytest.mark.asyncio
async def test_duplicate_version_rejection() -> None:
    """Test rejection of duplicate version IDs."""
    mgr = DocumentLifecycleManager()
    v1 = await mgr.create_version(version_id="custom-ver-id")

    with pytest.raises(DocumentLifecycleError, match="Duplicate version ID"):
        await mgr.create_version(version_id="custom-ver-id")


@pytest.mark.asyncio
async def test_missing_parent_version_rejection() -> None:
    """Test rejection when parent version ID does not exist."""
    mgr = DocumentLifecycleManager()

    with pytest.raises(DocumentLifecycleError, match="Parent version ID 'non-existent' does not exist"):
        await mgr.create_version(parent_version_id="non-existent")


@pytest.mark.asyncio
async def test_document_id_mismatch_rejection() -> None:
    """Test rejection when parent version document ID does not match specified document ID."""
    mgr = DocumentLifecycleManager()
    v1 = await mgr.create_version(title="Doc A")

    with pytest.raises(DocumentLifecycleError, match="Document ID mismatch"):
        await mgr.create_version(document_id="wrong-doc-id", parent_version_id=v1.version_id)


@pytest.mark.asyncio
async def test_create_child_from_soft_deleted_parent_rejection() -> None:
    """Test rejection when attempting to derive a child version from a soft-deleted parent."""
    mgr = DocumentLifecycleManager()
    v1 = await mgr.create_version(title="Parent Doc")
    await mgr.transition_state(v1.document_id, v1.version_id, DocumentLifecycleState.LOGICAL_DELETE)

    with pytest.raises(DocumentLifecycleError, match="soft-deleted parent version"):
        await mgr.create_child_version(parent_version_id=v1.version_id)


@pytest.mark.asyncio
async def test_non_existent_version_lookups() -> None:
    """Test handling of non-existent version lookups."""
    mgr = DocumentLifecycleManager()

    with pytest.raises(DocumentLifecycleError, match="not found"):
        await mgr.get_version("doc-1", "non-existent-version")

    with pytest.raises(DocumentLifecycleError, match="No document versions found"):
        await mgr.get_latest_version("non-existent-doc")

    with pytest.raises(DocumentLifecycleError, match="No document lineage found"):
        await mgr.get_lineage("non-existent-doc")


@pytest.mark.asyncio
async def test_same_state_transition_idempotent() -> None:
    """Test transitioning to the same current state returns metadata cleanly."""
    mgr = DocumentLifecycleManager()
    v1 = await mgr.create_version(title="Doc Same State")

    m1 = await mgr.transition_state(v1.document_id, v1.version_id, DocumentLifecycleState.DRAFT)
    assert m1.lifecycle_state == DocumentLifecycleState.DRAFT


@pytest.mark.asyncio
async def test_version_document_id_mismatch_in_transition() -> None:
    """Test transition_state rejects mismatched document_id."""
    mgr = DocumentLifecycleManager()
    v1 = await mgr.create_version(title="Doc Mismatch")

    with pytest.raises(DocumentLifecycleError, match="Document ID mismatch"):
        await mgr.transition_state("wrong-doc-id", v1.version_id, DocumentLifecycleState.REVIEW)


@pytest.mark.asyncio
async def test_get_version_document_id_mismatch() -> None:
    """Test get_version rejects mismatched document_id."""
    mgr = DocumentLifecycleManager()
    v1 = await mgr.create_version(title="Doc Mismatch Get")

    with pytest.raises(DocumentLifecycleError, match="Document ID mismatch"):
        await mgr.get_version("wrong-doc-id", v1.version_id)

