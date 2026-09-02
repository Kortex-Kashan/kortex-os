"""Unit tests for the Document Ontology system (Milestone 3).

Target: 100% pass rate, high line coverage for ontology.py.
"""

from __future__ import annotations

import pytest

from kortex.engines.document.exceptions import DocumentTemplateError
from kortex.engines.document.ontology import (
    DocumentOntology,
    InvariantOperator,
    OntologyField,
    OntologyFieldType,
    OntologyInvariantRule,
    OntologyRegistry,
    apply_invariant_operator,
    compute_invariant_target,
)


def _payslip_ontology(version: str = "1.0.0") -> DocumentOntology:
    """Build the spec's Payslip ontology example: Net Salary = Gross Salary - Total Deductions."""
    return DocumentOntology(
        ontology_id="ontology.payslip.v1",
        entity_name="Payslip",
        version=version,
        fields=[
            OntologyField(name="employee_id", field_type=OntologyFieldType.STRING),
            OntologyField(name="gross_salary", field_type=OntologyFieldType.NUMBER),
            OntologyField(name="total_deductions", field_type=OntologyFieldType.NUMBER),
            OntologyField(name="net_salary", field_type=OntologyFieldType.NUMBER),
            OntologyField(name="notes", field_type=OntologyFieldType.STRING, required=False),
        ],
        child_structures={
            "employee_info": [
                OntologyField(name="name", field_type=OntologyFieldType.STRING),
                OntologyField(name="department", field_type=OntologyFieldType.STRING),
            ]
        },
        invariant_rules=[
            OntologyInvariantRule(
                name="net_equals_gross_minus_deductions",
                description="Net Salary = Gross Salary - Total Deductions",
                operator=InvariantOperator.DIFFERENCE_EQUALS,
                target_field="net_salary",
                operand_fields=["gross_salary", "total_deductions"],
            )
        ],
    )


# -- Arithmetic evaluator ------------------------------------------------------


def test_apply_invariant_operator_sum_equals() -> None:
    """SUM_EQUALS verification holds when target equals sum of operands."""
    assert apply_invariant_operator(InvariantOperator.SUM_EQUALS, 30.0, [10.0, 20.0]) is True
    assert apply_invariant_operator(InvariantOperator.SUM_EQUALS, 31.0, [10.0, 20.0]) is False


def test_apply_invariant_operator_difference_equals() -> None:
    """DIFFERENCE_EQUALS verification holds when target equals operand[0] - sum(operand[1:])."""
    assert apply_invariant_operator(InvariantOperator.DIFFERENCE_EQUALS, 80.0, [100.0, 20.0]) is True
    assert apply_invariant_operator(InvariantOperator.DIFFERENCE_EQUALS, 79.0, [100.0, 20.0]) is False


def test_apply_invariant_operator_tolerance() -> None:
    """Floating-point tolerance absorbs negligible rounding differences."""
    assert apply_invariant_operator(InvariantOperator.SUM_EQUALS, 30.0000001, [10.0, 20.0], tolerance=1e-4) is True


def test_compute_invariant_target_sum_equals() -> None:
    """compute_invariant_target derives sum for SUM_EQUALS."""
    assert compute_invariant_target(InvariantOperator.SUM_EQUALS, [10.0, 20.0, 5.0]) == 35.0


def test_compute_invariant_target_difference_equals() -> None:
    """compute_invariant_target derives operand[0] - sum(rest) for DIFFERENCE_EQUALS."""
    assert compute_invariant_target(InvariantOperator.DIFFERENCE_EQUALS, [100.0, 20.0]) == 80.0


def test_compute_invariant_target_difference_equals_requires_operand() -> None:
    """DIFFERENCE_EQUALS with zero operands raises DocumentTemplateError."""
    with pytest.raises(DocumentTemplateError, match="at least one operand"):
        compute_invariant_target(InvariantOperator.DIFFERENCE_EQUALS, [])


# -- DocumentOntology.validate_structure ---------------------------------------


def test_validate_structure_valid_payload() -> None:
    """A fully compliant payload validates with no errors."""
    ontology = _payslip_ontology()
    data = {
        "employee_id": "E-1",
        "gross_salary": 1000,
        "total_deductions": 200,
        "net_salary": 800,
        "employee_info": {"name": "Alice", "department": "Engineering"},
    }
    report = ontology.validate_structure(data)
    assert report.is_valid is True
    assert report.errors == []


