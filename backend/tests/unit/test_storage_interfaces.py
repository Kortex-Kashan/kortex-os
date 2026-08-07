"""
Unit tests for KORTEX Storage Engine Interfaces & Data Models (Milestone 1).
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from kortex.engines.storage.interfaces import (
    ICacheStore,
    IDataStore,
    IEngineDiagnostics,
    IFileStore,
    IObjectStore,
)
from kortex.engines.storage.models import BucketConfig, FileMetadata, ObjectMetadata


def test_object_metadata_model_validation() -> None:
    """Test ObjectMetadata creation and default values."""
    meta = ObjectMetadata(
        storage_key="test_bucket/file.pdf",
        bucket_name="test_bucket",
        file_name="file.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    assert meta.storage_key == "test_bucket/file.pdf"
    assert meta.bucket_name == "test_bucket"
    assert meta.file_name == "file.pdf"
    assert meta.mime_type == "application/pdf"
    assert meta.file_size_bytes == 1024
    assert meta.sha256_hash == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert isinstance(meta.created_at, datetime)


def test_object_metadata_invalid_size() -> None:
    """Test ObjectMetadata validation error for negative size."""
    with pytest.raises(ValidationError):
        ObjectMetadata(
            storage_key="key",
            bucket_name="bucket",
            file_name="file.txt",
            file_size_bytes=-5,
            sha256_hash="hash",
        )


def test_file_metadata_model() -> None:
    """Test FileMetadata fields and timestamps."""
    meta = FileMetadata(
        relative_path="docs/readme.md",
        file_name="readme.md",
        mime_type="text/markdown",
        file_size_bytes=512,
        sha256_hash="abcd1234hash",
    )
    assert meta.relative_path == "docs/readme.md"
    assert meta.file_name == "readme.md"
    assert meta.file_size_bytes == 512
    assert isinstance(meta.created_at, datetime)
    assert isinstance(meta.modified_at, datetime)


def test_bucket_config_model() -> None:
    """Test BucketConfig model creation and defaults."""
    cfg = BucketConfig(
        bucket_name="invoices",
        provider_type="local_fs",
        base_path="storage_data/invoices",
    )
    assert cfg.bucket_name == "invoices"
    assert cfg.provider_type == "local_fs"
    assert cfg.base_path == "storage_data/invoices"
    assert cfg.options == {}
    assert cfg.id is not None


class DummyDataStore:
    async def get_session(self):
        yield None

    async def execute_in_transaction(self, action):
        return None


class DummyFileStore:
    async def read_file(self, relative_path: str) -> bytes:
        return b""

    async def write_file(self, relative_path: str, content: bytes) -> FileMetadata:
        return FileMetadata(
            relative_path=relative_path,
            file_name="f",
            file_size_bytes=0,
            sha256_hash="h",
        )

    async def delete_file(self, relative_path: str) -> bool:
        return True

    async def file_exists(self, relative_path: str) -> bool:
        return True

    async def list_files(self, relative_path: str = ""):
        return []

    async def get_metadata(self, relative_path: str):
        return FileMetadata(
            relative_path=relative_path,
            file_name="f",
            file_size_bytes=0,
            sha256_hash="h",
        )


class DummyObjectStore:
    async def put_object(self, bucket_name: str, object_key: str, data: bytes, mime_type: str = "application/octet-stream"):
        return ObjectMetadata(
            storage_key=object_key,
            bucket_name=bucket_name,
            file_name="f",
            file_size_bytes=len(data),
            sha256_hash="h",
        )

    async def get_object(self, bucket_name: str, object_key: str) -> bytes:
        return b""

    async def delete_object(self, bucket_name: str, object_key: str) -> bool:
        return True

    async def object_exists(self, bucket_name: str, object_key: str) -> bool:
        return True

    async def list_objects(self, bucket_name: str, prefix=None):
        return []


class DummyCacheStore:
    async def get(self, key: str):
        return None

    async def set(self, key: str, value: str, ttl_seconds=None):
        return True

    async def delete(self, key: str):
        return True

    async def clear(self):
        return True


class DummyEngineDiagnostics:
    def health(self):
        return {"status": "HEALTHY"}

    def metrics(self):
        return {}

    def diagnostics(self):
        return {}

    def status(self):
        return "READY"

    def version(self):
        return "1.0.0"

    def capabilities(self):
        return []


def test_interface_runtime_checks() -> None:
    """Verify runtime_checkable isinstance verification for protocols."""
    assert isinstance(DummyDataStore(), IDataStore)
    assert isinstance(DummyFileStore(), IFileStore)
    assert isinstance(DummyObjectStore(), IObjectStore)
    assert isinstance(DummyCacheStore(), ICacheStore)
    assert isinstance(DummyEngineDiagnostics(), IEngineDiagnostics)
