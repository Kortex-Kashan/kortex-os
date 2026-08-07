"""
Unit tests for KORTEX Recipe Engine Schema, Security, and Checksum Validator.
"""

import pytest
from kortex.engines.recipe.manifest import RecipeManifestManager
from kortex.engines.recipe.models import RecipeDefinition, RecipeManifest, RecipeStep
from kortex.engines.recipe.validator import RecipeValidator


def test_validator_security_rejection() -> None:
    validator = RecipeValidator()

    # Valid non-code files
    safe_files = {
        "recipe.yaml": b"inputs: []\nsteps: []",
        "manifest.yaml": b"id: r1\nname: n1\nnamespace: k.r\nversion: 1.0.0",
        "doc.txt": b"Documentation",
    }
    res_safe = validator.validate_security(safe_files)
    assert res_safe.is_valid is True
    assert len(res_safe.errors) == 0

    # Malicious executable file types
    unsafe_files = {
        "recipe.yaml": b"...",
        "exploit.py": b"import os; os.system('echo hack')",
        "malware.exe": b"MZ...",
        "script.sh": b"#!/bin/bash",
    }
    res_unsafe = validator.validate_security(unsafe_files)
    assert res_unsafe.is_valid is False
    assert len(res_unsafe.errors) == 3
    assert any(".py" in err for err in res_unsafe.errors)
    assert any(".exe" in err for err in res_unsafe.errors)
    assert any(".sh" in err for err in res_unsafe.errors)


def test_validator_schema_validation() -> None:
    validator = RecipeValidator()

    manifest = RecipeManifest(
        id="rec-01",
        name="Valid Recipe",
        namespace="kortex.test.recipe",
        version="1.0.0",
        checksum="123",
    )
    step1 = RecipeStep(id="s1", name="First Step")
    step2 = RecipeStep(id="s2", name="Second Step")

    definition = RecipeDefinition(manifest=manifest, steps=[step1, step2])
    res = validator.validate_schema(definition)
    assert res.is_valid is True
    assert len(res.errors) == 0


def test_validator_schema_invalid_fields() -> None:
    validator = RecipeValidator()

    # Empty name, empty namespace, empty steps
    m_empty = RecipeManifest(id="rec-empty", name="", namespace="", version="1.0.0", checksum="123")
    def_empty = RecipeDefinition(manifest=m_empty, steps=[])

    res = validator.validate_schema(def_empty)
    assert res.is_valid is False
    assert any("title 'name' must not be empty" in err for err in res.errors)
    assert any("'namespace' must not be empty" in err for err in res.errors)
    assert any("at least one execution step" in err for err in res.errors)

    # Step missing id or step missing name
    m_ok = RecipeManifest(id="rec-step-err", name="Name", namespace="kortex.ok", version="1.0.0", checksum="123")
    step_no_id = RecipeStep(id="", name="No ID Step")
    step_no_name = RecipeStep(id="s2", name="")
    def_step_err = RecipeDefinition(manifest=m_ok, steps=[step_no_id, step_no_name])

    res_step = validator.validate_schema(def_step_err)
    assert res_step.is_valid is False
    assert any("missing required 'id'" in err for err in res_step.errors)
    assert any("missing human-readable 'name'" in err for err in res_step.errors)


def test_validator_checksum_and_full_validation() -> None:
    validator = RecipeValidator()
    manifest = RecipeManifest(
        id="rec-chk",
        name="Checksum Recipe",
        namespace="kortex.chk",
        version="1.0.0",
        checksum="",
    )
    definition = RecipeDefinition(manifest=manifest, steps=[RecipeStep(id="s1", name="Step")])

    computed_checksum = RecipeManifestManager.calculate_checksum(manifest)
    manifest.checksum = computed_checksum

    assert validator.validate_checksum(computed_checksum, definition) is True
    assert validator.validate_checksum("wrong_checksum", definition) is False

    full_res = validator.validate_recipe(definition, raw_files={"recipe.yaml": b"..."})
    assert full_res.is_valid is True
