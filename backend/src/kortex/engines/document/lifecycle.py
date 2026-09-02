"""Document Lifecycle, Versioning, and Lineage Manager for KORTEX OS Document Engine.

This module implements DocumentLifecycleManager, which governs state machine transitions,
version chain enforcement, parent-child lineage tracking, atomic publication with CAS gate,
and published document immutability in accordance with Section 9 of the Document Engine
Implementation Specification (Version 3.0.0) and Amendment M2-ARCH-01.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
import uuid
from typing import ClassVar, cast

from kortex.engines.document.exceptions import DocumentLifecycleError
from kortex.engines.document.interfaces import (
    IDocumentLifecycleManager,
    IDocumentRepository,
)
from kortex.engines.document.models import (
    Document,
    DocumentLifecycleState,
    DocumentMetadata,
    DocumentVersion,
    SecurityMetadata,
)
from kortex.engines.document.security import DefaultVerificationService, IVerificationService
from kortex.engines.storage.interfaces import ICacheStore

logger = logging.getLogger("kortex.engines.document.lifecycle")

# Regular expression for SemVer 2.0.0 validation (consistent with TemplateLibrary and ConnectorRegistry)
SEMVER_REGEX = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


class DocumentLifecycleManager(IDocumentLifecycleManager):
    """Manager for document version creation, lifecycle state transitions, lineage tracking, and immutability.

    Strictly enforces:
    1. Valid lifecycle state machine transitions (DRAFT -> REVIEW -> PUBLISHED -> SUPERSEDED -> ARCHIVED /
    LOGICAL_DELETE).
    2. Immutability of PUBLISHED, SUPERSEDED, ARCHIVED, and LOGICAL_DELETE versions.
    3. Parent-child version chain integrity and lineage traversal.
    4. Deterministic SemVer patch derivation (e.g. 1.0.0 -> 1.0.1 -> 1.0.2).
    5. Atomic version publication via compare-and-swap (CAS) on DocumentRecord.current_version_id (M2-ARCH-01).
    6. Multi-tenant boundary isolation across all lifecycle operations.
    """

    VALID_TRANSITIONS: ClassVar[dict[DocumentLifecycleState, set[DocumentLifecycleState]]] = {
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

    IMMUTABLE_STATES: ClassVar[set[DocumentLifecycleState]] = {
        DocumentLifecycleState.PUBLISHED,
        DocumentLifecycleState.SUPERSEDED,
        DocumentLifecycleState.ARCHIVED,
        DocumentLifecycleState.LOGICAL_DELETE,
    }

    METADATA_CACHE_TTL_SECONDS = 300

    def __init__(
        self,
        repository: IDocumentRepository | None = None,
        verification_service: IVerificationService | None = None,
        cache_store: ICacheStore | None = None,
    ) -> None:
        """Initialize the DocumentLifecycleManager.

        Args:
            repository: Optional IDocumentRepository for relational persistence.
                        If None, operates in standalone in-memory mode.
            verification_service: Optional IVerificationService used to compute SHA256
                                   integrity hashes when publishing a version with a payload.
                                   Defaults to DefaultVerificationService when not supplied.
            cache_store: Optional ICacheStore backing the tenant-scoped Metadata Cache.
                         When None, metadata is always read from repository/in-memory state.
        """
        self._repository = repository
        self._versions: dict[str, DocumentVersion] = {}
        self._document_chains: dict[str, list[str]] = {}
        self._current_versions: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._verification_service = verification_service or DefaultVerificationService()
        self._cache_store = cache_store

    @property
    def repository(self) -> IDocumentRepository | None:
        """Return the configured IDocumentRepository, or None if in-memory mode."""
        return self._repository

    @property
    def cache_store(self) -> ICacheStore | None:
        """Return the configured ICacheStore backing the Metadata Cache, or None if uncached."""
        return self._cache_store

    @staticmethod
    def _metadata_cache_key(document_id: str, version_id: str, tenant_id: str) -> str:
        """Build the Metadata Cache key for a specific document version."""
        return f"doc_engine:{tenant_id}:metadata:{document_id}:{version_id}"

    async def _invalidate_metadata_cache(self, document_id: str, version_id: str, tenant_id: str) -> None:
        """Invalidate the Metadata Cache entry for a specific document version, if caching is enabled."""
        if self._cache_store is not None:
            await self._cache_store.delete(self._metadata_cache_key(document_id, version_id, tenant_id))

    def validate_transition(self, current_state: DocumentLifecycleState, target_state: DocumentLifecycleState) -> bool:
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

    @staticmethod
    def derive_next_version_number(parent_version_number: str) -> str:
        """Derive the next patch SemVer number from an immediate parent's version string.

        Adheres strictly to SemVer 2.0.0 specification (MAJOR.MINOR.PATCH).

        Examples:
            1.0.0 -> 1.0.1
            1.0.9 -> 1.0.10
            1.9.9 -> 1.9.10

        Args:
            parent_version_number: Strict SemVer 2.0.0 string of the parent version.

        Returns:
            Incremented SemVer string (MAJOR.MINOR.PATCH+1).

        Raises:
            DocumentLifecycleError: If parent_version_number is not a valid SemVer 2.0.0 format.
        """
        match = SEMVER_REGEX.match(parent_version_number.strip())
        if not match:
            raise DocumentLifecycleError(
                f"Invalid semantic version format: '{parent_version_number}'. "
                f"Must follow SemVer 2.0.0 (MAJOR.MINOR.PATCH)."
            )
        groups = match.groupdict()
        major = int(groups["major"])
        minor = int(groups["minor"])
        patch = int(groups["patch"])
        return f"{major}.{minor}.{patch + 1}"

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
        tenant_id: str = "default",
    ) -> DocumentVersion:
        """Create a new document version snapshot (root genesis or child revision).

        Args:
            document_id: Root document UUID. Generated if creating a new document without parent.
            title: Document title.
            author_id: User identifier creating the version.
            parent_version_id: Parent version UUID if creating a child version.
            version_number: Semantic version string (e.g. '1.0.0'). Derived from parent if omitted.
            version_id: Optional specific UUID for the new version.
            security_metadata: Optional security classification and labels.
            created_at: ISO 8601 UTC timestamp string.
            tenant_id: Tenant partition identifier.

        Returns:
            The created DocumentVersion domain instance.

        Raises:
            DocumentLifecycleError: If parent is missing, soft-deleted, tenant/document mismatch,
                                    or invalid SemVer format.
        """
        async with self._lock:
            parent_version: DocumentVersion | None = None
            if parent_version_id is not None:
                parent_version = await self._get_version_internal(
                    parent_version_id, document_id=document_id, tenant_id=tenant_id
                )
                if parent_version is None:
                    raise DocumentLifecycleError(f"Parent version ID '{parent_version_id}' does not exist.")

                if document_id is not None and parent_version.document_id != document_id:
                    raise DocumentLifecycleError(
                        f"Document ID mismatch: expected '{parent_version.document_id}', got '{document_id}'."
                    )
                target_document_id = parent_version.document_id

                if parent_version.metadata.lifecycle_state == DocumentLifecycleState.LOGICAL_DELETE:
                    raise DocumentLifecycleError(
                        f"Cannot create child version from soft-deleted parent version '{parent_version_id}'."
                    )

                target_version_id = version_id or str(uuid.uuid4())
                lineage_path = [*list(parent_version.metadata.lineage_path), target_version_id]
                if version_number is not None:
                    if not SEMVER_REGEX.match(version_number.strip()):
                        raise DocumentLifecycleError(
                            f"Invalid semantic version format: '{version_number}'. "
                            f"Must follow SemVer 2.0.0 (MAJOR.MINOR.PATCH)."
                        )
                    effective_version_number = version_number
                else:
                    effective_version_number = self.derive_next_version_number(parent_version.version_number)
            else:
                target_document_id = document_id or str(uuid.uuid4())
                target_version_id = version_id or str(uuid.uuid4())
                lineage_path = [target_version_id]
                if version_number is not None:
                    if not SEMVER_REGEX.match(version_number.strip()):
                        raise DocumentLifecycleError(
                            f"Invalid semantic version format: '{version_number}'. "
                            f"Must follow SemVer 2.0.0 (MAJOR.MINOR.PATCH)."
                        )
                    effective_version_number = version_number
                else:
                    effective_version_number = "1.0.0"

            timestamp = created_at or datetime.datetime.now(datetime.UTC).isoformat()

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
                version_number=effective_version_number,
                created_at=timestamp,
                created_by=author_id,
                is_immutable=False,
                metadata=metadata,
            )

            if self._repository is not None:
                # Ensure root document record exists
                doc = await self._repository.get_document(target_document_id, tenant_id=tenant_id, include_deleted=True)
                if doc is None:
                    await self._repository.create_document(
                        Document(
                            document_id=target_document_id,
                            tenant_id=tenant_id,
                            title=title,
                            current_version_id=None,
                        )
                    )
                return await self._repository.create_version(doc_version, tenant_id=tenant_id)
            else:
                if target_version_id in self._versions:
                    raise DocumentLifecycleError(f"Duplicate version ID '{target_version_id}' already exists.")
                # Enforce version_number uniqueness per document in in-memory mode
                existing_chain = self._document_chains.get(target_document_id, [])
                for existing_vid in existing_chain:
                    if (
                        existing_vid in self._versions
                        and self._versions[existing_vid].version_number == effective_version_number
                    ):
                        raise DocumentLifecycleError(
                            f"Duplicate version number '{effective_version_number}' for document "
                            f"'{target_document_id}'."
                        )

                self._versions[target_version_id] = doc_version
                if target_document_id not in self._document_chains:
                    self._document_chains[target_document_id] = []
                self._document_chains[target_document_id].append(target_version_id)
                return doc_version

    async def create_child_version(
        self,
        parent_version_id: str,
        document_id: str | None = None,
        title: str | None = None,
        author_id: str = "system",
        version_number: str | None = None,
        version_id: str | None = None,
        security_metadata: SecurityMetadata | None = None,
        tenant_id: str = "default",
    ) -> DocumentVersion:
        """Create a new DRAFT child version derived from an existing parent version.

        Args:
            parent_version_id: UUID of the parent version to derive from.
            document_id: Optional document UUID. Verified against parent.
            title: Optional title override. Defaults to parent title.
            author_id: User identifier creating the version.
            version_number: Optional explicit SemVer string.
            version_id: Optional specific version UUID for the child version.
            security_metadata: Optional security metadata override.
            tenant_id: Tenant partition identifier.

        Returns:
            The created child DocumentVersion instance.
        """
        parent = await self.get_version_object(parent_version_id, document_id=document_id, tenant_id=tenant_id)
        effective_title = title if title is not None else parent.metadata.title
        effective_sec = security_metadata or parent.metadata.security_metadata

        return await self.create_version(
            document_id=parent.document_id,
            title=effective_title,
            author_id=author_id,
            parent_version_id=parent_version_id,
            version_number=version_number,
            version_id=version_id,
            security_metadata=effective_sec,
            tenant_id=tenant_id,
        )

    async def transition_state(
        self,
        document_id: str,
        version_id: str,
        target_state: DocumentLifecycleState,
        published_at: str | None = None,
        tenant_id: str = "default",
        payload: bytes | None = None,
    ) -> DocumentMetadata:
        """Transition a document version to a target lifecycle state.

        When target_state == PUBLISHED, atomically acquires the publication gate via compare-and-swap (CAS)
        on DocumentRecord.current_version_id, transitions child to PUBLISHED, supersedes the active predecessor,
        and sets is_immutable = True. When PUBLISHED and a payload is supplied, a SHA256 integrity hash is
        computed via IVerificationService and recorded on DocumentMetadata.sha256_hash.

        Args:
            document_id: Root document UUID.
            version_id: Specific version UUID to transition.
            target_state: Proposed target state.
            published_at: Optional ISO timestamp override when publishing.
            tenant_id: Tenant partition identifier.
            payload: Optional binary payload being published, used to compute a SHA256
                     integrity hash. Ignored for non-PUBLISHED transitions.

        Returns:
            Updated DocumentMetadata instance.

        Raises:
            DocumentLifecycleError: If version is missing, state transition is invalid,
                                    or publication CAS gate collision occurs.
        """
        async with self._lock:
            version = await self.get_version_object(version_id, document_id=document_id, tenant_id=tenant_id)

            current_state = version.metadata.lifecycle_state
            self.validate_transition(current_state, target_state)

            if current_state == target_state:
                return version.metadata

            if target_state == DocumentLifecycleState.PUBLISHED:
                sha256_hash: str | None = None
                if payload is not None:
                    sha256_hash = await self._verification_service.compute_hash(payload)

                if self._repository is not None:
                    child_ver, _ = await self._repository.publish_version(
                        document_id=document_id,
                        version_id=version_id,
                        parent_version_id=version.parent_version_id,
                        published_at=published_at,
                        tenant_id=tenant_id,
                        sha256_hash=sha256_hash,
                    )
                    await self._invalidate_metadata_cache(document_id, version_id, tenant_id)
                    if version.parent_version_id is not None:
                        await self._invalidate_metadata_cache(document_id, version.parent_version_id, tenant_id)
                    return child_ver.metadata
                else:
                    # In-memory atomic publication with CAS check
                    timestamp = published_at or datetime.datetime.now(datetime.UTC).isoformat()
                    if version.parent_version_id is not None:
                        parent = self._versions.get(version.parent_version_id)
                        if parent is None:
                            raise DocumentLifecycleError(
                                f"Cannot supersede non-existent parent version '{version.parent_version_id}' for "
                                f"document '{document_id}'."
                            )
                        if parent.document_id != document_id:
                            raise DocumentLifecycleError(
                                f"Parent version '{version.parent_version_id}' belongs to document "
                                f"'{parent.document_id}', expected '{document_id}'."
                            )
                        if parent.metadata.security_metadata.tenant_id != tenant_id:
                            raise DocumentLifecycleError(
                                f"Parent version '{version.parent_version_id}' belongs to tenant "
                                f"'{parent.metadata.security_metadata.tenant_id}', expected '{tenant_id}'."
                            )
                        if parent.metadata.lifecycle_state != DocumentLifecycleState.PUBLISHED:
                            raise DocumentLifecycleError(
                                f"Cannot supersede parent version '{version.parent_version_id}': parent is in "
                                f"'{parent.metadata.lifecycle_state.value}' state, expected 'PUBLISHED'."
                            )
                        if self._current_versions.get(document_id) != version.parent_version_id:
                            raise DocumentLifecycleError(
                                f"Concurrent publication collision or invalid predecessor: document '{document_id}' "
                                f"current_version_id does not match expected predecessor '{version.parent_version_id}'."
                            )
                        # Supersede parent
                        parent_meta = parent.metadata.model_copy(
                            update={
                                "lifecycle_state": DocumentLifecycleState.SUPERSEDED,
                                "is_immutable": True,
                            }
                        )
                        self._versions[version.parent_version_id] = parent.model_copy(
                            update={"is_immutable": True, "metadata": parent_meta}
                        )
                    else:
                        if self._current_versions.get(document_id) is not None:
                            raise DocumentLifecycleError(
                                f"Concurrent publication collision or invalid predecessor: document '{document_id}' "
                                f"current_version_id is not NULL."
                            )

                    self._current_versions[document_id] = version_id
                    child_meta = version.metadata.model_copy(
                        update={
                            "lifecycle_state": DocumentLifecycleState.PUBLISHED,
                            "is_immutable": True,
                            "published_at": timestamp,
                            "sha256_hash": sha256_hash if sha256_hash is not None else version.metadata.sha256_hash,
                        }
                    )
                    self._versions[version_id] = version.model_copy(
                        update={"is_immutable": True, "metadata": child_meta}
                    )
                    await self._invalidate_metadata_cache(document_id, version_id, tenant_id)
                    if version.parent_version_id is not None:
                        await self._invalidate_metadata_cache(document_id, version.parent_version_id, tenant_id)
                    return child_meta
            else:
                new_is_immutable = target_state in self.IMMUTABLE_STATES
                if self._repository is not None:
                    updated_ver = await self._repository.update_version_state(
                        document_id=document_id,
                        version_id=version_id,
                        target_state=target_state,
                        is_immutable=new_is_immutable,
                        published_at=published_at,
                        tenant_id=tenant_id,
                    )
                    await self._invalidate_metadata_cache(document_id, version_id, tenant_id)
                    return updated_ver.metadata
                else:
                    updated_meta = version.metadata.model_copy(
                        update={
                            "lifecycle_state": target_state,
                            "is_immutable": new_is_immutable,
                        }
                    )
                    self._versions[version_id] = version.model_copy(
                        update={"is_immutable": new_is_immutable, "metadata": updated_meta}
                    )
                    await self._invalidate_metadata_cache(document_id, version_id, tenant_id)
                    return updated_meta

    async def get_version(self, document_id: str, version_id: str, tenant_id: str = "default") -> DocumentMetadata:
        """Retrieve DocumentMetadata for a specific version.

        Args:
            document_id: Root document UUID.
            version_id: Specific version UUID.
            tenant_id: Tenant partition identifier.

        Returns:
            DocumentMetadata instance.

        Raises:
            DocumentLifecycleError: If version missing or document ID mismatch.
        """
        version = await self.get_version_object(version_id, document_id=document_id, tenant_id=tenant_id)
        return version.metadata

    async def get_version_object(
        self, version_id: str, document_id: str | None = None, tenant_id: str = "default"
    ) -> DocumentVersion:
        """Retrieve full DocumentVersion domain instance by version ID.

        Args:
            version_id: Version UUID.
            document_id: Optional document UUID for validation.
            tenant_id: Tenant partition identifier.

        Returns:
            DocumentVersion object.

        Raises:
            DocumentLifecycleError: If version_id does not exist or document ID mismatch.
        """
        cache_store = self._cache_store
        cache_key: str | None = None
        if cache_store is not None and document_id is not None:
            cache_key = self._metadata_cache_key(document_id, version_id, tenant_id)
            cached_version = await cache_store.get(cache_key)
            if cached_version is not None:
                return cast(DocumentVersion, cached_version)

        version = await self._get_version_internal(version_id, document_id=document_id, tenant_id=tenant_id)
        if version is None:
            raise DocumentLifecycleError(f"Document version ID '{version_id}' not found.")
        if document_id is not None and version.document_id != document_id:
            raise DocumentLifecycleError(
                f"Document ID mismatch for version '{version_id}': expected '{document_id}', got "
                f"'{version.document_id}'."
            )

        if cache_key is not None and cache_store is not None:
            await cache_store.set(cache_key, version, ttl_seconds=self.METADATA_CACHE_TTL_SECONDS)

        return version

    async def _get_version_internal(
        self, version_id: str, document_id: str | None = None, tenant_id: str = "default"
    ) -> DocumentVersion | None:
        """Internal lookup for DocumentVersion without throwing if not found."""
        if self._repository is not None:
            if document_id is not None:
                return await self._repository.get_version(document_id, version_id, tenant_id=tenant_id)
            else:
                # Search all versions in repository or query by version_id
                # In DocumentRepository, get_version requires document_id. If document_id is None,
                # we list versions for candidate documents or execute get_version if known.
                # DocumentLifecycleManager callers typically supply document_id.
                return None
        else:
            return self._versions.get(version_id)

    async def get_latest_version(self, document_id: str, tenant_id: str = "default") -> DocumentMetadata:
        """Retrieve the newest / most recently created version metadata for a document entity.

        Note: This returns the latest created version in the lineage chain (which may be in DRAFT or REVIEW state).
        To retrieve the currently active PUBLISHED version, inspect `Document.current_version_id`.

        Args:
            document_id: Root document UUID.
            tenant_id: Tenant partition identifier.

        Returns:
            DocumentMetadata of the latest created version.

        Raises:
            DocumentLifecycleError: If no versions exist for document_id.
        """
        if self._repository is not None:
            ver = await self._repository.get_latest_version(document_id, tenant_id=tenant_id)
            if ver is None:
                raise DocumentLifecycleError(f"No document versions found for document ID '{document_id}'.")
            return ver.metadata
        else:
            if document_id not in self._document_chains or not self._document_chains[document_id]:
                raise DocumentLifecycleError(f"No document versions found for document ID '{document_id}'.")
            latest_version_id = self._document_chains[document_id][-1]
            return self._versions[latest_version_id].metadata

    async def get_lineage(self, document_id: str, tenant_id: str = "default") -> list[DocumentMetadata]:
        """Retrieve full version lineage chain for a document entity in creation order.

        Args:
            document_id: Root document UUID.
            tenant_id: Tenant partition identifier.

        Returns:
            Ordered list of DocumentMetadata objects from genesis root to latest version.

        Raises:
            DocumentLifecycleError: If document_id is not found or has no versions.
        """
        if self._repository is not None:
            versions = await self._repository.list_versions(document_id, tenant_id=tenant_id)
            if not versions:
                raise DocumentLifecycleError(f"No document lineage found for document ID '{document_id}'.")
            return [v.metadata for v in versions]
        else:
            if document_id not in self._document_chains or not self._document_chains[document_id]:
                raise DocumentLifecycleError(f"No document lineage found for document ID '{document_id}'.")
            return [self._versions[vid].metadata for vid in self._document_chains[document_id] if vid in self._versions]

    async def is_immutable(self, document_id: str, version_id: str, tenant_id: str = "default") -> bool:
        """Check whether a document version is locked against edits.

        Args:
            document_id: Root document UUID.
            version_id: Specific version UUID.
            tenant_id: Tenant partition identifier.

        Returns:
            True if version is in an immutable state (PUBLISHED, SUPERSEDED, ARCHIVED, LOGICAL_DELETE)
            or has is_immutable == True.
        """
        metadata = await self.get_version(document_id, version_id, tenant_id=tenant_id)
        return metadata.is_immutable or metadata.lifecycle_state in self.IMMUTABLE_STATES


__all__ = ["DocumentLifecycleManager"]
