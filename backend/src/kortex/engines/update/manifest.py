"""KORTEX Update Engine manifest parsing, validation, and schema enforcement.

Phase 7 — Production Hardening — Update Engine.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from kortex.engines.update.crypto import parse_json_safe
from kortex.engines.update.exceptions import UpdateManifestError
from kortex.engines.update.models import UpdateManifest

SUPPORTED_MANIFEST_SCHEMA_VERSIONS: frozenset[str] = frozenset({"kortex-update-manifest-v1.0"})


class UpdateManifestParser:
    """Validator and parser for KORTEX update manifests."""

    @classmethod
    def parse_dict(cls, data: dict[str, Any]) -> UpdateManifest:
        """Parse and validate a dictionary into an UpdateManifest model."""
        if not isinstance(data, dict):
            raise UpdateManifestError(f"Manifest data must be a dictionary, got {type(data).__name__}")

        schema_version = data.get("manifest_version")
        if schema_version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
            raise UpdateManifestError(
                f"Unsupported manifest schema version: '{schema_version}'. "
                f"Supported: {sorted(SUPPORTED_MANIFEST_SCHEMA_VERSIONS)}"
            )

        try:
            manifest = UpdateManifest.model_validate(data)
        except ValidationError as exc:
            raise UpdateManifestError(f"Manifest schema validation failed: {exc}") from exc

        # Expiration check
        try:
            # Handle ISO formats with or without trailing Z / offset
            expires_at_str = manifest.expires_at.replace("Z", "+00:00")
            expires_at = datetime.datetime.fromisoformat(expires_at_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=datetime.UTC)
            now_utc = datetime.datetime.now(datetime.UTC)
            if expires_at < now_utc:
                raise UpdateManifestError(
                    f"Update manifest '{manifest.manifest_id}' expired at {manifest.expires_at} "
                    f"(current: {now_utc.isoformat()})"
                )
        except (ValueError, TypeError) as exc:
            raise UpdateManifestError(f"Invalid manifest expires_at format '{manifest.expires_at}': {exc}") from exc

        return manifest

    @classmethod
    def parse_raw(cls, raw_json: str | bytes) -> UpdateManifest:
        """Parse raw JSON string or bytes with duplicate-key protection into an UpdateManifest."""
        data = parse_json_safe(raw_json)
        return cls.parse_dict(data)

    @classmethod
    def parse_file(cls, path: Path | str) -> UpdateManifest:
        """Parse manifest file from disk."""
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Manifest file not found: {file_path}")
        raw = file_path.read_bytes()
        return cls.parse_raw(raw)
