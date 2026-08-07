"""
Unit tests for KORTEX BlobObjectStore (Milestone 5).
"""

from __future__ import annotations

import pytest

from kortex.core.exceptions import ResourceNotFoundError
from kortex.engines.storage.interfaces import IObjectStore
from kortex.engines.storage.stores.file_store import LocalFileStore
from kortex.engines.storage.stores.object_store import BlobObjectStore


@pytest.mark.asyncio
async def test_object_store_protocol_compliance(tmp_path) -> None:
    """Test BlobObjectStore satisfies IObjectStore protocol."""
    file_store = LocalFileStore(tmp_path)
    object_store = BlobObjectStore(file_store)
    assert isinstance(object_store, IObjectStore)


@pytest.mark.asyncio
async def test_put_and_get_object(tmp_path) -> None:
    """Test putting and getting blob objects."""
    file_store = LocalFileStore(tmp_path)
    object_store = BlobObjectStore(file_store)
    data = b"KORTEX Blob Payload PDF binary stream"

    meta = await object_store.put_object(
        bucket_name="reports",
        object_key="2026/annual_report.pdf",
        data=data,
        mime_type="application/pdf",
    )
    assert meta.bucket_name == "reports"
    assert meta.file_name == "annual_report.pdf"
    assert meta.file_size_bytes == len(data)
    assert meta.mime_type == "application/pdf"
    assert len(meta.sha256_hash) == 64

    retrieved = await object_store.get_object("reports", "2026/annual_report.pdf")
    assert retrieved == data


@pytest.mark.asyncio
async def test_object_exists_and_delete(tmp_path) -> None:
    """Test object_exists and delete_object methods."""
    file_store = LocalFileStore(tmp_path)
    object_store = BlobObjectStore(file_store)
    bucket = "avatars"
    key = "user_42.png"

    assert not await object_store.object_exists(bucket, key)
    await object_store.put_object(bucket, key, b"PNG_DATA", mime_type="image/png")
    assert await object_store.object_exists(bucket, key)

    deleted = await object_store.delete_object(bucket, key)
    assert deleted
    assert not await object_store.object_exists(bucket, key)
    assert not await object_store.delete_object(bucket, key)


@pytest.mark.asyncio
async def test_list_objects_with_prefix(tmp_path) -> None:
    """Test listing objects in a bucket with prefix filtering."""
    file_store = LocalFileStore(tmp_path)
    object_store = BlobObjectStore(file_store)
    bucket = "invoices"

    await object_store.put_object(bucket, "2026/inv_01.pdf", b"inv1")
    await object_store.put_object(bucket, "2026/inv_02.pdf", b"inv2")
    await object_store.put_object(bucket, "2025/inv_99.pdf", b"inv99")

    all_objs = await object_store.list_objects(bucket)
    assert len(all_objs) == 3

    filtered = await object_store.list_objects(bucket, prefix="2026/")
    assert len(filtered) == 2
    keys = {o.file_name for o in filtered}
    assert "inv_01.pdf" in keys
    assert "inv_02.pdf" in keys


@pytest.mark.asyncio
async def test_get_missing_object_raises_error(tmp_path) -> None:
    """Test retrieving non-existent object raises ResourceNotFoundError."""
    file_store = LocalFileStore(tmp_path)
    object_store = BlobObjectStore(file_store)
    with pytest.raises(ResourceNotFoundError, match="Object 'missing.bin' not found in bucket 'test'"):
        await object_store.get_object("test", "missing.bin")
