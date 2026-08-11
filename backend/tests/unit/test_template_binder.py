"""Unit tests for TemplateBinder (Milestone 4).

Target: 100% pass rate, 100% line coverage for template_binder.py.
"""

from __future__ import annotations

import pytest

from kortex.engines.document.exceptions import DocumentTemplateError
from kortex.engines.document.models import BindingContext, TemplateSchema, ValidationReport
from kortex.engines.document.template_binder import BindingResult, TemplateBinder, resolve_dotted_path
from kortex.engines.document.template_library import TemplateLibrary


def test_resolve_dotted_path_helper() -> None:
    """Test resolve_dotted_path helper function edge cases."""
    data = {
        "simple": "val",
        "nested": {"level1": {"level2": 42}},
        "dotted.key": "direct_dotted_val",
        "non_dict_level": "string_val",
    }

    assert resolve_dotted_path(data, "simple") == (True, "val")
    assert resolve_dotted_path(data, "nested.level1.level2") == (True, 42)
    assert resolve_dotted_path(data, "dotted.key") == (True, "direct_dotted_val")
    assert resolve_dotted_path(data, "non_existent") == (False, None)
    assert resolve_dotted_path(data, "nested.invalid_key") == (False, None)
    assert resolve_dotted_path(data, "non_dict_level.sub_key") == (False, None)
    assert resolve_dotted_path({}, "key") == (False, None)
    assert resolve_dotted_path("not_a_dict", "key") == (False, None)  # type: ignore[arg-type]
    assert resolve_dotted_path(data, "") == (False, None)


@pytest.mark.asyncio
async def test_bind_simple_placeholders() -> None:
    """1. Bind simple placeholders."""
    lib = TemplateLibrary(load_defaults=False)
    binder = TemplateBinder(template_library=lib)
    assert binder.library is lib

    schema = TemplateSchema(
        template_id="simple.tmpl",
        name="Simple Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Simple placeholders test",
        placeholders=["user_name"],
    )
    context = BindingContext(context_id="ctx-1", data={"user_name": "Alice"})

    result = await binder.bind_schema(schema, context)
    assert result.validation_report.is_valid is True
    assert result.resolved_values["user_name"] == "Alice"
    assert len(result.unresolved_placeholders) == 0


@pytest.mark.asyncio
async def test_bind_multiple_placeholders() -> None:
    """2. Bind multiple placeholders."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="multi.tmpl",
        name="Multi Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Multiple placeholders test",
        placeholders=["employee_name", "basic_salary", "net_salary"],
    )
    context = BindingContext(
        context_id="ctx-2",
        data={
            "employee_name": "John",
            "basic_salary": 50000,
            "net_salary": 45000,
        },
    )

    result = await binder.bind_schema(schema, context)
    assert result.validation_report.is_valid is True
    assert result.resolved_values == {
        "employee_name": "John",
        "basic_salary": 50000,
        "net_salary": 45000,
    }


@pytest.mark.asyncio
async def test_bind_computed_fields() -> None:
    """3. Bind computed fields."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="comp.tmpl",
        name="Computed Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Computed fields test",
        placeholders=["gross_salary", "tax_amount"],
    )
    context = BindingContext(
        context_id="ctx-3",
        data={"gross_salary": 60000},
        computed_fields={"tax_amount": 12000},
    )

    result = await binder.bind_schema(schema, context)
    assert result.validation_report.is_valid is True
    assert result.resolved_values["tax_amount"] == 12000
    assert "tax_amount" in result.validation_report.computed_fields_resolved


