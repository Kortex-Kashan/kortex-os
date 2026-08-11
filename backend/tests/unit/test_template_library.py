"""Unit tests for TemplateLibrary (Milestone 3).

Target: 100% pass rate, 100% line coverage for template_library.py.
"""

from __future__ import annotations

import pytest

from kortex.engines.document.exceptions import DocumentTemplateError
from kortex.engines.document.models import AdapterCapability, TemplateSchema
from kortex.engines.document.template_library import TemplateLibrary, parse_semver


def test_parse_semver_valid() -> None:
    """Test parse_semver with valid SemVer strings."""
    assert parse_semver("1.0.0") == (1, 0, 0, 1, "")
    assert parse_semver("2.15.3-alpha") == (2, 15, 3, 0, "alpha")
    assert parse_semver("0.1.0+build.100") == (0, 1, 0, 1, "")


def test_parse_semver_invalid() -> None:
    """Test parse_semver with invalid SemVer strings."""
    with pytest.raises(DocumentTemplateError, match="Invalid semantic version format"):
        parse_semver("1.0")

    with pytest.raises(DocumentTemplateError, match="Invalid semantic version format"):
        parse_semver("v1.0.0")

    with pytest.raises(DocumentTemplateError, match="Invalid semantic version format"):
        parse_semver("invalid_version")


@pytest.mark.asyncio
async def test_standard_templates_preloaded() -> None:
    """Test that standard declarative templates are pre-loaded by default."""
    lib = TemplateLibrary(load_defaults=True)
    templates = await lib.list_templates()
    assert len(templates) >= 11

    invoice_tmpl = await lib.get_template("invoice.declarative.v1")
    assert invoice_tmpl.name == "Standard Invoice Template"
    assert invoice_tmpl.namespace == "kortex.finance.invoice"

    payslip_tmpl = await lib.get_template("payslip.declarative.v1")
    assert payslip_tmpl.name == "Standard Payslip Template"
    assert payslip_tmpl.namespace == "kortex.hr.payroll"


@pytest.mark.asyncio
async def test_register_and_get_template() -> None:
    """Test registering a new template schema and retrieving it."""
    lib = TemplateLibrary(load_defaults=False)
    schema = TemplateSchema(
        template_id="custom.template.v1",
        name="Custom Test Template",
        namespace="kortex.custom.domain",
        version="1.0.0",
        description="A test template",
        placeholders=["user_id", "title"],
        required_fields=["user_id"],
    )

    installed = await lib.install_template(schema)
    assert installed is True

    fetched = await lib.get_template("custom.template.v1")
    assert fetched.template_id == "custom.template.v1"
    assert fetched.version == "1.0.0"

    specific = await lib.get_specific_version("custom.template.v1", "1.0.0")
    assert specific == fetched


@pytest.mark.asyncio
async def test_semver_resolution_latest_version() -> None:
    """Test latest version resolution using SemVer comparison."""
    lib = TemplateLibrary(load_defaults=False)
    v1 = TemplateSchema(
        template_id="multi.version.tmpl",
        name="Multi Version Template",
        namespace="kortex.multi.domain",
        version="1.0.0",
        description="Version 1.0.0",
    )
    v2 = TemplateSchema(
        template_id="multi.version.tmpl",
        name="Multi Version Template",
        namespace="kortex.multi.domain",
        version="1.2.0",
        description="Version 1.2.0",
    )
    v3 = TemplateSchema(
        template_id="multi.version.tmpl",
        name="Multi Version Template",
        namespace="kortex.multi.domain",
        version="2.0.0",
        description="Version 2.0.0",
    )
    v1_1 = TemplateSchema(
        template_id="multi.version.tmpl",
        name="Multi Version Template",
        namespace="kortex.multi.domain",
        version="1.1.5",
        description="Version 1.1.5",
    )

    await lib.register_template(v1)
    await lib.register_template(v2)
    await lib.register_template(v3)
    await lib.register_template(v1_1)

    latest = await lib.get_latest_version("multi.version.tmpl")
    assert latest.version == "2.0.0"

    specific = await lib.get_template("multi.version.tmpl", version="1.1.5")
    assert specific.version == "1.1.5"