def test_validate_structure_missing_required_field() -> None:
    """A missing required field produces an error and is listed as missing."""
    ontology = _payslip_ontology()
    data = {
        "gross_salary": 1000,
        "total_deductions": 200,
        "net_salary": 800,
        "employee_info": {"name": "Alice", "department": "Engineering"},
    }
    report = ontology.validate_structure(data)
    assert report.is_valid is False
    assert "employee_id" in report.missing_placeholders
    assert any("employee_id" in e for e in report.errors)


def test_validate_structure_missing_optional_field_is_warning_only() -> None:
    """A missing optional field is a warning, not an error."""
    ontology = _payslip_ontology()
    data = {
        "employee_id": "E-1",
        "gross_salary": 1000,
        "total_deductions": 200,
        "net_salary": 800,
        "employee_info": {"name": "Alice", "department": "Engineering"},
    }
    report = ontology.validate_structure(data)
    assert report.is_valid is True
    assert any("notes" in w for w in report.warnings)


def test_validate_structure_type_mismatch() -> None:
    """A field whose value doesn't match its declared type is reported as a type mismatch."""
    ontology = _payslip_ontology()
    data = {
        "employee_id": "E-1",
        "gross_salary": "not-a-number",
        "total_deductions": 200,
        "net_salary": 800,
        "employee_info": {"name": "Alice", "department": "Engineering"},
    }
    report = ontology.validate_structure(data)
    assert report.is_valid is False
    assert any("gross_salary" in m for m in report.type_mismatches)


def test_validate_structure_missing_child_structure() -> None:
    """A missing required child structure produces an error."""
    ontology = _payslip_ontology()
    data = {
        "employee_id": "E-1",
        "gross_salary": 1000,
        "total_deductions": 200,
        "net_salary": 800,
    }
    report = ontology.validate_structure(data)
    assert report.is_valid is False
    assert any("employee_info" in e for e in report.errors)


def test_validate_structure_child_structure_missing_subfield() -> None:
    """A present child structure missing one of its own required sub-fields is reported."""
    ontology = _payslip_ontology()
    data = {
        "employee_id": "E-1",
        "gross_salary": 1000,
        "total_deductions": 200,
        "net_salary": 800,
        "employee_info": {"name": "Alice"},
    }
    report = ontology.validate_structure(data)
    assert report.is_valid is False
    assert "employee_info.department" in report.missing_placeholders
    assert any("employee_info.department" in e for e in report.errors)


def test_resolve_field_dotted_path_traversal() -> None:
    """A multi-segment dotted path not present as a literal key traverses nested dicts."""
    ontology = DocumentOntology(
        ontology_id="ontology.nested.v1",
        entity_name="Nested",
        version="1.0.0",
        fields=[OntologyField(name="summary.net_salary", field_type=OntologyFieldType.NUMBER)],
    )
    data = {"summary": {"net_salary": 800}}
    report = ontology.validate_structure(data)
    assert report.is_valid is True

    missing_data = {"summary": {"gross_salary": 1000}}
    missing_report = ontology.validate_structure(missing_data)
    assert missing_report.is_valid is False
    assert "summary.net_salary" in missing_report.missing_placeholders


def test_validate_structure_child_structure_field_type_mismatch() -> None:
    """A type mismatch inside a child structure is qualified with the structure name."""
    ontology = _payslip_ontology()
    data = {
        "employee_id": "E-1",
        "gross_salary": 1000,
        "total_deductions": 200,
        "net_salary": 800,
        "employee_info": {"name": "Alice", "department": 12345},
    }
    report = ontology.validate_structure(data)
    assert report.is_valid is False
    assert any("employee_info.department" in m for m in report.type_mismatches)


# -- DocumentOntology.validate_invariants / validate ---------------------------


def test_validate_invariants_satisfied() -> None:
    """A payload satisfying the Payslip invariant validates cleanly."""
    ontology = _payslip_ontology()
    data = {"gross_salary": 1000, "total_deductions": 200, "net_salary": 800}
    report = ontology.validate_invariants(data)
    assert report.is_valid is True
    assert report.errors == []


def test_validate_invariants_violated() -> None:
    """A payload violating the Payslip invariant produces a descriptive error."""
    ontology = _payslip_ontology()
    data = {"gross_salary": 1000, "total_deductions": 200, "net_salary": 850}
    report = ontology.validate_invariants(data)
    assert report.is_valid is False
    assert any("net_equals_gross_minus_deductions" in e for e in report.errors)


