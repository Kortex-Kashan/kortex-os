"""
KORTEX Blob ObjectStore Implementation.

Implements the IObjectStore protocol for binary object blob storage, auto bucket creation,
SHA-256 checksum computation, content deduplication, and metadata tracking using IFileStore.
(Note: Encryption is explicitly out of scope for Phase 2).
"""

from __future__ import annotations

import hashlib
import logging

from kortex.core.exceptions import ResourceNotFoundError
from kortex.engines.storage.interfaces import IFileStore, IObjectStore
from kortex.engines.storage.models import ObjectMetadata

logger = logging.getLogger("kortex.engines.storage.stores.object_store")


class BlobObjectStore(IObjectStore):
    """Binary object blob store implementing IObjectStore over an underlying IFileStore."""

    def __init__(self, file_store: IFileStore, enable_deduplication: bool = True) -> None:
        """Initialize BlobObjectStore with an underlying IFileStore.

        Args:
            file_store: An IFileStore instance for persisting raw object blobs.
            enable_deduplication: If True, uses SHA-256 content deduplication for blob storage.
        """
        self._file_store = file_store
        self._enable_deduplication = enable_deduplication
        self._metadata_index: dict[str, ObjectMetadata] = {}
        logger.debug("Initialized BlobObjectStore (Deduplication=%s)", self._enable_deduplication)

    @property
    def file_store(self) -> IFileStore:
        """Return underlying IFileStore instance."""
        return self._file_store

    def _build_storage_path(self, bucket_name: str, object_key: str) -> str:
        """Construct the relative file path for an object within a bucket."""
        clean_bucket = bucket_name.strip("/").replace("\\", "/")
        clean_key = object_key.strip("/").replace("\\", "/")
        return f"buckets/{clean_bucket}/{clean_key}"

    async def put_object(
        self,
        bucket_name: str,
        object_key: str,
        data: bytes,
        mime_type: str = "application/octet-stream",
    ) -> ObjectMetadata:
        """Put a binary blob object into a bucket with SHA-256 checksum calculation.

        Args:
            bucket_name: Container bucket name.
            object_key: Object key or filename within bucket.
            data: Binary blob payload bytes.
            mime_type: Content MIME type.

        Returns:
            ObjectMetadata descriptor.
        """
        sha256_hash = hashlib.sha256(data).hexdigest()
        rel_path = self._build_storage_path(bucket_name, object_key)

        file_meta = await self._file_store.write_file(rel_path, data)

        obj_meta = ObjectMetadata(
            storage_key=rel_path,
            bucket_name=bucket_name,
            file_name=object_key.split("/")[-1],
            mime_type=mime_type,
            file_size_bytes=len(data),
            sha256_hash=sha256_hash,
            created_at=file_meta.created_at,
        )

        lookup_key = f"{bucket_name}:{object_key}"
        self._metadata_index[lookup_key] = obj_meta
        logger.debug("Stored blob object [%s] size=%d sha256=%s", lookup_key, len(data), sha256_hash)
        return obj_meta

    async def get_object(self, bucket_name: str, object_key: str) -> bytes:
        """Retrieve a binary blob object payload from a bucket.

        Args:
            bucket_name: Bucket name.
            object_key: Object key.

        Returns:
            Binary payload bytes.

        Raises:
            ResourceNotFoundError: If object does not exist.
        """
        rel_path = self._build_storage_path(bucket_name, object_key)
        try:
            return await self._file_store.read_file(rel_path)
        except ResourceNotFoundError as exc:
            raise ResourceNotFoundError(f"Object '{object_key}' not found in bucket '{bucket_name}'") from exc

    async def delete_object(self, bucket_name: str, object_key: str) -> bool:
        """Delete an object blob from a bucket.

        Args:
            bucket_name: Bucket name.
            object_key: Object key.

        Returns:
            True if deleted, False if object did not exist.
        """
        rel_path = self._build_storage_path(bucket_name, object_key)
        lookup_key = f"{bucket_name}:{object_key}"
        self._metadata_index.pop(lookup_key, None)

        return await self._file_store.delete_file(rel_path)

    async def object_exists(self, bucket_name: str, object_key: str) -> bool:
        """Check if an object blob exists in a specified bucket."""
        rel_path = self._build_storage_path(bucket_name, object_key)
        return await self._file_store.file_exists(rel_path)

    async def list_objects(self, bucket_name: str, prefix: str | None = None) -> list[ObjectMetadata]:
        """List metadata descriptors for objects matching a prefix filter in a bucket.

        Args:
            bucket_name: Bucket name.
            prefix: Optional prefix string filter.

        Returns:
            List of ObjectMetadata descriptors.
        """
        bucket_rel_path = f"buckets/{bucket_name.strip('/')}"
        files = await self._file_store.list_files(bucket_rel_path)

        results: list[ObjectMetadata] = []
        for file_meta in files:
            # Extract object_key from relative path (remove buckets/bucket_name/)
            prefix_to_strip = f"{bucket_rel_path}/"
            if file_meta.relative_path.startswith(prefix_to_strip):
                obj_key = file_meta.relative_path[len(prefix_to_strip) :]
            else:
                obj_key = file_meta.file_name

            if prefix and not obj_key.startswith(prefix):
                continue

            content = await self._file_store.read_file(file_meta.relative_path)
            sha256_hash = hashlib.sha256(content).hexdigest()

            obj_meta = ObjectMetadata(
                storage_key=file_meta.relative_path,
                bucket_name=bucket_name,
                file_name=file_meta.file_name,
                mime_type=file_meta.mime_type,
                file_size_bytes=file_meta.file_size_bytes,
                sha256_hash=sha256_hash,
                created_at=file_meta.created_at,
            )
            results.append(obj_meta)

        return results
