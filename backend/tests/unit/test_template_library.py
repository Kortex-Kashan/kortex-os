"""Unit tests for TemplateLibrary (Milestone 3).

Target: 100% pass rate, 100% line coverage for template_library.py.
"""

from __future__ import annotations

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.engines.document.exceptions import DocumentTemplateError
from kortex.engines.document.models import AdapterCapability, TemplateSchema
from kortex.engines.document.persistence import TemplateRepository
from kortex.engines.document.template_library import TemplateLibrary, parse_semver
from kortex.engines.storage.stores.cache_store import MemoryCacheStore
from kortex.engines.storage.stores.data_store import RelationalDataStore


@pytest.fixture
async def test_db(tmp_path):
    """Create an isolated file-backed SQLite database manager and initialize all tables."""
    db_file = tmp_path / "test_template_library.db"
    db_manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{db_file}")
    await db_manager.connect()
    await db_manager.create_all_tables()
    yield db_manager
    await db_manager.disconnect()


@pytest.fixture
def data_store(test_db: DatabaseEngineManager) -> RelationalDataStore:
    """Create a RelationalDataStore backed by the test database."""
    return RelationalDataStore(test_db)


@pytest.fixture
def repository(data_store: RelationalDataStore) -> TemplateRepository:
    """Create a TemplateRepository backed by the test data store."""
    return TemplateRepository(data_store=data_store)


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

    with pytest.raises(DocumentTemplateError, match=r"version '9.9.9' not found"):
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

    with pytest.raises(DocumentTemplateError, match=r"version '2.0.0' not found"):
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


# -- Persisted mode (Milestone 3: storage/data abstraction integration) -------


@pytest.mark.asyncio
async def test_repository_mode_register_and_get_survives_fresh_instance(
    data_store: RelationalDataStore,
) -> None:
    """A template registered through a repository-backed library is readable by a fresh instance."""
    repository = TemplateRepository(data_store=data_store)
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    assert lib.repository is repository

    schema = TemplateSchema(
        template_id="persisted.custom.v1",
        name="Persisted Custom Template",
        namespace="kortex.custom.persisted",
        version="1.0.0",
        description="A persisted test template",
        placeholders=["field_a"],
    )
    await lib.register_template(schema)

    fresh_lib = TemplateLibrary(load_defaults=False, repository=TemplateRepository(data_store=data_store))
    fetched = await fresh_lib.get_template("persisted.custom.v1")
    assert fetched.template_id == "persisted.custom.v1"
    assert fetched.namespace == "kortex.custom.persisted"


@pytest.mark.asyncio
async def test_repository_mode_duplicate_registration_raises(
    repository: TemplateRepository,
) -> None:
    """Registering the same template_id + version twice through a repository-backed library raises."""
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    schema = TemplateSchema(
        template_id="persisted.dup.v1",
        name="Persisted Dup Template",
        namespace="kortex.custom.persisted",
        version="1.0.0",
        description="Duplicate test",
    )
    await lib.register_template(schema)

    with pytest.raises(DocumentTemplateError, match="Duplicate template registration"):
        await lib.register_template(schema)


@pytest.mark.asyncio
async def test_repository_mode_cannot_shadow_standard_template(
    repository: TemplateRepository,
) -> None:
    """A repository-backed library still refuses to re-register a built-in standard template ID."""
    lib = TemplateLibrary(load_defaults=True, repository=repository)
    shadow_schema = TemplateSchema(
        template_id="invoice.declarative.v1",
        name="Malicious Shadow Invoice",
        namespace="kortex.finance.invoice",
        version="1.0.0",
        description="Attempted shadow of the standard invoice template",
    )
    with pytest.raises(DocumentTemplateError, match="Duplicate template registration"):
        await lib.register_template(shadow_schema)