def test_validate_invariants_missing_operand() -> None:
    """A missing/non-numeric operand field is reported without raising."""
    ontology = _payslip_ontology()
    data = {"gross_salary": 1000, "net_salary": 800}
    report = ontology.validate_invariants(data)
    assert report.is_valid is False
    assert any("total_deductions" in e for e in report.errors)


def test_validate_invariants_missing_target() -> None:
    """A missing/non-numeric target field is reported without raising."""
    ontology = _payslip_ontology()
    data = {"gross_salary": 1000, "total_deductions": 200}
    report = ontology.validate_invariants(data)
    assert report.is_valid is False
    assert any("net_salary" in e for e in report.errors)


def test_validate_combined_is_valid_only_when_both_pass() -> None:
    """validate() combines structural and invariant validation into one report."""
    ontology = _payslip_ontology()
    good_data = {
        "employee_id": "E-1",
        "gross_salary": 1000,
        "total_deductions": 200,
        "net_salary": 800,
        "employee_info": {"name": "Alice", "department": "Engineering"},
    }
    assert ontology.validate(good_data).is_valid is True

    bad_invariant_data = dict(good_data, net_salary=999)
    combined = ontology.validate(bad_invariant_data)
    assert combined.is_valid is False
    assert any("net_equals_gross_minus_deductions" in e for e in combined.errors)


# -- OntologyRegistry -----------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_register_and_get() -> None:
    """Registering an ontology allows retrieval by entity name."""
    registry = OntologyRegistry()
    ontology = _payslip_ontology()
    registered = await registry.register_ontology(ontology)
    assert registered is ontology

    fetched = await registry.get_ontology("Payslip")
    assert fetched.ontology_id == "ontology.payslip.v1"


@pytest.mark.asyncio
async def test_registry_duplicate_registration_raises() -> None:
    """Registering the same entity_name + version twice raises DocumentTemplateError."""
    registry = OntologyRegistry()
    await registry.register_ontology(_payslip_ontology())
    with pytest.raises(DocumentTemplateError, match="Duplicate ontology registration"):
        await registry.register_ontology(_payslip_ontology())


@pytest.mark.asyncio
async def test_registry_get_not_found_raises() -> None:
    """Requesting an unregistered entity raises DocumentTemplateError."""
    registry = OntologyRegistry()
    with pytest.raises(DocumentTemplateError, match="not found in registry"):
        await registry.get_ontology("Invoice")


@pytest.mark.asyncio
async def test_registry_get_specific_version_not_found_raises() -> None:
    """Requesting a registered entity's unregistered version raises DocumentTemplateError."""
    registry = OntologyRegistry()
    await registry.register_ontology(_payslip_ontology(version="1.0.0"))
    with pytest.raises(DocumentTemplateError, match=r"version '2.0.0' not found"):
        await registry.get_ontology("Payslip", version="2.0.0")


@pytest.mark.asyncio
async def test_registry_semver_resolution_latest_version() -> None:
    """Omitting version resolves to the highest registered SemVer."""
    registry = OntologyRegistry()
    await registry.register_ontology(_payslip_ontology(version="1.0.0"))
    await registry.register_ontology(_payslip_ontology(version="1.1.0"))

    latest = await registry.get_ontology("Payslip")
    assert latest.version == "1.1.0"

    specific = await registry.get_ontology("Payslip", version="1.0.0")
    assert specific.version == "1.0.0"


@pytest.mark.asyncio
async def test_registry_list_ontologies_returns_latest_per_entity() -> None:
    """list_ontologies returns exactly the latest version for each registered entity."""
    registry = OntologyRegistry()
    await registry.register_ontology(_payslip_ontology(version="1.0.0"))
    await registry.register_ontology(_payslip_ontology(version="1.1.0"))

    invoice_ontology = DocumentOntology(
        ontology_id="ontology.invoice.v1",
        entity_name="Invoice",
        version="1.0.0",
    )
    await registry.register_ontology(invoice_ontology)

    listed = await registry.list_ontologies()
    assert len(listed) == 2
    versions_by_entity = {o.entity_name: o.version for o in listed}
    assert versions_by_entity["Payslip"] == "1.1.0"
    assert versions_by_entity["Invoice"] == "1.0.0"
