"""Security Integration and Storage Engine Bindings for KORTEX OS Document Engine.

This module implements DocumentSecurityVerifier, DefaultVerificationService, IVerificationService,
and DocumentStorageBinder in accordance with Section 18, Section 19, and Milestone 7 of the
Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Callable, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.document.exceptions import DocumentSecurityError
from kortex.engines.document.models import (
    AdapterCapability,
    SecurityClassification,
)
from kortex.engines.storage.interfaces import (
    ICacheStore,
    IDataStore,
    IFileStore,
    IObjectStore,
)
from kortex.engines.storage.models import FileMetadata, ObjectMetadata


@runtime_checkable
class IVerificationService(Protocol):
    """Interface for document SHA256 cryptographic hash generation and verification."""

    async def compute_hash(self, data: bytes) -> str:
        """Compute SHA256 cryptographic hash string for payload data."""
        ...

    async def verify_hash(self, data: bytes, expected_hash: str) -> bool:
        """Verify binary data payload against expected SHA256 hash."""
        ...

    async def sign_document(self, data: bytes, secret_key: str | None = None) -> str:
        """Generate HMAC-SHA256 signature for document payload."""
        ...

    async def verify_signature(
        self, data: bytes, signature: str, secret_key: str | None = None
    ) -> bool:
        """Verify HMAC-SHA256 signature for document payload."""
        ...


class DefaultVerificationService(IVerificationService):
    """Default implementation of IVerificationService using SHA256 and HMAC."""

    def __init__(self, default_secret_key: str = "kortex_default_verification_key") -> None:
        self._secret_key = default_secret_key

    async def compute_hash(self, data: bytes) -> str:
        """Compute SHA256 cryptographic hash string for payload data."""
        if data is None:
            raise DocumentSecurityError("Cannot compute hash for None data payload.")
        return hashlib.sha256(data).hexdigest()

    async def verify_hash(self, data: bytes, expected_hash: str) -> bool:
        """Verify binary data payload against expected SHA256 hash."""
        if data is None or not expected_hash:
            return False
        computed = await self.compute_hash(data)
        return hmac.compare_digest(computed.lower(), expected_hash.strip().lower())

    async def sign_document(self, data: bytes, secret_key: str | None = None) -> str:
        """Generate HMAC-SHA256 signature for document payload."""
        if data is None:
            raise DocumentSecurityError("Cannot sign None data payload.")
        key = (secret_key or self._secret_key).encode("utf-8")
        return hmac.new(key, data, hashlib.sha256).hexdigest()

    async def verify_signature(
        self, data: bytes, signature: str, secret_key: str | None = None
    ) -> bool:
        """Verify HMAC-SHA256 signature for document payload."""
        if data is None or not signature:
            return False
        computed = await self.sign_document(data, secret_key=secret_key)
        return hmac.compare_digest(computed.lower(), signature.strip().lower())


class DocumentSecurityVerifier:
    """Verifier for document security classification labels, hash verification, and capability permissions."""

    def __init__(self, verification_service: IVerificationService | None = None) -> None:
        """Initialize DocumentSecurityVerifier with an optional IVerificationService."""
        self._verification_service = (
            verification_service if verification_service is not None else DefaultVerificationService()
        )

    @property
    def verification_service(self) -> IVerificationService:
        """Return configured IVerificationService instance."""
        return self._verification_service

    def validate_security_classification(
        self, classification: SecurityClassification | str
    ) -> SecurityClassification:
        """Validate and resolve SecurityClassification level.

        Args:
            classification: SecurityClassification enum or string.

        Returns:
            Resolved SecurityClassification enum.

        Raises:
            DocumentSecurityError: If classification is invalid.
        """
        if isinstance(classification, SecurityClassification):
            return classification
        try:
            return SecurityClassification(str(classification).upper().strip())
        except ValueError as err:
            raise DocumentSecurityError(
                f"Invalid security classification level: '{classification}'."
            ) from err

    async def verify_document_integrity(self, data: bytes, expected_hash: str) -> bool:
        """Verify document integrity using SHA256 hash comparison.

        Args:
            data: Binary payload data.
            expected_hash: Expected SHA256 hex string.

        Returns:
            True if hash matches; False otherwise.
        """
        return await self._verification_service.verify_hash(data, expected_hash)

    def enforce_capability_permission(
        self, capability: AdapterCapability, granted_permissions: list[str]
    ) -> bool:
        """Check whether capability is authorized under granted permission keys.

        Args:
            capability: Requested AdapterCapability enum.
            granted_permissions: List of granted permission strings.

        Returns:
            True if authorized; False otherwise.
        """
        if not granted_permissions:
            return False
        cap_clean = capability.value.lower()
        for perm in granted_permissions:
            perm_clean = perm.lower()
            if perm_clean in (cap_clean, "*", "document:admin", f"document:{cap_clean}"):
                return True
        return False


class DocumentStorageBinder:
    """Storage Engine integration binder managing bindings across IDataStore, IFileStore, IObjectStore, ICacheStore."""

    def __init__(
        self,
        data_store: IDataStore | None = None,
        file_store: IFileStore | None = None,
        object_store: IObjectStore | None = None,
        cache_store: ICacheStore | None = None,
    ) -> None:
        """Initialize DocumentStorageBinder with optional Storage Engine store instances."""
        self._data_store = data_store
        self._file_store = file_store
        self._object_store = object_store
        self._cache_store = cache_store

    @property
    def data_store(self) -> IDataStore | None:
        """Return configured IDataStore."""
        return self._data_store

    @property
    def file_store(self) -> IFileStore | None:
        """Return configured IFileStore."""
        return self._file_store

    @property
    def object_store(self) -> IObjectStore | None:
        """Return configured IObjectStore."""
        return self._object_store

    @property
    def cache_store(self) -> ICacheStore | None:
        """Return configured ICacheStore."""
        return self._cache_store

    # Template file bindings (IFileStore)
    async def save_template_schema(
        self, relative_path: str, schema_bytes: bytes
    ) -> FileMetadata | None:
        """Write declarative template schema file to sandboxed file storage."""
        if self._file_store is None:
            return None
        return await self._file_store.write_file(relative_path, schema_bytes)

    async def load_template_schema(self, relative_path: str) -> bytes | None:
        """Read declarative template schema file from sandboxed file storage."""
        if self._file_store is None:
            return None
        if not await self._file_store.file_exists(relative_path):
            return None
        return await self._file_store.read_file(relative_path)

    # Object storage bindings (IObjectStore)
    async def store_document_output(
        self,
        bucket_name: str,
        object_key: str,
        data: bytes,
        mime_type: str = "application/pdf",
    ) -> ObjectMetadata | None:
        """Store immutable document output binary blob in object store."""
        if self._object_store is None:
            return None
        return await self._object_store.put_object(
            bucket_name, object_key, data, mime_type=mime_type
        )

    async def retrieve_document_output(self, bucket_name: str, object_key: str) -> bytes | None:
        """Retrieve document output binary blob from object store."""
        if self._object_store is None:
            return None
        if not await self._object_store.object_exists(bucket_name, object_key):
            return None
        return await self._object_store.get_object(bucket_name, object_key)

    # Multi-level caching (ICacheStore)
    async def cache_get(self, cache_category: str, key: str, tenant_id: str = "default") -> Any | None:
        """Retrieve value from multi-level cache store, scoped to tenant_id."""
        if self._cache_store is None:
            return None
        composite_key = f"doc_engine:{tenant_id}:{cache_category}:{key}"
        return await self._cache_store.get(composite_key)

    async def cache_set(
        self,
        cache_category: str,
        key: str,
        value: Any,
        ttl_seconds: int | None = 300,
        tenant_id: str = "default",
    ) -> bool:
        """Store value in multi-level cache store with optional TTL, scoped to tenant_id."""
        if self._cache_store is None:
            return False
        composite_key = f"doc_engine:{tenant_id}:{cache_category}:{key}"
        return await self._cache_store.set(composite_key, value, ttl_seconds=ttl_seconds)

    # Relational persistence (IDataStore)
    async def persist_transactional(self, action: Callable[[AsyncSession], Any]) -> Any | None:
        """Execute transactional database action against IDataStore."""
        if self._data_store is None:
            return None
        return await self._data_store.execute_in_transaction(action)


__all__ = [
    "DefaultVerificationService",
    "DocumentSecurityVerifier",
    "DocumentStorageBinder",
    "IVerificationService",
]
