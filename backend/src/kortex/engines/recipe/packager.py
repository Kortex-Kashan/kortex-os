"""
KORTEX Recipe Engine Package Archiver.

Creates and verifies standalone `.kortex-recipe` binary ZIP archives with SHA256
payload checksum calculation and cryptographic signature placeholders.
"""

from __future__ import annotations

import hashlib
import io
from typing import Dict, Optional
import zipfile

from kortex.engines.recipe.exceptions import RecipePackageError
from kortex.engines.recipe.manifest import RecipeManifestManager
from kortex.engines.recipe.models import RecipeManifest, RecipePackage


class RecipePackager:
    """Archiver for producing and verifying .kortex-recipe packages."""

    def create_package(
        self,
        recipe_files: Dict[str, bytes],
        manifest: RecipeManifest,
        signature: Optional[str] = None,
    ) -> RecipePackage:
        """Compress recipe folder assets into a .kortex-recipe archive package.

        Args:
            recipe_files: Mapping of relative file paths to raw byte content.
            manifest: RecipeManifest model for payload metadata.
            signature: Optional digital signature string placeholder.

        Returns:
            RecipePackage containing binary ZIP payload and SHA256 checksum.

        Raises:
            RecipePackageError: If payload compression fails.
        """
        try:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for path, content in recipe_files.items():
                    zf.writestr(path, content)

            payload_bytes = buffer.getvalue()
            checksum = hashlib.sha256(payload_bytes).hexdigest()
            file_name = f"{manifest.id}-{manifest.version}.kortex-recipe"

            return RecipePackage(
                package_id=manifest.id,
                file_name=file_name,
                checksum=checksum,
                payload_bytes=payload_bytes,
                signature=signature,
            )
        except Exception as e:
            raise RecipePackageError(f"Failed to assemble .kortex-recipe package: {e}") from e

    def verify_checksum(self, package: RecipePackage) -> bool:
        """Verify that binary payload matches stored SHA256 checksum hash.

        Args:
            package: Target RecipePackage instance.

        Returns:
            True if payload checksum matches stored hash.
        """
        computed = hashlib.sha256(package.payload_bytes).hexdigest()
        return computed == package.checksum