@pytest.mark.asyncio
async def test_repository_mode_falls_back_to_standard_templates(
    repository: TemplateRepository,
) -> None:
    """A repository-backed library still resolves built-in standard templates not persisted."""
    lib = TemplateLibrary(load_defaults=True, repository=repository)
    invoice = await lib.get_template("invoice.declarative.v1")
    assert invoice.name == "Standard Invoice Template"

    templates = await lib.list_templates()
    assert len(templates) >= 11

    found = await lib.search_templates("payslip")
    assert any(t.template_id == "payslip.declarative.v1" for t in found)


@pytest.mark.asyncio
async def test_repository_mode_get_not_found_raises(repository: TemplateRepository) -> None:
    """Requesting an unregistered, non-standard template_id in repository mode raises."""
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    with pytest.raises(DocumentTemplateError, match="not found in library"):
        await lib.get_template("nonexistent.template.v1")


@pytest.mark.asyncio
async def test_repository_mode_update_rejects_existing_version(
    repository: TemplateRepository,
) -> None:
    """update_template in repository mode rejects an already-persisted version."""
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    schema = TemplateSchema(
        template_id="persisted.update.v1",
        name="Persisted Update Template",
        namespace="kortex.custom.persisted",
        version="1.0.0",
        description="Update test",
    )
    await lib.register_template(schema)

    with pytest.raises(DocumentTemplateError, match="Cannot update immutable"):
        await lib.update_template(schema)


@pytest.mark.asyncio
async def test_repository_mode_delete_specific_version(repository: TemplateRepository) -> None:
    """delete_template in repository mode removes a specific persisted version."""
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    schema = TemplateSchema(
        template_id="persisted.delete.v1",
        name="Persisted Delete Template",
        namespace="kortex.custom.persisted",
        version="1.0.0",
        description="Delete test",
    )
    await lib.register_template(schema)

    deleted = await lib.delete_template("persisted.delete.v1", version="1.0.0")
    assert deleted is True

    with pytest.raises(DocumentTemplateError, match="not found in library"):
        await lib.get_template("persisted.delete.v1")


@pytest.mark.asyncio
async def test_repository_mode_delete_missing_raises(repository: TemplateRepository) -> None:
    """delete_template in repository mode raises when the template_id doesn't exist."""
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    with pytest.raises(DocumentTemplateError, match="Cannot delete"):
        await lib.delete_template("nonexistent.delete.v1")


@pytest.mark.asyncio
async def test_repository_mode_delete_missing_version_raises(
    repository: TemplateRepository,
) -> None:
    """delete_template in repository mode raises when the specific version doesn't exist."""
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    schema = TemplateSchema(
        template_id="persisted.delete_ver.v1",
        name="Persisted Delete Version Template",
        namespace="kortex.custom.persisted",
        version="1.0.0",
        description="Delete version test",
    )
    await lib.register_template(schema)

    with pytest.raises(DocumentTemplateError, match=r"version '2.0.0' not found"):
        await lib.delete_template("persisted.delete_ver.v1", version="2.0.0")


@pytest.mark.asyncio
async def test_repository_mode_delete_all_versions(repository: TemplateRepository) -> None:
    """delete_template with no version in repository mode removes every persisted version."""
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    await lib.register_template(
        TemplateSchema(
            template_id="persisted.delete_all.v1",
            name="Persisted Delete All Template",
            namespace="kortex.custom.persisted",
            version="1.0.0",
            description="Delete all v1",
        )
    )
    await lib.register_template(
        TemplateSchema(
            template_id="persisted.delete_all.v1",
            name="Persisted Delete All Template",
            namespace="kortex.custom.persisted",
            version="2.0.0",
            description="Delete all v2",
        )
    )

    deleted = await lib.delete_template("persisted.delete_all.v1")
    assert deleted is True

    with pytest.raises(DocumentTemplateError, match="not found in library"):
        await lib.get_template("persisted.delete_all.v1")