@pytest.mark.asyncio
async def test_duplicate_registration_rejection() -> None:
    """Test duplicate registration of exact template_id and version is rejected."""
    lib = TemplateLibrary(load_defaults=False)
    schema = TemplateSchema(
        template_id="dup.tmpl",
        name="Dup Template",
        namespace="kortex.dup.domain",
        version="1.0.0",
        description="Dup description",
    )

    await lib.register_template(schema)

    with pytest.raises(DocumentTemplateError, match="Duplicate template registration"):
        await lib.register_template(schema)

    with pytest.raises(DocumentTemplateError, match="Cannot update immutable registered template"):
        await lib.update_template(schema)


@pytest.mark.asyncio
async def test_update_template_new_version() -> None:
    """Test update_template registers a new version of an existing template."""
    lib = TemplateLibrary(load_defaults=False)
    v1 = TemplateSchema(
        template_id="upd.tmpl",
        name="Upd Template",
        namespace="kortex.upd.domain",
        version="1.0.0",
        description="Initial version",
    )
    v2 = TemplateSchema(
        template_id="upd.tmpl",
        name="Upd Template v2",
        namespace="kortex.upd.domain",
        version="2.0.0",
        description="Updated version",
    )

    await lib.register_template(v1)
    res = await lib.update_template(v2)
    assert res.version == "2.0.0"

    latest = await lib.get_latest_version("upd.tmpl")
    assert latest.version == "2.0.0"


@pytest.mark.asyncio
async def test_validation_missing_required_fields() -> None:
    """Test validation errors for missing or empty required fields."""
    lib = TemplateLibrary(load_defaults=False)

    # Empty template_id
    with pytest.raises(DocumentTemplateError, match="template_id"):
        await lib.register_template(
            TemplateSchema(
                template_id="",
                name="Name",
                namespace="kortex.test",
                version="1.0.0",
                description="Desc",
            )
        )

    # Empty name
    with pytest.raises(DocumentTemplateError, match="name"):
        await lib.register_template(
            TemplateSchema(
                template_id="id1",
                name="",
                namespace="kortex.test",
                version="1.0.0",
                description="Desc",
            )
        )

    # Empty description
    with pytest.raises(DocumentTemplateError, match="description"):
        await lib.register_template(
            TemplateSchema(
                template_id="id1",
                name="Name",
                namespace="kortex.test",
                version="1.0.0",
                description="",
            )
        )


@pytest.mark.asyncio
async def test_validation_invalid_namespace() -> None:
    """Test validation of invalid namespace formats."""
    lib = TemplateLibrary(load_defaults=False)

    with pytest.raises(DocumentTemplateError, match="Invalid namespace format"):
        await lib.register_template(
            TemplateSchema(
                template_id="id1",
                name="Name",
                namespace="invalid_no_dot",
                version="1.0.0",
                description="Desc",
            )
        )

    with pytest.raises(DocumentTemplateError, match="Invalid namespace format"):
        await lib.register_template(
            TemplateSchema(
                template_id="id1",
                name="Name",
                namespace="kortex invalid space",
                version="1.0.0",
                description="Desc",
            )
        )


@pytest.mark.asyncio
async def test_validation_invalid_placeholders_and_required_fields() -> None:
    """Test validation of invalid placeholder and required field strings."""
    lib = TemplateLibrary(load_defaults=False)

    # Invalid placeholder
    with pytest.raises(DocumentTemplateError, match="Invalid placeholder definition"):
        await lib.register_template(
            TemplateSchema(
                template_id="id1",
                name="Name",
                namespace="kortex.test",
                version="1.0.0",
                description="Desc",
                placeholders=["valid_field", "invalid field with space!"],
            )
        )

    # Invalid required field
    with pytest.raises(DocumentTemplateError, match="Invalid required field definition"):
        await lib.register_template(
            TemplateSchema(
                template_id="id1",
                name="Name",
                namespace="kortex.test",
                version="1.0.0",
                description="Desc",
                placeholders=["valid_field"],
                required_fields=["bad-field@symbol"],
            )
        )


