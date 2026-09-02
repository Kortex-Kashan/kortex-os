"""
KORTEX Storage Engine Path Sandbox Security Guard.

Enforces canonical path resolution and sandbox directory isolation to prevent
directory traversal attacks (`../`, symlink bypasses, absolute path escapes).
"""

from __future__ import annotations

import logging
from pathlib import Path

from kortex.core.exceptions import KortexError

logger = logging.getLogger("kortex.engines.storage.sandbox")


class PathSandboxError(KortexError):
    """Raised when a path access attempt violates sandbox boundaries."""


class PathSandboxValidator:
    """Validates and resolves file paths within a strict base directory sandbox."""

    def __init__(self, base_directory: str | Path) -> None:
        """Initialize the path sandbox with an absolute base directory path.

        Args:
            base_directory: Absolute or relative base directory for the sandbox.
        """
        self._base_dir = Path(base_directory).resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("Initialized PathSandboxValidator with base dir: %s", self._base_dir)

    @property
    def base_directory(self) -> Path:
        """Return the resolved absolute base directory of the sandbox."""
        return self._base_dir

    def resolve_sandboxed_path(self, relative_or_absolute_path: str | Path) -> Path:
        """Resolve a target path and verify it remains strictly within the base directory.

        Args:
            relative_or_absolute_path: The target file or directory path.

        Returns:
            Resolved absolute Path object guaranteed to reside within base_directory.

        Raises:
            PathSandboxError: If the path attempts to escape the base directory (e.g. '../').
        """
        raw_path = Path(relative_or_absolute_path)

        # If relative, anchor to base_directory
        target_path = (self._base_dir / raw_path).resolve() if not raw_path.is_absolute() else raw_path.resolve()

        # Check path containment
        try:
            target_path.relative_to(self._base_dir)
        except ValueError:
            logger.warning(
                "Path sandbox violation attempt blocked: '%s' (Base dir: '%s')",
                relative_or_absolute_path,
                self._base_dir,
            )
            # `from None`: the internal `relative_to` ValueError carries no
            # diagnostic value and this is a security-boundary rejection.
            raise PathSandboxError(
                f"Security violation: Access path '{relative_or_absolute_path}' escapes sandbox base directory."
            ) from None

        return target_path

    def get_relative_string(self, full_path: str | Path) -> str:
        """Convert a full path inside the sandbox into a canonical relative string format (POSIX style).

        Args:
            full_path: Target path inside sandbox.

        Returns:
            Relative POSIX path string (using forward slashes).
        """
        resolved = self.resolve_sandboxed_path(full_path)
        rel_path = resolved.relative_to(self._base_dir)
        return rel_path.as_posix()
