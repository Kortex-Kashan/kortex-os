"""
Unit tests for KORTEX Recipe Engine Loader.
"""

import io
import zipfile

import pytest

from kortex.engines.recipe.exceptions import RecipePackageError
from kortex.engines.recipe.loader import RecipeLoader


def test_loader_from_folder_files() -> None:
    loader = RecipeLoader()

    files = {
        "manifest.yaml": b"id: r1\nname: Test Loader\nnamespace: kortex.load\nversion: 1.0.0\nchecksum: 123",
        "recipe.yaml": b"inputs: []\nsteps:\n  - id: s1\n    name: Step One",
    }
    definition = loader.load_from_folder_files(files)
    assert definition.manifest.id == "r1"
    assert len(definition.steps) == 1
    assert definition.steps[0].id == "s1"


def test_loader_from_package_bytes() -> None:
    loader = RecipeLoader()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "manifest.yaml", "id: r_pkg\nname: Pkg Recipe\nnamespace: kortex.pkg\nversion: 1.0.0\nchecksum: 123"
        )
        zf.writestr("recipe.yaml", "steps:\n  - id: s_pkg\n    name: Pkg Step")

    package_bytes = buffer.getvalue()
    definition = loader.load_from_package(package_bytes)
    assert definition.manifest.id == "r_pkg"
    assert definition.steps[0].id == "s_pkg"


def test_loader_invalid_zip() -> None:
    loader = RecipeLoader()
    with pytest.raises(RecipePackageError, match=r"Invalid .kortex-recipe package archive"):
        loader.load_from_package(b"not a zip file")
