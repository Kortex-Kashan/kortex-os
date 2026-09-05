"""Unit tests for Update Engine compatibility, SemVer ordering, and environment gates.

Phase 7 — Production Hardening — Update Engine.
Verifies strict semantic versioning, downgrade prevention, upgrade hops, platform/architecture matching,
and Python runtime version checks.
"""

from __future__ import annotations

import pytest

from kortex.engines.update.compatibility import CompatibilityEvaluator, parse_semver
from kortex.engines.update.exceptions import (
    UpdateCompatibilityError,
    UpdateDowngradeError,
    UpdatePlatformMismatchError,
)
from kortex.engines.update.models import (
    UpdateManifest,
    UpdateManifestCompatibility,
    UpdateManifestDatabase,
    UpdateManifestPackage,
    UpdateManifestVersion,
)


def create_manifest(
    target_version: str = "0.2.0",
    min_supported_version: str = "0.1.0",
    platforms: list[str] | None = None,
    architectures: list[str] | None = None,
    python_version_min: str = "3.11",
) -> UpdateManifest:
    """Helper to build an UpdateManifest for compatibility tests."""
    return UpdateManifest(
        manifest_id="mf-compat-test",
        created_at="2026-09-05T00:00:00Z",
        expires_at="2026-09-12T00:00:00Z",
        key_id="k1",
        signature="s1",
        version=UpdateManifestVersion(
            target_version=target_version,
            min_supported_version=min_supported_version,
            release_channel="stable",
        ),
        compatibility=UpdateManifestCompatibility(
            platforms=platforms or ["windows", "linux", "darwin", "win32"],
            architectures=architectures or ["x86_64", "amd64", "arm64", "aarch64"],
            python_version_min=python_version_min,
        ),
        package=UpdateManifestPackage(
            filename="upd.zip",
            sha256="hash",
            size_bytes=100,
            uncompressed_bytes=200,
            file_count=1,
        ),
        database=UpdateManifestDatabase(requires_migration=False),
    )


def test_semver_parsing_and_ordering() -> None:
    """Verify semantic version parsing and ordering rules."""
    v1 = parse_semver("0.1.0")
    v2 = parse_semver("0.2.0")
    v10 = parse_semver("1.0.0")
    v_pre = parse_semver("0.2.0-alpha.1")

    assert v1 < v2
    assert v2 < v10
    assert v1 != v2
    assert v1 == parse_semver("0.1.0")
    assert v_pre < v2  # prerelease is lower precedence than release

    with pytest.raises(UpdateCompatibilityError):
        parse_semver("not-a-valid-semver")


def test_valid_upgrade_passes() -> None:
    """Verify standard valid upgrade succeeds."""
    evaluator = CompatibilityEvaluator(current_version="0.1.0")
    manifest = create_manifest(target_version="0.2.0", min_supported_version="0.1.0")
    evaluator.evaluate_compatibility(manifest)  # Should not raise


def test_same_version_reapply_passes() -> None:
    """Verify same version (target == current) passes downgrade gate."""
    evaluator = CompatibilityEvaluator(current_version="0.1.0")
    manifest = create_manifest(target_version="0.1.0", min_supported_version="0.1.0")
    evaluator.evaluate_compatibility(manifest)


def test_downgrade_rejected() -> None:
    """Verify downgrade attempt raises UpdateDowngradeError."""
    evaluator = CompatibilityEvaluator(current_version="0.3.0")
    manifest = create_manifest(target_version="0.2.0", min_supported_version="0.1.0")

    with pytest.raises(UpdateDowngradeError) as exc_info:
        evaluator.evaluate_compatibility(manifest)
    assert "Cannot downgrade" in str(exc_info.value)


def test_intermediate_hop_required() -> None:
    """Verify upgrade fails if current version is below manifest min_supported_version."""
    evaluator = CompatibilityEvaluator(current_version="0.1.0")
    manifest = create_manifest(target_version="0.5.0", min_supported_version="0.3.0")

    with pytest.raises(UpdateCompatibilityError) as exc_info:
        evaluator.evaluate_compatibility(manifest)
    assert "Intermediate upgrade required" in str(exc_info.value)


def test_unsupported_platform_rejected() -> None:
    """Verify platform mismatch raises UpdatePlatformMismatchError."""
    evaluator = CompatibilityEvaluator(current_version="0.1.0")
    manifest = create_manifest(
        target_version="0.2.0",
        platforms=["fictional-os"],
    )

    with pytest.raises(UpdatePlatformMismatchError) as exc_info:
        evaluator.evaluate_compatibility(manifest)
    assert "platform not supported" in str(exc_info.value).lower()


def test_unsupported_architecture_rejected() -> None:
    """Verify architecture mismatch raises UpdatePlatformMismatchError."""
    evaluator = CompatibilityEvaluator(current_version="0.1.0")
    manifest = create_manifest(
        target_version="0.2.0",
        architectures=["riscv64", "sparc"],
    )

    with pytest.raises(UpdatePlatformMismatchError) as exc_info:
        evaluator.evaluate_compatibility(manifest)
    assert "architecture not supported" in str(exc_info.value).lower()


def test_unsupported_python_version_rejected() -> None:
    """Verify Python runtime version below minimum raises UpdateCompatibilityError."""
    evaluator = CompatibilityEvaluator(current_version="0.1.0")
    # Require an impossibly high Python version e.g. 3.99
    manifest = create_manifest(
        target_version="0.2.0",
        python_version_min="3.99",
    )

    with pytest.raises(UpdateCompatibilityError) as exc_info:
        evaluator.evaluate_compatibility(manifest)
    assert "below required minimum Python" in str(exc_info.value)
