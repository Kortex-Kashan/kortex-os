"""
KORTEX Sandboxed Local FileStore Implementation.

Implements the IFileStore protocol for sandboxed local file system operations
(read, write, delete, exists, list, metadata) with automatic SHA-256 checksum computation
using Python standard library asyncio thread pools.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Union

from kortex.core.exceptions import ResourceNotFoundError
from kortex.engines.storage.interfaces import IFileStore
from kortex.engines.storage.models import FileMetadata
from kortex.engines.storage.sandbox import PathSandboxValidator

logger = logging.getLogger("kortex.engines.storage.stores.file_store")


class LocalFileStore(IFileStore):
    """Local sandboxed file system store implementing IFileStore."""

    def __init__(self, base_directory: Union[str, Path]) -> None:
        """Initialize LocalFileStore with a sandboxed base directory path.

        Args:
            base_directory: Root directory path for file storage sandbox.
        """
        self._sandbox = PathSandboxValidator(base_directory)
        logger.debug("Initialized LocalFileStore with base dir: %s", self._sandbox.base_directory)

    @property
    def sandbox(self) -> PathSandboxValidator:
        """Return the underlying PathSandboxValidator instance."""
        return self._sandbox

    async def read_file(self, relative_path: str) -> bytes:
        """Read file contents as binary bytes from sandboxed file storage.

        Args:
            relative_path: Relative file path within sandbox.

        Returns:
            Binary payload bytes.

        Raises:
            ResourceNotFoundError: If file does not exist.
        """
        target_path = self._sandbox.resolve_sandboxed_path(relative_path)
        if not target_path.is_file():
            raise ResourceNotFoundError(f"File not found in storage: '{relative_path}'")

        return await asyncio.to_thread(target_path.read_bytes)

    async def write_file(self, relative_path: str, content: bytes) -> FileMetadata:
        """Write binary bytes to a file within sandboxed storage and return file metadata.

        Args:
            relative_path: Relative target file path within sandbox.
            content: Binary bytes to write.

        Returns:
            FileMetadata descriptor.
        """
        target_path = self._sandbox.resolve_sandboxed_path(relative_path)

        def _write() -> None:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(content)

        await asyncio.to_thread(_write)
        logger.debug("Wrote %d bytes to file: '%s'", len(content), relative_path)
        return await self.get_metadata(relative_path)

    async def delete_file(self, relative_path: str) -> bool:
        """Delete a file from sandboxed storage.

        Args:
            relative_path: Relative file path.

        Returns:
            True if deleted, False if file did not exist.
        """
        target_path = self._sandbox.resolve_sandboxed_path(relative_path)
        if not target_path.exists():
            return False

        if target_path.is_file():
            await asyncio.to_thread(target_path.unlink)
            logger.debug("Deleted file from storage: '%s'", relative_path)
            return True
        return False

    async def file_exists(self, relative_path: str) -> bool:
        """Check if a file exists within sandboxed storage."""
        target_path = self._sandbox.resolve_sandboxed_path(relative_path)
        return await asyncio.to_thread(target_path.is_file)

    async def list_files(self, relative_path: str = "") -> List[FileMetadata]:
        """List metadata for files contained within a relative folder path.

        Args:
            relative_path: Relative folder path within sandbox.

        Returns:
            List of FileMetadata descriptors for files found.
        """
        target_dir = self._sandbox.resolve_sandboxed_path(relative_path)
        if not target_dir.exists() or not target_dir.is_dir():
            return []

        def _scan() -> List[Path]:
            return [p for p in target_dir.rglob("*") if p.is_file()]

        file_paths = await asyncio.to_thread(_scan)
        results: List[FileMetadata] = []
        for file_path in file_paths:
            rel_posix = self._sandbox.get_relative_string(file_path)
            meta = await self.get_metadata(rel_posix)
            results.append(meta)
        return results

    async def get_metadata(self, relative_path: str) -> FileMetadata:
        """Retrieve FileMetadata descriptor for a specific relative file path.

        Args:
            relative_path: Relative file path within sandbox.

        Returns:
            FileMetadata descriptor.

        Raises:
            ResourceNotFoundError: If file does not exist.
        """
        target_path = self._sandbox.resolve_sandboxed_path(relative_path)
        if not target_path.is_file():
            raise ResourceNotFoundError(f"File not found in storage: '{relative_path}'")

        stat_result = await asyncio.to_thread(os.stat, target_path)
        content = await self.read_file(relative_path)
        sha256_hash = hashlib.sha256(content).hexdigest()

        mime_type, _ = mimetypes.guess_type(target_path.name)
        if not mime_type:
            mime_type = "application/octet-stream"

        return FileMetadata(
            relative_path=self._sandbox.get_relative_string(target_path),
            file_name=target_path.name,
            mime_type=mime_type,
            file_size_bytes=stat_result.st_size,
            sha256_hash=sha256_hash,
            created_at=datetime.fromtimestamp(stat_result.st_ctime, tz=timezone.utc),
            modified_at=datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc),
        )
