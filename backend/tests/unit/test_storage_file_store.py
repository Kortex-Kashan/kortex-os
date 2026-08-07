"""
Unit tests for KORTEX LocalFileStore (Milestone 4).
"""

from __future__ import annotations

import pytest

from kortex.core.exceptions import ResourceNotFoundError
from kortex.engines.storage.interfaces import IFileStore
from kortex.engines.storage.sandbox import PathSandboxError
from kortex.engines.storage.stores.file_store import LocalFileStore


@pytest.mark.asyncio
async def test_file_store_protocol_compliance(tmp_path) -> None:
    """Test LocalFileStore satisfies IFileStore protocol."""
    store = LocalFileStore(tmp_path)
    assert isinstance(store, IFileStore)


@pytest.mark.asyncio
async def test_write_and_read_file(tmp_path) -> None:
    """Test writing and reading files in LocalFileStore."""
    store = LocalFileStore(tmp_path)
    payload = b"Hello, KORTEX Storage Engine!"

    meta = await store.write_file("documents/hello.txt", payload)
    assert meta.relative_path == "documents/hello.txt"
    assert meta.file_name == "hello.txt"
    assert meta.file_size_bytes == len(payload)
    assert meta.mime_type == "text/plain"
    assert len(meta.sha256_hash) == 64

    content = await store.read_file("documents/hello.txt")
    assert content == payload


@pytest.mark.asyncio
async def test_file_exists_and_delete(tmp_path) -> None:
    """Test file_exists and delete_file methods."""
    store = LocalFileStore(tmp_path)
    path = "reports/monthly.pdf"

    assert not await store.file_exists(path)
    await store.write_file(path, b"%PDF-dummy")
    assert await store.file_exists(path)

    deleted = await store.delete_file(path)
    assert deleted
    assert not await store.file_exists(path)

    # Deleting non-existent file returns False
    assert not await store.delete_file(path)


@pytest.mark.asyncio
async def test_list_files(tmp_path) -> None:
    """Test listing files in a sandboxed folder."""
    store = LocalFileStore(tmp_path)
    await store.write_file("folder_a/file1.txt", b"a1")
    await store.write_file("folder_a/file2.txt", b"a2")
    await store.write_file("folder_b/file3.txt", b"b1")

    all_files = await store.list_files()
    assert len(all_files) == 3

    folder_a_files = await store.list_files("folder_a")
    assert len(folder_a_files) == 2
    paths = {f.relative_path for f in folder_a_files}
    assert "folder_a/file1.txt" in paths
    assert "folder_a/file2.txt" in paths


@pytest.mark.asyncio
async def test_read_missing_file_raises_error(tmp_path) -> None:
    """Test reading missing file raises ResourceNotFoundError."""
    store = LocalFileStore(tmp_path)
    with pytest.raises(ResourceNotFoundError):
        await store.read_file("non_existent.txt")


@pytest.mark.asyncio
async def test_path_traversal_prevention_in_file_store(tmp_path) -> None:
    """Test path traversal attempts raise PathSandboxError."""
    store = LocalFileStore(tmp_path)
    with pytest.raises(PathSandboxError):
        await store.write_file("../malicious.sh", b"echo hack")
