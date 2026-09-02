"""
Unit tests for KORTEX Recipe Engine YAML parser and manifest parsing.
"""

import pytest

from kortex.engines.recipe.exceptions import RecipeValidationError
from kortex.engines.recipe.manifest import RecipeManifestManager
from kortex.engines.recipe.parser import RecipeParser

SAMPLE_MANIFEST_YAML = """
id: "recipe-payroll-01"
name: "Monthly Payroll Processing"
namespace: "kortex.hr.payroll"
version: "1.2.0"
description: "Executes end of month payroll processing"
author:
  name: "KORTEX Engineering"
  email: "eng@kortex.ai"
capabilities_required:
  - "kortex.storage.data.session"
checksum: "d41d8cd98f00b204e9800998ecf8427e"
"""

SAMPLE_RECIPE_YAML = """
manifest:
  id: "recipe-payroll-01"
  name: "Monthly Payroll Processing"
  namespace: "kortex.hr.payroll"
  version: "1.2.0"
  description: "Executes end of month payroll processing"
  checksum: "d41d8cd98f00b204e9800998ecf8427e"

inputs:
  - name: "period_id"
    type: "string"
    required: true

steps:
  - id: "step_calc"
    name: "Calculate Payroll"
    capability: "kortex.hr.payroll.calculate"
    parameters:
      period: "${inputs.period_id}"
    retry_attempts: 3

outputs:
  - name: "status"
    type: "string"
    value_expression: "completed"

settings:
  timeout_seconds: 1800
  priority: "HIGH"
  trigger: "MANUAL"
"""


def test_recipe_parser_basic_yaml() -> None:
    parser = RecipeParser()
    parsed = parser.parse_yaml_string(SAMPLE_MANIFEST_YAML)
    assert parsed["id"] == "recipe-payroll-01"
    assert parsed["namespace"] == "kortex.hr.payroll"

    assert parser.parse_schema_yaml("type: object")["type"] == "object"
    assert parser.parse_permissions_yaml("permissions: []")["permissions"] == []


def test_recipe_parser_invalid_yaml() -> None:
    parser = RecipeParser()
    with pytest.raises(RecipeValidationError, match="Invalid YAML syntax"):
        parser.parse_yaml_string("id: [unclosed bracket")


def test_recipe_parser_non_dict_yaml() -> None:
    parser = RecipeParser()
    with pytest.raises(RecipeValidationError, match="Parsed YAML must be a root key-value dictionary"):
        parser.parse_yaml_string("- item 1\n- item 2")


def test_recipe_parser_definition() -> None:
    parser = RecipeParser()
    definition = parser.parse_definition(SAMPLE_RECIPE_YAML)
    assert definition.manifest.id == "recipe-payroll-01"
    assert len(definition.inputs) == 1
    assert definition.inputs[0].name == "period_id"
    assert len(definition.steps) == 1
    assert definition.steps[0].id == "step_calc"
    assert definition.settings.priority == "HIGH"


def test_recipe_parser_separate_manifest() -> None:
    parser = RecipeParser()
    recipe_no_manifest = """
inputs:
  - name: "test_input"
steps:
  - id: "s1"
    name: "Step 1"
"""
    definition = parser.parse_definition(raw_recipe=recipe_no_manifest, raw_manifest=SAMPLE_MANIFEST_YAML)
    assert definition.manifest.id == "recipe-payroll-01"
    assert len(definition.steps) == 1

    # Missing manifest error
    with pytest.raises(RecipeValidationError, match="Recipe specification must include a valid manifest section"):
        parser.parse_definition(raw_recipe=recipe_no_manifest)


def test_recipe_manifest_manager_field_validations() -> None:
    # Missing ID
    with pytest.raises(RecipeValidationError, match="missing mandatory field: 'id'"):
        RecipeManifestManager.parse_manifest_dict({})

    # Missing name
    with pytest.raises(RecipeValidationError, match="missing mandatory field: 'name'"):
        RecipeManifestManager.parse_manifest_dict({"id": "r1"})

    # Missing namespace
    with pytest.raises(RecipeValidationError, match="missing mandatory field: 'namespace'"):
        RecipeManifestManager.parse_manifest_dict({"id": "r1", "name": "n1"})

    # Invalid namespace format
    with pytest.raises(RecipeValidationError, match="Invalid manifest namespace format"):
        RecipeManifestManager.parse_manifest_dict({"id": "r1", "name": "n1", "namespace": "INVALID"})

    # Model validation error
    with pytest.raises(RecipeValidationError, match="Failed to validate RecipeManifest model"):
        RecipeManifestManager.parse_manifest_dict({"id": "r1", "name": "n1", "namespace": "kortex.r", "author": 12345})

    # YAML manifest syntax error
    with pytest.raises(RecipeValidationError, match="Failed to parse manifest YAML"):
        RecipeManifestManager.parse_manifest_yaml("invalid: [yaml")

    # Non-dict YAML manifest
    with pytest.raises(RecipeValidationError, match="Manifest YAML content must root to a dictionary mapping"):
        RecipeManifestManager.parse_manifest_yaml("- item 1")


def test_recipe_manifest_checksum_calculation() -> None:
    parser = RecipeParser()
    manifest = parser.parse_manifest_yaml(SAMPLE_MANIFEST_YAML)
    checksum1 = RecipeManifestManager.calculate_checksum(manifest)
    checksum2 = RecipeManifestManager.calculate_checksum(manifest)
    assert checksum1 == checksum2
    assert len(checksum1) == 64

    # Test dictionary input for calculate_checksum
    dict_checksum = RecipeManifestManager.calculate_checksum({"id": "r1", "name": "n1"})
    assert len(dict_checksum) == 64

    # Test other input
    fallback_checksum = RecipeManifestManager.calculate_checksum("string_input")  # type: ignore
    assert len(fallback_checksum) == 64
