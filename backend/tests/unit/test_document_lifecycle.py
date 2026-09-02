"""Unit tests for DocumentLifecycleManager in KORTEX OS Document Engine.

Covers state machine transitions, immutability locks, lineage graph traversal,
deterministic SemVer derivation, repository-backed operations with CAS gate publication,
and multi-tenant isolation.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from kortex.core.db import DatabaseEngineManager
from kortex.engines.document.exceptions import DocumentLifecycleError
from kortex.engines.document.interfaces import (
    IDocumentLifecycleManager,
)
from kortex.engines.document.lifecycle import DocumentLifecycleManager
from kortex.engines.document.models import (
    DocumentLifecycleState,
    DocumentMetadata,
    DocumentVersion,
    SecurityMetadata,
)
from kortex.engines.document.persistence import DocumentRepository
from kortex.engines.document.security import DefaultVerificationService
from kortex.engines.storage.stores.cache_store import MemoryCacheStore
from kortex.engines.storage.stores.data_store import RelationalDataStore


@pytest_asyncio.fixture
async def db_manager(tmp_path) -> DatabaseEngineManager:
    """Create file-backed SQLite DatabaseEngineManager."""
    db_file = tmp_path / "test_document_lifecycle.db"
    mgr = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_file}")
    await mgr.connect()
    await mgr.create_all_tables()
    yield mgr
    await mgr.disconnect()


@pytest_asyncio.fixture
async def data_store(db_manager: DatabaseEngineManager) -> RelationalDataStore:
    """Create RelationalDataStore fixture."""
    return RelationalDataStore(db_manager)


@pytest_asyncio.fixture
async def concurrent_data_store(tmp_path) -> RelationalDataStore:
    """Create a file-backed RelationalDataStore supporting true independent concurrent transactions."""
    db_file = tmp_path / "concurrent_lifecycle_test.db"
    db_mgr = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_file}")
    await db_mgr.connect()
    await db_mgr.create_all_tables()
    store = RelationalDataStore(db_mgr)
    yield store
    await db_mgr.disconnect()


@pytest_asyncio.fixture
async def repository(data_store: RelationalDataStore) -> DocumentRepository:
    """Create DocumentRepository fixture."""
    return DocumentRepository(data_store)


@pytest.fixture
def memory_lifecycle_manager() -> DocumentLifecycleManager:
    """Create in-memory DocumentLifecycleManager fixture."""
    return DocumentLifecycleManager(repository=None)


@pytest.fixture
def repo_lifecycle_manager(repository: DocumentRepository) -> DocumentLifecycleManager:
    """Create repository-backed DocumentLifecycleManager fixture."""
    return DocumentLifecycleManager(repository=repository)


# =============================================================================
# 1. Protocol Compliance & Invariants
# =============================================================================


def test_protocol_compliance(
    memory_lifecycle_manager: DocumentLifecycleManager,
    repo_lifecycle_manager: DocumentLifecycleManager,
) -> None:
    """Verify DocumentLifecycleManager satisfies IDocumentLifecycleManager protocol."""
    assert isinstance(memory_lifecycle_manager, IDocumentLifecycleManager)
    assert isinstance(repo_lifecycle_manager, IDocumentLifecycleManager)
    assert memory_lifecycle_manager.repository is None
    assert repo_lifecycle_manager.repository is not None


def test_semver_patch_derivation(memory_lifecycle_manager: DocumentLifecycleManager) -> None:
    """Verify deterministic SemVer patch derivation rules and strict SemVer 2.0.0 enforcement."""
    derive = memory_lifecycle_manager.derive_next_version_number

    # Valid SemVer 2.0.0 patch increments
    assert derive("1.0.0") == "1.0.1"
    assert derive("1.0.1") == "1.0.2"
    assert derive("1.0.9") == "1.0.10"
    assert derive("1.4.9") == "1.4.10"
    assert derive("1.9.9") == "1.9.10"
    assert derive("2.10.99") == "2.10.100"

    # Invalid SemVer formats MUST raise DocumentLifecycleError
    invalid_versions = [
        "1",
        "1.2",
        "1.a.3",
        "foo",
        "custom-v1",
        "01.2.3",
        "1.b",
        "x",
        "1.2.3.4",
        "v1.0.0",
        "",
        "   ",
        "1.0.0.0",
        "1.0.0-",
    ]
    for inv in invalid_versions:
        with pytest.raises(DocumentLifecycleError, match="Invalid semantic version format"):
            derive(inv)


# =============================================================================
# 2. State Machine Transitions & Immutability
# =============================================================================


def test_valid_transitions_matrix(memory_lifecycle_manager: DocumentLifecycleManager) -> None:
    """Verify all valid transitions in the state machine matrix."""
    mgr = memory_lifecycle_manager

    # DRAFT
    assert mgr.validate_transition(DocumentLifecycleState.DRAFT, DocumentLifecycleState.REVIEW) is True
    assert mgr.validate_transition(DocumentLifecycleState.DRAFT, DocumentLifecycleState.PUBLISHED) is True
    assert mgr.validate_transition(DocumentLifecycleState.DRAFT, DocumentLifecycleState.LOGICAL_DELETE) is True

    # REVIEW
    assert mgr.validate_transition(DocumentLifecycleState.REVIEW, DocumentLifecycleState.DRAFT) is True
    assert mgr.validate_transition(DocumentLifecycleState.REVIEW, DocumentLifecycleState.PUBLISHED) is True
    assert mgr.validate_transition(DocumentLifecycleState.REVIEW, DocumentLifecycleState.LOGICAL_DELETE) is True

    # PUBLISHED
    assert mgr.validate_transition(DocumentLifecycleState.PUBLISHED, DocumentLifecycleState.SUPERSEDED) is True
    assert mgr.validate_transition(DocumentLifecycleState.PUBLISHED, DocumentLifecycleState.ARCHIVED) is True
    assert mgr.validate_transition(DocumentLifecycleState.PUBLISHED, DocumentLifecycleState.LOGICAL_DELETE) is True

    # SUPERSEDED
    assert mgr.validate_transition(DocumentLifecycleState.SUPERSEDED, DocumentLifecycleState.ARCHIVED) is True
    assert mgr.validate_transition(DocumentLifecycleState.SUPERSEDED, DocumentLifecycleState.LOGICAL_DELETE) is True

    # ARCHIVED
    assert mgr.validate_transition(DocumentLifecycleState.ARCHIVED, DocumentLifecycleState.LOGICAL_DELETE) is True

    # Idempotent same-state
    for state in DocumentLifecycleState:
        assert mgr.validate_transition(state, state) is True


def test_invalid_transitions_matrix(memory_lifecycle_manager: DocumentLifecycleManager) -> None:
    """Verify invalid transitions are rejected with DocumentLifecycleError."""
    mgr = memory_lifecycle_manager

    invalid_pairs = [
        (DocumentLifecycleState.DRAFT, DocumentLifecycleState.SUPERSEDED),
        (DocumentLifecycleState.DRAFT, DocumentLifecycleState.ARCHIVED),
        (DocumentLifecycleState.REVIEW, DocumentLifecycleState.SUPERSEDED),
        (DocumentLifecycleState.REVIEW, DocumentLifecycleState.ARCHIVED),
        (DocumentLifecycleState.PUBLISHED, DocumentLifecycleState.DRAFT),
        (DocumentLifecycleState.PUBLISHED, DocumentLifecycleState.REVIEW),
        (DocumentLifecycleState.SUPERSEDED, DocumentLifecycleState.DRAFT),
        (DocumentLifecycleState.SUPERSEDED, DocumentLifecycleState.REVIEW),
        (DocumentLifecycleState.SUPERSEDED, DocumentLifecycleState.PUBLISHED),
        (DocumentLifecycleState.ARCHIVED, DocumentLifecycleState.DRAFT),
        (DocumentLifecycleState.ARCHIVED, DocumentLifecycleState.REVIEW),
        (DocumentLifecycleState.ARCHIVED, DocumentLifecycleState.PUBLISHED),
        (DocumentLifecycleState.ARCHIVED, DocumentLifecycleState.SUPERSEDED),
        (DocumentLifecycleState.LOGICAL_DELETE, DocumentLifecycleState.DRAFT),
        (DocumentLifecycleState.LOGICAL_DELETE, DocumentLifecycleState.PUBLISHED),
        (DocumentLifecycleState.LOGICAL_DELETE, DocumentLifecycleState.ARCHIVED),
    ]

    for current, target in invalid_pairs:
        with pytest.raises(DocumentLifecycleError, match="Invalid lifecycle transition"):
            mgr.validate_transition(current, target)


# =============================================================================
# 3. In-Memory Lifecycle Operations
# =============================================================================


@pytest.mark.asyncio
async def test_in_memory_root_and_child_version_creation(
    memory_lifecycle_manager: DocumentLifecycleManager,
) -> None:
    """Verify root and child version creation and lineage in-memory."""
    mgr = memory_lifecycle_manager

    # 1. Create root version
    v1 = await mgr.create_version(
        title="Invoice Genesis",
        author_id="user-alice",
        version_id="ver-1",
        document_id="doc-inv-1",
    )
    assert v1.document_id == "doc-inv-1"
    assert v1.version_id == "ver-1"
    assert v1.version_number == "1.0.0"
    assert v1.parent_version_id is None
    assert v1.metadata.lineage_path == ["ver-1"]
    assert v1.metadata.lifecycle_state == DocumentLifecycleState.DRAFT
    assert await mgr.is_immutable("doc-inv-1", "ver-1") is False

    # 2. Transition V1 to REVIEW then PUBLISHED
    m1_rev = await mgr.transition_state("doc-inv-1", "ver-1", DocumentLifecycleState.REVIEW)
    assert m1_rev.lifecycle_state == DocumentLifecycleState.REVIEW

    m1_pub = await mgr.transition_state("doc-inv-1", "ver-1", DocumentLifecycleState.PUBLISHED)
    assert m1_pub.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert m1_pub.is_immutable is True
    assert await mgr.is_immutable("doc-inv-1", "ver-1") is True

    # 3. Create child version V2 derived from V1
    v2 = await mgr.create_child_version(
        parent_version_id="ver-1",
        document_id="doc-inv-1",
        title="Invoice Revision 2",
        author_id="user-bob",
    )
    assert v2.document_id == "doc-inv-1"
    assert v2.parent_version_id == "ver-1"
    assert v2.version_number == "1.0.1"
    assert v2.metadata.lineage_path == ["ver-1", v2.version_id]
    assert v2.metadata.lifecycle_state == DocumentLifecycleState.DRAFT

    # 4. Create child version V3 with explicit SemVer
    v3 = await mgr.create_child_version(
        parent_version_id=v2.version_id,
        document_id="doc-inv-1",
        version_number="2.0.0",
        author_id="user-charlie",
    )
    assert v3.version_number == "2.0.0"
    assert v3.metadata.lineage_path == ["ver-1", v2.version_id, v3.version_id]

    # 5. Publish V2 -> V1 must be SUPERSEDED in memory
    m2_pub = await mgr.transition_state("doc-inv-1", v2.version_id, DocumentLifecycleState.PUBLISHED)
    assert m2_pub.lifecycle_state == DocumentLifecycleState.PUBLISHED

    v1_meta = await mgr.get_version("doc-inv-1", "ver-1")
    assert v1_meta.lifecycle_state == DocumentLifecycleState.SUPERSEDED
    assert v1_meta.is_immutable is True

    # 6. Lineage traversal
    lineage = await mgr.get_lineage("doc-inv-1")
    assert len(lineage) == 3
    assert [m.version_id for m in lineage] == ["ver-1", v2.version_id, v3.version_id]

    # 7. Latest version lookup
    latest = await mgr.get_latest_version("doc-inv-1")
    assert latest.version_id == v3.version_id


@pytest.mark.asyncio
async def test_in_memory_rejections_and_edge_cases(
    memory_lifecycle_manager: DocumentLifecycleManager,
) -> None:
    """Verify in-memory error handling for invalid operations."""
    mgr = memory_lifecycle_manager

    # 1. Missing parent version
    with pytest.raises(DocumentLifecycleError, match=r"not found|does not exist"):
        await mgr.create_child_version(parent_version_id="missing-parent")

    # 2. Duplicate version ID
    await mgr.create_version(document_id="doc-dup", version_id="ver-unique")
    with pytest.raises(DocumentLifecycleError, match="Duplicate version ID"):
        await mgr.create_version(document_id="doc-dup", version_id="ver-unique")

    # 3. Duplicate version number
    with pytest.raises(DocumentLifecycleError, match="Duplicate version number"):
        await mgr.create_version(document_id="doc-dup", version_id="ver-2", version_number="1.0.0")

    # 4. Soft-deleted parent derivation rejection
    await mgr.create_version(document_id="doc-del", version_id="ver-del")
    await mgr.transition_state("doc-del", "ver-del", DocumentLifecycleState.LOGICAL_DELETE)
    with pytest.raises(DocumentLifecycleError, match="Cannot create child version from soft-deleted"):
        await mgr.create_child_version(parent_version_id="ver-del", document_id="doc-del")

    # 5. Document ID mismatch
    with pytest.raises(DocumentLifecycleError, match="Document ID mismatch"):
        await mgr.get_version(document_id="wrong-doc", version_id="ver-del")


# =============================================================================
# 4. Repository-Backed Lifecycle Operations
# =============================================================================


@pytest.mark.asyncio
async def test_repository_backed_lifecycle_flow(
    repo_lifecycle_manager: DocumentLifecycleManager,
    repository: DocumentRepository,
) -> None:
    """Verify complete repository-backed lifecycle transitions, CAS publication, and lineage."""
    mgr = repo_lifecycle_manager

    # 1. Create genesis version
    v1 = await mgr.create_version(
        document_id="doc-repo-1",
        title="Payslip Root",
        author_id="hr-admin",
        version_id="v1-repo",
        tenant_id="tenant-acme",
    )
    assert v1.document_id == "doc-repo-1"
    assert v1.version_id == "v1-repo"
    assert v1.version_number == "1.0.0"

    # 2. Publish genesis version via CAS
    m1_pub = await mgr.transition_state(
        document_id="doc-repo-1",
        version_id="v1-repo",
        target_state=DocumentLifecycleState.PUBLISHED,
        tenant_id="tenant-acme",
    )
    assert m1_pub.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert m1_pub.is_immutable is True

    doc = await repository.get_document("doc-repo-1", tenant_id="tenant-acme")
    assert doc is not None
    assert doc.current_version_id == "v1-repo"

    # 3. Create child version V2 derived from V1
    v2 = await mgr.create_child_version(
        parent_version_id="v1-repo",
        document_id="doc-repo-1",
        title="Payslip Revision 2",
        author_id="hr-admin",
        tenant_id="tenant-acme",
    )
    assert v2.version_number == "1.0.1"
    assert v2.metadata.lineage_path == ["v1-repo", v2.version_id]

    # 4. Publish child V2 -> V1 atomically becomes SUPERSEDED
    m2_pub = await mgr.transition_state(
        document_id="doc-repo-1",
        version_id=v2.version_id,
        target_state=DocumentLifecycleState.PUBLISHED,
        tenant_id="tenant-acme",
    )
    assert m2_pub.lifecycle_state == DocumentLifecycleState.PUBLISHED

    # Verify parent V1 is now SUPERSEDED
    v1_reloaded = await mgr.get_version("doc-repo-1", "v1-repo", tenant_id="tenant-acme")
    assert v1_reloaded.lifecycle_state == DocumentLifecycleState.SUPERSEDED
    assert v1_reloaded.is_immutable is True

    # Verify document current pointer updated to V2
    doc_reloaded = await repository.get_document("doc-repo-1", tenant_id="tenant-acme")
    assert doc_reloaded is not None
    assert doc_reloaded.current_version_id == v2.version_id

    # 5. Retrieve full lineage
    lineage = await mgr.get_lineage("doc-repo-1", tenant_id="tenant-acme")
    assert len(lineage) == 2
    assert lineage[0].version_id == "v1-repo"
    assert lineage[1].version_id == v2.version_id

    # 6. Archive V1
    m1_arch = await mgr.transition_state(
        document_id="doc-repo-1",
        version_id="v1-repo",
        target_state=DocumentLifecycleState.ARCHIVED,
        tenant_id="tenant-acme",
    )
    assert m1_arch.lifecycle_state == DocumentLifecycleState.ARCHIVED


@pytest.mark.asyncio
async def test_repository_backed_tenant_isolation(
    repo_lifecycle_manager: DocumentLifecycleManager,
) -> None:
    """Verify strict tenant isolation in repository-backed lifecycle manager."""
    mgr = repo_lifecycle_manager

    # Tenant Alpha creates a version
    await mgr.create_version(
        document_id="doc-tenant-iso",
        title="Tenant A Doc",
        version_id="ver-a-1",
        tenant_id="tenant-alpha",
    )

    # Tenant Beta attempts to access Tenant Alpha's version
    with pytest.raises(DocumentLifecycleError, match="not found"):
        await mgr.get_version(
            document_id="doc-tenant-iso",
            version_id="ver-a-1",
            tenant_id="tenant-beta",
        )

    # Tenant Beta attempts to derive from Tenant Alpha's version
    with pytest.raises(DocumentLifecycleError, match=r"not found|does not exist"):
        await mgr.create_child_version(
            parent_version_id="ver-a-1",
            document_id="doc-tenant-iso",
            tenant_id="tenant-beta",
        )


# =============================================================================
# 5. Concurrency Race & CAS Gate Proof
# =============================================================================


@pytest.mark.asyncio
async def test_competing_publication_race_cas_rejection(
    repository: DocumentRepository,
    repo_lifecycle_manager: DocumentLifecycleManager,
) -> None:
    """Prove that sibling publication race allows exactly one winner while the other fails cleanly."""
    mgr = repo_lifecycle_manager

    # 1. Setup V1 as PUBLISHED
    await mgr.create_version(
        document_id="doc-race-1",
        title="Base Version",
        version_id="ver-base-1",
        tenant_id="tenant-race",
    )
    await mgr.transition_state(
        document_id="doc-race-1",
        version_id="ver-base-1",
        target_state=DocumentLifecycleState.PUBLISHED,
        tenant_id="tenant-race",
    )

    # 2. Create competing sibling drafts V2 and V3 (both derived from V1)
    v2 = await mgr.create_child_version(
        parent_version_id="ver-base-1",
        document_id="doc-race-1",
        version_number="1.0.1",
        tenant_id="tenant-race",
    )
    v3 = await mgr.create_child_version(
        parent_version_id="ver-base-1",
        document_id="doc-race-1",
        version_number="1.0.2",
        tenant_id="tenant-race",
    )

    # 3. Transaction A publishes V2 (wins the CAS gate)
    m2_pub = await mgr.transition_state(
        document_id="doc-race-1",
        version_id=v2.version_id,
        target_state=DocumentLifecycleState.PUBLISHED,
        tenant_id="tenant-race",
    )
    assert m2_pub.lifecycle_state == DocumentLifecycleState.PUBLISHED

    # 4. Transaction B attempts to publish V3 with stale predecessor V1
    # Because V1 is now SUPERSEDED and current_version_id is V2, CAS and parent state checks reject B
    with pytest.raises(DocumentLifecycleError, match=r"expected 'PUBLISHED'|Concurrent publication collision"):
        await mgr.transition_state(
            document_id="doc-race-1",
            version_id=v3.version_id,
            target_state=DocumentLifecycleState.PUBLISHED,
            tenant_id="tenant-race",
        )

    # 5. Verify final database state: V1=SUPERSEDED, V2=PUBLISHED, V3=DRAFT
    v1_final = await mgr.get_version("doc-race-1", "ver-base-1", tenant_id="tenant-race")
    v2_final = await mgr.get_version("doc-race-1", v2.version_id, tenant_id="tenant-race")
    v3_final = await mgr.get_version("doc-race-1", v3.version_id, tenant_id="tenant-race")

    assert v1_final.lifecycle_state == DocumentLifecycleState.SUPERSEDED
    assert v2_final.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert v3_final.lifecycle_state == DocumentLifecycleState.DRAFT

    doc_final = await repository.get_document("doc-race-1", tenant_id="tenant-race")
    assert doc_final is not None
    assert doc_final.current_version_id == v2.version_id


@pytest.mark.asyncio
async def test_additional_semver_and_lifecycle_edge_cases(
    memory_lifecycle_manager: DocumentLifecycleManager,
    repo_lifecycle_manager: DocumentLifecycleManager,
) -> None:
    """Verify strict SemVer 2.0.0 validation and repository edge cases."""
    # Invalid SemVer in create_version (in-memory mode)
    with pytest.raises(DocumentLifecycleError, match="Invalid semantic version format"):
        await memory_lifecycle_manager.create_version(
            document_id="doc-semver-inv",
            version_number="custom-v1",
            tenant_id="tenant-edge",
        )

    with pytest.raises(DocumentLifecycleError, match="Invalid semantic version format"):
        await memory_lifecycle_manager.create_version(
            document_id="doc-semver-inv2",
            version_number="01.2.3",
            tenant_id="tenant-edge",
        )

    # Invalid SemVer in child version creation
    await memory_lifecycle_manager.create_version(
        document_id="doc-semver-child-inv",
        version_id="ver-semver-root",
        tenant_id="tenant-edge",
    )
    with pytest.raises(DocumentLifecycleError, match="Invalid semantic version format"):
        await memory_lifecycle_manager.create_version(
            document_id="doc-semver-child-inv",
            parent_version_id="ver-semver-root",
            version_number="invalid.child.ver",
            tenant_id="tenant-edge",
        )

    # Repository mode edge cases
    repo_mgr = repo_lifecycle_manager

    # 1. create_version with missing parent in repo mode
    with pytest.raises(DocumentLifecycleError, match="does not exist"):
        await repo_mgr.create_version(
            document_id="doc-edge-1",
            parent_version_id="ghost-parent",
            tenant_id="tenant-edge",
        )

    # 2. create_version with soft-deleted parent in repo mode
    await repo_mgr.create_version(
        document_id="doc-edge-del",
        version_id="ver-edge-del",
        tenant_id="tenant-edge",
    )
    await repo_mgr.transition_state(
        document_id="doc-edge-del",
        version_id="ver-edge-del",
        target_state=DocumentLifecycleState.LOGICAL_DELETE,
        tenant_id="tenant-edge",
    )
    with pytest.raises(DocumentLifecycleError, match="Cannot create child version from soft-deleted"):
        await repo_mgr.create_version(
            document_id="doc-edge-del",
            parent_version_id="ver-edge-del",
            tenant_id="tenant-edge",
        )

    # 3. transition_state with document ID mismatch
    with pytest.raises(DocumentLifecycleError, match=r"not found|Document ID mismatch"):
        await repo_mgr.transition_state(
            document_id="wrong-doc-id",
            version_id="ver-edge-del",
            target_state=DocumentLifecycleState.LOGICAL_DELETE,
            tenant_id="tenant-edge",
        )

    # 4. transition_state idempotent same-state in repo mode
    await repo_mgr.create_version(
        document_id="doc-edge-idemp",
        version_id="ver-edge-idemp",
        tenant_id="tenant-edge",
    )
    meta_idemp = await repo_mgr.transition_state(
        document_id="doc-edge-idemp",
        version_id="ver-edge-idemp",
        target_state=DocumentLifecycleState.DRAFT,
        tenant_id="tenant-edge",
    )
    assert meta_idemp.lifecycle_state == DocumentLifecycleState.DRAFT

    # 5. Non-existent document get_latest_version / get_lineage in repo mode
    with pytest.raises(DocumentLifecycleError, match="No document versions found"):
        await repo_mgr.get_latest_version("doc-ghost", tenant_id="tenant-edge")

    with pytest.raises(DocumentLifecycleError, match="No document lineage found"):
        await repo_mgr.get_lineage("doc-ghost", tenant_id="tenant-edge")

    # repo get_version_object with document_id=None raises not found
    with pytest.raises(DocumentLifecycleError, match="not found"):
        await repo_mgr.get_version_object("ver-any", document_id=None)

    # repo get_latest_version returns latest metadata for created document
    latest_repo_meta = await repo_mgr.get_latest_version("doc-edge-idemp", tenant_id="tenant-edge")
    assert latest_repo_meta.version_id == "ver-edge-idemp"

    # 6. In-memory edge cases: document ID mismatch, idempotent transition, non-existent lookups
    mem_mgr = memory_lifecycle_manager

    await mem_mgr.create_version(document_id="doc-mem-parent-1", version_id="ver-mem-parent-1")
    with pytest.raises(DocumentLifecycleError, match="Document ID mismatch"):
        await mem_mgr.create_version(
            document_id="doc-mem-mismatched",
            parent_version_id="ver-mem-parent-1",
        )

    with pytest.raises(DocumentLifecycleError, match="Document ID mismatch"):
        await mem_mgr.transition_state(
            document_id="wrong-doc-id",
            version_id="ver-mem-parent-1",
            target_state=DocumentLifecycleState.LOGICAL_DELETE,
        )

    meta_mem_idemp = await mem_mgr.transition_state(
        document_id="doc-mem-parent-1",
        version_id="ver-mem-parent-1",
        target_state=DocumentLifecycleState.DRAFT,
    )
    assert meta_mem_idemp.lifecycle_state == DocumentLifecycleState.DRAFT

    with pytest.raises(DocumentLifecycleError, match="No document versions found"):
        await mem_mgr.get_latest_version("doc-ghost-mem")

    with pytest.raises(DocumentLifecycleError, match="No document lineage found"):
        await mem_mgr.get_lineage("doc-ghost-mem")

    # 7. In-memory publication validation edge cases
    # A. Parent does not exist in memory
    mem_orphan = memory_lifecycle_manager
    v_orphan = DocumentVersion(
        version_id="ver-orphan",
        document_id="doc-mem-val",
        parent_version_id="ver-missing-parent",
        version_number="1.0.1",
        created_at="2026-01-01T00:00:00Z",
        created_by="user",
        metadata=DocumentMetadata(
            document_id="doc-mem-val",
            version_id="ver-orphan",
            parent_version_id="ver-missing-parent",
            title="Orphan",
            author_id="user",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-01T00:00:00Z",
        ),
    )
    mem_orphan._versions["ver-orphan"] = v_orphan
    mem_orphan._document_chains["doc-mem-val"] = ["ver-orphan"]
    with pytest.raises(DocumentLifecycleError, match="Cannot supersede non-existent parent version"):
        await mem_orphan.transition_state("doc-mem-val", "ver-orphan", DocumentLifecycleState.PUBLISHED)

    # B. Parent belongs to different document
    mem_cross_doc = memory_lifecycle_manager
    v_parent_other_doc = DocumentVersion(
        version_id="ver-parent-doc-other",
        document_id="doc-other",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="user",
        metadata=DocumentMetadata(
            document_id="doc-other",
            version_id="ver-parent-doc-other",
            title="Parent Other Doc",
            author_id="user",
            lifecycle_state=DocumentLifecycleState.PUBLISHED,
            created_at="2026-01-01T00:00:00Z",
        ),
    )
    v_child_bad_doc = DocumentVersion(
        version_id="ver-child-bad-doc",
        document_id="doc-mem-target",
        parent_version_id="ver-parent-doc-other",
        version_number="1.0.1",
        created_at="2026-01-01T00:00:00Z",
        created_by="user",
        metadata=DocumentMetadata(
            document_id="doc-mem-target",
            version_id="ver-child-bad-doc",
            parent_version_id="ver-parent-doc-other",
            title="Child Bad Doc",
            author_id="user",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-01T00:00:00Z",
        ),
    )
    mem_cross_doc._versions["ver-parent-doc-other"] = v_parent_other_doc
    mem_cross_doc._versions["ver-child-bad-doc"] = v_child_bad_doc
    mem_cross_doc._document_chains["doc-mem-target"] = ["ver-child-bad-doc"]
    with pytest.raises(DocumentLifecycleError, match="belongs to document 'doc-other', expected 'doc-mem-target'"):
        await mem_cross_doc.transition_state("doc-mem-target", "ver-child-bad-doc", DocumentLifecycleState.PUBLISHED)

    # C. Parent belongs to different tenant
    mem_cross_tenant = memory_lifecycle_manager
    v_parent_other_tenant = DocumentVersion(
        version_id="ver-parent-tenant-other",
        document_id="doc-tenant-test",
        version_number="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        created_by="user",
        metadata=DocumentMetadata(
            document_id="doc-tenant-test",
            version_id="ver-parent-tenant-other",
            title="Parent Other Tenant",
            author_id="user",
            lifecycle_state=DocumentLifecycleState.PUBLISHED,
            created_at="2026-01-01T00:00:00Z",
            security_metadata=SecurityMetadata(tenant_id="tenant-beta"),
        ),
    )
    v_child_bad_tenant = DocumentVersion(
        version_id="ver-child-bad-tenant",
        document_id="doc-tenant-test",
        parent_version_id="ver-parent-tenant-other",
        version_number="1.0.1",
        created_at="2026-01-01T00:00:00Z",
        created_by="user",
        metadata=DocumentMetadata(
            document_id="doc-tenant-test",
            version_id="ver-child-bad-tenant",
            parent_version_id="ver-parent-tenant-other",
            title="Child Bad Tenant",
            author_id="user",
            lifecycle_state=DocumentLifecycleState.DRAFT,
            created_at="2026-01-01T00:00:00Z",
            security_metadata=SecurityMetadata(tenant_id="tenant-alpha"),
        ),
    )
    mem_cross_tenant._versions["ver-parent-tenant-other"] = v_parent_other_tenant
    mem_cross_tenant._versions["ver-child-bad-tenant"] = v_child_bad_tenant
    mem_cross_tenant._document_chains["doc-tenant-test"] = ["ver-child-bad-tenant"]
    with pytest.raises(DocumentLifecycleError, match="belongs to tenant 'tenant-beta', expected 'tenant-alpha'"):
        await mem_cross_tenant.transition_state(
            "doc-tenant-test", "ver-child-bad-tenant", DocumentLifecycleState.PUBLISHED, tenant_id="tenant-alpha"
        )

    # D. In-memory parent not published
    mem_not_pub = memory_lifecycle_manager
    await mem_not_pub.create_version(document_id="doc-not-pub", version_id="ver-np-1")
    await mem_not_pub.create_child_version(
        parent_version_id="ver-np-1", document_id="doc-not-pub", version_id="ver-np-2", version_number="1.0.1"
    )
    with pytest.raises(DocumentLifecycleError, match="expected 'PUBLISHED'"):
        await mem_not_pub.transition_state("doc-not-pub", "ver-np-2", DocumentLifecycleState.PUBLISHED)

    # E. In-memory publication when parent is already superseded
    mem_cas = memory_lifecycle_manager
    await mem_cas.create_version(document_id="doc-mem-cas", version_id="ver-cas-p")
    await mem_cas.transition_state("doc-mem-cas", "ver-cas-p", DocumentLifecycleState.PUBLISHED)
    await mem_cas.create_child_version(
        parent_version_id="ver-cas-p", document_id="doc-mem-cas", version_id="ver-cas-c1", version_number="1.0.1"
    )
    await mem_cas.create_child_version(
        parent_version_id="ver-cas-p", document_id="doc-mem-cas", version_id="ver-cas-c2", version_number="1.0.2"
    )
    await mem_cas.transition_state("doc-mem-cas", "ver-cas-c1", DocumentLifecycleState.PUBLISHED)
    with pytest.raises(DocumentLifecycleError, match=r"Cannot supersede parent version.*expected 'PUBLISHED'"):
        await mem_cas.transition_state("doc-mem-cas", "ver-cas-c2", DocumentLifecycleState.PUBLISHED)

    # F. In-memory publication CAS collision on genesis
    mem_cas_gen = memory_lifecycle_manager
    await mem_cas_gen.create_version(document_id="doc-mem-gen-cas", version_id="ver-gen-1")
    await mem_cas_gen.create_version(document_id="doc-mem-gen-cas", version_id="ver-gen-2", version_number="1.0.1")
    await mem_cas_gen.transition_state("doc-mem-gen-cas", "ver-gen-1", DocumentLifecycleState.PUBLISHED)
    with pytest.raises(DocumentLifecycleError, match="Concurrent publication collision"):
        await mem_cas_gen.transition_state("doc-mem-gen-cas", "ver-gen-2", DocumentLifecycleState.PUBLISHED)

    # G. In-memory publication CAS mismatch on parent (parent is published but current_version_id moved)
    mem_cas_parent = memory_lifecycle_manager
    await mem_cas_parent.create_version(document_id="doc-cas-p", version_id="ver-cp-1")
    await mem_cas_parent.transition_state("doc-cas-p", "ver-cp-1", DocumentLifecycleState.PUBLISHED)
    await mem_cas_parent.create_child_version(
        parent_version_id="ver-cp-1", document_id="doc-cas-p", version_id="ver-cp-2", version_number="1.0.1"
    )
    # Reset parent state back to PUBLISHED to isolate the current_versions mismatch on line 400
    p_rec = mem_cas_parent._versions["ver-cp-1"]
    mem_cas_parent._versions["ver-cp-1"] = p_rec.model_copy(
        update={"metadata": p_rec.metadata.model_copy(update={"lifecycle_state": DocumentLifecycleState.PUBLISHED})}
    )
    mem_cas_parent._current_versions["doc-cas-p"] = "ver-different"
    with pytest.raises(DocumentLifecycleError, match="Concurrent publication collision or invalid predecessor"):
        await mem_cas_parent.transition_state("doc-cas-p", "ver-cp-2", DocumentLifecycleState.PUBLISHED)

    # H. In-memory genesis publication when current_version_id is already set
    mem_cas_gen2 = memory_lifecycle_manager
    await mem_cas_gen2.create_version(document_id="doc-gen-set", version_id="ver-gen-set-1")
    mem_cas_gen2._current_versions["doc-gen-set"] = "ver-already-published"
    with pytest.raises(DocumentLifecycleError, match="current_version_id is not NULL"):
        await mem_cas_gen2.transition_state("doc-gen-set", "ver-gen-set-1", DocumentLifecycleState.PUBLISHED)


@pytest.mark.asyncio
async def test_pointer_invariants_and_version_ownership_via_manager(
    repo_lifecycle_manager: DocumentLifecycleManager,
    repository: DocumentRepository,
) -> None:
    """Verify that create_version and create_child_version leave Document.current_version_id unchanged."""
    mgr = repo_lifecycle_manager

    # 1. Create genesis version V1 as DRAFT
    v1 = await mgr.create_version(
        document_id="doc-ownership-test",
        title="Ownership Test",
        version_id="ver-own-1",
        tenant_id="tenant-own",
    )
    assert v1.metadata.lifecycle_state == DocumentLifecycleState.DRAFT

    doc_0 = await repository.get_document("doc-ownership-test", tenant_id="tenant-own")
    assert doc_0 is not None
    assert doc_0.current_version_id is None

    # 2. Transition V1 to REVIEW -> pointer still None
    await mgr.transition_state(
        document_id="doc-ownership-test",
        version_id="ver-own-1",
        target_state=DocumentLifecycleState.REVIEW,
        tenant_id="tenant-own",
    )
    doc_1 = await repository.get_document("doc-ownership-test", tenant_id="tenant-own")
    assert doc_1 is not None
    assert doc_1.current_version_id is None

    # 3. Publish V1 -> pointer becomes ver-own-1
    await mgr.transition_state(
        document_id="doc-ownership-test",
        version_id="ver-own-1",
        target_state=DocumentLifecycleState.PUBLISHED,
        tenant_id="tenant-own",
    )
    doc_2 = await repository.get_document("doc-ownership-test", tenant_id="tenant-own")
    assert doc_2 is not None
    assert doc_2.current_version_id == "ver-own-1"

    # 4. Create child V2 from V1 -> leaves V1=PUBLISHED, V2=DRAFT, current_version_id=ver-own-1
    v2 = await mgr.create_child_version(
        parent_version_id="ver-own-1",
        document_id="doc-ownership-test",
        version_id="ver-own-2",
        tenant_id="tenant-own",
    )
    assert v2.metadata.lifecycle_state == DocumentLifecycleState.DRAFT

    doc_3 = await repository.get_document("doc-ownership-test", tenant_id="tenant-own")
    assert doc_3 is not None
    assert doc_3.current_version_id == "ver-own-1"

    v1_state = await mgr.get_version("doc-ownership-test", "ver-own-1", tenant_id="tenant-own")
    assert v1_state.lifecycle_state == DocumentLifecycleState.PUBLISHED

    # 5. Publish V2 -> pointer becomes ver-own-2, V1 becomes SUPERSEDED
    await mgr.transition_state(
        document_id="doc-ownership-test",
        version_id="ver-own-2",
        target_state=DocumentLifecycleState.PUBLISHED,
        tenant_id="tenant-own",
    )
    doc_4 = await repository.get_document("doc-ownership-test", tenant_id="tenant-own")
    assert doc_4 is not None
    assert doc_4.current_version_id == "ver-own-2"

    v1_state_final = await mgr.get_version("doc-ownership-test", "ver-own-1", tenant_id="tenant-own")
    v2_state_final = await mgr.get_version("doc-ownership-test", "ver-own-2", tenant_id="tenant-own")
    assert v1_state_final.lifecycle_state == DocumentLifecycleState.SUPERSEDED
    assert v2_state_final.lifecycle_state == DocumentLifecycleState.PUBLISHED


@pytest.mark.asyncio
async def test_concurrent_genesis_publication_via_manager(
    concurrent_data_store: RelationalDataStore,
) -> None:
    """Verify concurrent genesis publication via independent DocumentLifecycleManagers allows exactly one winner."""
    repo_a = DocumentRepository(concurrent_data_store)
    repo_b = DocumentRepository(concurrent_data_store)
    mgr_a = DocumentLifecycleManager(repository=repo_a)
    mgr_b = DocumentLifecycleManager(repository=repo_b)

    await mgr_a.create_version(
        document_id="doc-conc-mgr-gen",
        title="Gen A",
        version_id="ver-mgr-gen-A",
        version_number="1.0.0",
        tenant_id="tenant-mgr",
    )
    await mgr_a.create_version(
        document_id="doc-conc-mgr-gen",
        title="Gen B",
        version_id="ver-mgr-gen-B",
        version_number="1.0.1",
        tenant_id="tenant-mgr",
    )

    results = await asyncio.gather(
        mgr_a.transition_state(
            "doc-conc-mgr-gen", "ver-mgr-gen-A", DocumentLifecycleState.PUBLISHED, tenant_id="tenant-mgr"
        ),
        mgr_b.transition_state(
            "doc-conc-mgr-gen", "ver-mgr-gen-B", DocumentLifecycleState.PUBLISHED, tenant_id="tenant-mgr"
        ),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], DocumentLifecycleError)

    winning_meta = successes[0]
    losing_ver_id = "ver-mgr-gen-B" if winning_meta.version_id == "ver-mgr-gen-A" else "ver-mgr-gen-A"

    doc = await repo_a.get_document("doc-conc-mgr-gen", tenant_id="tenant-mgr")
    assert doc is not None
    assert doc.current_version_id == winning_meta.version_id

    l_ver = await mgr_a.get_version("doc-conc-mgr-gen", losing_ver_id, tenant_id="tenant-mgr")
    assert l_ver.lifecycle_state == DocumentLifecycleState.DRAFT
    assert l_ver.is_immutable is False


@pytest.mark.asyncio
async def test_concurrent_sibling_publication_via_manager(
    concurrent_data_store: RelationalDataStore,
) -> None:
    """Verify concurrent sibling publication via independent DocumentLifecycleManagers allows exactly one winner."""
    repo_a = DocumentRepository(concurrent_data_store)
    repo_b = DocumentRepository(concurrent_data_store)
    mgr_a = DocumentLifecycleManager(repository=repo_a)
    mgr_b = DocumentLifecycleManager(repository=repo_b)

    await mgr_a.create_version(
        document_id="doc-conc-mgr-sib",
        title="Root Parent",
        version_id="ver-mgr-parent",
        tenant_id="tenant-mgr",
    )
    await mgr_a.transition_state(
        "doc-conc-mgr-sib", "ver-mgr-parent", DocumentLifecycleState.PUBLISHED, tenant_id="tenant-mgr"
    )

    await mgr_a.create_child_version(
        parent_version_id="ver-mgr-parent",
        document_id="doc-conc-mgr-sib",
        version_id="ver-mgr-child-A",
        version_number="1.0.1",
        tenant_id="tenant-mgr",
    )
    await mgr_a.create_child_version(
        parent_version_id="ver-mgr-parent",
        document_id="doc-conc-mgr-sib",
        version_id="ver-mgr-child-B",
        version_number="1.0.2",
        tenant_id="tenant-mgr",
    )

    results = await asyncio.gather(
        mgr_a.transition_state(
            "doc-conc-mgr-sib", "ver-mgr-child-A", DocumentLifecycleState.PUBLISHED, tenant_id="tenant-mgr"
        ),
        mgr_b.transition_state(
            "doc-conc-mgr-sib", "ver-mgr-child-B", DocumentLifecycleState.PUBLISHED, tenant_id="tenant-mgr"
        ),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], DocumentLifecycleError)

    winning_meta = successes[0]
    losing_ver_id = "ver-mgr-child-B" if winning_meta.version_id == "ver-mgr-child-A" else "ver-mgr-child-A"

    doc = await repo_a.get_document("doc-conc-mgr-sib", tenant_id="tenant-mgr")
    assert doc is not None
    assert doc.current_version_id == winning_meta.version_id

    p_meta = await mgr_a.get_version("doc-conc-mgr-sib", "ver-mgr-parent", tenant_id="tenant-mgr")
    w_meta = await mgr_a.get_version("doc-conc-mgr-sib", winning_meta.version_id, tenant_id="tenant-mgr")
    l_meta = await mgr_a.get_version("doc-conc-mgr-sib", losing_ver_id, tenant_id="tenant-mgr")

    assert p_meta.lifecycle_state == DocumentLifecycleState.SUPERSEDED
    assert p_meta.is_immutable is True

    assert w_meta.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert w_meta.is_immutable is True

    assert l_meta.lifecycle_state == DocumentLifecycleState.DRAFT
    assert l_meta.is_immutable is False


# =============================================================================
# 5. Milestone 7: SHA256 Integrity Hashing on Publish
# =============================================================================


@pytest.mark.asyncio
async def test_in_memory_publish_computes_sha256_hash_from_payload(
    memory_lifecycle_manager: DocumentLifecycleManager,
) -> None:
    """Test that publishing with a payload computes and records a real SHA256 hash (in-memory mode)."""
    mgr = memory_lifecycle_manager
    payload = b"[INVOICE_PDF_BYTES]"

    await mgr.create_version(document_id="doc-hash-mem", version_id="ver-hash-mem")
    meta = await mgr.transition_state(
        "doc-hash-mem",
        "ver-hash-mem",
        DocumentLifecycleState.PUBLISHED,
        payload=payload,
    )

    expected_hash = await DefaultVerificationService().compute_hash(payload)
    assert meta.sha256_hash is not None
    assert meta.sha256_hash == expected_hash
    assert len(meta.sha256_hash) == 64


@pytest.mark.asyncio
async def test_in_memory_publish_without_payload_leaves_hash_none(
    memory_lifecycle_manager: DocumentLifecycleManager,
) -> None:
    """Test that publishing without a payload leaves sha256_hash unset (backward compatible)."""
    mgr = memory_lifecycle_manager

    await mgr.create_version(document_id="doc-hash-none", version_id="ver-hash-none")
    meta = await mgr.transition_state("doc-hash-none", "ver-hash-none", DocumentLifecycleState.PUBLISHED)

    assert meta.sha256_hash is None


@pytest.mark.asyncio
async def test_repository_backed_publish_computes_and_persists_sha256_hash(
    repo_lifecycle_manager: DocumentLifecycleManager,
    repository: DocumentRepository,
) -> None:
    """Test that repository-backed publish threads sha256_hash through to persistence.py and
    that it round-trips correctly on a subsequent read."""
    mgr = repo_lifecycle_manager
    payload = b"[PAYSLIP_PDF_BYTES]"

    await mgr.create_version(document_id="doc-hash-repo", version_id="ver-hash-repo", tenant_id="tenant-hash")
    meta = await mgr.transition_state(
        "doc-hash-repo",
        "ver-hash-repo",
        DocumentLifecycleState.PUBLISHED,
        tenant_id="tenant-hash",
        payload=payload,
    )

    expected_hash = await DefaultVerificationService().compute_hash(payload)
    assert meta.sha256_hash == expected_hash

    # Round-trip: a fresh read via the repository must reflect the persisted hash.
    reread = await mgr.get_version("doc-hash-repo", "ver-hash-repo", tenant_id="tenant-hash")
    assert reread.sha256_hash == expected_hash

    version_record = await repository.get_version("doc-hash-repo", "ver-hash-repo", tenant_id="tenant-hash")
    assert version_record is not None
    assert version_record.metadata.sha256_hash == expected_hash


# =============================================================================
# 6. Milestone 7: Metadata Cache (read-through + invalidation)
# =============================================================================


@pytest.mark.asyncio
async def test_metadata_cache_read_through_and_invalidation_on_transition(
    repository: DocumentRepository,
) -> None:
    """Test Metadata Cache read-through behavior and invalidation on transition_state.

    Read order: the cache is consulted first; on a miss, resolution proceeds through the
    existing repository read path unchanged, and the result is cached. A subsequent
    transition_state() call must invalidate the cached entry so the next read reflects the
    new state rather than a stale cached one.
    """
    cache_store = MemoryCacheStore()
    mgr = DocumentLifecycleManager(repository=repository, cache_store=cache_store)
    assert mgr.cache_store is cache_store

    await mgr.create_version(document_id="doc-cache", version_id="ver-cache", tenant_id="tenant-cache")

    # First read: cache miss, populates cache.
    first = await mgr.get_version_object("ver-cache", document_id="doc-cache", tenant_id="tenant-cache")
    assert first.metadata.lifecycle_state == DocumentLifecycleState.DRAFT

    cache_key = DocumentLifecycleManager._metadata_cache_key("doc-cache", "ver-cache", "tenant-cache")
    assert await cache_store.get(cache_key) is not None

    # Second read: served from cache (same object identity as what was cached).
    second = await mgr.get_version_object("ver-cache", document_id="doc-cache", tenant_id="tenant-cache")
    assert second.metadata.lifecycle_state == DocumentLifecycleState.DRAFT

    # Transition invalidates the cache entry.
    await mgr.transition_state("doc-cache", "ver-cache", DocumentLifecycleState.REVIEW, tenant_id="tenant-cache")
    assert await cache_store.get(cache_key) is None

    # Next read reflects the new state, not a stale cached DRAFT.
    third = await mgr.get_version_object("ver-cache", document_id="doc-cache", tenant_id="tenant-cache")
    assert third.metadata.lifecycle_state == DocumentLifecycleState.REVIEW


@pytest.mark.asyncio
async def test_metadata_cache_publish_invalidates_both_child_and_parent(
    repository: DocumentRepository,
) -> None:
    """Test that publishing a child version invalidates the Metadata Cache for both the
    newly-published child and its superseded parent."""
    cache_store = MemoryCacheStore()
    mgr = DocumentLifecycleManager(repository=repository, cache_store=cache_store)

    await mgr.create_version(document_id="doc-cache-pub", version_id="ver-cache-parent", tenant_id="tenant-cache-pub")
    await mgr.transition_state(
        "doc-cache-pub",
        "ver-cache-parent",
        DocumentLifecycleState.PUBLISHED,
        tenant_id="tenant-cache-pub",
    )

    await mgr.create_child_version(
        parent_version_id="ver-cache-parent",
        document_id="doc-cache-pub",
        version_id="ver-cache-child",
        tenant_id="tenant-cache-pub",
    )

    # Warm the cache for both parent and child before publishing the child.
    await mgr.get_version_object("ver-cache-parent", document_id="doc-cache-pub", tenant_id="tenant-cache-pub")
    await mgr.get_version_object("ver-cache-child", document_id="doc-cache-pub", tenant_id="tenant-cache-pub")

    parent_key = DocumentLifecycleManager._metadata_cache_key("doc-cache-pub", "ver-cache-parent", "tenant-cache-pub")
    child_key = DocumentLifecycleManager._metadata_cache_key("doc-cache-pub", "ver-cache-child", "tenant-cache-pub")
    assert await cache_store.get(parent_key) is not None
    assert await cache_store.get(child_key) is not None

    await mgr.transition_state(
        "doc-cache-pub",
        "ver-cache-child",
        DocumentLifecycleState.PUBLISHED,
        tenant_id="tenant-cache-pub",
    )

    assert await cache_store.get(parent_key) is None
    assert await cache_store.get(child_key) is None

    parent_meta = await mgr.get_version("doc-cache-pub", "ver-cache-parent", tenant_id="tenant-cache-pub")
    child_meta = await mgr.get_version("doc-cache-pub", "ver-cache-child", tenant_id="tenant-cache-pub")
    assert parent_meta.lifecycle_state == DocumentLifecycleState.SUPERSEDED
    assert child_meta.lifecycle_state == DocumentLifecycleState.PUBLISHED


@pytest.mark.asyncio
async def test_metadata_cache_absent_preserves_uncached_behavior(
    memory_lifecycle_manager: DocumentLifecycleManager,
) -> None:
    """Test that omitting cache_store preserves exactly today's uncached behavior."""
    mgr = memory_lifecycle_manager
    assert mgr.cache_store is None

    await mgr.create_version(document_id="doc-no-cache", version_id="ver-no-cache")
    meta = await mgr.get_version("doc-no-cache", "ver-no-cache")
    assert meta.lifecycle_state == DocumentLifecycleState.DRAFT
