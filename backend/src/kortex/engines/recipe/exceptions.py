"""
KORTEX Recipe Engine Exception Hierarchy.

Defines all custom domain exceptions raised during recipe loading, validation,
compilation, packaging, installation, upgrade, permission checking, and version resolution.
"""

from __future__ import annotations

from kortex.core.exceptions import KortexError


class RecipeError(KortexError):
    """Base exception for all Recipe Engine errors."""


class RecipeValidationError(RecipeError):
    """Raised when a recipe fails structural, schema, DSL, or security validation."""


class RecipeCompilationError(RecipeError):
    """Raised when translating a recipe DSL into a WorkflowDefinition fails."""


class RecipeCompatibilityError(RecipeError):
    """Raised when a recipe fails Kernel or engine version compatibility checks."""


class RecipePermissionError(RecipeError):
    """Raised when a recipe requests unauthorized permissions or capabilities."""


class RecipeDependencyError(RecipeError):
    """Raised when required recipe dependencies cannot be resolved or satisfied."""


class RecipeInstallationError(RecipeError):
    """Raised during recipe installation, upgrade, or removal failure."""


class RecipePackageError(RecipeError):
    """Raised when opening, reading, or creating .kortex-recipe packages fails."""


class RecipeSignatureError(RecipeError):
    """Raised when digital signature or SHA256 checksum verification fails."""


class RecipeVersionError(RecipeError):
    """Raised when SemVer resolution or version comparison fails."""
