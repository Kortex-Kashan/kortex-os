"""
KORTEX Storage Engine Pydantic Data Models.

Defines strongly typed metadata models for object store items, file store items,
and bucket configurations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ObjectMetadata(BaseModel):
    """Metadata representation for a stored binary object blob in IObjectStore."""

    storage_key: str = Field(..., description="Unique key or path of the object in storage")
    bucket_name: str = Field(..., description="Name of the container bucket")
    file_name: str = Field(..., description="Original filename of the stored object")
    mime_type: str = Field("application/octet-stream", description="MIME type of the binary payload")
    file_size_bytes: int = Field(..., ge=0, description="Size of the payload in bytes")
    sha256_hash: str = Field(..., description="SHA-256 hash checksum of the payload")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the object was stored",
    )


class FileMetadata(BaseModel):
    """Metadata representation for a file on the local sandboxed file system in IFileStore."""

    relative_path: str = Field(..., description="Canonical relative path within the sandboxed storage root")
    file_name: str = Field(..., description="Basename of the file")
    mime_type: str = Field("application/octet-stream", description="MIME content type")
    file_size_bytes: int = Field(..., ge=0, description="File size in bytes")
    sha256_hash: str = Field(..., description="SHA-256 checksum of file content")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp",
    )
    modified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last modified timestamp",
    )


class BucketConfig(BaseModel):
    """Configuration descriptor for an object storage bucket."""

    id: UUID = Field(default_factory=uuid4, description="Unique bucket ID")
    bucket_name: str = Field(..., description="Unique identifier name for the storage bucket")
    provider_type: str = Field("local_fs", description="Storage provider type (e.g., local_fs, s3, azure)")
    base_path: str = Field(..., description="Base directory or root path for bucket files")
    options: Dict[str, Any] = Field(default_factory=dict, description="Provider-specific configuration options")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the bucket configuration was created",
    )
