"""Relational Persistence Repository for KORTEX OS Document Engine.

This module implements DocumentRepository, providing transactional persistence, CRUD operations,
tenant isolation, version chain management, and domain/ORM entity mapping via Storage Engine IDataStore.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.document.exceptions import (
    DocumentEngineError,
    DocumentLifecycleError,
    DocumentValidationError,
)
from kortex.engines.document.interfaces import IDocumentRepository, ITemplateRepository
from kortex.engines.document.models import (
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
    SecurityClassification,
    SecurityMetadata,
    TemplateSchema,
    TemplateSchemaRecord,
    ValidationReport,
)
from kortex.engines.document.template_library import parse_semver
from kortex.engines.storage.interfaces import IDataStore

logger = logging.getLogger("kortex.engines.document.persistence")

# Regular expression for SemVer 2.0.0 validation (consistent with TemplateLibrary and ConnectorRegistry)
SEMVER_REGEX = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


class DocumentRepository(IDocumentRepository):
    """Repository handling all relational database persistence for the Document Engine."""

    def __init__(self, data_store: IDataStore) -> None:
        """Initialize DocumentRepository with Storage Engine IDataStore.

        Args:
            data_store: Storage Engine relational persistence provider.
        """
        self._data_store = data_store
        self._publish_lock = asyncio.Lock()

    # -- Domain <-> ORM Entity Mapping Helpers --------------------------------

    @staticmethod
    def _document_to_domain(record: DocumentRecord) -> Document:
        """Convert a DocumentRecord ORM entity to a Document Pydantic domain model."""
        meta_dict = {}
        if record.metadata_json:
            try:
                meta_dict = json.loads(record.metadata_json)
            except Exception:
                meta_dict = {}

        created_str = (
            record.created_at.isoformat()
            if record.created_at is not None
            else None
        )
        updated_str = (
            record.updated_at.isoformat()
            if record.updated_at is not None
            else None
        )

        return Document(
            document_id=record.id,
            tenant_id=record.tenant_id,
            current_version_id=record.current_version_id,
            title=record.title,
            document_type=record.document_type,
            created_at=created_str,
            updated_at=updated_str,
            metadata=meta_dict,
        )

    @staticmethod
    def _version_to_domain(record: DocumentVersionRecord) -> DocumentVersion:
        """Convert a DocumentVersionRecord ORM entity to a DocumentVersion Pydantic domain model."""
        labels = []
        if record.security_labels_json:
            try:
                labels = json.loads(record.security_labels_json)
            except Exception:
                labels = []

        lineage = []
        if record.lineage_path_json:
            try:
                lineage = json.loads(record.lineage_path_json)
            except Exception:
                lineage = []

        try:
            sec_class = SecurityClassification(record.security_classification)
        except Exception:
            sec_class = SecurityClassification.INTERNAL

        sec_meta = SecurityMetadata(
            classification=sec_class,
            labels=labels,
            owner_id=record.security_owner_id,
            tenant_id=record.tenant_id,
        )

        try:
            state = DocumentLifecycleState(record.lifecycle_state)
        except Exception:
            state = DocumentLifecycleState.DRAFT

        created_str = (
            record.created_at.isoformat()
            if record.created_at is not None
            else datetime.datetime.now(datetime.timezone.utc).isoformat()
        )
        published_str = (
            record.published_at.isoformat()
            if record.published_at is not None
            else None
        )

        doc_meta = DocumentMetadata(
            document_id=record.document_id,
            version_id=record.id,
            parent_version_id=record.parent_version_id,
            lifecycle_state=state,
            lineage_path=lineage,
            title=record.title,
            author_id=record.author_id,
            is_immutable=record.is_immutable,
            security_metadata=sec_meta,
            file_size_bytes=record.file_size_bytes,
            sha256_hash=record.sha256_hash,
            storage_key=record.storage_key,
            bucket_name=record.bucket_name,
            created_at=created_str,
            published_at=published_str,
        )

        content = None
        if record.storage_key:
            content = DocumentContent(
                storage_key=record.storage_key,
                bucket_name=record.bucket_name or "documents",
                mime_type=record.mime_type,
                file_size_bytes=record.file_size_bytes,
                sha256_hash=record.sha256_hash,
            )

        return DocumentVersion(
            version_id=record.id,
            document_id=record.document_id,
            parent_version_id=record.parent_version_id,
            version_number=record.version_number,
            created_at=created_str,
            created_by=record.author_id,
            is_immutable=record.is_immutable,
            metadata=doc_meta,
            content=content,
        )

    @staticmethod
    def _profile_to_domain(record: DocumentOperationProfileRecord) -> DocumentOperationProfile:
        """Convert a DocumentOperationProfileRecord ORM entity to a DocumentOperationProfile domain model."""
        pipeline_def = None
        if record.pipeline_definition_json:
            try:
                pipeline_data = json.loads(record.pipeline_definition_json)
                pipeline_def = AdapterPipelineDefinition.model_validate(pipeline_data)
            except Exception:
                pipeline_def = None

        perms = []
        if record.permissions_json:
            try:
                perms = json.loads(record.permissions_json)
            except Exception:
                perms = []

        return DocumentOperationProfile(
            id=record.profile_id,
            name=record.name,
            namespace=record.namespace,
            version=record.version,
            description=record.description,
            business_operation=record.business_operation,
            required_template_id=record.required_template_id,
            adapter_pipeline=pipeline_def,
            permissions=perms,
            output_bucket=record.output_bucket,
        )

    # -- Root Document CRUD Operations ----------------------------------------

    async def create_document(self, document: Document) -> Document:
        """Persist a new root Document entity.

        Args:
            document: Document domain model instance.

        Returns:
            Persisted Document domain model.

        Raises:
            DocumentValidationError: If document already exists.
        """
        async def _action(session: AsyncSession) -> Document:
            # Check for duplicate document ID under same tenant
            stmt = select(DocumentRecord).where(
                DocumentRecord.id == document.document_id,
                DocumentRecord.tenant_id == document.tenant_id,
            )
            res = await session.execute(stmt)
            if res.scalar_one_or_none() is not None:
                raise DocumentValidationError(
                    f"Document with ID '{document.document_id}' already exists for tenant '{document.tenant_id}'."
                )

            metadata_str = json.dumps(document.metadata) if document.metadata else None

            record = DocumentRecord(
                id=document.document_id,
                tenant_id=document.tenant_id,
                title=document.title,
                document_type=document.document_type,
                current_version_id=None,
                is_deleted=False,
                metadata_json=metadata_str,
            )
            session.add(record)
            await session.flush()
            return self._document_to_domain(record)

        return await self._data_store.execute_in_transaction(_action)

    async def get_document(
        self, document_id: str, tenant_id: str = "default", include_deleted: bool = False
    ) -> Document | None:
        """Retrieve root Document entity by ID and tenant.

        Args:
            document_id: Unique document identifier.
            tenant_id: Tenant partition identifier.
            include_deleted: If True, include soft-deleted documents.

        Returns:
            Document domain model or None if not found.
        """
        async def _action(session: AsyncSession) -> Document | None:
            query = select(DocumentRecord).where(
                DocumentRecord.id == document_id,
                DocumentRecord.tenant_id == tenant_id,
            )
            if not include_deleted:
                query = query.where(DocumentRecord.is_deleted.is_(False))

            res = await session.execute(query)
            record = res.scalar_one_or_none()
            if record is None:
                return None
            return self._document_to_domain(record)

        return await self._data_store.execute_in_transaction(_action)

    async def update_document(self, document: Document) -> Document:
        """Update existing root Document entity attributes.

        Note: DocumentRecord.current_version_id is publication-owned state and cannot
        be modified through update_document. It is updated exclusively via publish_version().

        Args:
            document: Updated Document domain model.

        Returns:
            Updated Document domain model.

        Raises:
            DocumentValidationError: If document is not found.
        """
        async def _action(session: AsyncSession) -> Document:
            stmt = select(DocumentRecord).where(
                DocumentRecord.id == document.document_id,
                DocumentRecord.tenant_id == document.tenant_id,
                DocumentRecord.is_deleted.is_(False),
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                raise DocumentValidationError(
                    f"Cannot update non-existent or deleted document '{document.document_id}' for tenant '{document.tenant_id}'."
                )

            record.title = document.title
            record.document_type = document.document_type
            if document.metadata is not None:
                record.metadata_json = json.dumps(document.metadata)

            await session.flush()
            return self._document_to_domain(record)

        return await self._data_store.execute_in_transaction(_action)

    async def soft_delete_document(self, document_id: str, tenant_id: str = "default") -> bool:
        """Logically soft-delete a document and transition child versions to LOGICAL_DELETE.

        Args:
            document_id: Unique document identifier.
            tenant_id: Tenant partition identifier.

        Returns:
            True if document was found and soft-deleted; False otherwise.
        """
        async def _action(session: AsyncSession) -> bool:
            stmt = select(DocumentRecord).where(
                DocumentRecord.id == document_id,
                DocumentRecord.tenant_id == tenant_id,
                DocumentRecord.is_deleted.is_(False),
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                return False

            record.is_deleted = True

            # Also transition all child versions to LOGICAL_DELETE
            ver_stmt = (
                update(DocumentVersionRecord)
                .where(
                    DocumentVersionRecord.document_id == document_id,
                    DocumentVersionRecord.tenant_id == tenant_id,
                )
                .values(
                    lifecycle_state=DocumentLifecycleState.LOGICAL_DELETE.value,
                    is_immutable=True,
                )
            )
            await session.execute(ver_stmt)
            await session.flush()
            return True

        return await self._data_store.execute_in_transaction(_action)

    async def hard_delete_document(self, document_id: str, tenant_id: str = "default") -> bool:
        """Physically delete document record and cascade delete all child versions.

        Args:
            document_id: Unique document identifier.
            tenant_id: Tenant partition identifier.

        Returns:
            True if deleted; False if not found.
        """
        async def _action(session: AsyncSession) -> bool:
            stmt = select(DocumentRecord).where(
                DocumentRecord.id == document_id,
                DocumentRecord.tenant_id == tenant_id,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                return False

            # Explicit cascade deletion for SQLite compatibility
            await session.execute(
                delete(DocumentVersionRecord).where(
                    DocumentVersionRecord.document_id == document_id,
                    DocumentVersionRecord.tenant_id == tenant_id,
                )
            )
            await session.delete(record)
            await session.flush()
            return True

        return await self._data_store.execute_in_transaction(_action)

    async def list_documents(
        self,
        tenant_id: str = "default",
        document_type: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Document]:
        """List root documents matching tenant and optional filters.

        Args:
            tenant_id: Tenant partition identifier.
            document_type: Optional document type filter.
            include_deleted: If True, include soft-deleted documents.
            limit: Maximum records to return.
            offset: Pagination offset.

        Returns:
            List of Document domain models.
        """
        async def _action(session: AsyncSession) -> list[Document]:
            query = select(DocumentRecord).where(DocumentRecord.tenant_id == tenant_id)
            if not include_deleted:
                query = query.where(DocumentRecord.is_deleted.is_(False))
            if document_type is not None:
                query = query.where(DocumentRecord.document_type == document_type)

            query = query.order_by(DocumentRecord.created_at.desc()).limit(limit).offset(offset)
            res = await session.execute(query)
            records = res.scalars().all()
            return [self._document_to_domain(r) for r in records]

        return await self._data_store.execute_in_transaction(_action)

    # -- Document Version CRUD Operations -------------------------------------

    async def create_version(
        self, version: DocumentVersion, tenant_id: str = "default"
    ) -> DocumentVersion:
        """Persist an immutable DocumentVersion snapshot.

        Args:
            version: DocumentVersion domain model instance.
            tenant_id: Tenant partition identifier.

        Returns:
            Persisted DocumentVersion domain model.

        Raises:
            DocumentValidationError: If parent document does not exist or version number conflicts.
        """
        async def _action(session: AsyncSession) -> DocumentVersion:
            # 1. Verify parent document exists under tenant
            doc_stmt = select(DocumentRecord).where(
                DocumentRecord.id == version.document_id,
                DocumentRecord.tenant_id == tenant_id,
                DocumentRecord.is_deleted.is_(False),
            )
            doc_res = await session.execute(doc_stmt)
            doc_record = doc_res.scalar_one_or_none()
            if doc_record is None:
                raise DocumentValidationError(
                    f"Cannot create version for non-existent document '{version.document_id}' under tenant '{tenant_id}'."
                )

            # 2. Validate SemVer 2.0.0 format
            if not SEMVER_REGEX.match(version.version_number.strip()):
                raise DocumentLifecycleError(
                    f"Invalid semantic version format: '{version.version_number}'. "
                    f"Must follow SemVer 2.0.0 (MAJOR.MINOR.PATCH)."
                )

            # 3. Check for duplicate version_id or duplicate version_number under same document
            ver_check_stmt = select(DocumentVersionRecord).where(
                DocumentVersionRecord.document_id == version.document_id,
                DocumentVersionRecord.tenant_id == tenant_id,
                DocumentVersionRecord.version_number == version.version_number,
            )
            ver_res = await session.execute(ver_check_stmt)
            if ver_res.scalar_one_or_none() is not None:
                raise DocumentLifecycleError(
                    f"Version '{version.version_number}' already exists for document '{version.document_id}' in tenant '{tenant_id}'."
                )

            meta = version.metadata
            sec = meta.security_metadata
            sec_labels_str = json.dumps(sec.labels) if sec.labels else None
            lineage_str = json.dumps(meta.lineage_path) if meta.lineage_path else None

            # 3. Create version record
            ver_record = DocumentVersionRecord(
                id=version.version_id,
                document_id=version.document_id,
                tenant_id=tenant_id,
                parent_version_id=version.parent_version_id,
                version_number=version.version_number,
                lifecycle_state=meta.lifecycle_state.value,
                is_immutable=version.is_immutable,
                author_id=version.created_by,
                title=meta.title,
                storage_key=meta.storage_key,
                bucket_name=meta.bucket_name or "documents",
                mime_type=version.content.mime_type if version.content else "application/octet-stream",
                file_size_bytes=meta.file_size_bytes,
                sha256_hash=meta.sha256_hash,
                security_classification=sec.classification.value,
                security_labels_json=sec_labels_str,
                security_owner_id=sec.owner_id,
                lineage_path_json=lineage_str,
                published_at=None,
            )
            session.add(ver_record)
            await session.flush()

            return self._version_to_domain(ver_record)

        return await self._data_store.execute_in_transaction(_action)

    async def get_version(
        self, document_id: str, version_id: str, tenant_id: str = "default"
    ) -> DocumentVersion | None:
        """Retrieve specific DocumentVersion snapshot.

        Args:
            document_id: Unique document identifier.
            version_id: Unique version identifier.
            tenant_id: Tenant partition identifier.

        Returns:
            DocumentVersion domain model or None if not found.
        """
        async def _action(session: AsyncSession) -> DocumentVersion | None:
            stmt = select(DocumentVersionRecord).where(
                DocumentVersionRecord.id == version_id,
                DocumentVersionRecord.document_id == document_id,
                DocumentVersionRecord.tenant_id == tenant_id,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                return None
            return self._version_to_domain(record)

        return await self._data_store.execute_in_transaction(_action)

    async def get_latest_version(
        self, document_id: str, tenant_id: str = "default"
    ) -> DocumentVersion | None:
        """Retrieve most recently created version snapshot for a document.

        Note: Returns newest created version (which may be in DRAFT or REVIEW state).
        To retrieve the active published version, inspect `Document.current_version_id`.

        Args:
            document_id: Unique document identifier.
            tenant_id: Tenant partition identifier.

        Returns:
            Latest created DocumentVersion domain model or None if no versions exist.
        """
        async def _action(session: AsyncSession) -> DocumentVersion | None:
            stmt = (
                select(DocumentVersionRecord)
                .where(
                    DocumentVersionRecord.document_id == document_id,
                    DocumentVersionRecord.tenant_id == tenant_id,
                )
                .order_by(
                    DocumentVersionRecord.created_at.desc(),
                    DocumentVersionRecord.version_number.desc(),
                )
                .limit(1)
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                return None
            return self._version_to_domain(record)

        return await self._data_store.execute_in_transaction(_action)

    async def list_versions(
        self, document_id: str, tenant_id: str = "default"
    ) -> list[DocumentVersion]:
        """List all version snapshots for a document in creation order.

        Args:
            document_id: Unique document identifier.
            tenant_id: Tenant partition identifier.

        Returns:
            List of DocumentVersion domain models.
        """
        async def _action(session: AsyncSession) -> list[DocumentVersion]:
            stmt = (
                select(DocumentVersionRecord)
                .where(
                    DocumentVersionRecord.document_id == document_id,
                    DocumentVersionRecord.tenant_id == tenant_id,
                )
                .order_by(
                    DocumentVersionRecord.created_at.asc(),
                    DocumentVersionRecord.version_number.asc(),
                )
            )
            res = await session.execute(stmt)
            records = res.scalars().all()
            return [self._version_to_domain(r) for r in records]

        return await self._data_store.execute_in_transaction(_action)

    async def get_version_by_number(
        self, document_id: str, version_number: str, tenant_id: str = "default"
    ) -> DocumentVersion | None:
        """Retrieve version snapshot matching exact version number string.

        Args:
            document_id: Unique document identifier.
            version_number: SemVer version string (e.g. '1.0.0').
            tenant_id: Tenant partition identifier.

        Returns:
            DocumentVersion domain model or None if not found.
        """
        async def _action(session: AsyncSession) -> DocumentVersion | None:
            stmt = select(DocumentVersionRecord).where(
                DocumentVersionRecord.document_id == document_id,
                DocumentVersionRecord.version_number == version_number,
                DocumentVersionRecord.tenant_id == tenant_id,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                return None
            return self._version_to_domain(record)

        return await self._data_store.execute_in_transaction(_action)

    async def update_version_state(
        self,
        document_id: str,
        version_id: str,
        target_state: DocumentLifecycleState,
        is_immutable: bool,
        published_at: str | None = None,
        tenant_id: str = "default",
    ) -> DocumentVersion:
        """Update lifecycle state and immutability lock for a version snapshot.

        Args:
            document_id: Unique document identifier.
            version_id: Unique version identifier.
            target_state: New DocumentLifecycleState.
            is_immutable: Immutability lock flag.
            published_at: Optional ISO timestamp of publication.
            tenant_id: Tenant partition identifier.

        Returns:
            Updated DocumentVersion domain model.

        Raises:
            DocumentLifecycleError: If version is not found.
        """
        if target_state == DocumentLifecycleState.PUBLISHED:
            raise DocumentLifecycleError(
                f"Direct transition to 'PUBLISHED' state via update_version_state is forbidden for "
                f"version '{version_id}' of document '{document_id}'. Use publish_version() for atomic "
                f"publication and aggregate pointer synchronization."
            )

        async def _action(session: AsyncSession) -> DocumentVersion:
            stmt = select(DocumentVersionRecord).where(
                DocumentVersionRecord.id == version_id,
                DocumentVersionRecord.document_id == document_id,
                DocumentVersionRecord.tenant_id == tenant_id,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                raise DocumentLifecycleError(
                    f"Cannot update state for non-existent version '{version_id}' of document '{document_id}'."
                )

            record.lifecycle_state = target_state.value
            record.is_immutable = is_immutable
            if published_at:
                try:
                    record.published_at = datetime.datetime.fromisoformat(published_at)
                except Exception:
                    record.published_at = datetime.datetime.now(datetime.timezone.utc)

            await session.flush()
            return self._version_to_domain(record)

        return await self._data_store.execute_in_transaction(_action)

    async def publish_version(
        self,
        document_id: str,
        version_id: str,
        parent_version_id: str | None = None,
        published_at: str | None = None,
        tenant_id: str = "default",
    ) -> tuple[DocumentVersion, DocumentVersion | None]:
        """Atomically transition a document version to PUBLISHED, supersede its predecessor, and update the document pointer.

        Uses an atomic compare-and-swap (CAS) update on DocumentRecord.current_version_id to guarantee that exactly
        one transaction succeeds in concurrent publication races.

        Args:
            document_id: Unique document identifier.
            version_id: Unique version identifier to publish.
            parent_version_id: Optional expected active predecessor version identifier.
            published_at: Optional ISO timestamp of publication.
            tenant_id: Tenant partition identifier.

        Returns:
            A tuple of (published_child_version, superseded_parent_version_or_none).

        Raises:
            DocumentLifecycleError: If child is missing/invalid state, parent is missing/non-published,
                                    lineage validation fails, or CAS publication gate fails.
        """
        async def _action(session: AsyncSession) -> tuple[DocumentVersion, DocumentVersion | None]:
            # 1. Fetch and validate child version
            child_stmt = select(DocumentVersionRecord).where(
                DocumentVersionRecord.id == version_id,
                DocumentVersionRecord.document_id == document_id,
                DocumentVersionRecord.tenant_id == tenant_id,
            )
            child_res = await session.execute(child_stmt)
            child_record = child_res.scalar_one_or_none()
            if child_record is None:
                raise DocumentLifecycleError(
                    f"Cannot publish non-existent version '{version_id}' of document '{document_id}'."
                )

            # Child must be in DRAFT or REVIEW
            if child_record.lifecycle_state not in (
                DocumentLifecycleState.DRAFT.value,
                DocumentLifecycleState.REVIEW.value,
            ):
                raise DocumentLifecycleError(
                    f"Cannot publish version '{version_id}' in lifecycle state '{child_record.lifecycle_state}'. "
                    f"Version must be in DRAFT or REVIEW state."
                )

            # Validate child-parent lineage relationship:
            # - If child has a parent: caller must supply matching parent_version_id
            # - If child has no parent (genesis): caller must supply parent_version_id=None
            if child_record.parent_version_id != parent_version_id:
                raise DocumentLifecycleError(
                    f"Lineage mismatch for publication of version '{version_id}': version record has "
                    f"parent_version_id='{child_record.parent_version_id}', but publication requested with "
                    f"parent_version_id='{parent_version_id}'."
                )

            parent_domain: DocumentVersion | None = None
            parent_record: DocumentVersionRecord | None = None

            # 2. Fetch and validate parent version if supplied
            if parent_version_id is not None:
                parent_stmt = select(DocumentVersionRecord).where(
                    DocumentVersionRecord.id == parent_version_id,
                    DocumentVersionRecord.document_id == document_id,
                    DocumentVersionRecord.tenant_id == tenant_id,
                )
                parent_res = await session.execute(parent_stmt)
                parent_record = parent_res.scalar_one_or_none()
                if parent_record is None:
                    raise DocumentLifecycleError(
                        f"Cannot supersede non-existent parent version '{parent_version_id}' for document '{document_id}'."
                    )
                if parent_record.lifecycle_state != DocumentLifecycleState.PUBLISHED.value:
                    raise DocumentLifecycleError(
                        f"Cannot supersede parent version '{parent_version_id}': parent is in '{parent_record.lifecycle_state}' state, "
                        f"expected 'PUBLISHED'."
                    )

            # 3. Execute authoritative aggregate-level CAS publication gate on documents table
            if parent_version_id is not None:
                doc_cas_stmt = (
                    update(DocumentRecord)
                    .where(
                        DocumentRecord.id == document_id,
                        DocumentRecord.tenant_id == tenant_id,
                        DocumentRecord.current_version_id == parent_version_id,
                    )
                    .values(current_version_id=version_id)
                )
            else:
                doc_cas_stmt = (
                    update(DocumentRecord)
                    .where(
                        DocumentRecord.id == document_id,
                        DocumentRecord.tenant_id == tenant_id,
                        DocumentRecord.current_version_id.is_(None),
                    )
                    .values(current_version_id=version_id)
                )

            doc_cas_res = await session.execute(doc_cas_stmt)
            if doc_cas_res.rowcount != 1:
                raise DocumentLifecycleError(
                    f"Concurrent publication collision or invalid predecessor: document '{document_id}' "
                    f"current_version_id does not match expected predecessor '{parent_version_id}'."
                )

            # 4. Mutate parent version to SUPERSEDED (if applicable)
            if parent_record is not None:
                parent_record.lifecycle_state = DocumentLifecycleState.SUPERSEDED.value
                parent_record.is_immutable = True
                parent_domain = self._version_to_domain(parent_record)

            # 5. Mutate child version to PUBLISHED
            child_record.lifecycle_state = DocumentLifecycleState.PUBLISHED.value
            child_record.is_immutable = True
            if published_at:
                try:
                    child_record.published_at = datetime.datetime.fromisoformat(published_at)
                except Exception:
                    child_record.published_at = datetime.datetime.now(datetime.timezone.utc)
            else:
                child_record.published_at = datetime.datetime.now(datetime.timezone.utc)

            await session.flush()

            child_domain = self._version_to_domain(child_record)
            return (child_domain, parent_domain)

        async with self._publish_lock:
            return await self._data_store.execute_in_transaction(_action)

    # -- Operation History Persistence ----------------------------------------

    async def record_operation_history(
        self,
        request_id: str,
        profile_id: str,
        status: str,
        tenant_id: str = "default",
        document_id: str | None = None,
        version_id: str | None = None,
        user_id: str | None = None,
        execution_time_ms: float = 0.0,
        output_storage_key: str | None = None,
        validation_report: ValidationReport | None = None,
        errors: list[str] | None = None,
    ) -> None:
        """Record sanitized document operation execution history.

        Args:
            request_id: Unique request identifier.
            profile_id: Identifier of executed profile.
            status: Execution status string (e.g. 'SUCCESS', 'FAILED').
            tenant_id: Tenant partition identifier.
            document_id: Optional document identifier.
            version_id: Optional version identifier.
            user_id: Optional user identifier.
            execution_time_ms: Execution duration in milliseconds.
            output_storage_key: Optional output blob storage key.
            validation_report: Optional validation report.
            errors: Optional list of error messages (sanitized).

        Raises:
            DocumentEngineError: If request_id already exists.
        """
        async def _action(session: AsyncSession) -> None:
            # Check duplicate request_id
            stmt = select(DocumentOperationHistoryRecord).where(
                DocumentOperationHistoryRecord.request_id == request_id
            )
            res = await session.execute(stmt)
            if res.scalar_one_or_none() is not None:
                raise DocumentEngineError(
                    f"Operation history for request_id '{request_id}' already exists."
                )

            val_str = json.dumps(validation_report.model_dump()) if validation_report else None
            err_str = json.dumps(errors) if errors else None

            record = DocumentOperationHistoryRecord(
                id=str(uuid.uuid4()),
                request_id=request_id,
                tenant_id=tenant_id,
                profile_id=profile_id,
                document_id=document_id,
                version_id=version_id,
                user_id=user_id,
                status=status,
                execution_time_ms=execution_time_ms,
                output_storage_key=output_storage_key,
                validation_report_json=val_str,
                errors_json=err_str,
            )
            session.add(record)
            await session.flush()

        await self._data_store.execute_in_transaction(_action)

    async def get_operation_history(
        self, request_id: str, tenant_id: str = "default"
    ) -> dict[str, Any] | None:
        """Retrieve operation execution history entry by request ID.

        Args:
            request_id: Operation request ID.
            tenant_id: Tenant partition identifier.

        Returns:
            Dictionary containing sanitized history data, or None if not found.
        """
        async def _action(session: AsyncSession) -> dict[str, Any] | None:
            stmt = select(DocumentOperationHistoryRecord).where(
                DocumentOperationHistoryRecord.request_id == request_id,
                DocumentOperationHistoryRecord.tenant_id == tenant_id,
            )
            res = await session.execute(stmt)
            rec = res.scalar_one_or_none()
            if rec is None:
                return None

            val_report = None
            if rec.validation_report_json:
                try:
                    val_report = json.loads(rec.validation_report_json)
                except Exception:
                    val_report = None

            err_list = []
            if rec.errors_json:
                try:
                    err_list = json.loads(rec.errors_json)
                except Exception:
                    err_list = []

            return {
                "request_id": rec.request_id,
                "tenant_id": rec.tenant_id,
                "profile_id": rec.profile_id,
                "document_id": rec.document_id,
                "version_id": rec.version_id,
                "user_id": rec.user_id,
                "status": rec.status,
                "execution_time_ms": rec.execution_time_ms,
                "output_storage_key": rec.output_storage_key,
                "validation_report": val_report,
                "errors": err_list,
                "created_at": rec.created_at.isoformat() if rec.created_at else None,
            }

        return await self._data_store.execute_in_transaction(_action)

    async def list_operation_history(
        self,
        tenant_id: str = "default",
        profile_id: str | None = None,
        document_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List operation history entries matching criteria.

        Args:
            tenant_id: Tenant partition identifier.
            profile_id: Optional profile ID filter.
            document_id: Optional document ID filter.
            limit: Maximum entries to return.
            offset: Pagination offset.

        Returns:
            List of operation history dictionaries.
        """
        async def _action(session: AsyncSession) -> list[dict[str, Any]]:
            query = select(DocumentOperationHistoryRecord).where(
                DocumentOperationHistoryRecord.tenant_id == tenant_id
            )
            if profile_id is not None:
                query = query.where(DocumentOperationHistoryRecord.profile_id == profile_id)
            if document_id is not None:
                query = query.where(DocumentOperationHistoryRecord.document_id == document_id)

            query = (
                query.order_by(DocumentOperationHistoryRecord.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            res = await session.execute(query)
            records = res.scalars().all()

            results = []
            for rec in records:
                err_list = []
                if rec.errors_json:
                    try:
                        err_list = json.loads(rec.errors_json)
                    except Exception:
                        err_list = []

                results.append(
                    {
                        "request_id": rec.request_id,
                        "tenant_id": rec.tenant_id,
                        "profile_id": rec.profile_id,
                        "document_id": rec.document_id,
                        "version_id": rec.version_id,
                        "status": rec.status,
                        "execution_time_ms": rec.execution_time_ms,
                        "output_storage_key": rec.output_storage_key,
                        "errors": err_list,
                        "created_at": rec.created_at.isoformat() if rec.created_at else None,
                    }
                )
            return results

        return await self._data_store.execute_in_transaction(_action)

    # -- Operation Profile Catalog Persistence --------------------------------

    async def save_operation_profile(
        self, profile: DocumentOperationProfile, tenant_id: str = "default"
    ) -> DocumentOperationProfile:
        """Persist or update a DocumentOperationProfile definition.

        Args:
            profile: DocumentOperationProfile domain model.
            tenant_id: Tenant partition identifier.

        Returns:
            Persisted DocumentOperationProfile domain model.
        """
        async def _action(session: AsyncSession) -> DocumentOperationProfile:
            stmt = select(DocumentOperationProfileRecord).where(
                DocumentOperationProfileRecord.tenant_id == tenant_id,
                DocumentOperationProfileRecord.profile_id == profile.id,
                DocumentOperationProfileRecord.version == profile.version,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()

            pipe_str = (
                json.dumps(profile.adapter_pipeline.model_dump())
                if profile.adapter_pipeline
                else None
            )
            perms_str = json.dumps(profile.permissions) if profile.permissions else None

            if record is not None:
                record.name = profile.name
                record.namespace = profile.namespace
                record.description = profile.description
                record.business_operation = profile.business_operation
                record.required_template_id = profile.required_template_id
                record.output_bucket = profile.output_bucket
                record.pipeline_definition_json = pipe_str
                record.permissions_json = perms_str
            else:
                record = DocumentOperationProfileRecord(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    profile_id=profile.id,
                    name=profile.name,
                    namespace=profile.namespace,
                    version=profile.version,
                    description=profile.description,
                    business_operation=profile.business_operation,
                    required_template_id=profile.required_template_id,
                    output_bucket=profile.output_bucket,
                    pipeline_definition_json=pipe_str,
                    permissions_json=perms_str,
                )
                session.add(record)

            await session.flush()
            return self._profile_to_domain(record)

        return await self._data_store.execute_in_transaction(_action)

    async def get_operation_profile(
        self, profile_id: str, version: str | None = None, tenant_id: str = "default"
    ) -> DocumentOperationProfile | None:
        """Retrieve DocumentOperationProfile by profile ID and optional version.

        Args:
            profile_id: Profile identifier string.
            version: Optional SemVer version string.
            tenant_id: Tenant partition identifier.

        Returns:
            DocumentOperationProfile domain model or None if not found.
        """
        async def _action(session: AsyncSession) -> DocumentOperationProfile | None:
            query = select(DocumentOperationProfileRecord).where(
                DocumentOperationProfileRecord.tenant_id == tenant_id,
                DocumentOperationProfileRecord.profile_id == profile_id,
            )
            if version is not None:
                query = query.where(DocumentOperationProfileRecord.version == version)
            else:
                query = query.order_by(DocumentOperationProfileRecord.created_at.desc())

            res = await session.execute(query)
            record = res.scalars().first()
            if record is None:
                return None
            return self._profile_to_domain(record)

        return await self._data_store.execute_in_transaction(_action)

    async def list_operation_profiles(
        self,
        tenant_id: str = "default",
        business_operation: str | None = None,
        namespace: str | None = None,
    ) -> list[DocumentOperationProfile]:
        """List registered operation profiles matching criteria.

        Args:
            tenant_id: Tenant partition identifier.
            business_operation: Optional business operation name filter.
            namespace: Optional namespace filter.

        Returns:
            List of DocumentOperationProfile domain models.
        """
        async def _action(session: AsyncSession) -> list[DocumentOperationProfile]:
            query = select(DocumentOperationProfileRecord).where(
                DocumentOperationProfileRecord.tenant_id == tenant_id
            )
            if business_operation is not None:
                query = query.where(
                    DocumentOperationProfileRecord.business_operation == business_operation
                )
            if namespace is not None:
                query = query.where(DocumentOperationProfileRecord.namespace == namespace)

            query = query.order_by(DocumentOperationProfileRecord.profile_id.asc())
            res = await session.execute(query)
            records = res.scalars().all()
            return [self._profile_to_domain(r) for r in records]

        return await self._data_store.execute_in_transaction(_action)

    async def delete_operation_profile(
        self, profile_id: str, version: str, tenant_id: str = "default"
    ) -> bool:
        """Delete an operation profile version record.

        Args:
            profile_id: Profile identifier string.
            version: SemVer version string.
            tenant_id: Tenant partition identifier.

        Returns:
            True if deleted; False if not found.
        """
        async def _action(session: AsyncSession) -> bool:
            stmt = select(DocumentOperationProfileRecord).where(
                DocumentOperationProfileRecord.tenant_id == tenant_id,
                DocumentOperationProfileRecord.profile_id == profile_id,
                DocumentOperationProfileRecord.version == version,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                return False

            await session.delete(record)
            await session.flush()
            return True

        return await self._data_store.execute_in_transaction(_action)


class TemplateRepository(ITemplateRepository):
    """Repository handling relational database persistence for declarative Template Schemas."""

    def __init__(self, data_store: IDataStore) -> None:
        """Initialize TemplateRepository with Storage Engine IDataStore.

        Args:
            data_store: Storage Engine relational persistence provider.
        """
        self._data_store = data_store

    @staticmethod
    def _record_to_domain(record: TemplateSchemaRecord) -> TemplateSchema:
        """Convert a TemplateSchemaRecord ORM entity to a TemplateSchema domain model."""
        placeholders = json.loads(record.placeholders_json) if record.placeholders_json else []
        required_fields = (
            json.loads(record.required_fields_json) if record.required_fields_json else []
        )
        schema_definition = (
            json.loads(record.schema_definition_json) if record.schema_definition_json else {}
        )

        return TemplateSchema(
            template_id=record.template_id,
            name=record.name,
            namespace=record.namespace,
            version=record.version,
            description=record.description,
            placeholders=placeholders,
            required_fields=required_fields,
            schema_definition=schema_definition,
        )

    async def save_template(
        self, schema: TemplateSchema, tenant_id: str = "default"
    ) -> TemplateSchema:
        """Persist a new declarative TemplateSchema version.

        Args:
            schema: TemplateSchema domain model instance.
            tenant_id: Tenant partition identifier.

        Returns:
            The persisted TemplateSchema instance.

        Raises:
            DocumentEngineError: If the template_id + version pair already exists.
        """
        async def _action(session: AsyncSession) -> TemplateSchema:
            stmt = select(TemplateSchemaRecord).where(
                TemplateSchemaRecord.tenant_id == tenant_id,
                TemplateSchemaRecord.template_id == schema.template_id,
                TemplateSchemaRecord.version == schema.version,
            )
            res = await session.execute(stmt)
            if res.scalar_one_or_none() is not None:
                raise DocumentEngineError(
                    f"Duplicate template registration: '{schema.template_id}' version "
                    f"'{schema.version}' is already registered."
                )

            record = TemplateSchemaRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                template_id=schema.template_id,
                version=schema.version,
                name=schema.name,
                namespace=schema.namespace,
                description=schema.description,
                placeholders_json=json.dumps(schema.placeholders) if schema.placeholders else None,
                required_fields_json=(
                    json.dumps(schema.required_fields) if schema.required_fields else None
                ),
                schema_definition_json=(
                    json.dumps(schema.schema_definition) if schema.schema_definition else None
                ),
            )
            session.add(record)
            await session.flush()
            return self._record_to_domain(record)

        return await self._data_store.execute_in_transaction(_action)

    async def get_template(
        self, template_id: str, version: str | None = None, tenant_id: str = "default"
    ) -> TemplateSchema | None:
        """Retrieve a TemplateSchema by template_id and optional version.

        If version is omitted, returns the latest version based on SemVer comparison,
        consistent with TemplateLibrary's own in-memory latest-version resolution.

        Args:
            template_id: Template identifier string.
            version: Optional SemVer string.
            tenant_id: Tenant partition identifier.

        Returns:
            TemplateSchema domain model, or None if not found.
        """
        async def _action(session: AsyncSession) -> TemplateSchema | None:
            query = select(TemplateSchemaRecord).where(
                TemplateSchemaRecord.tenant_id == tenant_id,
                TemplateSchemaRecord.template_id == template_id,
            )
            if version is not None:
                query = query.where(TemplateSchemaRecord.version == version)

            res = await session.execute(query)
            records = res.scalars().all()
            if not records:
                return None

            if version is not None:
                return self._record_to_domain(records[0])

            latest = max(records, key=lambda r: parse_semver(r.version))
            return self._record_to_domain(latest)

        return await self._data_store.execute_in_transaction(_action)

    async def list_templates(
        self, tenant_id: str = "default", namespace: str | None = None
    ) -> list[TemplateSchema]:
        """List persisted template versions matching tenant and optional namespace filter.

        Args:
            tenant_id: Tenant partition identifier.
            namespace: Optional namespace filter.

        Returns:
            List of TemplateSchema domain models (all persisted versions, not deduplicated).
        """
        async def _action(session: AsyncSession) -> list[TemplateSchema]:
            query = select(TemplateSchemaRecord).where(TemplateSchemaRecord.tenant_id == tenant_id)
            if namespace is not None:
                query = query.where(TemplateSchemaRecord.namespace == namespace)

            query = query.order_by(TemplateSchemaRecord.template_id.asc())
            res = await session.execute(query)
            records = res.scalars().all()
            return [self._record_to_domain(r) for r in records]

        return await self._data_store.execute_in_transaction(_action)

    async def delete_template(
        self, template_id: str, version: str, tenant_id: str = "default"
    ) -> bool:
        """Delete a specific persisted template version.

        Args:
            template_id: Template identifier string.
            version: SemVer version string.
            tenant_id: Tenant partition identifier.

        Returns:
            True if deleted; False if not found.
        """
        async def _action(session: AsyncSession) -> bool:
            stmt = select(TemplateSchemaRecord).where(
                TemplateSchemaRecord.tenant_id == tenant_id,
                TemplateSchemaRecord.template_id == template_id,
                TemplateSchemaRecord.version == version,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                return False

            await session.delete(record)
            await session.flush()
            return True

        return await self._data_store.execute_in_transaction(_action)


__all__ = [
    "DocumentRepository",
    "TemplateRepository",
]
