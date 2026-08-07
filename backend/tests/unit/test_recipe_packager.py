"""
Unit tests for KORTEX Recipe Engine Packager (.kortex-recipe).
"""

import pytest
from kortex.engines.recipe.models import RecipeManifest
from kortex.engines.recipe.packager import RecipePackager


def test_packager_create_and_verify() -> None:
    packager = RecipePackager()
    manifest = RecipeManifest(
        id="rec-pkg-01",
        name="Packaged Recipe",
        namespace="kortex.pkg.test",
        version="1.0.0",
        checksum="123",
    )

    files = {
        "manifest.yaml": b"id: rec-pkg-01\n...",
        "recipe.yaml": b"steps: []",
    }

    pkg = packager.create_package(files, manifest, signature="sig_test_123")
    assert pkg.package_id == "rec-pkg-01"
    assert pkg.file_name == "rec-pkg-01-1.0.0.kortex-recipe"
    assert len(pkg.payload_bytes) > 0
    assert pkg.signature == "sig_test_123"

    assert packager.verify_checksum(pkg) is True

    # Tamper with payload
    tampered_pkg = pkg.model_copy(update={"payload_bytes": b"corrupted bytes"})
    assert packager.verify_checksum(tampered_pkg) is False