@pytest.mark.asyncio
async def test_repository_mode_semver_resolution_latest_version(
    repository: TemplateRepository,
) -> None:
    """Repository-mode latest-version resolution uses SemVer comparison, matching in-memory mode."""
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    await lib.register_template(
        TemplateSchema(
            template_id="persisted.multi.v1",
            name="Persisted Multi Version",
            namespace="kortex.custom.persisted",
            version="1.0.0",
            description="v1",
        )
    )
    await lib.register_template(
        TemplateSchema(
            template_id="persisted.multi.v1",
            name="Persisted Multi Version",
            namespace="kortex.custom.persisted",
            version="1.10.0",
            description="v1.10",
        )
    )
    await lib.register_template(
        TemplateSchema(
            template_id="persisted.multi.v1",
            name="Persisted Multi Version",
            namespace="kortex.custom.persisted",
            version="1.2.0",
            description="v1.2",
        )
    )

    latest = await lib.get_latest_version("persisted.multi.v1")
    assert latest.version == "1.10.0"


@pytest.mark.asyncio
async def test_tenant_isolation_register_and_get_per_call(
    repository: TemplateRepository,
) -> None:
    """A single TemplateLibrary instance keeps per-call tenant_id partitions fully isolated."""
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    schema = TemplateSchema(
        template_id="tenant.scoped.tmpl",
        name="Tenant A Template",
        namespace="kortex.tenant.scoped",
        version="1.0.0",
        description="Registered under tenant-a",
    )

    await lib.register_template(schema, tenant_id="tenant-a")

    # Tenant A can read its own registration.
    fetched_a = await lib.get_template("tenant.scoped.tmpl", tenant_id="tenant-a")
    assert fetched_a.name == "Tenant A Template"

    # Tenant B cannot see tenant A's template through the same TemplateLibrary instance.
    with pytest.raises(DocumentTemplateError, match="not found"):
        await lib.get_template("tenant.scoped.tmpl", tenant_id="tenant-b")

    # Tenant B may register its own version of the same template_id+version independently.
    other_schema = TemplateSchema(
        template_id="tenant.scoped.tmpl",
        name="Tenant B Template",
        namespace="kortex.tenant.scoped",
        version="1.0.0",
        description="Registered under tenant-b",
    )
    await lib.register_template(other_schema, tenant_id="tenant-b")
    fetched_b = await lib.get_template("tenant.scoped.tmpl", tenant_id="tenant-b")
    assert fetched_b.name == "Tenant B Template"

    # Confirming isolation held: tenant A's read is unaffected by tenant B's registration.
    fetched_a_again = await lib.get_template("tenant.scoped.tmpl", tenant_id="tenant-a")
    assert fetched_a_again.name == "Tenant A Template"


@pytest.mark.asyncio
async def test_tenant_isolation_list_and_search_scoped_per_call(
    repository: TemplateRepository,
) -> None:
    """list_templates/search_templates only surface the calling tenant's persisted templates."""
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    await lib.register_template(
        TemplateSchema(
            template_id="tenant.a.only",
            name="Only In Tenant A",
            namespace="kortex.tenant.a",
            version="1.0.0",
            description="Belongs to tenant-a",
        ),
        tenant_id="tenant-a",
    )
    await lib.register_template(
        TemplateSchema(
            template_id="tenant.b.only",
            name="Only In Tenant B",
            namespace="kortex.tenant.b",
            version="1.0.0",
            description="Belongs to tenant-b",
        ),
        tenant_id="tenant-b",
    )

    tenant_a_list = await lib.list_templates(tenant_id="tenant-a")
    tenant_a_ids = {t.template_id for t in tenant_a_list}
    assert "tenant.a.only" in tenant_a_ids
    assert "tenant.b.only" not in tenant_a_ids

    tenant_b_search = await lib.search_templates("Only In", tenant_id="tenant-b")
    tenant_b_ids = {t.template_id for t in tenant_b_search}
    assert "tenant.b.only" in tenant_b_ids
    assert "tenant.a.only" not in tenant_b_ids


