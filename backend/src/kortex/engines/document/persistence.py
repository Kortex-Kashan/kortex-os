"""Relational Persistence Repository for KORTEX OS Document Engine.

This module implements DocumentRepository, providing transactional persistence, CRUD operations,
tenant isolation, version chain management, and domain/ORM entity mapping via Storage Engine IDataStore.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.document.exceptions import (
    DocumentEngineError,
    DocumentLifecycleError,
    DocumentValidationError,
)
from kortex.engines.document.interfaces import IDocumentRepository
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
    ValidationReport,
)
from kortex.engines.storage.interfaces import IDataStore

logger = logging.getLogger("kortex.engines.document.persistence")


class DocumentRepository(IDocumentRepository):
    """Repository handling all relational database persistence for the Document Engine."""

    def __init__(self, data_store: IDataStore) -> None:
        """Initialize DocumentRepository with Storage Engine IDataStore.

        Args:
            data_store: Storage Engine relational persistence provider.
        """
        self._data_store = data_store

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
                current_version_id=document.current_version_id,
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
            record.current_version_id = document.current_version_id
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

            # 2. Check for duplicate version_id or duplicate version_number under same document
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

            # 4. Update parent document's current_version_id pointer
            doc_record.current_version_id = version.version_id
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

        Args:
            document_id: Unique document identifier.
            tenant_id: Tenant partition identifier.

        Returns:
            Latest DocumentVersion domain model or None if no versions exist.
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


__all__ = [
    "DocumentRepository",
]
