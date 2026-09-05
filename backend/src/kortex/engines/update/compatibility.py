"""KORTEX Update Engine compatibility, versioning, and environment evaluation.

Phase 7 — Production Hardening — Update Engine.
Enforces strict semantic versioning, downgrade rejection, and platform/architecture matching.
"""

from __future__ import annotations

import functools
import platform
import re
import sys

from kortex.engines.update.constants import CURRENT_ENGINE_VERSION
from kortex.engines.update.exceptions import (
    UpdateCompatibilityError,
    UpdateDowngradeError,
    UpdatePlatformMismatchError,
)
from kortex.engines.update.models import UpdateManifest

_SEMVER_REGEX = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


@functools.total_ordering
class SemVer:
    """Semantic version object supporting standard semver comparison ordering."""

    def __init__(self, major: int, minor: int, patch: int, prerelease: str = "") -> None:
        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = prerelease

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return False
        return (self.major, self.minor, self.patch, self.prerelease) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        if (self.major, self.minor, self.patch) != (other.major, other.minor, other.patch):
            return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)
        # Normal versions have higher precedence than prereleases
        if not self.prerelease and other.prerelease:
            return False
        if self.prerelease and not other.prerelease:
            return True
        return self.prerelease < other.prerelease

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            base += f"-{self.prerelease}"
        return base


def parse_semver(version_str: str) -> SemVer:
    """Parse a semantic version string into a SemVer tuple."""
    v_clean = version_str.strip().lstrip("v")
    match = _SEMVER_REGEX.match(v_clean)
    if not match:
        raise UpdateCompatibilityError(f"Invalid semantic version string: '{version_str}'")
    groups = match.groupdict()
    return SemVer(
        major=int(groups["major"]),
        minor=int(groups["minor"]),
        patch=int(groups["patch"]),
        prerelease=groups.get("prerelease") or "",
    )


class CompatibilityEvaluator:
    """Evaluates whether an update manifest is compatible with the running KORTEX system."""

    def __init__(self, current_version: str | None = None) -> None:
        self._current_version_str = current_version or CURRENT_ENGINE_VERSION
        self._current_semver = parse_semver(self._current_version_str)

    @property
    def current_version(self) -> str:
        return self._current_version_str

    def evaluate_compatibility(self, manifest: UpdateManifest) -> None:
        """Evaluate all compatibility gates against the current system.

        Raises UpdateCompatibilityError, UpdateDowngradeError, or UpdatePlatformMismatchError on failure.
        """
        target_semver = parse_semver(manifest.version.target_version)
        min_supported_semver = parse_semver(manifest.version.min_supported_version)

        # 1. Downgrade Prevention Gate
        if target_semver < self._current_semver:
            raise UpdateDowngradeError(
                f"Cannot downgrade from current version {self._current_version_str} "
                f"to target {manifest.version.target_version}."
            )

        # 2. Minimum Supported Version Gate (Upgrade Hop Validity)
        if self._current_semver < min_supported_semver:
            raise UpdateCompatibilityError(
                f"Current version {self._current_version_str} is below minimum supported version "
                f"{manifest.version.min_supported_version} for target {manifest.version.target_version}. "
                f"Intermediate upgrade required."
            )

        # 3. Platform Matching Gate
        current_platform = sys.platform
        supported_platforms = [p.lower() for p in manifest.compatibility.platforms]
        # Normalize linux / darwin / win32
        matched_platform = any(
            current_platform.startswith(p) or (p == "windows" and current_platform == "win32")
            for p in supported_platforms
        )
        if not matched_platform:
            raise UpdatePlatformMismatchError(
                f"Target platform not supported. Host platform '{current_platform}', "
                f"manifest supports {manifest.compatibility.platforms}."
            )

        # 4. CPU Architecture Matching Gate
        current_machine = platform.machine().lower()
        supported_archs = [a.lower() for a in manifest.compatibility.architectures]
        arch_aliases = {
            "amd64": {"x86_64", "amd64"},
            "x86_64": {"x86_64", "amd64"},
            "arm64": {"arm64", "aarch64"},
            "aarch64": {"arm64", "aarch64"},
        }
        effective_current_archs = arch_aliases.get(current_machine, {current_machine})
        matched_arch = any(arch in supported_archs for arch in effective_current_archs)
        if not matched_arch:
            raise UpdatePlatformMismatchError(
                f"Target architecture not supported. Host architecture '{current_machine}', "
                f"manifest supports {manifest.compatibility.architectures}."
            )

        # 5. Python Runtime Compatibility Gate
        if manifest.compatibility.python_version_min:
            min_py = tuple(int(x) for x in manifest.compatibility.python_version_min.split("."))
            cur_py = (sys.version_info.major, sys.version_info.minor)
            if cur_py < min_py:
                raise UpdateCompatibilityError(
                    f"Host Python {cur_py[0]}.{cur_py[1]} is below required minimum Python "
                    f"{manifest.compatibility.python_version_min}."
                )