@pytest.mark.asyncio
async def test_tenant_isolation_delete_scoped_per_call(
    repository: TemplateRepository,
) -> None:
    """Deleting a template under one tenant_id must not affect another tenant's copy."""
    lib = TemplateLibrary(load_defaults=False, repository=repository)
    schema_a = TemplateSchema(
        template_id="tenant.delete.tmpl",
        name="Tenant A Delete Target",
        namespace="kortex.tenant.delete",
        version="1.0.0",
        description="Tenant A copy",
    )
    schema_b = TemplateSchema(
        template_id="tenant.delete.tmpl",
        name="Tenant B Delete Target",
        namespace="kortex.tenant.delete",
        version="1.0.0",
        description="Tenant B copy",
    )
    await lib.register_template(schema_a, tenant_id="tenant-a")
    await lib.register_template(schema_b, tenant_id="tenant-b")

    await lib.delete_template("tenant.delete.tmpl", tenant_id="tenant-a")

    with pytest.raises(DocumentTemplateError, match="not found"):
        await lib.get_template("tenant.delete.tmpl", tenant_id="tenant-a")

    # Tenant B's copy survives tenant A's deletion untouched.
    still_there = await lib.get_template("tenant.delete.tmpl", tenant_id="tenant-b")
    assert still_there.name == "Tenant B Delete Target"


@pytest.mark.asyncio
async def test_tenant_isolation_standard_templates_available_to_every_tenant(
    repository: TemplateRepository,
) -> None:
    """Built-in standard templates remain available regardless of the calling tenant_id."""
    lib = TemplateLibrary(load_defaults=True, repository=repository)

    invoice_for_a = await lib.get_template("invoice.declarative.v1", tenant_id="tenant-a")
    invoice_for_b = await lib.get_template("invoice.declarative.v1", tenant_id="tenant-b")
    assert invoice_for_a.name == "Standard Invoice Template"
    assert invoice_for_b.name == "Standard Invoice Template"


@pytest.mark.asyncio
async def test_tenant_isolation_falls_back_to_constructor_default(
    repository: TemplateRepository,
) -> None:
    """Omitting tenant_id on a per-call operation falls back to the constructor's tenant_id."""
    lib = TemplateLibrary(load_defaults=False, repository=repository, tenant_id="tenant-default-fallback")
    schema = TemplateSchema(
        template_id="fallback.tmpl",
        name="Fallback Template",
        namespace="kortex.fallback",
        version="1.0.0",
        description="Registered with no explicit tenant_id override",
    )

    await lib.register_template(schema)

    # Explicit lookup under the constructor's own default tenant_id finds it.
    fetched = await lib.get_template("fallback.tmpl", tenant_id="tenant-default-fallback")
    assert fetched.name == "Fallback Template"

    # Omitting tenant_id on the call also finds it (falls back to the same constructor default).
    fetched_via_default = await lib.get_template("fallback.tmpl")
    assert fetched_via_default.name == "Fallback Template"

    # A different tenant_id does not see it.
    with pytest.raises(DocumentTemplateError, match="not found"):
        await lib.get_template("fallback.tmpl", tenant_id="some-other-tenant")


# =============================================================================
# Milestone 7: Template Schema Cache
# =============================================================================


@pytest.mark.asyncio
async def test_template_schema_cache_read_through_on_latest_lookup() -> None:
    """Test that a latest-version get_template() populates the Template Schema Cache
    and that a subsequent call is served from cache."""
    cache_store = MemoryCacheStore()
    lib = TemplateLibrary(load_defaults=False, cache_store=cache_store, tenant_id="tenant-cache")
    assert lib.cache_store is cache_store

    schema = TemplateSchema(
        template_id="cache.tmpl",
        name="Cache Template",
        namespace="kortex.cache.test",
        version="1.0.0",
        description="Template used to exercise the Template Schema Cache",
    )
    await lib.register_template(schema, tenant_id="tenant-cache")

    cache_key = TemplateLibrary._template_cache_key("cache.tmpl", "tenant-cache")

    # register_template's own invalidation should leave no stale entry beforehand.
    assert await cache_store.get(cache_key) is None

    first = await lib.get_template("cache.tmpl", tenant_id="tenant-cache")
    assert first.name == "Cache Template"
    assert await cache_store.get(cache_key) is not None

    second = await lib.get_template("cache.tmpl", tenant_id="tenant-cache")
    assert second.name == "Cache Template"


