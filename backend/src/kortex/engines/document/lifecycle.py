"""Document Lifecycle, Versioning, and Lineage Manager for KORTEX OS Document Engine.

This module implements DocumentLifecycleManager, which governs state machine transitions,
version chain enforcement, parent-child lineage tracking, and published document immutability
in accordance with Section 9 of the Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from kortex.engines.document.exceptions import DocumentLifecycleError
from kortex.engines.document.models import (
    DocumentLifecycleState,
    DocumentMetadata,
    DocumentVersion,
    SecurityMetadata,
)


class DocumentLifecycleManager:
    """Manager for document version creation, lifecycle state transitions, and lineage tracking.

    Strictly enforces:
    1. Valid lifecycle state machine transitions.
    2. Immutability of PUBLISHED, SUPERSEEDED, ARCHIVED, and LOGICAL_DELETE versions.
    3. Parent-child version chain integrity and lineage traversal.
    4. Rejection of duplicate version identifiers and broken lineage.
    """

    VALID_TRANSITIONS: dict[DocumentLifecycleState, set[DocumentLifecycleState]] = {
        DocumentLifecycleState.DRAFT: {
            DocumentLifecycleState.REVIEW,
            DocumentLifecycleState.PUBLISHED,
            DocumentLifecycleState.LOGICAL_DELETE,
        },
        DocumentLifecycleState.REVIEW: {
            DocumentLifecycleState.DRAFT,
            DocumentLifecycleState.PUBLISHED,
            DocumentLifecycleState.LOGICAL_DELETE,
        },
        DocumentLifecycleState.PUBLISHED: {
            DocumentLifecycleState.SUPERSEDED,
            DocumentLifecycleState.ARCHIVED,
            DocumentLifecycleState.LOGICAL_DELETE,
        },
        DocumentLifecycleState.SUPERSEDED: {
            DocumentLifecycleState.ARCHIVED,
            DocumentLifecycleState.LOGICAL_DELETE,
        },
        DocumentLifecycleState.ARCHIVED: {
            DocumentLifecycleState.LOGICAL_DELETE,
        },
        DocumentLifecycleState.LOGICAL_DELETE: set(),
    }

    IMMUTABLE_STATES: set[DocumentLifecycleState] = {
        DocumentLifecycleState.PUBLISHED,
        DocumentLifecycleState.SUPERSEDED,
        DocumentLifecycleState.ARCHIVED,
        DocumentLifecycleState.LOGICAL_DELETE,
    }

    def __init__(self) -> None:
        """Initialize the in-memory document version store and lineage maps."""
        # Maps version_id -> DocumentVersion
        self._versions: dict[str, DocumentVersion] = {}
        # Maps document_id -> list of version_ids in creation order
        self._document_chains: dict[str, list[str]] = {}

    def validate_transition(
        self, current_state: DocumentLifecycleState, target_state: DocumentLifecycleState
    ) -> bool:
        """Validate whether a state transition from current_state to target_state is permitted.

        Args:
            current_state: Current lifecycle state of the document version.
            target_state: Proposed target lifecycle state.

        Returns:
            True if transition is allowed.

        Raises:
            DocumentLifecycleError: If the transition is invalid.
        """
        if current_state == target_state:
            return True

        allowed = self.VALID_TRANSITIONS.get(current_state, set())
        if target_state not in allowed:
            raise DocumentLifecycleError(
                f"Invalid lifecycle transition from {current_state.value} to {target_state.value}."
            )
        return True

    async def create_version(
        self,
        document_id: str | None = None,
        title: str = "Untitled Document",
        author_id: str = "system",
        parent_version_id: str | None = None,
        version_number: str | None = None,
        version_id: str | None = None,
        security_metadata: SecurityMetadata | None = None,
        created_at: str | None = None,
    ) -> DocumentVersion:
        """Create a new document version (root or child).

        Args:
            document_id: Root document UUID. Generated if creating a new document.
            title: Document title.
            author_id: User identifier creating the version.
            parent_version_id: Parent version UUID if creating a child version.
            version_number: Semantic version string (e.g. '1.0.0'). Auto-generated if omitted.
            version_id: Optional specific UUID for the new version.
            security_metadata: Optional security classification and labels.
            created_at: ISO 8601 UTC timestamp string.

        Returns:
            The created DocumentVersion instance.

        Raises:
            DocumentLifecycleError: If version_id exists, parent is missing or invalid.
        """
        target_version_id = version_id or str(uuid.uuid4())
        if target_version_id in self._versions:
            raise DocumentLifecycleError(
                f"Duplicate version ID '{target_version_id}' already exists."
            )

        parent_version: DocumentVersion | None = None
        if parent_version_id is not None:
            if parent_version_id not in self._versions:
                raise DocumentLifecycleError(
                    f"Parent version ID '{parent_version_id}' does not exist."
                )
            parent_version = self._versions[parent_version_id]

            if document_id is not None and parent_version.document_id != document_id:
                raise DocumentLifecycleError(
                    f"Document ID mismatch: expected '{parent_version.document_id}', got '{document_id}'."
                )
            document_id = parent_version.document_id

            if parent_version.metadata.lifecycle_state == DocumentLifecycleState.LOGICAL_DELETE:
                raise DocumentLifecycleError(
                    f"Cannot create child version from soft-deleted parent version '{parent_version_id}'."
                )

        target_document_id = document_id or str(uuid.uuid4())
        timestamp = created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()

        if parent_version is not None:
            lineage_path = list(parent_version.metadata.lineage_path) + [target_version_id]
            if version_number is None:
                # Derive next version number
                chain_len = len(self._document_chains.get(target_document_id, [])) + 1
                version_number = f"1.0.{chain_len - 1}"
        else:
            lineage_path = [target_version_id]
            version_number = version_number or "1.0.0"

        metadata = DocumentMetadata(
            document_id=target_document_id,
            version_id=target_version_id,
            parent_version_id=parent_version_id,
            lifecycle_state=DocumentLifecycleState.DRAFT,
            lineage_path=lineage_path,
            title=title,
            author_id=author_id,
            is_immutable=False,
            security_metadata=security_metadata or SecurityMetadata(),
            created_at=timestamp,
        )

        doc_version = DocumentVersion(
            version_id=target_version_id,
            document_id=target_document_id,
            parent_version_id=parent_version_id,
            version_number=version_number,
            created_at=timestamp,
            created_by=author_id,
            is_immutable=False,
            metadata=metadata,
        )

        self._versions[target_version_id] = doc_version
        if target_document_id not in self._document_chains:
            self._document_chains[target_document_id] = []
        self._document_chains[target_document_id].append(target_version_id)

        return doc_version

    async def create_child_version(
        self,
        parent_version_id: str,
        title: str | None = None,
        author_id: str = "system",
        version_number: str | None = None,
        security_metadata: SecurityMetadata | None = None,
    ) -> DocumentVersion:
        """Create a new DRAFT child version linked to an existing parent version.

        Args:
            parent_version_id: UUID of the parent version to derive from.
            title: Optional title override. Defaults to parent title.
            author_id: User identifier.
            version_number: Optional SemVer string.
            security_metadata: Optional security metadata override.

        Returns:
            The created child DocumentVersion.
        """
        parent = await self.get_version_object(parent_version_id)
        effective_title = title if title is not None else parent.metadata.title
        effective_sec = security_metadata or parent.metadata.security_metadata

        return await self.create_version(
            document_id=parent.document_id,
            title=effective_title,
            author_id=author_id,
            parent_version_id=parent_version_id,
            version_number=version_number,
            security_metadata=effective_sec,
        )

    async def transition_state(
        self,
        document_id: str,
        version_id: str,
        target_state: DocumentLifecycleState,
        published_at: str | None = None,
    ) -> DocumentMetadata:
        """Transition a document version to a target lifecycle state.

        Args:
            document_id: Root document UUID.
            version_id: Specific version UUID to transition.
            target_state: Proposed target state.
            published_at: Optional ISO timestamp override when publishing.

        Returns:
            Updated DocumentMetadata.

        Raises:
            DocumentLifecycleError: If version missing, mismatch, or transition invalid.
        """
        version = await self.get_version_object(version_id)
        if version.document_id != document_id:
            raise DocumentLifecycleError(
                f"Document ID mismatch for version '{version_id}': expected '{document_id}', got '{version.document_id}'."
            )

        current_state = version.metadata.lifecycle_state

        # Validate state machine rules
        self.validate_transition(current_state, target_state)

        if current_state == target_state:
            return version.metadata

        new_is_immutable = target_state in self.IMMUTABLE_STATES
        timestamp = published_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        effective_published_at = (
            timestamp
            if target_state == DocumentLifecycleState.PUBLISHED
            else version.metadata.published_at
        )

        updated_metadata = DocumentMetadata(
            document_id=version.document_id,
            version_id=version.version_id,
            parent_version_id=version.parent_version_id,
            lifecycle_state=target_state,
            lineage_path=version.metadata.lineage_path,
            title=version.metadata.title,
            author_id=version.metadata.author_id,
            is_immutable=new_is_immutable,
            security_metadata=version.metadata.security_metadata,
            file_size_bytes=version.metadata.file_size_bytes,
            sha256_hash=version.metadata.sha256_hash,
            storage_key=version.metadata.storage_key,
            bucket_name=version.metadata.bucket_name,
            created_at=version.metadata.created_at,
            published_at=effective_published_at,
        )

        updated_version = DocumentVersion(
            version_id=version.version_id,
            document_id=version.document_id,
            parent_version_id=version.parent_version_id,
            version_number=version.version_number,
            created_at=version.created_at,
            created_by=version.created_by,
            is_immutable=new_is_immutable,
            metadata=updated_metadata,
        )

        self._versions[version_id] = updated_version

        # If transitioning to PUBLISHED and parent version was PUBLISHED, automatically mark parent as SUPERSEEDED
        if (
            target_state == DocumentLifecycleState.PUBLISHED
            and version.parent_version_id is not None
        ):
            parent = self._versions.get(version.parent_version_id)
            if parent and parent.metadata.lifecycle_state == DocumentLifecycleState.PUBLISHED:
                await self.transition_state(
                    document_id=parent.document_id,
                    version_id=parent.version_id,
                    target_state=DocumentLifecycleState.SUPERSEDED,
                )

        return updated_metadata

    async def get_version(self, document_id: str, version_id: str) -> DocumentMetadata:
        """Retrieve DocumentMetadata for a specific version.

        Args:
            document_id: Root document UUID.
            version_id: Specific version UUID.

        Returns:
            DocumentMetadata instance.

        Raises:
            DocumentLifecycleError: If version missing or document ID mismatch.
        """
        version = await self.get_version_object(version_id)
        if version.document_id != document_id:
            raise DocumentLifecycleError(
                f"Document ID mismatch for version '{version_id}': expected '{document_id}', got '{version.document_id}'."
            )
        return version.metadata

    async def get_version_object(self, version_id: str) -> DocumentVersion:
        """Retrieve full DocumentVersion instance by version ID.

        Args:
            version_id: Version UUID.

        Returns:
            DocumentVersion object.

        Raises:
            DocumentLifecycleError: If version_id does not exist.
        """
        if version_id not in self._versions:
            raise DocumentLifecycleError(f"Document version ID '{version_id}' not found.")
        return self._versions[version_id]

    async def get_latest_version(self, document_id: str) -> DocumentMetadata:
        """Retrieve the newest / latest version metadata for a document entity.

        Args:
            document_id: Root document UUID.

        Returns:
            DocumentMetadata of the latest version.

        Raises:
            DocumentLifecycleError: If no versions exist for document_id.
        """
        if document_id not in self._document_chains or not self._document_chains[document_id]:
            raise DocumentLifecycleError(f"No document versions found for document ID '{document_id}'.")

        latest_version_id = self._document_chains[document_id][-1]
        return self._versions[latest_version_id].metadata

    async def get_lineage(self, document_id: str) -> list[DocumentMetadata]:
        """Retrieve full version lineage chain for a document entity.

        Args:
            document_id: Root document UUID.

        Returns:
            Ordered list of DocumentMetadata objects from root to latest version.

        Raises:
            DocumentLifecycleError: If document_id is not found.
        """
        if document_id not in self._document_chains or not self._document_chains[document_id]:
            raise DocumentLifecycleError(f"No document lineage found for document ID '{document_id}'.")

        return [
            self._versions[vid].metadata
            for vid in self._document_chains[document_id]
            if vid in self._versions
        ]

    async def is_immutable(self, document_id: str, version_id: str) -> bool:
        """Check whether a document version is locked against edits.

        Args:
            document_id: Root document UUID.
            version_id: Specific version UUID.

        Returns:
            True if version is immutable.
        """
        metadata = await self.get_version(document_id, version_id)
        return metadata.is_immutable or metadata.lifecycle_state in self.IMMUTABLE_STATES


__all__ = ["DocumentLifecycleManager"]