@pytest.mark.asyncio
async def test_bind_nested_dotted_path_fields() -> None:
    """4. Bind nested dotted-path fields."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="nested.tmpl",
        name="Nested Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Nested dotted path test",
        placeholders=["employee.name", "employee.department.name", "salary.net_amount"],
    )
    context = BindingContext(
        context_id="ctx-4",
        data={
            "employee": {
                "name": "Jane",
                "department": {"name": "Engineering"},
            },
            "salary": {"net_amount": 75000},
        },
    )

    result = await binder.bind_schema(schema, context)
    assert result.validation_report.is_valid is True
    assert result.resolved_values["employee.name"] == "Jane"
    assert result.resolved_values["employee.department.name"] == "Engineering"
    assert result.resolved_values["salary.net_amount"] == 75000


@pytest.mark.asyncio
async def test_missing_required_field() -> None:
    """5. Missing required field."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="req.tmpl",
        name="Required Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Required field test",
        placeholders=["employee_name"],
        required_fields=["employee_id"],
    )
    context = BindingContext(context_id="ctx-5", data={"employee_name": "Bob"})

    result = await binder.bind_schema(schema, context)
    assert result.validation_report.is_valid is False
    assert "employee_id" in result.validation_report.missing_placeholders
    assert len(result.validation_report.errors) == 1
    assert "Missing required field: 'employee_id'" in result.validation_report.errors[0]


@pytest.mark.asyncio
async def test_missing_optional_field() -> None:
    """6. Missing optional field."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="opt.tmpl",
        name="Optional Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Optional field test",
        placeholders=["required_name", "optional_middle_name"],
        required_fields=["required_name"],
    )
    context = BindingContext(context_id="ctx-6", data={"required_name": "Charlie"})

    result = await binder.bind_schema(schema, context)
    assert result.validation_report.is_valid is True
    assert "optional_middle_name" in result.unresolved_placeholders
    assert len(result.validation_report.warnings) == 1
    assert "Missing optional placeholder: 'optional_middle_name'" in result.validation_report.warnings[0]


@pytest.mark.asyncio
async def test_unknown_placeholder() -> None:
    """7. Unknown placeholder in context data (extra fields ignored cleanly)."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="extra.tmpl",
        name="Extra Fields Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Extra fields test",
        placeholders=["known_field"],
    )
    context = BindingContext(
        context_id="ctx-7",
        data={"known_field": "Known", "unknown_field": "Unknown"},
    )

    result = await binder.bind_schema(schema, context)
    assert result.validation_report.is_valid is True
    assert "known_field" in result.resolved_values
    assert "unknown_field" not in result.resolved_values


@pytest.mark.asyncio
async def test_invalid_placeholder_identifier() -> None:
    """8. Invalid placeholder identifier in schema."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="inv.tmpl",
        name="Invalid Placeholder Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Invalid placeholder test",
        placeholders=["invalid placeholder with space!"],
    )
    context = BindingContext(context_id="ctx-8", data={})

    result = await binder.bind_schema(schema, context)
    assert result.validation_report.is_valid is False
    assert len(result.validation_report.errors) == 1
    assert "Invalid placeholder identifier" in result.validation_report.errors[0]


@pytest.mark.asyncio
async def test_template_not_found() -> None:
    """9. Template not found in library."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    context = BindingContext(context_id="ctx-9")

    with pytest.raises(DocumentTemplateError, match="not found in library"):
        await binder.bind_template("non_existent_tmpl", context)


@pytest.mark.asyncio
async def test_specific_template_version() -> None:
    """10. Specific template version binding."""
    lib = TemplateLibrary(load_defaults=False)
    v1 = TemplateSchema(
        template_id="ver.tmpl",
        name="Versioned Template",
        namespace="kortex.test",
        version="1.0.0",
        description="V1",
        placeholders=["v1_field"],
    )
    v2 = TemplateSchema(
        template_id="ver.tmpl",
        name="Versioned Template",
        namespace="kortex.test",
        version="2.0.0",
        description="V2",
        placeholders=["v2_field"],
    )
    await lib.register_template(v1)
    await lib.register_template(v2)

    binder = TemplateBinder(template_library=lib)
    context = BindingContext(context_id="ctx-10", data={"v1_field": "V1_val", "v2_field": "V2_val"})

    res_v1 = await binder.bind_template("ver.tmpl", context, version="1.0.0")
    assert res_v1.version == "1.0.0"
    assert "v1_field" in res_v1.resolved_values

    res_v2 = await binder.bind_template("ver.tmpl", context, version="2.0.0")
    assert res_v2.version == "2.0.0"
    assert "v2_field" in res_v2.resolved_values