@pytest.mark.asyncio
async def test_template_schema_cache_specific_version_lookup_is_never_cached() -> None:
    """Test that pinned-version lookups (version supplied) bypass the cache entirely."""
    cache_store = MemoryCacheStore()
    lib = TemplateLibrary(load_defaults=False, cache_store=cache_store, tenant_id="tenant-cache-pin")

    schema = TemplateSchema(
        template_id="pinned.tmpl",
        name="Pinned Template",
        namespace="kortex.cache.pinned",
        version="1.0.0",
        description="Template used to verify pinned-version lookups are never cached",
    )
    await lib.register_template(schema, tenant_id="tenant-cache-pin")

    await lib.get_template("pinned.tmpl", version="1.0.0", tenant_id="tenant-cache-pin")

    latest_key = TemplateLibrary._template_cache_key("pinned.tmpl", "tenant-cache-pin")
    assert await cache_store.get(latest_key) is None


@pytest.mark.asyncio
async def test_template_schema_cache_invalidated_on_register_new_version() -> None:
    """Test that registering a new version invalidates the cached latest-version resolution."""
    cache_store = MemoryCacheStore()
    lib = TemplateLibrary(load_defaults=False, cache_store=cache_store, tenant_id="tenant-cache-inv")

    v1 = TemplateSchema(
        template_id="invalidate.tmpl",
        name="Invalidate V1",
        namespace="kortex.cache.invalidate",
        version="1.0.0",
        description="Version 1",
    )
    await lib.register_template(v1, tenant_id="tenant-cache-inv")

    cached_v1 = await lib.get_template("invalidate.tmpl", tenant_id="tenant-cache-inv")
    assert cached_v1.name == "Invalidate V1"

    v2 = TemplateSchema(
        template_id="invalidate.tmpl",
        name="Invalidate V2",
        namespace="kortex.cache.invalidate",
        version="1.1.0",
        description="Version 2",
    )
    await lib.register_template(v2, tenant_id="tenant-cache-inv")

    # Cache must have been invalidated by the new registration, not served the stale V1 result.
    resolved = await lib.get_template("invalidate.tmpl", tenant_id="tenant-cache-inv")
    assert resolved.name == "Invalidate V2"
    assert resolved.version == "1.1.0"


@pytest.mark.asyncio
async def test_template_schema_cache_invalidated_on_delete(
    repository: TemplateRepository,
) -> None:
    """Test that delete_template invalidates the cached latest-version resolution."""
    cache_store = MemoryCacheStore()
    lib = TemplateLibrary(
        load_defaults=False, repository=repository, cache_store=cache_store, tenant_id="tenant-cache-del"
    )

    schema = TemplateSchema(
        template_id="delete.cache.tmpl",
        name="Delete Cache Template",
        namespace="kortex.cache.delete",
        version="1.0.0",
        description="Template used to verify delete invalidates the cache",
    )
    await lib.register_template(schema, tenant_id="tenant-cache-del")
    await lib.get_template("delete.cache.tmpl", tenant_id="tenant-cache-del")

    cache_key = TemplateLibrary._template_cache_key("delete.cache.tmpl", "tenant-cache-del")
    assert await cache_store.get(cache_key) is not None

    await lib.delete_template("delete.cache.tmpl", tenant_id="tenant-cache-del")
    assert await cache_store.get(cache_key) is None

    with pytest.raises(DocumentTemplateError, match="not found"):
        await lib.get_template("delete.cache.tmpl", tenant_id="tenant-cache-del")


@pytest.mark.asyncio
async def test_template_schema_cache_absent_preserves_uncached_behavior() -> None:
    """Test that omitting cache_store preserves exactly today's uncached resolution behavior."""
    lib = TemplateLibrary(load_defaults=True)
    assert lib.cache_store is None

    tmpl = await lib.get_template("invoice.declarative.v1")
    assert tmpl.name == "Standard Invoice Template"
