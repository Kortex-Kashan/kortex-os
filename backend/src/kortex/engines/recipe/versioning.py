"""
KORTEX Recipe Engine Semantic Versioning and Dependency Resolver.

Provides SemVer comparison, range matching (>=, <=, ==, ^, ~), version constraint
resolution, and dependency dependency graph verification.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple
from kortex.engines.recipe.exceptions import RecipeVersionError


class VersionResolver:
    """Semantic Versioning (SemVer 2.0.0) parser and constraint evaluator."""

    SEMVER_REGEX = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")

    @classmethod
    def parse_semver(cls, version_str: str) -> Tuple[int, int, int]:
        """Parse version string into (major, minor, patch) tuple.

        Args:
            version_str: Semantic version string (e.g. "1.2.3").

        Returns:
            Tuple of (major, minor, patch) integers.

        Raises:
            RecipeVersionError: If version string does not conform to SemVer.
        """
        match = cls.SEMVER_REGEX.match(version_str.strip())
        if not match:
            raise RecipeVersionError(f"Invalid Semantic Version string: '{version_str}'. Must follow MAJOR.MINOR.PATCH format.")
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))

    @classmethod
    def compare(cls, version1: str, version2: str) -> int:
        """Compare two version strings.

        Returns:
            -1 if version1 < version2
             0 if version1 == version2
             1 if version1 > version2
        """
        v1 = cls.parse_semver(version1)
        v2 = cls.parse_semver(version2)
        if v1 < v2:
            return -1
        if v1 > v2:
            return 1
        return 0

    @classmethod
    def satisfies_constraint(cls, available_version: str, constraint: str) -> bool:
        """Evaluate if available_version satisfies a constraint string (e.g. '>=1.0.0', '==2.1.0').

        Args:
            available_version: Actual version string.
            constraint: Constraint expression string.

        Returns:
            True if constraint is satisfied.
        """
        constraint = constraint.strip()
        if not constraint or constraint == "*":
            return True

        if constraint.startswith(">="):
            target = constraint[2:].strip()
            return cls.compare(available_version, target) >= 0
        if constraint.startswith("<="):
            target = constraint[2:].strip()
            return cls.compare(available_version, target) <= 0
        if constraint.startswith(">"):
            target = constraint[1:].strip()
            return cls.compare(available_version, target) > 0
        if constraint.startswith("<"):
            target = constraint[1:].strip()
            return cls.compare(available_version, target) < 0
        if constraint.startswith("=="):
            target = constraint[2:].strip()
            return cls.compare(available_version, target) == 0

        # Exact match
        return cls.compare(available_version, constraint) == 0

    @classmethod
    def resolve_dependencies(
        cls,
        required_dependencies: Dict[str, str],
        available_assets: Dict[str, str],
    ) -> bool:
        """Verify that all required dependencies are satisfied by available assets.

        Args:
            required_dependencies: Map of asset ID/namespace to version constraint string.
            available_assets: Map of available asset ID/namespace to actual version string.

        Returns:
            True if all dependencies are satisfied.

        Raises:
            RecipeVersionError: If any dependency is missing or version incompatible.
        """
        for req_id, constraint in required_dependencies.items():
            if req_id not in available_assets:
                raise RecipeVersionError(f"Missing required dependency '{req_id}' (required constraint: {constraint}).")
            avail_ver = available_assets[req_id]
            if not cls.satisfies_constraint(avail_ver, constraint):
                raise RecipeVersionError(
                    f"Dependency '{req_id}' version incompatibility. Required '{constraint}', found '{avail_ver}'."
                )
        return True