@pytest.mark.asyncio
async def test_latest_template_version() -> None:
    """11. Latest template version binding when version is omitted."""
    lib = TemplateLibrary(load_defaults=False)
    v1 = TemplateSchema(
        template_id="latest.tmpl",
        name="Latest Template",
        namespace="kortex.test",
        version="1.0.0",
        description="V1",
    )
    v2 = TemplateSchema(
        template_id="latest.tmpl",
        name="Latest Template",
        namespace="kortex.test",
        version="2.5.0",
        description="V2",
    )
    await lib.register_template(v1)
    await lib.register_template(v2)

    binder = TemplateBinder(template_library=lib)
    context = BindingContext(context_id="ctx-11")

    res = await binder.bind_template("latest.tmpl", context)
    assert res.version == "2.5.0"


@pytest.mark.asyncio
async def test_templateschema_immutability() -> None:
    """12. TemplateSchema immutability after binding."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="immut.schema",
        name="Immutable Schema",
        namespace="kortex.test",
        version="1.0.0",
        description="Immutability test",
        placeholders=["key1"],
    )
    context = BindingContext(context_id="ctx-12", data={"key1": "val1"})

    schema_dict_before = schema.model_dump()
    await binder.bind_schema(schema, context)
    schema_dict_after = schema.model_dump()

    assert schema_dict_before == schema_dict_after


@pytest.mark.asyncio
async def test_bindingcontext_immutability() -> None:
    """13. BindingContext immutability after binding."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="immut.context",
        name="Immutable Context",
        namespace="kortex.test",
        version="1.0.0",
        description="Immutability test",
        placeholders=["key1"],
    )
    context = BindingContext(context_id="ctx-13", data={"key1": "val1"})

    context_dict_before = context.model_dump()
    await binder.bind_schema(schema, context)
    context_dict_after = context.model_dump()

    assert context_dict_before == context_dict_after


@pytest.mark.asyncio
async def test_empty_binding_context() -> None:
    """14. Empty binding context with required fields fails validation deterministically."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="empty.ctx.tmpl",
        name="Empty Context Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Empty context test",
        required_fields=["mandatory_field"],
    )
    context = BindingContext(context_id="ctx-14")

    result = await binder.bind_schema(schema, context)
    assert result.validation_report.is_valid is False
    assert "mandatory_field" in result.validation_report.missing_placeholders


@pytest.mark.asyncio
async def test_deterministic_output() -> None:
    """15. Deterministic output given identical inputs."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="det.tmpl",
        name="Deterministic Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Deterministic test",
        placeholders=["a", "b"],
        required_fields=["a"],
    )
    context = BindingContext(context_id="ctx-15", data={"a": 10, "b": 20})

    result1 = await binder.bind_schema(schema, context)
    result2 = await binder.bind_schema(schema, context)

    assert result1.model_dump() == result2.model_dump()


@pytest.mark.asyncio
async def test_computed_fields_precedence_over_data() -> None:
    """16. Duplicate/ambiguous data resolution (computed fields take precedence)."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="ambig.tmpl",
        name="Ambiguous Field Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Precedence test",
        placeholders=["total_amount"],
    )
    context = BindingContext(
        context_id="ctx-16",
        data={"total_amount": 100},
        computed_fields={"total_amount": 150},
    )

    result = await binder.bind_schema(schema, context)
    assert result.resolved_values["total_amount"] == 150
    assert "total_amount" in result.validation_report.computed_fields_resolved


@pytest.mark.asyncio
async def test_security_against_code_execution() -> None:
    """17. Security test proving arbitrary expressions/code are not executed."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    malicious_payload = "__import__('os').system('echo HACKED')"
    schema = TemplateSchema(
        template_id="sec.tmpl",
        name="Security Test Template",
        namespace="kortex.test",
        version="1.0.0",
        description="Security test",
        placeholders=["payload"],
    )
    context = BindingContext(context_id="ctx-17", data={"payload": malicious_payload})

    result = await binder.bind_schema(schema, context)
    assert result.validation_report.is_valid is True
    # Verify string was treated strictly as a literal value and NOT evaluated
    assert result.resolved_values["payload"] == malicious_payload


