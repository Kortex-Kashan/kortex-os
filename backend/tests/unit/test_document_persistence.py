"""Unit tests for Document Engine Relational Persistence Layer (Milestone D2).

Target: 100% pass rate, >=95% code coverage across persistence models and repository operations.
"""

from __future__ import annotations

import asyncio
import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.engines.document.exceptions import (
    DocumentEngineError,
    DocumentLifecycleError,
    DocumentValidationError,
)
from kortex.engines.document.interfaces import IDocumentRepository
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterPipelineDefinition,
    Document,
    DocumentContent,
    DocumentLifecycleState,
    DocumentMetadata,
    DocumentOperationHistoryRecord,
    DocumentOperationProfile,
    DocumentOperationProfileRecord,
    DocumentRecord,
    DocumentVersion,
    DocumentVersionRecord,
    PipelineExecutionMode,
    PipelineStage,
    SecurityClassification,
    SecurityMetadata,
    ValidationReport,
)
from kortex.engines.document.persistence import DocumentRepository
from kortex.engines.storage.stores.data_store import RelationalDataStore


@pytest.fixture
async def test_db(tmp_path):
    """Create an isolated file-backed SQLite database manager and initialize all tables."""
    db_file = tmp_path / "test_document_persistence.db"
    db_manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{db_file}")
    await db_manager.connect()
    await db_manager.create_all_tables()
    yield db_manager
    await db_manager.disconnect()


@pytest.fixture
def data_store(test_db: DatabaseEngineManager) -> RelationalDataStore:
    """Create a RelationalDataStore backed by the test database."""
    return RelationalDataStore(test_db)


@pytest.fixture
async def concurrent_data_store(tmp_path) -> RelationalDataStore:
    """Create a file-backed RelationalDataStore supporting true independent concurrent transactions."""
    db_file = tmp_path / "concurrent_test.db"
    db_manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{db_file}")
    await db_manager.connect()
    await db_manager.create_all_tables()
    store = RelationalDataStore(db_manager)
    yield store
    await db_manager.disconnect()


@pytest.fixture
def repository(data_store: RelationalDataStore) -> DocumentRepository:
    """Create a DocumentRepository backed by the test data store."""
    return DocumentRepository(data_store)


# --- Test Interface Protocol Compliance ---


def test_repository_protocol_compliance(repository: DocumentRepository) -> None:
    """Verify DocumentRepository implements IDocumentRepository protocol."""
    assert isinstance(repository, IDocumentRepository)


# --- Test Document CRUD Operations ---


@pytest.mark.asyncio
async def test_document_creation_and_retrieval(repository: DocumentRepository) -> None:
    """Test creating and retrieving a root Document domain model."""
    doc = Document(
        document_id="doc-001",
        tenant_id="tenant-alpha",
        title="Master Service Agreement",
        document_type="CONTRACT",
        metadata={"department": "legal", "confidential": True},
    )

    created = await repository.create_document(doc)
    assert created.document_id == "doc-001"
    assert created.tenant_id == "tenant-alpha"
    assert created.title == "Master Service Agreement"
    assert created.document_type == "CONTRACT"
    assert created.metadata == {"department": "legal", "confidential": True}
    assert created.created_at is not None

    # Retrieve document
    fetched = await repository.get_document("doc-001", tenant_id="tenant-alpha")
    assert fetched is not None
    assert fetched.document_id == "doc-001"
    assert fetched.title == "Master Service Agreement"
    assert fetched.metadata["department"] == "legal"


@pytest.mark.asyncio
async def test_duplicate_document_creation_rejection(repository: DocumentRepository) -> None:
    """Test that creating a document with duplicate ID under the same tenant raises error."""
    doc = Document(
        document_id="doc-dup",
        tenant_id="tenant-alpha",
        title="Original Document",
    )
    await repository.create_document(doc)

    with pytest.raises(DocumentValidationError, match="already exists"):
        await repository.create_document(doc)


@pytest.mark.asyncio
async def test_document_update(repository: DocumentRepository) -> None:
    """Test updating existing document attributes."""
    doc = Document(
        document_id="doc-update",
        tenant_id="tenant-alpha",
        title="Original Title",
        document_type="INVOICE",
    )
    await repository.create_document(doc)

    updated_doc = Document(
        document_id="doc-update",
        tenant_id="tenant-alpha",
        title="Updated Title",
        document_type="INVOICE_AMENDED",
        current_version_id="ver-updated-1",
        metadata={"amended_by": "finance_lead"},
    )
    result = await repository.update_document(updated_doc)
    assert result.title == "Updated Title"
    assert result.document_type == "INVOICE_AMENDED"
    # current_version_id must remain None (cannot be set via update_document)
    assert result.current_version_id is None
    assert result.metadata["amended_by"] == "finance_lead"

    fetched = await repository.get_document("doc-update", tenant_id="tenant-alpha")
    assert fetched is not None
    assert fetched.title == "Updated Title"
    assert fetched.current_version_id is None


