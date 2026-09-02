"""Declarative Document Ontology system for the KORTEX OS Document Engine.

This module implements DocumentOntology, OntologyRegistry, and a small deterministic
arithmetic evaluator for invariant rules, allowing KORTEX OS to understand the semantic
structure, typed fields, and invariant relationships of business documents without
requiring active AI models or LLM calls, in accordance with Section 8.1 of the Document
Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.document.exceptions import DocumentTemplateError
from kortex.engines.document.models import ValidationReport
from kortex.engines.document.template_library import parse_semver


class OntologyFieldType(str, Enum):
    """Declarative typed-field kinds recognized by the Document Ontology system."""

    STRING = "STRING"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    ARRAY = "ARRAY"
    OBJECT = "OBJECT"


_PYTHON_TYPE_CHECKS: dict[OntologyFieldType, tuple[type, ...]] = {
    OntologyFieldType.STRING: (str,),
    OntologyFieldType.NUMBER: (int, float),
    OntologyFieldType.BOOLEAN: (bool,),
    OntologyFieldType.ARRAY: (list, tuple),
    OntologyFieldType.OBJECT: (dict,),
}


class InvariantOperator(str, Enum):
    """Deterministic arithmetic operators supported by declarative invariant rules.

    No expression parsing, eval(), or exec() is ever used: every operator is a fixed,
    reviewed arithmetic relation over named numeric fields.
    """

    SUM_EQUALS = "SUM_EQUALS"
    """target_field == sum(operand_fields)."""

    DIFFERENCE_EQUALS = "DIFFERENCE_EQUALS"
    """target_field == operand_fields[0] - sum(operand_fields[1:])."""


def apply_invariant_operator(
    operator: InvariantOperator,
    target_value: float,
    operand_values: list[float],
    tolerance: float = 1e-6,
) -> bool:
    """Verify whether target_value satisfies operator against operand_values.

    Args:
        operator: Declarative arithmetic operator.
        target_value: Numeric value being verified.
        operand_values: Ordered numeric operand values.
        tolerance: Absolute floating-point comparison tolerance.

    Returns:
        True if the invariant holds within tolerance.

    Raises:
        DocumentTemplateError: If operand_values is empty for DIFFERENCE_EQUALS.
    """
    expected = compute_invariant_target(operator, operand_values)
    return abs(target_value - expected) <= tolerance


def compute_invariant_target(operator: InvariantOperator, operand_values: list[float]) -> float:
    """Derive the target value implied by operator over operand_values.

    Args:
        operator: Declarative arithmetic operator.
        operand_values: Ordered numeric operand values.

    Returns:
        The computed target value.

    Raises:
        DocumentTemplateError: If operand_values is empty for DIFFERENCE_EQUALS.
    """
    if operator == InvariantOperator.SUM_EQUALS:
        return float(sum(operand_values))
    if operator == InvariantOperator.DIFFERENCE_EQUALS:
        if not operand_values:
            raise DocumentTemplateError("DIFFERENCE_EQUALS invariant requires at least one operand field.")
        return float(operand_values[0] - sum(operand_values[1:]))
    raise DocumentTemplateError(f"Unsupported invariant operator: '{operator}'.")


def _resolve_field(data: dict[str, Any], path: str) -> tuple[bool, Any]:
    """Resolve a dotted-path lookup (e.g. 'summary.net_salary') in a nested dictionary.

    Args:
        data: The dictionary to search.
        path: Key or dotted-path string to look up.

    Returns:
        Tuple of (found: bool, value: Any).
    """
    if not isinstance(data, dict) or not path:
        return (False, None)

    if path in data:
        return (True, data[path])

    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return (False, None)
    return (True, current)


class OntologyField(BaseModel):
    """Declarative typed field definition within a Document Ontology entity or child structure."""

    model_config = ConfigDict(frozen=True)

    name: str
    field_type: OntologyFieldType
    required: bool = True
    description: str = ""


class OntologyInvariantRule(BaseModel):
    """Declarative, deterministic arithmetic invariant over named numeric fields.

    Example: Payslip 'Net Salary = Gross Salary - Total Deductions' is expressed as
    operator=DIFFERENCE_EQUALS, target_field='net_salary',
    operand_fields=['gross_salary', 'total_deductions'].
    """

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    operator: InvariantOperator
    target_field: str
    operand_fields: list[str] = Field(default_factory=list)
    tolerance: float = 1e-6


class DocumentOntology(BaseModel):
    """Declarative structural schema describing a business document entity's semantics.

    AI-independent and fully deterministic: structural and invariant validation never
    requires an active AI model or LLM call.
    """

    model_config = ConfigDict(frozen=True)

    ontology_id: str
    entity_name: str
    version: str
    fields: list[OntologyField] = Field(default_factory=list)
    child_structures: dict[str, list[OntologyField]] = Field(default_factory=dict)
    invariant_rules: list[OntologyInvariantRule] = Field(default_factory=list)
    relationship_mappings: dict[str, str] = Field(default_factory=dict)

    def validate_structure(self, data: dict[str, Any]) -> ValidationReport:
        """Validate that data satisfies this ontology's required fields and declared types.

        Args:
            data: Candidate document data payload (flat or nested via dotted paths).

        Returns:
            ValidationReport summarizing structural validity.
        """
        errors: list[str] = []
        warnings: list[str] = []
        missing: list[str] = []
        type_mismatches: list[str] = []

        for field in self.fields:
            found, value = _resolve_field(data, field.name)
            if not found:
                missing.append(field.name)
                if field.required:
                    errors.append(f"Missing required ontology field: '{field.name}'.")
                else:
                    warnings.append(f"Missing optional ontology field: '{field.name}'.")
                continue

            expected_types = _PYTHON_TYPE_CHECKS[field.field_type]
            if not isinstance(value, expected_types):
                type_mismatches.append(
                    f"Field '{field.name}' expected type {field.field_type.value}, got '{type(value).__name__}'."
                )
                errors.append(f"Type mismatch for field '{field.name}': expected {field.field_type.value}.")

        for structure_name, structure_fields in self.child_structures.items():
            found, structure_value = _resolve_field(data, structure_name)
            if not found or not isinstance(structure_value, dict):
                errors.append(f"Missing required child structure: '{structure_name}'.")
                missing.append(structure_name)
                continue

            for field in structure_fields:
                sub_found, sub_value = _resolve_field(structure_value, field.name)
                if not sub_found:
                    qualified = f"{structure_name}.{field.name}"
                    missing.append(qualified)
                    if field.required:
                        errors.append(f"Missing required ontology field: '{qualified}'.")
                    else:
                        warnings.append(f"Missing optional ontology field: '{qualified}'.")
                    continue

                expected_types = _PYTHON_TYPE_CHECKS[field.field_type]
                if not isinstance(sub_value, expected_types):
                    qualified = f"{structure_name}.{field.name}"
                    type_mismatches.append(
                        f"Field '{qualified}' expected type {field.field_type.value}, got '{type(sub_value).__name__}'."
                    )
                    errors.append(f"Type mismatch for field '{qualified}': expected {field.field_type.value}.")

        return ValidationReport(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            missing_placeholders=sorted(set(missing)),
            type_mismatches=type_mismatches,
            computed_fields_resolved=[],
        )

    def validate_invariants(self, data: dict[str, Any]) -> ValidationReport:
        """Validate that data satisfies all declared deterministic invariant rules.

        Args:
            data: Candidate document data payload (flat or nested via dotted paths).

        Returns:
            ValidationReport summarizing invariant validity.
        """
        errors: list[str] = []
        warnings: list[str] = []

        for rule in self.invariant_rules:
            target_found, target_value = _resolve_field(data, rule.target_field)
            if not target_found or not isinstance(target_value, (int, float)):
                errors.append(
                    f"Cannot evaluate invariant '{rule.name}': target field "
                    f"'{rule.target_field}' is missing or non-numeric."
                )
                continue

            operand_values: list[float] = []
            operand_missing = False
            for operand_name in rule.operand_fields:
                found, value = _resolve_field(data, operand_name)
                if not found or not isinstance(value, (int, float)):
                    errors.append(
                        f"Cannot evaluate invariant '{rule.name}': operand field "
                        f"'{operand_name}' is missing or non-numeric."
                    )
                    operand_missing = True
                    continue
                operand_values.append(float(value))

            if operand_missing:
                continue

            if not apply_invariant_operator(rule.operator, float(target_value), operand_values, rule.tolerance):
                errors.append(
                    f"Invariant '{rule.name}' violated: field '{rule.target_field}' "
                    f"does not satisfy {rule.operator.value} over {rule.operand_fields}."
                )

        return ValidationReport(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            missing_placeholders=[],
            type_mismatches=[],
            computed_fields_resolved=[],
        )

    def validate(self, data: dict[str, Any]) -> ValidationReport:
        """Validate both structure and invariant rules against data in a single report.

        Args:
            data: Candidate document data payload.

        Returns:
            Combined ValidationReport.
        """
        structure_report = self.validate_structure(data)
        invariant_report = self.validate_invariants(data)

        return ValidationReport(
            is_valid=structure_report.is_valid and invariant_report.is_valid,
            errors=structure_report.errors + invariant_report.errors,
            warnings=structure_report.warnings + invariant_report.warnings,
            missing_placeholders=structure_report.missing_placeholders,
            type_mismatches=structure_report.type_mismatches,
            computed_fields_resolved=[],
        )


class OntologyRegistry:
    """Local-first, versioned, in-memory registry for Document Ontology definitions.

    Mirrors TemplateLibrary's versioned-dictionary storage pattern for consistency.
    """

    def __init__(self) -> None:
        """Initialize an empty ontology catalog."""
        self._ontologies: dict[str, dict[str, DocumentOntology]] = {}

    async def register_ontology(self, ontology: DocumentOntology) -> DocumentOntology:
        """Register a DocumentOntology definition after validating uniqueness.

        Args:
            ontology: DocumentOntology instance to register.

        Returns:
            The registered DocumentOntology instance.

        Raises:
            DocumentTemplateError: If the entity_name + version pair is already registered.
        """
        entity_name = ontology.entity_name.strip()
        version = ontology.version.strip()
        parse_semver(version)

        if entity_name in self._ontologies and version in self._ontologies[entity_name]:
            raise DocumentTemplateError(
                f"Duplicate ontology registration: '{entity_name}' version '{version}' is already registered."
            )

        self._ontologies.setdefault(entity_name, {})[version] = ontology
        return ontology

    async def get_ontology(self, entity_name: str, version: str | None = None) -> DocumentOntology:
        """Retrieve a DocumentOntology by entity name and optional version.

        Args:
            entity_name: Ontology entity name (e.g. 'Payslip').
            version: Optional SemVer string. Resolves latest version if omitted.

        Returns:
            DocumentOntology instance.

        Raises:
            DocumentTemplateError: If entity_name or requested version is not found.
        """
        entity_name = entity_name.strip()
        if entity_name not in self._ontologies or not self._ontologies[entity_name]:
            raise DocumentTemplateError(f"Ontology '{entity_name}' not found in registry.")

        if version is not None:
            version = version.strip()
            if version not in self._ontologies[entity_name]:
                raise DocumentTemplateError(f"Ontology '{entity_name}' version '{version}' not found.")
            return self._ontologies[entity_name][version]

        versions = list(self._ontologies[entity_name].keys())
        latest_version = sorted(versions, key=parse_semver)[-1]
        return self._ontologies[entity_name][latest_version]

    async def list_ontologies(self) -> list[DocumentOntology]:
        """List the latest version of every registered ontology.

        Returns:
            List of latest DocumentOntology instances.
        """
        result: list[DocumentOntology] = []
        for entity_name in self._ontologies:
            result.append(await self.get_ontology(entity_name))
        return result


__all__ = [
    "DocumentOntology",
    "InvariantOperator",
    "OntologyField",
    "OntologyFieldType",
    "OntologyInvariantRule",
    "OntologyRegistry",
    "apply_invariant_operator",
    "compute_invariant_target",
]