@pytest.mark.asyncio
async def test_validation_report_correctness() -> None:
    """18. ValidationReport correctness verification."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="val.report.tmpl",
        name="Report Template",
        namespace="kortex.test",
        version="1.0.0",
        description="ValidationReport correctness test",
        placeholders=["opt_f"],
        required_fields=["req_f"],
    )
    context = BindingContext(context_id="ctx-18", computed_fields={"comp_f": "val"})

    report = await binder.bind(schema, context)
    assert isinstance(report, ValidationReport)
    assert report.is_valid is False
    assert "req_f" in report.missing_placeholders
    assert "opt_f" in report.missing_placeholders
    assert len(report.errors) == 1
    assert len(report.warnings) == 1


@pytest.mark.asyncio
async def test_multiple_templates_same_context() -> None:
    """19. Multiple templates using the same BindingContext instance."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema1 = TemplateSchema(
        template_id="tmpl1",
        name="Template 1",
        namespace="kortex.test",
        version="1.0.0",
        description="T1",
        placeholders=["user_id"],
    )
    schema2 = TemplateSchema(
        template_id="tmpl2",
        name="Template 2",
        namespace="kortex.test",
        version="1.0.0",
        description="T2",
        placeholders=["user_id", "email"],
    )
    context = BindingContext(
        context_id="shared-ctx",
        data={"user_id": "usr-100", "email": "usr@kortex.os"},
    )

    res1 = await binder.bind_schema(schema1, context)
    res2 = await binder.bind_schema(schema2, context)

    assert res1.resolved_values == {"user_id": "usr-100"}
    assert res2.resolved_values == {"user_id": "usr-100", "email": "usr@kortex.os"}


@pytest.mark.asyncio
async def test_regression_compatibility_with_standard_templates() -> None:
    """20. Regression compatibility with standard pre-loaded templates in TemplateLibrary."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=True))

    invoice_context = BindingContext(
        context_id="inv-ctx-100",
        data={
            "invoice_number": "INV-2026-001",
            "customer_name": "ACME Corp",
            "total_amount": 1250.50,
            "issue_date": "2026-08-08",
        },
    )

    res_inv = await binder.bind_template("invoice.declarative.v1", invoice_context)
    assert res_inv.validation_report.is_valid is True
    assert res_inv.resolved_values["invoice_number"] == "INV-2026-001"
    assert res_inv.resolved_values["total_amount"] == 1250.50

    payslip_context = BindingContext(
        context_id="pay-ctx-200",
        data={
            "employee_id": "EMP-789",
            "employee_name": "David Miller",
            "basic_salary": 8000,
            "net_salary": 7200,
            "period": "2026-08",
        },
    )

    res_pay = await binder.bind_template("payslip.declarative.v1", payslip_context)
    assert res_pay.validation_report.is_valid is True
    assert res_pay.resolved_values["employee_id"] == "EMP-789"
    assert res_pay.resolved_values["net_salary"] == 7200


@pytest.mark.asyncio
async def test_invalid_input_error_handling() -> None:
    """Test error handling when bind_schema receives None inputs."""
    binder = TemplateBinder(template_library=TemplateLibrary(load_defaults=False))
    schema = TemplateSchema(
        template_id="err.tmpl",
        name="Error",
        namespace="kortex.test",
        version="1.0.0",
        description="Err",
    )
    context = BindingContext(context_id="ctx-err")

    with pytest.raises(DocumentTemplateError, match="TemplateSchema input cannot be None"):
        await binder.bind_schema(None, context)  # type: ignore[arg-type]

    with pytest.raises(DocumentTemplateError, match="BindingContext input cannot be None"):
        await binder.bind_schema(schema, None)  # type: ignore[arg-type]
