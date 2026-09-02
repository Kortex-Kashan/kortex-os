"""
Unit tests for KORTEX Recipe Engine Permission and least-privilege checks.
"""

import pytest

from kortex.engines.recipe.exceptions import RecipePermissionError
from kortex.engines.recipe.models import RecipeDefinition, RecipeManifest, RecipeStep
from kortex.engines.recipe.permissions import PermissionValidator


def test_permission_validator_authorized() -> None:
    manifest = RecipeManifest(
        id="r-perm",
        name="Perm Recipe",
        namespace="kortex.perm",
        version="1.0.0",
        capabilities_required=["kortex.storage.data.session"],
        permissions_required=["hr:read"],
        checksum="123",
    )
    step = RecipeStep(id="s1", name="Step", capability="kortex.storage.data.session")
    definition = RecipeDefinition(manifest=manifest, steps=[step])

    granted_caps = ["kortex.storage.data.session", "kortex.storage.file.store"]
    granted_perms = ["hr:read", "hr:write"]
    assert PermissionValidator.validate_permissions(definition, granted_caps, granted_perms) is True


def test_permission_validator_unauthorized_step_capability() -> None:
    manifest = RecipeManifest(
        id="r-perm",
        name="Perm Recipe",
        namespace="kortex.perm",
        version="1.0.0",
        checksum="123",
    )
    step = RecipeStep(id="s1", name="Step", capability="kortex.unauthorized.cap")
    definition = RecipeDefinition(manifest=manifest, steps=[step])

    with pytest.raises(RecipePermissionError, match="invokes unauthorized capability"):
        PermissionValidator.validate_permissions(definition, ["kortex.storage.data.session"])


def test_permission_validator_unauthorized_permission() -> None:
    manifest = RecipeManifest(
        id="r-perm",
        name="Perm Recipe",
        namespace="kortex.perm",
        version="1.0.0",
        permissions_required=["finance:admin"],
        checksum="123",
    )
    definition = RecipeDefinition(manifest=manifest, steps=[RecipeStep(id="s1", name="Step")])

    with pytest.raises(RecipePermissionError, match="requires permission 'finance:admin'"):
        PermissionValidator.validate_permissions(definition, [], ["hr:read"])


def test_permission_yaml_parser() -> None:
    data = {"permissions": [{"resource": "hr", "action": "read"}]}
    perms = PermissionValidator.parse_permissions_yaml_dict(data)
    assert len(perms) == 1
    assert perms[0].resource == "hr"