@pytest.mark.asyncio
async def test_delete_template() -> None:
    """Test deleting specific version vs all versions of a template."""
    lib = TemplateLibrary(load_defaults=False)
    v1 = TemplateSchema(
        template_id="del.tmpl",
        name="Del Template",
        namespace="kortex.del.domain",
        version="1.0.0",
        description="V1",
    )
    v2 = TemplateSchema(
        template_id="del.tmpl",
        name="Del Template",
        namespace="kortex.del.domain",
        version="2.0.0",
        description="V2",
    )

    await lib.register_template(v1)
    await lib.register_template(v2)

    # Delete specific version v1
    deleted_v1 = await lib.delete_template("del.tmpl", version="1.0.0")
    assert deleted_v1 is True

    # Check v2 still exists
    remaining = await lib.get_template("del.tmpl")
    assert remaining.version == "2.0.0"

    # Delete remaining versions
    deleted_all = await lib.remove_template("del.tmpl")
    assert deleted_all is True

    # Verify template is completely gone
    with pytest.raises(DocumentTemplateError, match="not found"):
        await lib.get_template("del.tmpl")


@pytest.mark.asyncio
async def test_delete_non_existent_template() -> None:
    """Test delete_template error handling for non-existent template or version."""
    lib = TemplateLibrary(load_defaults=False)

    with pytest.raises(DocumentTemplateError, match="Cannot delete: Template 'missing' not found"):
        await lib.delete_template("missing")

    # Register template then attempt deleting non-existent version
    v1 = TemplateSchema(
        template_id="exist.tmpl",
        name="Exist",
        namespace="kortex.exist",
        version="1.0.0",
        description="Desc",
    )
    await lib.register_template(v1)

    with pytest.raises(DocumentTemplateError, match="version '9.9.9' not found"):
        await lib.delete_template("exist.tmpl", version="9.9.9")


@pytest.mark.asyncio
async def test_filtering_and_search() -> None:
    """Test namespace, business operation, capability, and query keyword filtering."""
    lib = TemplateLibrary(load_defaults=True)

    # Search by namespace
    payroll_templates = await lib.search_by_namespace("kortex.hr.payroll")
    assert len(payroll_templates) >= 2
    assert all(t.namespace == "kortex.hr.payroll" for t in payroll_templates)

    alias_namespace = await lib.get_by_namespace("kortex.finance.invoice")
    assert len(alias_namespace) == 1
    assert alias_namespace[0].template_id == "invoice.declarative.v1"

    # Search by business operation
    invoice_ops = await lib.search_by_business_operation("GENERATE_INVOICE")
    assert len(invoice_ops) == 1
    assert invoice_ops[0].template_id == "invoice.declarative.v1"

    # Search by capability
    gen_caps = await lib.search_by_capability(AdapterCapability.GENERATE)
    assert len(gen_caps) >= 11

    # Search by query keyword and tags
    query_matches = await lib.search_templates("invoice", tags=["billing"])
    assert len(query_matches) == 1
    assert query_matches[0].template_id == "invoice.declarative.v1"

    # Search query with non-matching tags
    no_matches = await lib.search_templates("invoice", tags=["non_existent_tag"])
    assert len(no_matches) == 0


@pytest.mark.asyncio
async def test_get_template_non_existent() -> None:
    """Test get_template with non-existent template ID or version."""
    lib = TemplateLibrary(load_defaults=False)

    with pytest.raises(DocumentTemplateError, match="not found in library"):
        await lib.get_template("unknown.template")

    with pytest.raises(DocumentTemplateError, match="not found in library"):
        await lib.get_latest_version("unknown.template")

    schema = TemplateSchema(
        template_id="known.template",
        name="Known",
        namespace="kortex.known",
        version="1.0.0",
        description="Desc",
    )
    await lib.register_template(schema)

    with pytest.raises(DocumentTemplateError, match="version '2.0.0' not found"):
        await lib.get_template("known.template", version="2.0.0")


@pytest.mark.asyncio
async def test_delete_single_version_removes_template_entry() -> None:
    """Test deleting the only version of a template removes the root template entry."""
    lib = TemplateLibrary(load_defaults=False)
    tmpl = TemplateSchema(
        template_id="single.ver.tmpl",
        name="Single Ver",
        namespace="kortex.single",
        version="1.0.0",
        description="Desc",
    )
    await lib.register_template(tmpl)
    deleted = await lib.delete_template("single.ver.tmpl", version="1.0.0")
    assert deleted is True

    with pytest.raises(DocumentTemplateError, match="not found in library"):
        await lib.get_template("single.ver.tmpl")


@pytest.mark.asyncio
async def test_list_templates_capability_mismatch() -> None:
    """Test list_templates capability filtering when template does not match capability."""
    lib = TemplateLibrary(load_defaults=True)
    ocr_templates = await lib.list_templates(capability=AdapterCapability.OCR)
    assert len(ocr_templates) == 0

