"""
Unit tests for KORTEX Recipe Engine SemVer and System Compatibility.
"""

import pytest

from kortex.engines.recipe.compatibility import CompatibilityValidator
from kortex.engines.recipe.exceptions import RecipeCompatibilityError, RecipeVersionError
from kortex.engines.recipe.models import RecipeCompatibility, RecipeDefinition, RecipeManifest, RecipeStep
from kortex.engines.recipe.versioning import VersionResolver


def test_version_resolver_semver_operators() -> None:
    assert VersionResolver.parse_semver("1.2.3") == (1, 2, 3)
    assert VersionResolver.parse_semver("v2.0.0") == (2, 0, 0)
    with pytest.raises(RecipeVersionError):
        VersionResolver.parse_semver("invalid.ver")

    assert VersionResolver.compare("1.0.0", "1.2.0") == -1
    assert VersionResolver.compare("2.0.0", "1.9.9") == 1
    assert VersionResolver.compare("1.0.0", "1.0.0") == 0

    assert VersionResolver.satisfies_constraint("1.5.0", ">=1.0.0") is True
    assert VersionResolver.satisfies_constraint("1.5.0", "<=2.0.0") is True
    assert VersionResolver.satisfies_constraint("1.5.0", ">1.0.0") is True
    assert VersionResolver.satisfies_constraint("1.5.0", "<2.0.0") is True
    assert VersionResolver.satisfies_constraint("1.5.0", "==1.5.0") is True
    assert VersionResolver.satisfies_constraint("1.5.0", "*") is True
    assert VersionResolver.satisfies_constraint("1.5.0", "") is True


def test_version_resolver_dependencies() -> None:
    reqs = {"kortex.kernel": ">=0.1.0", "kortex.storage": "==1.0.0"}
    avail = {"kortex.kernel": "0.1.0", "kortex.storage": "1.0.0"}
    assert VersionResolver.resolve_dependencies(reqs, avail) is True

    # Missing dep
    with pytest.raises(RecipeVersionError, match="Missing required dependency"):
        VersionResolver.resolve_dependencies({"kortex.missing": ">=1.0.0"}, avail)

    # Incompatible dep
    with pytest.raises(RecipeVersionError, match="version incompatibility"):
        VersionResolver.resolve_dependencies({"kortex.kernel": ">=2.0.0"}, avail)


def test_compatibility_validator_all_components() -> None:
    manifest = RecipeManifest(id="c1", name="Compat", namespace="kortex.c", version="1.0.0", checksum="1")
    compat = RecipeCompatibility(
        kernel=">=0.1.0",
        workflow_engine=">=1.0.0",
        storage_engine=">=1.0.0",
        document_engine=">=1.0.0",
        connector_engine=">=1.0.0",
        module_versions={"kortex.hr": ">=1.0.0"},
    )
    definition = RecipeDefinition(manifest=manifest, steps=[RecipeStep(id="s1", name="Step")], compatibility=compat)

    system_ok = {
        "kernel": "0.1.0",
        "workflow_engine": "1.0.0",
        "storage_engine": "1.0.0",
        "document_engine": "1.0.0",
        "connector_engine": "1.0.0",
        "kortex.hr": "1.0.0",
    }
    assert CompatibilityValidator.validate_compatibility(definition, system_ok) is True

    # Workflow failure
    with pytest.raises(RecipeCompatibilityError, match="Workflow Engine version"):
        CompatibilityValidator.validate_compatibility(definition, {**system_ok, "workflow_engine": "0.9.0"})

    # Storage failure
    with pytest.raises(RecipeCompatibilityError, match="Storage Engine version"):
        CompatibilityValidator.validate_compatibility(definition, {**system_ok, "storage_engine": "0.9.0"})

    # Document failure
    with pytest.raises(RecipeCompatibilityError, match="Document Engine version"):
        CompatibilityValidator.validate_compatibility(definition, {**system_ok, "document_engine": "0.9.0"})

    # Connector failure
    with pytest.raises(RecipeCompatibilityError, match="Connector Engine version"):
        CompatibilityValidator.validate_compatibility(definition, {**system_ok, "connector_engine": "0.9.0"})

    # Missing module
    with pytest.raises(RecipeCompatibilityError, match=r"Required module 'kortex.hr' is not installed"):
        CompatibilityValidator.validate_compatibility(
            definition, {k: v for k, v in system_ok.items() if k != "kortex.hr"}
        )

    # Incompatible module
    with pytest.raises(RecipeCompatibilityError, match=r"Module 'kortex.hr' version '0.5.0' does not satisfy"):
        CompatibilityValidator.validate_compatibility(definition, {**system_ok, "kortex.hr": "0.5.0"})