@pytest.mark.asyncio
async def test_update_document_pointer_bypass_rejection(repository: DocumentRepository) -> None:
    """Verify that ordinary document updates cannot move current_version_id to DRAFT, REVIEW, or arbitrary versions."""
    # 1. Create document
    doc = Document(
        document_id="doc-bypass-test",
        tenant_id="tenant-alpha",
        title="Bypass Test Doc",
        current_version_id="fabricated-v1",  # Fabricated pointer in constructor
    )
    created = await repository.create_document(doc)
    # create_document must initialize current_version_id to None
    assert created.current_version_id is None

    # 2. Create a DRAFT version
    m_draft = DocumentMetadata(
        document_id="doc-bypass-test",
        version_id="ver-draft-1",
        title="Draft V1",
        author_id="author",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v_draft = DocumentVersion(
        version_id="ver-draft-1",
        document_id="doc-bypass-test",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="author",
        metadata=m_draft,
    )
    await repository.create_version(v_draft, tenant_id="tenant-alpha")

    # Attempt to update document with DRAFT version_id
    doc_attempt_draft = Document(
        document_id="doc-bypass-test",
        tenant_id="tenant-alpha",
        title="Attempt Draft Pointer",
        current_version_id="ver-draft-1",
    )
    res_draft = await repository.update_document(doc_attempt_draft)
    assert res_draft.current_version_id is None

    reloaded_1 = await repository.get_document("doc-bypass-test", tenant_id="tenant-alpha")
    assert reloaded_1 is not None
    assert reloaded_1.current_version_id is None

    # 3. Create a REVIEW version
    m_rev = DocumentMetadata(
        document_id="doc-bypass-test",
        version_id="ver-review-1",
        title="Review V1",
        author_id="author",
        lifecycle_state=DocumentLifecycleState.REVIEW,
        created_at="2026-01-01T00:00:00Z",
    )
    v_rev = DocumentVersion(
        version_id="ver-review-1",
        document_id="doc-bypass-test",
        version_number="1.0.1",
        created_at="2026-01-01T00:00:00Z",
        created_by="author",
        metadata=m_rev,
    )
    await repository.create_version(v_rev, tenant_id="tenant-alpha")

    # Attempt to update document with REVIEW version_id
    doc_attempt_rev = Document(
        document_id="doc-bypass-test",
        tenant_id="tenant-alpha",
        title="Attempt Review Pointer",
        current_version_id="ver-review-1",
    )
    res_rev = await repository.update_document(doc_attempt_rev)
    assert res_rev.current_version_id is None

    # 4. Attempt to update document with arbitrary nonexistent version_id
    doc_attempt_arbitrary = Document(
        document_id="doc-bypass-test",
        tenant_id="tenant-alpha",
        title="Attempt Arbitrary Pointer",
        current_version_id="ver-nonexistent-999",
    )
    res_arb = await repository.update_document(doc_attempt_arbitrary)
    assert res_arb.current_version_id is None

    reloaded_2 = await repository.get_document("doc-bypass-test", tenant_id="tenant-alpha")
    assert reloaded_2 is not None
    assert reloaded_2.current_version_id is None


@pytest.mark.asyncio
async def test_document_update_non_existent(repository: DocumentRepository) -> None:
    """Test that updating a non-existent document raises error."""
    doc = Document(
        document_id="doc-missing",
        tenant_id="tenant-alpha",
        title="Ghost Document",
    )
    with pytest.raises(DocumentValidationError, match="Cannot update non-existent"):
        await repository.update_document(doc)


@pytest.mark.asyncio
async def test_document_soft_delete_and_filtering(repository: DocumentRepository) -> None:
    """Test soft-deleting a document and verifying query filtering."""
    doc = Document(
        document_id="doc-soft-del",
        tenant_id="tenant-alpha",
        title="To Be Deleted",
    )
    await repository.create_document(doc)

    # Soft delete
    deleted = await repository.soft_delete_document("doc-soft-del", tenant_id="tenant-alpha")
    assert deleted is True

    # Attempting to soft-delete again returns False
    assert await repository.soft_delete_document("doc-soft-del", tenant_id="tenant-alpha") is False

    # Default query excludes soft-deleted
    fetched_default = await repository.get_document("doc-soft-del", tenant_id="tenant-alpha")
    assert fetched_default is None

    # Include deleted returns the document
    fetched_all = await repository.get_document("doc-soft-del", tenant_id="tenant-alpha", include_deleted=True)
    assert fetched_all is not None
    assert fetched_all.document_id == "doc-soft-del"


@pytest.mark.asyncio
async def test_document_listing_and_pagination(repository: DocumentRepository) -> None:
    """Test listing documents with filtering and pagination."""
    for i in range(5):
        doc = Document(
            document_id=f"doc-list-{i}",
            tenant_id="tenant-alpha",
            title=f"Doc {i}",
            document_type="INVOICE" if i % 2 == 0 else "RECEIPT",
        )
        await repository.create_document(doc)

    # List all
    all_docs = await repository.list_documents(tenant_id="tenant-alpha")
    assert len(all_docs) == 5

    # Filter by type
    invoices = await repository.list_documents(tenant_id="tenant-alpha", document_type="INVOICE")
    assert len(invoices) == 3

    # Pagination
    page = await repository.list_documents(tenant_id="tenant-alpha", limit=2, offset=0)
    assert len(page) == 2


# --- Test Document Version CRUD & Lineage ---


@pytest.mark.asyncio
async def test_version_creation_and_retrieval(repository: DocumentRepository) -> None:
    """Test creating and retrieving DocumentVersion snapshots."""
    doc = Document(document_id="doc-ver-1", tenant_id="tenant-alpha", title="Policy Document")
    await repository.create_document(doc)

    sec_meta = SecurityMetadata(
        classification=SecurityClassification.CONFIDENTIAL,
        labels=["hr", "policy"],
        owner_id="hr_lead",
        tenant_id="tenant-alpha",
    )
    doc_meta = DocumentMetadata(
        document_id="doc-ver-1",
        version_id="ver-1-0",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        lineage_path=["ver-1-0"],
        title="Policy Document v1.0",
        author_id="hr_lead",
        security_metadata=sec_meta,
        file_size_bytes=24500,
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        storage_key="tenant-alpha/doc-ver-1/ver-1-0.pdf",
        bucket_name="documents",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    content = DocumentContent(
        storage_key="tenant-alpha/doc-ver-1/ver-1-0.pdf",
        bucket_name="documents",
        mime_type="application/pdf",
        file_size_bytes=24500,
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    version = DocumentVersion(
        version_id="ver-1-0",
        document_id="doc-ver-1",
        version_number="1.0.0",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        created_by="hr_lead",
        is_immutable=False,
        metadata=doc_meta,
        content=content,
    )

    created_ver = await repository.create_version(version, tenant_id="tenant-alpha")
    assert created_ver.version_id == "ver-1-0"
    assert created_ver.version_number == "1.0.0"
    assert created_ver.content is not None
    assert created_ver.content.storage_key == "tenant-alpha/doc-ver-1/ver-1-0.pdf"

    # Parent document current_version_id pointer should remain None for DRAFT
    parent_doc = await repository.get_document("doc-ver-1", tenant_id="tenant-alpha")
    assert parent_doc is not None
    assert parent_doc.current_version_id is None

    # Retrieve version directly
    fetched_ver = await repository.get_version("doc-ver-1", "ver-1-0", tenant_id="tenant-alpha")
    assert fetched_ver is not None
    assert fetched_ver.version_id == "ver-1-0"
    assert fetched_ver.metadata.security_metadata.classification == SecurityClassification.CONFIDENTIAL
    assert fetched_ver.metadata.security_metadata.labels == ["hr", "policy"]


@pytest.mark.asyncio
async def test_version_creation_non_existent_document(repository: DocumentRepository) -> None:
    """Test that creating a version for a non-existent document fails."""
    doc_meta = DocumentMetadata(
        document_id="doc-missing",
        version_id="ver-0",
        title="Title",
        author_id="author",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    ver = DocumentVersion(
        version_id="ver-0",
        document_id="doc-missing",
        version_number="1.0.0",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        created_by="author",
        metadata=doc_meta,
    )
    with pytest.raises(DocumentValidationError, match="Cannot create version for non-existent"):
        await repository.create_version(ver, tenant_id="tenant-alpha")


@pytest.mark.asyncio
async def test_version_number_uniqueness_rejection(repository: DocumentRepository) -> None:
    """Test that creating a version with duplicate version_number for same document raises error."""
    doc = Document(document_id="doc-ver-uniq", tenant_id="tenant-alpha", title="Unique Version Test")
    await repository.create_document(doc)

    doc_meta = DocumentMetadata(
        document_id="doc-ver-uniq",
        version_id="ver-1",
        title="V1",
        author_id="author",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    ver1 = DocumentVersion(
        version_id="ver-1",
        document_id="doc-ver-uniq",
        version_number="1.0.0",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        created_by="author",
        metadata=doc_meta,
    )
    await repository.create_version(ver1, tenant_id="tenant-alpha")

    # Attempting to create duplicate version_number '1.0.0' with new version_id
    doc_meta2 = DocumentMetadata(
        document_id="doc-ver-uniq",
        version_id="ver-1-duplicate",
        title="V1 Duplicate",
        author_id="author",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    ver2 = DocumentVersion(
        version_id="ver-1-duplicate",
        document_id="doc-ver-uniq",
        version_number="1.0.0",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        created_by="author",
        metadata=doc_meta2,
    )
    with pytest.raises(DocumentLifecycleError, match="already exists"):
        await repository.create_version(ver2, tenant_id="tenant-alpha")


@pytest.mark.asyncio
async def test_version_creation_invalid_semver_rejection(repository: DocumentRepository) -> None:
    """Test that creating a version with invalid SemVer format raises DocumentLifecycleError."""
    doc = Document(document_id="doc-ver-semver-test", tenant_id="tenant-alpha", title="SemVer Validation Test")
    await repository.create_document(doc)

    invalid_semvers = ["1", "1.2", "1.a.3", "foo", "custom-v1", "01.2.3", "1.0.0.0", "v1.0.0"]
    for idx, inv_semver in enumerate(invalid_semvers):
        meta = DocumentMetadata(
            document_id="doc-ver-semver-test",
            version_id=f"ver-inv-{idx}",
            title="Invalid Version Test",
            author_id="author",
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )
        ver = DocumentVersion(
            version_id=f"ver-inv-{idx}",
            document_id="doc-ver-semver-test",
            version_number=inv_semver,
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
            created_by="author",
            metadata=meta,
        )
        with pytest.raises(DocumentLifecycleError, match="Invalid semantic version format"):
            await repository.create_version(ver, tenant_id="tenant-alpha")


@pytest.mark.asyncio
async def test_version_lineage_and_latest_lookup(repository: DocumentRepository) -> None:
    """Test multiple version creation, listing in order, and latest version lookup."""
    doc = Document(document_id="doc-lineage", tenant_id="tenant-alpha", title="Lineage Test")
    await repository.create_document(doc)

    for i in range(1, 4):
        doc_meta = DocumentMetadata(
            document_id="doc-lineage",
            version_id=f"ver-{i}",
            parent_version_id=f"ver-{i - 1}" if i > 1 else None,
            title=f"Lineage Version {i}",
            author_id="author",
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )
        ver = DocumentVersion(
            version_id=f"ver-{i}",
            document_id="doc-lineage",
            parent_version_id=f"ver-{i - 1}" if i > 1 else None,
            version_number=f"1.{i}.0",
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
            created_by="author",
            metadata=doc_meta,
        )
        await repository.create_version(ver, tenant_id="tenant-alpha")

    # List all versions
    versions = await repository.list_versions("doc-lineage", tenant_id="tenant-alpha")
    assert len(versions) == 3
    assert [v.version_number for v in versions] == ["1.1.0", "1.2.0", "1.3.0"]

    # Latest version
    latest = await repository.get_latest_version("doc-lineage", tenant_id="tenant-alpha")
    assert latest is not None
    assert latest.version_id == "ver-3"
    assert latest.version_number == "1.3.0"

    # Lookup by exact version number
    v2 = await repository.get_version_by_number("doc-lineage", "1.2.0", tenant_id="tenant-alpha")
    assert v2 is not None
    assert v2.version_id == "ver-2"

    # Non-existent version
    assert await repository.get_version_by_number("doc-lineage", "9.9.9", tenant_id="tenant-alpha") is None
    assert await repository.get_version("doc-lineage", "ver-missing", tenant_id="tenant-alpha") is None


@pytest.mark.asyncio
async def test_update_version_state(repository: DocumentRepository) -> None:
    """Test updating version lifecycle state and immutability lock."""
    doc = Document(document_id="doc-state", tenant_id="tenant-alpha", title="State Transition")
    await repository.create_document(doc)

    doc_meta = DocumentMetadata(
        document_id="doc-state",
        version_id="ver-state-1",
        title="V1",
        author_id="author",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    ver = DocumentVersion(
        version_id="ver-state-1",
        document_id="doc-state",
        version_number="1.0.0",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        created_by="author",
        metadata=doc_meta,
    )
    await repository.create_version(ver, tenant_id="tenant-alpha")

    # Update to REVIEW
    updated_rev = await repository.update_version_state(
        document_id="doc-state",
        version_id="ver-state-1",
        target_state=DocumentLifecycleState.REVIEW,
        is_immutable=False,
        tenant_id="tenant-alpha",
    )
    assert updated_rev.metadata.lifecycle_state == DocumentLifecycleState.REVIEW
    assert updated_rev.is_immutable is False

    # Update to ARCHIVED
    updated_arch = await repository.update_version_state(
        document_id="doc-state",
        version_id="ver-state-1",
        target_state=DocumentLifecycleState.ARCHIVED,
        is_immutable=True,
        tenant_id="tenant-alpha",
    )
    assert updated_arch.metadata.lifecycle_state == DocumentLifecycleState.ARCHIVED
    assert updated_arch.is_immutable is True

    # Updating non-existent version raises error
    with pytest.raises(DocumentLifecycleError, match="Cannot update state"):
        await repository.update_version_state(
            document_id="doc-state",
            version_id="ver-missing",
            target_state=DocumentLifecycleState.ARCHIVED,
            is_immutable=True,
            tenant_id="tenant-alpha",
        )


@pytest.mark.asyncio
async def test_update_version_state_published_bypass_rejected(repository: DocumentRepository) -> None:
    """Verify that update_version_state rejects target_state=PUBLISHED with DocumentLifecycleError."""
    doc = Document(document_id="doc-bypass-pub", tenant_id="tenant-alpha", title="Bypass Pub Doc")
    await repository.create_document(doc)

    doc_meta = DocumentMetadata(
        document_id="doc-bypass-pub",
        version_id="ver-bypass-pub-1",
        title="Draft",
        author_id="author",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    ver = DocumentVersion(
        version_id="ver-bypass-pub-1",
        document_id="doc-bypass-pub",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="author",
        metadata=doc_meta,
    )
    await repository.create_version(ver, tenant_id="tenant-alpha")

    # Direct transition to PUBLISHED via update_version_state MUST be rejected
    with pytest.raises(
        DocumentLifecycleError, match="Direct transition to 'PUBLISHED' state via update_version_state is forbidden"
    ):
        await repository.update_version_state(
            document_id="doc-bypass-pub",
            version_id="ver-bypass-pub-1",
            target_state=DocumentLifecycleState.PUBLISHED,
            is_immutable=True,
            tenant_id="tenant-alpha",
        )

    # Prove version remains DRAFT and document.current_version_id remains None
    ver_check = await repository.get_version("doc-bypass-pub", "ver-bypass-pub-1", tenant_id="tenant-alpha")
    assert ver_check is not None
    assert ver_check.metadata.lifecycle_state == DocumentLifecycleState.DRAFT
    assert ver_check.is_immutable is False

    doc_check = await repository.get_document("doc-bypass-pub", tenant_id="tenant-alpha")
    assert doc_check is not None
    assert doc_check.current_version_id is None


# --- Test Multi-Tenant Isolation ---


@pytest.mark.asyncio
async def test_tenant_isolation_complete_boundary(repository: DocumentRepository) -> None:
    """Verify that Tenant A and Tenant B cannot access each other's documents or versions."""
    doc_a = Document(document_id="doc-tenant-a", tenant_id="tenant-A", title="Tenant A Doc")
    doc_b = Document(document_id="doc-tenant-b", tenant_id="tenant-B", title="Tenant B Doc")

    await repository.create_document(doc_a)
    await repository.create_document(doc_b)

    assert await repository.get_document("doc-tenant-b", tenant_id="tenant-A") is None
    assert await repository.get_document("doc-tenant-a", tenant_id="tenant-B") is None

    fetched_a = await repository.get_document("doc-tenant-a", tenant_id="tenant-A")
    fetched_b = await repository.get_document("doc-tenant-b", tenant_id="tenant-B")
    assert fetched_a is not None and fetched_a.title == "Tenant A Doc"
    assert fetched_b is not None and fetched_b.title == "Tenant B Doc"

    # Listing is strictly isolated
    list_a = await repository.list_documents(tenant_id="tenant-A")
    list_b = await repository.list_documents(tenant_id="tenant-B")
    assert len(list_a) == 1 and list_a[0].tenant_id == "tenant-A"
    assert len(list_b) == 1 and list_b[0].tenant_id == "tenant-B"


# --- Test Cascade Deletion & Foreign Key Integrity ---


@pytest.mark.asyncio
async def test_foreign_key_cascade_hard_delete(repository: DocumentRepository, data_store: RelationalDataStore) -> None:
    """Verify that hard deleting a DocumentRecord cascades to delete child DocumentVersionRecords."""
    doc = Document(document_id="doc-cascade", tenant_id="tenant-alpha", title="Cascade Test")
    await repository.create_document(doc)

    for i in range(1, 3):
        doc_meta = DocumentMetadata(
            document_id="doc-cascade",
            version_id=f"ver-cas-{i}",
            title=f"V{i}",
            author_id="author",
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )
        ver = DocumentVersion(
            version_id=f"ver-cas-{i}",
            document_id="doc-cascade",
            version_number=f"1.{i}.0",
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
            created_by="author",
            metadata=doc_meta,
        )
        await repository.create_version(ver, tenant_id="tenant-alpha")

    # Hard delete root document
    deleted = await repository.hard_delete_document("doc-cascade", tenant_id="tenant-alpha")
    assert deleted is True
    assert await repository.hard_delete_document("doc-cascade", tenant_id="tenant-alpha") is False

    # Verify versions are gone at database level
    async def _check_versions(session: AsyncSession) -> int:
        stmt = select(DocumentVersionRecord).where(DocumentVersionRecord.document_id == "doc-cascade")
        res = await session.execute(stmt)
        return len(res.scalars().all())

    count = await data_store.execute_in_transaction(_check_versions)
    assert count == 0


# --- Test Operation History Persistence ---


@pytest.mark.asyncio
async def test_operation_history_logging_and_retrieval(repository: DocumentRepository) -> None:
    """Test recording and retrieving sanitized operation history."""
    report = ValidationReport(
        is_valid=True,
        computed_fields_resolved=["tax", "total"],
    )
    req_id = "req-audit-100"

    await repository.record_operation_history(
        request_id=req_id,
        profile_id="profile.invoice.generate",
        status="SUCCESS",
        tenant_id="tenant-alpha",
        document_id="doc-inv-1",
        version_id="ver-inv-1-0",
        user_id="user-42",
        execution_time_ms=145.5,
        output_storage_key="tenant-alpha/doc-inv-1/ver-inv-1-0.pdf",
        validation_report=report,
        errors=["Minor warning note"],
    )

    # Retrieve history
    history = await repository.get_operation_history(req_id, tenant_id="tenant-alpha")
    assert history is not None
    assert history["request_id"] == req_id
    assert history["profile_id"] == "profile.invoice.generate"
    assert history["status"] == "SUCCESS"
    assert history["execution_time_ms"] == 145.5
    assert history["validation_report"]["is_valid"] is True
    assert history["errors"] == ["Minor warning note"]

    # Retrieve non-existent history
    assert await repository.get_operation_history("req-missing", tenant_id="tenant-alpha") is None

    # Duplicate request_id protection
    with pytest.raises(DocumentEngineError, match="already exists"):
        await repository.record_operation_history(
            request_id=req_id,
            profile_id="profile.invoice.generate",
            status="FAILED",
            tenant_id="tenant-alpha",
        )

    # List history
    history_list = await repository.list_operation_history(
        tenant_id="tenant-alpha", profile_id="profile.invoice.generate", document_id="doc-inv-1"
    )
    assert len(history_list) == 1


# --- Test Operation Profile Catalog Persistence ---


@pytest.mark.asyncio
async def test_operation_profile_persistence_and_versioning(
    repository: DocumentRepository,
) -> None:
    """Test saving, retrieving, and versioning DocumentOperationProfiles in database."""
    stage = PipelineStage(
        stage_id="stage-gen",
        adapter_id="adapter.reportlab.pdf",
        required_capability=AdapterCapability.GENERATE,
    )
    pipeline = AdapterPipelineDefinition(
        pipeline_id="pipe-inv",
        profile_id="profile.invoice.generate",
        stages=[stage],
        execution_mode=PipelineExecutionMode.SEQUENTIAL,
    )
    profile_v1 = DocumentOperationProfile(
        id="profile.invoice.generate",
        name="Invoice Generation Profile",
        namespace="kortex.finance.invoice",
        version="1.0.0",
        description="Generates standard customer invoice PDFs",
        business_operation="GENERATE_INVOICE",
        required_template_id="tmpl.invoice.standard",
        adapter_pipeline=pipeline,
        permissions=["document:generate", "finance:invoice"],
        output_bucket="invoices",
    )

    saved_v1 = await repository.save_operation_profile(profile_v1, tenant_id="tenant-alpha")
    assert saved_v1.id == "profile.invoice.generate"
    assert saved_v1.version == "1.0.0"
    assert saved_v1.permissions == ["document:generate", "finance:invoice"]

    # Save version 2.0.0 of same profile
    profile_v2 = profile_v1.model_copy(update={"version": "2.0.0", "description": "Generates v2 customer invoices"})
    await repository.save_operation_profile(profile_v2, tenant_id="tenant-alpha")

    # Fetch exact versions
    fetched_v1 = await repository.get_operation_profile(
        "profile.invoice.generate", version="1.0.0", tenant_id="tenant-alpha"
    )
    fetched_v2 = await repository.get_operation_profile(
        "profile.invoice.generate", version="2.0.0", tenant_id="tenant-alpha"
    )
    assert fetched_v1 is not None and fetched_v1.version == "1.0.0"
    assert fetched_v2 is not None and fetched_v2.version == "2.0.0"

    # Fetch with version=None (latest)
    fetched_latest = await repository.get_operation_profile(
        "profile.invoice.generate", version=None, tenant_id="tenant-alpha"
    )
    assert fetched_latest is not None

    # List profiles by business operation and namespace
    profiles = await repository.list_operation_profiles(
        tenant_id="tenant-alpha",
        business_operation="GENERATE_INVOICE",
        namespace="kortex.finance.invoice",
    )
    assert len(profiles) == 2

    # Delete v1
    deleted = await repository.delete_operation_profile(
        "profile.invoice.generate", version="1.0.0", tenant_id="tenant-alpha"
    )
    assert deleted is True
    assert (
        await repository.delete_operation_profile("profile.invoice.generate", version="1.0.0", tenant_id="tenant-alpha")
        is False
    )

    # Verify v1 is gone but v2 remains
    assert (
        await repository.get_operation_profile("profile.invoice.generate", version="1.0.0", tenant_id="tenant-alpha")
        is None
    )
    assert (
        await repository.get_operation_profile("profile.invoice.generate", version="2.0.0", tenant_id="tenant-alpha")
        is not None
    )


# --- Test Transaction Rollback ---


@pytest.mark.asyncio
async def test_transaction_rollback_guarantee(repository: DocumentRepository, data_store: RelationalDataStore) -> None:
    """Verify that any exception inside execute_in_transaction rolls back all partial writes."""
    doc_id = "doc-rollback-test"

    async def _failing_transaction(session: AsyncSession) -> None:
        record = DocumentRecord(
            id=doc_id,
            tenant_id="tenant-alpha",
            title="Rollback Test",
            document_type="TEMP",
        )
        session.add(record)
        await session.flush()
        # Trigger an intentional error midway
        raise RuntimeError("Simulated transaction failure!")

    with pytest.raises(RuntimeError, match="Simulated transaction failure!"):
        await data_store.execute_in_transaction(_failing_transaction)

    # Verify record was rolled back and does not exist
    fetched = await repository.get_document(doc_id, tenant_id="tenant-alpha")
    assert fetched is None


# --- Test JSON Fallback & Corrupted Data Handling ---


@pytest.mark.asyncio
async def test_json_parsing_resilience(data_store: RelationalDataStore, repository: DocumentRepository) -> None:
    """Verify repository resilience against malformed JSON in database text columns."""

    async def _inject_corrupt_records(session: AsyncSession) -> None:
        # Document with bad JSON
        doc_rec = DocumentRecord(
            id="doc-corrupt",
            tenant_id="tenant-alpha",
            title="Corrupt Doc",
            document_type="GENERIC",
            metadata_json="INVALID_JSON{",
        )
        session.add(doc_rec)

        # Version with bad JSON
        ver_rec = DocumentVersionRecord(
            id="ver-corrupt",
            document_id="doc-corrupt",
            tenant_id="tenant-alpha",
            version_number="1.0.0",
            lifecycle_state="UNKNOWN_STATE",
            is_immutable=False,
            author_id="author",
            title="Corrupt Ver",
            security_classification="INVALID_CLASSIFICATION",
            security_labels_json="INVALID_JSON[",
            lineage_path_json="INVALID_JSON[",
        )
        session.add(ver_rec)

        # Profile with bad JSON
        prof_rec = DocumentOperationProfileRecord(
            id="prof-rec-id",
            tenant_id="tenant-alpha",
            profile_id="prof-corrupt",
            name="Corrupt Profile",
            namespace="kortex.test",
            version="1.0.0",
            description="Test",
            business_operation="OP",
            pipeline_definition_json="INVALID_JSON{",
            permissions_json="INVALID_JSON[",
        )
        session.add(prof_rec)

    await data_store.execute_in_transaction(_inject_corrupt_records)

    # Document retrieval should fall back to empty metadata dict
    doc = await repository.get_document("doc-corrupt", tenant_id="tenant-alpha")
    assert doc is not None
    assert doc.metadata == {}

    # Version retrieval should fall back safely to DRAFT, INTERNAL, and empty lists
    ver = await repository.get_version("doc-corrupt", "ver-corrupt", tenant_id="tenant-alpha")
    assert ver is not None
    assert ver.metadata.lifecycle_state == DocumentLifecycleState.DRAFT
    assert ver.metadata.security_metadata.classification == SecurityClassification.INTERNAL
    assert ver.metadata.security_metadata.labels == []
    assert ver.metadata.lineage_path == []

    # Profile retrieval should fall back to None pipeline and empty perms
    prof = await repository.get_operation_profile("prof-corrupt", tenant_id="tenant-alpha")
    assert prof is not None
    assert prof.adapter_pipeline is None
    assert prof.permissions == []


@pytest.mark.asyncio
async def test_additional_persistence_edge_cases(
    repository: DocumentRepository, data_store: RelationalDataStore
) -> None:
    """Test remaining branch paths for 100% persistence coverage."""
    # 1. get_latest_version for document with zero versions
    doc = Document(document_id="doc-zero-ver", tenant_id="tenant-alpha", title="Empty Doc")
    await repository.create_document(doc)
    assert await repository.get_latest_version("doc-zero-ver", tenant_id="tenant-alpha") is None

    # 2. update_version_state with invalid published_at timestamp format
    doc_meta = DocumentMetadata(
        document_id="doc-zero-ver",
        version_id="ver-pub-test",
        title="V1",
        author_id="author",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    ver = DocumentVersion(
        version_id="ver-pub-test",
        document_id="doc-zero-ver",
        version_number="1.0.0",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        created_by="author",
        metadata=doc_meta,
    )
    await repository.create_version(ver, tenant_id="tenant-alpha")
    updated = await repository.update_version_state(
        document_id="doc-zero-ver",
        version_id="ver-pub-test",
        target_state=DocumentLifecycleState.ARCHIVED,
        is_immutable=True,
        published_at="INVALID_TIMESTAMP_STRING",
        tenant_id="tenant-alpha",
    )
    assert updated.metadata.published_at is not None

    # Verify invalid published_at timestamp in publish_version falls back safely
    doc_meta_pub = DocumentMetadata(
        document_id="doc-zero-ver",
        version_id="ver-pub-test-2",
        title="V2",
        author_id="author",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    ver_pub = DocumentVersion(
        version_id="ver-pub-test-2",
        document_id="doc-zero-ver",
        version_number="1.0.1",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        created_by="author",
        metadata=doc_meta_pub,
    )
    await repository.create_version(ver_pub, tenant_id="tenant-alpha")
    pub_child, _ = await repository.publish_version(
        document_id="doc-zero-ver",
        version_id="ver-pub-test-2",
        parent_version_id=None,
        published_at="INVALID_TIMESTAMP_STRING",
        tenant_id="tenant-alpha",
    )
    assert pub_child.metadata.published_at is not None

    # 3. get_operation_profile non-existent
    assert await repository.get_operation_profile("prof-ghost", tenant_id="tenant-alpha") is None

    # 4. list_operation_profiles with single filters
    prof = DocumentOperationProfile(
        id="prof-filter-test",
        name="Filter Test",
        namespace="kortex.filter.ns",
        version="1.0.0",
        description="Filter test description",
        business_operation="FILTER_OP",
    )
    await repository.save_operation_profile(prof, tenant_id="tenant-alpha")

    by_op = await repository.list_operation_profiles(tenant_id="tenant-alpha", business_operation="FILTER_OP")
    assert len(by_op) == 1
    assert by_op[0].id == "prof-filter-test"

    by_ns = await repository.list_operation_profiles(tenant_id="tenant-alpha", namespace="kortex.filter.ns")
    assert len(by_ns) == 1
    assert by_ns[0].id == "prof-filter-test"

    all_profs = await repository.list_operation_profiles(tenant_id="tenant-alpha")
    assert len(all_profs) >= 1

    # 5. list_operation_history with corrupt JSON
    async def _inject_corrupt_history(session: AsyncSession) -> None:
        rec = DocumentOperationHistoryRecord(
            id="hist-corrupt-id",
            request_id="req-corrupt-1",
            tenant_id="tenant-alpha",
            profile_id="prof-filter-test",
            status="FAILED",
            errors_json="INVALID_JSON[",
            validation_report_json="INVALID_JSON{",
        )
        session.add(rec)

    await data_store.execute_in_transaction(_inject_corrupt_history)

    fetched_hist = await repository.get_operation_history("req-corrupt-1", tenant_id="tenant-alpha")
    assert fetched_hist is not None
    assert fetched_hist["errors"] == []
    assert fetched_hist["validation_report"] is None

    listed_hist = await repository.list_operation_history(tenant_id="tenant-alpha", profile_id="prof-filter-test")
    assert len(listed_hist) >= 1


@pytest.mark.asyncio
async def test_operation_profile_in_place_update(
    repository: DocumentRepository, data_store: RelationalDataStore
) -> None:
    """Verify that re-saving an existing operation profile version updates the record in-place."""
    # 1. Save an operation profile version, e.g. 1.0.0
    stage = PipelineStage(
        stage_id="stage-orig",
        adapter_id="adapter.test.original",
        required_capability=AdapterCapability.GENERATE,
    )
    pipeline = AdapterPipelineDefinition(
        pipeline_id="pipe-orig",
        profile_id="profile.inplace.test",
        stages=[stage],
        execution_mode=PipelineExecutionMode.SEQUENTIAL,
    )
    original_profile = DocumentOperationProfile(
        id="profile.inplace.test",
        name="Original Profile Name",
        namespace="kortex.test.inplace",
        version="1.0.0",
        description="Original operational description",
        business_operation="GENERATE_INPLACE",
        required_template_id="tmpl.orig",
        adapter_pipeline=pipeline,
        permissions=["perm:read"],
        output_bucket="original_bucket",
    )
    await repository.save_operation_profile(original_profile, tenant_id="tenant-alpha")

    # 2. Modify the same profile keeping tenant_id, profile_id, and version identical
    modified_stage = PipelineStage(
        stage_id="stage-mod",
        adapter_id="adapter.test.modified",
        required_capability=AdapterCapability.TRANSFORM,
    )
    modified_pipeline = AdapterPipelineDefinition(
        pipeline_id="pipe-mod",
        profile_id="profile.inplace.test",
        stages=[modified_stage],
        execution_mode=PipelineExecutionMode.CONDITIONAL,
    )
    modified_profile = DocumentOperationProfile(
        id="profile.inplace.test",
        name="Updated In-Place Profile Name",
        namespace="kortex.test.inplace.updated",
        version="1.0.0",
        description="Updated operational description in-place",
        business_operation="GENERATE_INPLACE_UPDATED",
        required_template_id="tmpl.updated",
        adapter_pipeline=modified_pipeline,
        permissions=["perm:read", "perm:write"],
        output_bucket="updated_bucket",
    )

    # 3. Save it again
    saved_mod = await repository.save_operation_profile(modified_profile, tenant_id="tenant-alpha")
    assert saved_mod.name == "Updated In-Place Profile Name"

    # 4. Verify existing database record is updated rather than a second record created
    async def _count_profiles(session: AsyncSession) -> int:
        stmt = select(DocumentOperationProfileRecord).where(
            DocumentOperationProfileRecord.tenant_id == "tenant-alpha",
            DocumentOperationProfileRecord.profile_id == "profile.inplace.test",
        )
        res = await session.execute(stmt)
        return len(res.scalars().all())

    count = await data_store.execute_in_transaction(_count_profiles)
    assert count == 1

    # 5. Retrieve the profile
    retrieved = await repository.get_operation_profile(
        "profile.inplace.test", version="1.0.0", tenant_id="tenant-alpha"
    )
    assert retrieved is not None

    # 6. Verify modified fields are preserved
    assert retrieved.name == "Updated In-Place Profile Name"
    assert retrieved.namespace == "kortex.test.inplace.updated"
    assert retrieved.description == "Updated operational description in-place"
    assert retrieved.business_operation == "GENERATE_INPLACE_UPDATED"
    assert retrieved.required_template_id == "tmpl.updated"
    assert retrieved.output_bucket == "updated_bucket"
    assert retrieved.permissions == ["perm:read", "perm:write"]
    assert retrieved.adapter_pipeline is not None
    assert retrieved.adapter_pipeline.pipeline_id == "pipe-mod"
    assert retrieved.adapter_pipeline.execution_mode == PipelineExecutionMode.CONDITIONAL
    assert len(retrieved.adapter_pipeline.stages) == 1
    assert retrieved.adapter_pipeline.stages[0].stage_id == "stage-mod"

    # 7. Verify original version identity remains unchanged
    assert retrieved.id == "profile.inplace.test"
    assert retrieved.version == "1.0.0"


@pytest.mark.asyncio
async def test_publish_version_genesis_success(
    repository: DocumentRepository,
) -> None:
    """Verify successful atomic publication of a root genesis document version."""
    doc = Document(document_id="doc-genesis-test", tenant_id="tenant-alpha", title="Genesis Test")
    await repository.create_document(doc)

    # Create root draft version
    meta = DocumentMetadata(
        document_id="doc-genesis-test",
        version_id="ver-genesis-1",
        title="Genesis Title",
        author_id="user-1",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v1 = DocumentVersion(
        version_id="ver-genesis-1",
        document_id="doc-genesis-test",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-1",
        metadata=meta,
    )
    await repository.create_version(v1, tenant_id="tenant-alpha")

    # Publish genesis version
    child, parent = await repository.publish_version(
        document_id="doc-genesis-test",
        version_id="ver-genesis-1",
        parent_version_id=None,
        tenant_id="tenant-alpha",
    )

    assert parent is None
    assert child.version_id == "ver-genesis-1"
    assert child.metadata.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert child.is_immutable is True
    assert child.metadata.published_at is not None

    # Verify DocumentRecord.current_version_id updated
    reloaded_doc = await repository.get_document("doc-genesis-test", tenant_id="tenant-alpha")
    assert reloaded_doc is not None
    assert reloaded_doc.current_version_id == "ver-genesis-1"


@pytest.mark.asyncio
async def test_publish_version_records_sha256_hash_when_provided(
    repository: DocumentRepository,
) -> None:
    """Verify publish_version's optional sha256_hash param (Milestone 7) is recorded and
    round-trips on subsequent reads; omitting it leaves the field None (backward compatible)."""
    doc = Document(document_id="doc-hash-persist", tenant_id="tenant-alpha", title="Hash Test")
    await repository.create_document(doc)

    meta = DocumentMetadata(
        document_id="doc-hash-persist",
        version_id="ver-hash-persist",
        title="Hash Title",
        author_id="user-1",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v1 = DocumentVersion(
        version_id="ver-hash-persist",
        document_id="doc-hash-persist",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-1",
        metadata=meta,
    )
    await repository.create_version(v1, tenant_id="tenant-alpha")

    expected_hash = "a" * 64
    child, _ = await repository.publish_version(
        document_id="doc-hash-persist",
        version_id="ver-hash-persist",
        parent_version_id=None,
        tenant_id="tenant-alpha",
        sha256_hash=expected_hash,
    )

    assert child.metadata.sha256_hash == expected_hash

    reread = await repository.get_version("doc-hash-persist", "ver-hash-persist", tenant_id="tenant-alpha")
    assert reread is not None
    assert reread.metadata.sha256_hash == expected_hash


@pytest.mark.asyncio
async def test_publish_version_without_sha256_hash_leaves_field_none(
    repository: DocumentRepository,
) -> None:
    """Verify omitting sha256_hash on publish_version leaves the field unset (default None)."""
    doc = Document(document_id="doc-hash-omit", tenant_id="tenant-alpha", title="Hash Omit Test")
    await repository.create_document(doc)

    meta = DocumentMetadata(
        document_id="doc-hash-omit",
        version_id="ver-hash-omit",
        title="Hash Omit Title",
        author_id="user-1",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v1 = DocumentVersion(
        version_id="ver-hash-omit",
        document_id="doc-hash-omit",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-1",
        metadata=meta,
    )
    await repository.create_version(v1, tenant_id="tenant-alpha")

    child, _ = await repository.publish_version(
        document_id="doc-hash-omit",
        version_id="ver-hash-omit",
        parent_version_id=None,
        tenant_id="tenant-alpha",
    )

    assert child.metadata.sha256_hash is None


@pytest.mark.asyncio
async def test_publish_version_with_parent_superseding(
    repository: DocumentRepository,
) -> None:
    """Verify child publication atomically supersedes active parent and updates document pointer."""
    doc = Document(document_id="doc-parent-test", tenant_id="tenant-alpha", title="Parent Test")
    await repository.create_document(doc)

    # 1. Create and publish parent V1
    m1 = DocumentMetadata(
        document_id="doc-parent-test",
        version_id="ver-parent-1",
        title="Parent V1",
        author_id="user-1",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v1 = DocumentVersion(
        version_id="ver-parent-1",
        document_id="doc-parent-test",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-1",
        metadata=m1,
    )
    await repository.create_version(v1, tenant_id="tenant-alpha")
    await repository.publish_version(
        document_id="doc-parent-test",
        version_id="ver-parent-1",
        parent_version_id=None,
        tenant_id="tenant-alpha",
    )

    # 2. Create child draft V2
    m2 = DocumentMetadata(
        document_id="doc-parent-test",
        version_id="ver-child-2",
        parent_version_id="ver-parent-1",
        title="Child V2",
        author_id="user-2",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-02T00:00:00Z",
    )
    v2 = DocumentVersion(
        version_id="ver-child-2",
        document_id="doc-parent-test",
        parent_version_id="ver-parent-1",
        version_number="1.0.1",
        created_at="2026-01-02T00:00:00Z",
        created_by="user-2",
        metadata=m2,
    )
    await repository.create_version(v2, tenant_id="tenant-alpha")

    # 3. Publish child V2 with expected predecessor V1
    child, parent = await repository.publish_version(
        document_id="doc-parent-test",
        version_id="ver-child-2",
        parent_version_id="ver-parent-1",
        tenant_id="tenant-alpha",
    )

    assert child.version_id == "ver-child-2"
    assert child.metadata.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert child.is_immutable is True

    assert parent is not None
    assert parent.version_id == "ver-parent-1"
    assert parent.metadata.lifecycle_state == DocumentLifecycleState.SUPERSEDED
    assert parent.is_immutable is True

    # 4. Check DocumentRecord pointer
    reloaded_doc = await repository.get_document("doc-parent-test", tenant_id="tenant-alpha")
    assert reloaded_doc is not None
    assert reloaded_doc.current_version_id == "ver-child-2"


@pytest.mark.asyncio
async def test_publish_version_rejections(
    repository: DocumentRepository,
) -> None:
    """Verify rejections on missing child, invalid child state, missing parent,
    non-published parent, and CAS collision."""
    doc = Document(document_id="doc-rej-test", tenant_id="tenant-alpha", title="Rejection Test")
    await repository.create_document(doc)

    # 1. Non-existent child
    with pytest.raises(DocumentLifecycleError, match="Cannot publish non-existent version"):
        await repository.publish_version(
            document_id="doc-rej-test",
            version_id="ghost-version",
            tenant_id="tenant-alpha",
        )

    # 2. Child in invalid state (e.g. SUPERSEDED)
    m1 = DocumentMetadata(
        document_id="doc-rej-test",
        version_id="ver-invalid-state",
        title="Invalid State Version",
        author_id="user-1",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v1 = DocumentVersion(
        version_id="ver-invalid-state",
        document_id="doc-rej-test",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-1",
        metadata=m1,
    )
    await repository.create_version(v1, tenant_id="tenant-alpha")
    await repository.update_version_state(
        document_id="doc-rej-test",
        version_id="ver-invalid-state",
        target_state=DocumentLifecycleState.ARCHIVED,
        is_immutable=True,
        tenant_id="tenant-alpha",
    )

    with pytest.raises(DocumentLifecycleError, match="must be in DRAFT or REVIEW"):
        await repository.publish_version(
            document_id="doc-rej-test",
            version_id="ver-invalid-state",
            tenant_id="tenant-alpha",
        )

    # 3. Non-existent parent supplied
    m2 = DocumentMetadata(
        document_id="doc-rej-test",
        version_id="ver-child-draft",
        parent_version_id="ghost-parent",
        title="Child Draft",
        author_id="user-1",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v2 = DocumentVersion(
        version_id="ver-child-draft",
        document_id="doc-rej-test",
        parent_version_id="ghost-parent",
        version_number="1.0.1",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-1",
        metadata=m2,
    )
    await repository.create_version(v2, tenant_id="tenant-alpha")

    with pytest.raises(DocumentLifecycleError, match="non-existent parent version"):
        await repository.publish_version(
            document_id="doc-rej-test",
            version_id="ver-child-draft",
            parent_version_id="ghost-parent",
            tenant_id="tenant-alpha",
        )

    # 4. Parent not in PUBLISHED state (e.g. parent is DRAFT)
    m_parent_draft = DocumentMetadata(
        document_id="doc-rej-test",
        version_id="ver-parent-draft",
        title="Parent Draft",
        author_id="user-1",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v_parent_draft = DocumentVersion(
        version_id="ver-parent-draft",
        document_id="doc-rej-test",
        version_number="1.0.2",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-1",
        metadata=m_parent_draft,
    )
    await repository.create_version(v_parent_draft, tenant_id="tenant-alpha")

    # Create child of v_parent_draft
    m_child_of_draft = DocumentMetadata(
        document_id="doc-rej-test",
        version_id="ver-child-of-draft",
        parent_version_id="ver-parent-draft",
        title="Child of Draft",
        author_id="user-1",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v_child_of_draft = DocumentVersion(
        version_id="ver-child-of-draft",
        document_id="doc-rej-test",
        parent_version_id="ver-parent-draft",
        version_number="1.0.3",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-1",
        metadata=m_child_of_draft,
    )
    await repository.create_version(v_child_of_draft, tenant_id="tenant-alpha")

    with pytest.raises(DocumentLifecycleError, match="expected 'PUBLISHED'"):
        await repository.publish_version(
            document_id="doc-rej-test",
            version_id="ver-child-of-draft",
            parent_version_id="ver-parent-draft",
            tenant_id="tenant-alpha",
        )

    # 5. Genesis publication when a version is already PUBLISHED
    # Publish v_parent_draft
    await repository.publish_version(
        document_id="doc-rej-test",
        version_id="ver-parent-draft",
        parent_version_id=None,
        tenant_id="tenant-alpha",
    )

    # Create another genesis draft
    m_another_gen = DocumentMetadata(
        document_id="doc-rej-test",
        version_id="ver-another-genesis",
        title="Another Genesis",
        author_id="user-1",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v_another_gen = DocumentVersion(
        version_id="ver-another-genesis",
        document_id="doc-rej-test",
        version_number="1.0.4",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-1",
        metadata=m_another_gen,
    )
    await repository.create_version(v_another_gen, tenant_id="tenant-alpha")

    # Publishing ver-another-genesis with parent_version_id=None must fail because current_version_id is no longer NULL
    with pytest.raises(DocumentLifecycleError, match="Concurrent publication collision"):
        await repository.publish_version(
            document_id="doc-rej-test",
            version_id="ver-another-genesis",
            parent_version_id=None,
            tenant_id="tenant-alpha",
        )


@pytest.mark.asyncio
async def test_publish_version_lineage_and_parent_identity_validation(
    repository: DocumentRepository,
) -> None:
    """Verify strict validation of parent-child lineage, cross-document, and cross-tenant boundaries."""
    doc_a = Document(document_id="doc-lineage-a", tenant_id="tenant-alpha", title="Lineage Doc A")
    doc_b = Document(document_id="doc-lineage-b", tenant_id="tenant-alpha", title="Lineage Doc B")
    doc_beta = Document(document_id="doc-lineage-beta", tenant_id="tenant-beta", title="Lineage Doc Beta")

    await repository.create_document(doc_a)
    await repository.create_document(doc_b)
    await repository.create_document(doc_beta)

    # 1. Publish genesis version on Doc A
    v_a1 = DocumentVersion(
        version_id="ver-A-1",
        document_id="doc-lineage-a",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="author",
        metadata=DocumentMetadata(
            document_id="doc-lineage-a",
            version_id="ver-A-1",
            title="A1",
            author_id="author",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-01T00:00:00Z",
        ),
    )
    await repository.create_version(v_a1, tenant_id="tenant-alpha")
    await repository.publish_version(
        document_id="doc-lineage-a",
        version_id="ver-A-1",
        parent_version_id=None,
        tenant_id="tenant-alpha",
    )

    # 2. Publish genesis version on Doc B
    v_b1 = DocumentVersion(
        version_id="ver-B-1",
        document_id="doc-lineage-b",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="author",
        metadata=DocumentMetadata(
            document_id="doc-lineage-b",
            version_id="ver-B-1",
            title="B1",
            author_id="author",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-01T00:00:00Z",
        ),
    )
    await repository.create_version(v_b1, tenant_id="tenant-alpha")
    await repository.publish_version(
        document_id="doc-lineage-b",
        version_id="ver-B-1",
        parent_version_id=None,
        tenant_id="tenant-alpha",
    )

    # 3. Create child version A2 derived from A1
    v_a2 = DocumentVersion(
        version_id="ver-A-2",
        document_id="doc-lineage-a",
        parent_version_id="ver-A-1",
        version_number="1.0.1",
        created_at="2026-01-02T00:00:00Z",
        created_by="author",
        metadata=DocumentMetadata(
            document_id="doc-lineage-a",
            version_id="ver-A-2",
            parent_version_id="ver-A-1",
            title="A2",
            author_id="author",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-02T00:00:00Z",
        ),
    )
    await repository.create_version(v_a2, tenant_id="tenant-alpha")

    # A. Wrong parent ID rejection: caller passes parent_version_id="ver-wrong"
    with pytest.raises(DocumentLifecycleError, match="Lineage mismatch"):
        await repository.publish_version(
            document_id="doc-lineage-a",
            version_id="ver-A-2",
            parent_version_id="ver-wrong",
            tenant_id="tenant-alpha",
        )

    # B. Child with parent published as genesis (caller passes parent_version_id=None)
    with pytest.raises(DocumentLifecycleError, match="Lineage mismatch"):
        await repository.publish_version(
            document_id="doc-lineage-a",
            version_id="ver-A-2",
            parent_version_id=None,
            tenant_id="tenant-alpha",
        )

    # C. Genesis child published with parent ID
    v_a_gen_draft = DocumentVersion(
        version_id="ver-A-gen-draft",
        document_id="doc-lineage-a",
        parent_version_id=None,
        version_number="1.0.2",
        created_at="2026-01-03T00:00:00Z",
        created_by="author",
        metadata=DocumentMetadata(
            document_id="doc-lineage-a",
            version_id="ver-A-gen-draft",
            parent_version_id=None,
            title="A Gen Draft",
            author_id="author",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-03T00:00:00Z",
        ),
    )
    await repository.create_version(v_a_gen_draft, tenant_id="tenant-alpha")
    with pytest.raises(DocumentLifecycleError, match="Lineage mismatch"):
        await repository.publish_version(
            document_id="doc-lineage-a",
            version_id="ver-A-gen-draft",
            parent_version_id="ver-A-1",
            tenant_id="tenant-alpha",
        )

    # D. Cross-document parent: child on Doc A with parent_version_id="ver-B-1" (on Doc B)
    v_a_cross_doc = DocumentVersion(
        version_id="ver-A-cross-doc",
        document_id="doc-lineage-a",
        parent_version_id="ver-B-1",
        version_number="1.0.3",
        created_at="2026-01-04T00:00:00Z",
        created_by="author",
        metadata=DocumentMetadata(
            document_id="doc-lineage-a",
            version_id="ver-A-cross-doc",
            parent_version_id="ver-B-1",
            title="A Cross Doc",
            author_id="author",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-04T00:00:00Z",
        ),
    )
    await repository.create_version(v_a_cross_doc, tenant_id="tenant-alpha")
    with pytest.raises(DocumentLifecycleError, match="non-existent parent version"):
        await repository.publish_version(
            document_id="doc-lineage-a",
            version_id="ver-A-cross-doc",
            parent_version_id="ver-B-1",
            tenant_id="tenant-alpha",
        )

    # E. Cross-tenant parent: child on Tenant Alpha attempting to use parent on Tenant Beta
    v_beta1 = DocumentVersion(
        version_id="ver-Beta-1",
        document_id="doc-lineage-beta",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="author",
        metadata=DocumentMetadata(
            document_id="doc-lineage-beta",
            version_id="ver-Beta-1",
            title="Beta 1",
            author_id="author",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-01T00:00:00Z",
            security_metadata=SecurityMetadata(tenant_id="tenant-beta"),
        ),
    )
    await repository.create_version(v_beta1, tenant_id="tenant-beta")
    await repository.publish_version(
        document_id="doc-lineage-beta",
        version_id="ver-Beta-1",
        parent_version_id=None,
        tenant_id="tenant-beta",
    )

    v_a_cross_tenant = DocumentVersion(
        version_id="ver-A-cross-tenant",
        document_id="doc-lineage-a",
        parent_version_id="ver-Beta-1",
        version_number="1.0.4",
        created_at="2026-01-05T00:00:00Z",
        created_by="author",
        metadata=DocumentMetadata(
            document_id="doc-lineage-a",
            version_id="ver-A-cross-tenant",
            parent_version_id="ver-Beta-1",
            title="A Cross Tenant",
            author_id="author",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-05T00:00:00Z",
        ),
    )
    await repository.create_version(v_a_cross_tenant, tenant_id="tenant-alpha")
    with pytest.raises(DocumentLifecycleError, match="non-existent parent version"):
        await repository.publish_version(
            document_id="doc-lineage-a",
            version_id="ver-A-cross-tenant",
            parent_version_id="ver-Beta-1",
            tenant_id="tenant-alpha",
        )


@pytest.mark.asyncio
async def test_pointer_invariant_lifecycle_stages(
    repository: DocumentRepository,
) -> None:
    """Verify that current_version_id represents the active published version exclusively at all lifecycle stages."""
    # 1. Initial document creation
    doc = Document(document_id="doc-ptr-stage", tenant_id="tenant-alpha", title="Pointer Invariant Doc")
    await repository.create_document(doc)

    reloaded_0 = await repository.get_document("doc-ptr-stage", tenant_id="tenant-alpha")
    assert reloaded_0 is not None
    assert reloaded_0.current_version_id is None

    # 2. Create DRAFT genesis version (V1)
    m1 = DocumentMetadata(
        document_id="doc-ptr-stage",
        version_id="ver-ptr-1",
        title="V1 Draft",
        author_id="user-1",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v1 = DocumentVersion(
        version_id="ver-ptr-1",
        document_id="doc-ptr-stage",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-1",
        metadata=m1,
    )
    await repository.create_version(v1, tenant_id="tenant-alpha")

    # Pointer MUST remain None after DRAFT creation
    reloaded_1 = await repository.get_document("doc-ptr-stage", tenant_id="tenant-alpha")
    assert reloaded_1 is not None
    assert reloaded_1.current_version_id is None

    # 3. Transition V1 to REVIEW
    await repository.update_version_state(
        document_id="doc-ptr-stage",
        version_id="ver-ptr-1",
        target_state=DocumentLifecycleState.REVIEW,
        is_immutable=False,
        tenant_id="tenant-alpha",
    )

    # Pointer MUST remain None during REVIEW
    reloaded_2 = await repository.get_document("doc-ptr-stage", tenant_id="tenant-alpha")
    assert reloaded_2 is not None
    assert reloaded_2.current_version_id is None

    # 4. Publish V1
    child_1, _ = await repository.publish_version(
        document_id="doc-ptr-stage",
        version_id="ver-ptr-1",
        parent_version_id=None,
        tenant_id="tenant-alpha",
    )
    assert child_1.metadata.lifecycle_state == DocumentLifecycleState.PUBLISHED

    # Pointer MUST now point to V1
    reloaded_3 = await repository.get_document("doc-ptr-stage", tenant_id="tenant-alpha")
    assert reloaded_3 is not None
    assert reloaded_3.current_version_id == "ver-ptr-1"

    # 5. Create child DRAFT V2 derived from V1
    m2 = DocumentMetadata(
        document_id="doc-ptr-stage",
        version_id="ver-ptr-2",
        parent_version_id="ver-ptr-1",
        title="V2 Draft",
        author_id="user-2",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-02T00:00:00Z",
    )
    v2 = DocumentVersion(
        version_id="ver-ptr-2",
        document_id="doc-ptr-stage",
        parent_version_id="ver-ptr-1",
        version_number="1.0.1",
        created_at="2026-01-02T00:00:00Z",
        created_by="user-2",
        metadata=m2,
    )
    await repository.create_version(v2, tenant_id="tenant-alpha")

    # Pointer MUST still point to V1; V1 remains PUBLISHED, V2 is DRAFT
    reloaded_4 = await repository.get_document("doc-ptr-stage", tenant_id="tenant-alpha")
    assert reloaded_4 is not None
    assert reloaded_4.current_version_id == "ver-ptr-1"

    v1_check = await repository.get_version("doc-ptr-stage", "ver-ptr-1", tenant_id="tenant-alpha")
    v2_check = await repository.get_version("doc-ptr-stage", "ver-ptr-2", tenant_id="tenant-alpha")
    assert v1_check is not None and v1_check.metadata.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert v2_check is not None and v2_check.metadata.lifecycle_state == DocumentLifecycleState.DRAFT

    # 6. Transition V2 to REVIEW
    await repository.update_version_state(
        document_id="doc-ptr-stage",
        version_id="ver-ptr-2",
        target_state=DocumentLifecycleState.REVIEW,
        is_immutable=False,
        tenant_id="tenant-alpha",
    )
    reloaded_5 = await repository.get_document("doc-ptr-stage", tenant_id="tenant-alpha")
    assert reloaded_5 is not None
    assert reloaded_5.current_version_id == "ver-ptr-1"

    # 7. Publish V2 -> Pointer updates to V2, V1 becomes SUPERSEDED
    await repository.publish_version(
        document_id="doc-ptr-stage",
        version_id="ver-ptr-2",
        parent_version_id="ver-ptr-1",
        tenant_id="tenant-alpha",
    )

    reloaded_6 = await repository.get_document("doc-ptr-stage", tenant_id="tenant-alpha")
    assert reloaded_6 is not None
    assert reloaded_6.current_version_id == "ver-ptr-2"

    v1_final = await repository.get_version("doc-ptr-stage", "ver-ptr-1", tenant_id="tenant-alpha")
    v2_final = await repository.get_version("doc-ptr-stage", "ver-ptr-2", tenant_id="tenant-alpha")
    assert v1_final is not None and v1_final.metadata.lifecycle_state == DocumentLifecycleState.SUPERSEDED
    assert v2_final is not None and v2_final.metadata.lifecycle_state == DocumentLifecycleState.PUBLISHED


@pytest.mark.asyncio
async def test_concurrent_genesis_publication_race(
    concurrent_data_store: RelationalDataStore,
) -> None:
    """Verify that concurrent genesis publication attempts across independent
    repositories result in exactly one winner."""
    repo_a = DocumentRepository(concurrent_data_store)
    repo_b = DocumentRepository(concurrent_data_store)

    doc = Document(document_id="doc-conc-gen", tenant_id="tenant-alpha", title="Concurrent Genesis Doc")
    await repo_a.create_document(doc)

    # Create competing genesis draft versions A and B
    v_a = DocumentVersion(
        version_id="ver-gen-A",
        document_id="doc-conc-gen",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-A",
        metadata=DocumentMetadata(
            document_id="doc-conc-gen",
            version_id="ver-gen-A",
            title="Gen A",
            author_id="user-A",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-01T00:00:00Z",
        ),
    )
    v_b = DocumentVersion(
        version_id="ver-gen-B",
        document_id="doc-conc-gen",
        version_number="1.0.1",
        created_at="2026-01-01T00:00:01Z",
        created_by="user-B",
        metadata=DocumentMetadata(
            document_id="doc-conc-gen",
            version_id="ver-gen-B",
            title="Gen B",
            author_id="user-B",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-01T00:00:01Z",
        ),
    )
    await repo_a.create_version(v_a, tenant_id="tenant-alpha")
    await repo_a.create_version(v_b, tenant_id="tenant-alpha")

    # Run competing publications concurrently using independent repository instances
    results = await asyncio.gather(
        repo_a.publish_version(
            document_id="doc-conc-gen",
            version_id="ver-gen-A",
            parent_version_id=None,
            tenant_id="tenant-alpha",
        ),
        repo_b.publish_version(
            document_id="doc-conc-gen",
            version_id="ver-gen-B",
            parent_version_id=None,
            tenant_id="tenant-alpha",
        ),
        return_exceptions=True,
    )

    # Exactly one winner, one DocumentLifecycleError loser
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], DocumentLifecycleError)

    winning_ver, _ = successes[0]
    losing_ver_id = "ver-gen-B" if winning_ver.version_id == "ver-gen-A" else "ver-gen-A"

    # Reload document and verify current pointer
    reloaded_doc = await repo_a.get_document("doc-conc-gen", tenant_id="tenant-alpha")
    assert reloaded_doc is not None
    assert reloaded_doc.current_version_id == winning_ver.version_id

    # Verify winning version is PUBLISHED, losing version rolled back completely (remains DRAFT)
    w_ver = await repo_a.get_version("doc-conc-gen", winning_ver.version_id, tenant_id="tenant-alpha")
    l_ver = await repo_a.get_version("doc-conc-gen", losing_ver_id, tenant_id="tenant-alpha")

    assert w_ver is not None and w_ver.metadata.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert w_ver.is_immutable is True

    assert l_ver is not None and l_ver.metadata.lifecycle_state == DocumentLifecycleState.DRAFT
    assert l_ver.is_immutable is False

    # Check total published versions for document
    all_versions = await repo_a.list_versions("doc-conc-gen", tenant_id="tenant-alpha")
    published_versions = [v for v in all_versions if v.metadata.lifecycle_state == DocumentLifecycleState.PUBLISHED]
    assert len(published_versions) == 1


@pytest.mark.asyncio
async def test_concurrent_sibling_publication_race(
    concurrent_data_store: RelationalDataStore,
) -> None:
    """Verify that concurrent sibling child publications across independent
    repositories result in exactly one winner."""
    repo_a = DocumentRepository(concurrent_data_store)
    repo_b = DocumentRepository(concurrent_data_store)

    doc = Document(document_id="doc-conc-sib", tenant_id="tenant-alpha", title="Concurrent Sibling Doc")
    await repo_a.create_document(doc)

    # 1. Publish parent V1
    v1 = DocumentVersion(
        version_id="ver-parent-root",
        document_id="doc-conc-sib",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="lead",
        metadata=DocumentMetadata(
            document_id="doc-conc-sib",
            version_id="ver-parent-root",
            title="Root V1",
            author_id="lead",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-01T00:00:00Z",
        ),
    )
    await repo_a.create_version(v1, tenant_id="tenant-alpha")
    await repo_a.publish_version(
        document_id="doc-conc-sib",
        version_id="ver-parent-root",
        parent_version_id=None,
        tenant_id="tenant-alpha",
    )

    # 2. Create sibling child drafts derived from V1
    v2_a = DocumentVersion(
        version_id="ver-child-A",
        document_id="doc-conc-sib",
        parent_version_id="ver-parent-root",
        version_number="1.0.1",
        created_at="2026-01-02T00:00:00Z",
        created_by="user-A",
        metadata=DocumentMetadata(
            document_id="doc-conc-sib",
            version_id="ver-child-A",
            parent_version_id="ver-parent-root",
            title="Child A",
            author_id="user-A",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-02T00:00:00Z",
        ),
    )
    v2_b = DocumentVersion(
        version_id="ver-child-B",
        document_id="doc-conc-sib",
        parent_version_id="ver-parent-root",
        version_number="1.0.2",
        created_at="2026-01-02T00:00:01Z",
        created_by="user-B",
        metadata=DocumentMetadata(
            document_id="doc-conc-sib",
            version_id="ver-child-B",
            parent_version_id="ver-parent-root",
            title="Child B",
            author_id="user-B",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-02T00:00:01Z",
        ),
    )
    await repo_a.create_version(v2_a, tenant_id="tenant-alpha")
    await repo_a.create_version(v2_b, tenant_id="tenant-alpha")

    # 3. Competing concurrent sibling publication across independent repository instances
    results = await asyncio.gather(
        repo_a.publish_version(
            document_id="doc-conc-sib",
            version_id="ver-child-A",
            parent_version_id="ver-parent-root",
            tenant_id="tenant-alpha",
        ),
        repo_b.publish_version(
            document_id="doc-conc-sib",
            version_id="ver-child-B",
            parent_version_id="ver-parent-root",
            tenant_id="tenant-alpha",
        ),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], DocumentLifecycleError)

    winning_child, _superseded_parent = successes[0]
    losing_child_id = "ver-child-B" if winning_child.version_id == "ver-child-A" else "ver-child-A"

    # 4. Verify Document pointer
    reloaded_doc = await repo_a.get_document("doc-conc-sib", tenant_id="tenant-alpha")
    assert reloaded_doc is not None
    assert reloaded_doc.current_version_id == winning_child.version_id

    # 5. Verify states: parent = SUPERSEDED, winner = PUBLISHED, loser = DRAFT
    p_ver = await repo_a.get_version("doc-conc-sib", "ver-parent-root", tenant_id="tenant-alpha")
    w_ver = await repo_a.get_version("doc-conc-sib", winning_child.version_id, tenant_id="tenant-alpha")
    l_ver = await repo_a.get_version("doc-conc-sib", losing_child_id, tenant_id="tenant-alpha")

    assert p_ver is not None and p_ver.metadata.lifecycle_state == DocumentLifecycleState.SUPERSEDED
    assert p_ver.is_immutable is True

    assert w_ver is not None and w_ver.metadata.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert w_ver.is_immutable is True

    assert l_ver is not None and l_ver.metadata.lifecycle_state == DocumentLifecycleState.DRAFT
    assert l_ver.is_immutable is False

    # Check that exactly one version is PUBLISHED
    all_versions = await repo_a.list_versions("doc-conc-sib", tenant_id="tenant-alpha")
    published_versions = [v for v in all_versions if v.metadata.lifecycle_state == DocumentLifecycleState.PUBLISHED]
    assert len(published_versions) == 1


@pytest.mark.asyncio
async def test_publish_version_forced_failure_rollback(
    data_store: RelationalDataStore,
) -> None:
    """Verify that a forced exception after CAS step completely rolls back all publication mutations."""
    repo = DocumentRepository(data_store)
    doc = Document(document_id="doc-fail-rollback", tenant_id="tenant-alpha", title="Fail Rollback Doc")
    await repo.create_document(doc)

    meta = DocumentMetadata(
        document_id="doc-fail-rollback",
        version_id="ver-fail-1",
        title="V1",
        author_id="user-1",
        lifecycle_state=DocumentLifecycleState.DRAFT,
        created_at="2026-01-01T00:00:00Z",
    )
    v1 = DocumentVersion(
        version_id="ver-fail-1",
        document_id="doc-fail-rollback",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="user-1",
        metadata=meta,
    )
    await repo.create_version(v1, tenant_id="tenant-alpha")

    # Hook repository to raise an exception after the CAS execution inside transaction
    class FailingDocumentRepository(DocumentRepository):
        async def publish_version(self, *args, **kwargs):
            orig_converter = self._version_to_domain

            def _failing_converter(record):
                raise RuntimeError("Simulated transient failure post-CAS")

            self._version_to_domain = _failing_converter
            try:
                return await super().publish_version(*args, **kwargs)
            finally:
                self._version_to_domain = orig_converter

    failing_repo = FailingDocumentRepository(data_store)
    with pytest.raises(RuntimeError, match="Simulated transient failure post-CAS"):
        await failing_repo.publish_version(
            document_id="doc-fail-rollback",
            version_id="ver-fail-1",
            parent_version_id=None,
            tenant_id="tenant-alpha",
        )

    # Prove database state returned 100% to pre-publication state
    reloaded_doc = await repo.get_document("doc-fail-rollback", tenant_id="tenant-alpha")
    assert reloaded_doc is not None
    assert reloaded_doc.current_version_id is None

    reloaded_ver = await repo.get_version("doc-fail-rollback", "ver-fail-1", tenant_id="tenant-alpha")
    assert reloaded_ver is not None
    assert reloaded_ver.metadata.lifecycle_state == DocumentLifecycleState.DRAFT
    assert reloaded_ver.is_immutable is False
    assert reloaded_ver.metadata.published_at is None
