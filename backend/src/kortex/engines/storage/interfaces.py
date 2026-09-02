"""
KORTEX Storage Engine Abstract Interfaces & Protocols.

Defines the four core storage store protocols (IDataStore, IFileStore, IObjectStore, ICacheStore)
and the common diagnostics interface protocol (IEngineDiagnostics).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.storage.models import FileMetadata, ObjectMetadata


@runtime_checkable
class IDataStore(Protocol):
    """Relational transactional database persistence abstraction interface."""

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Acquire an asynchronous SQLAlchemy session for database operations."""
        ...

    async def execute_in_transaction(self, action: Callable[[AsyncSession], Any]) -> Any:
        """Execute a callable block within an isolated database transaction block."""
        ...


@runtime_checkable
class IFileStore(Protocol):
    """Sandboxed file system storage abstraction interface."""

    async def read_file(self, relative_path: str) -> bytes:
        """Read file contents as binary bytes from sandboxed file storage."""
        ...

    async def write_file(self, relative_path: str, content: bytes) -> FileMetadata:
        """Write binary bytes to a file within sandboxed storage and return file metadata."""
        ...

    async def delete_file(self, relative_path: str) -> bool:
        """Delete a file from sandboxed storage. Returns True if deleted, False if not found."""
        ...

    async def file_exists(self, relative_path: str) -> bool:
        """Check if a file exists within sandboxed storage."""
        ...

    async def list_files(self, relative_path: str = "") -> list[FileMetadata]:
        """List metadata for files contained within a relative folder path."""
        ...

    async def get_metadata(self, relative_path: str) -> FileMetadata:
        """Retrieve FileMetadata for a specific file path."""
        ...


@runtime_checkable
class IObjectStore(Protocol):
    """Binary object blob storage abstraction interface."""

    async def put_object(
        self,
        bucket_name: str,
        object_key: str,
        data: bytes,
        mime_type: str = "application/octet-stream",
    ) -> ObjectMetadata:
        """Put a binary blob object into a bucket with SHA-256 checksum calculation."""
        ...

    async def get_object(self, bucket_name: str, object_key: str) -> bytes:
        """Retrieve a binary blob object payload from a bucket."""
        ...

    async def delete_object(self, bucket_name: str, object_key: str) -> bool:
        """Delete an object blob from a bucket. Returns True if deleted, False if not found."""
        ...

    async def object_exists(self, bucket_name: str, object_key: str) -> bool:
        """Check if an object blob exists in a specified bucket."""
        ...

    async def list_objects(self, bucket_name: str, prefix: str | None = None) -> list[ObjectMetadata]:
        """List metadata descriptors for objects matching a prefix filter in a bucket."""
        ...


@runtime_checkable
class ICacheStore(Protocol):
    """Ephemeral in-memory key-value caching abstraction interface."""

    async def get(self, key: str) -> Any | None:
        """Retrieve a cached value by key. Returns None if key is missing or expired."""
        ...

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        """Store a value in cache with an optional Time-To-Live (TTL) in seconds."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete a key from the cache. Returns True if removed, False if missing."""
        ...

    async def clear(self) -> bool:
        """Clear all cached key-value entries from storage."""
        ...


@runtime_checkable
class IEngineDiagnostics(Protocol):
    """Standardized diagnostics interface exposed by all KORTEX System Engines."""

    def health(self) -> dict[str, Any]:
        """Return operational health status and diagnostic checks."""
        ...

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and throughput metrics."""
        ...

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics and system environment details."""
        ...

    def status(self) -> str:
        """Return current engine state name string."""
        ...

    def version(self) -> str:
        """Return semantic version string of the engine."""
        ...

    def capabilities(self) -> list[str]:
        """Return list of capability strings registered by the engine."""
        ...
