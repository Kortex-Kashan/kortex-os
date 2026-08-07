"""
KORTEX Recipe Engine Manifest Manager.

Parses, validates, and manages `manifest.yaml` assets for Recipe Engine.
Enforces canonical namespace structures and SHA256 checksum hashing.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional, Union

from kortex.engines.recipe.exceptions import RecipeValidationError
from kortex.engines.recipe.models import RecipeManifest

NAMESPACE_REGEX = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")


class RecipeManifestManager:
    """Manages Recipe Manifest parsing, validation, and SHA256 checksum calculation."""

    @staticmethod
    def parse_manifest_dict(data: Dict[str, Any]) -> RecipeManifest:
        """Parse dictionary payload into a RecipeManifest instance.

        Args:
            data: Raw dictionary loaded from manifest.yaml.

        Returns:
            Validated RecipeManifest model.

        Raises:
            RecipeValidationError: If required fields or namespace format are invalid.
        """
        if "id" not in data:
            raise RecipeValidationError("Manifest missing mandatory field: 'id'")
        if "name" not in data:
            raise RecipeValidationError("Manifest missing mandatory field: 'name'")
        if "namespace" not in data:
            raise RecipeValidationError("Manifest missing mandatory field: 'namespace'")

        namespace = data["namespace"]
        if not NAMESPACE_REGEX.match(namespace):
            raise RecipeValidationError(
                f"Invalid manifest namespace format '{namespace}'. Must match canonical format (e.g. kortex.hr.payroll)."
            )

        data_copy = dict(data)
        if "checksum" in data_copy and not isinstance(data_copy["checksum"], str):
            data_copy["checksum"] = str(data_copy["checksum"])

        try:
            return RecipeManifest(**data_copy)
        except Exception as e:
            raise RecipeValidationError(f"Failed to validate RecipeManifest model: {e}") from e

    @staticmethod
    def parse_manifest_yaml(yaml_content: str) -> RecipeManifest:
        """Parse raw YAML manifest content string.

        Args:
            yaml_content: YAML text content of manifest.yaml.

        Returns:
            Validated RecipeManifest model.
        """
        import yaml
        try:
            parsed = yaml.safe_load(yaml_content)
        except Exception as e:
            raise RecipeValidationError(f"Failed to parse manifest YAML: {e}") from e

        if not isinstance(parsed, dict):
            raise RecipeValidationError("Manifest YAML content must root to a dictionary mapping.")

        return RecipeManifestManager.parse_manifest_dict(parsed)

    @staticmethod
    def calculate_checksum(manifest: Union[RecipeManifest, Dict[str, Any]]) -> str:
        """Compute deterministic SHA256 checksum for a manifest.

        Args:
            manifest: RecipeManifest model or dictionary instance.

        Returns:
            Hexadecimal SHA256 digest string.
        """
        if isinstance(manifest, RecipeManifest):
            data = manifest.model_dump(exclude={"checksum", "signature"})
        elif isinstance(manifest, dict):
            data = {k: v for k, v in manifest.items() if k not in ("checksum", "signature")}
        else:
            data = {}

        dumped = json.dumps(data, sort_keys=True)
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()
