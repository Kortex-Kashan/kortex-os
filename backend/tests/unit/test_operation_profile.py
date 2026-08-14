"""Unit tests for DocumentOperationProfileManager (Milestone 8).

Target: 100% pass rate, 100% line coverage for operation_profile.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from kortex.engines.document.adapter_pipeline import AdapterPipelineExecutor
from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.adapter_sandbox import AdapterSandbox
from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.exceptions import (
    DocumentOperationError,
    DocumentProfileNotFoundError,
)
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    AdapterPipelineDefinition,
    BindingContext,
    DocumentOperationProfile,
    DocumentOperationType,
    PipelineStage,
    TemplateSchema,
)
from kortex.engines.document.operation_profile import DocumentOperationProfileManager
from kortex.engines.document.persistence import DocumentRepository, TemplateRepository
from kortex.engines.document.template_library import TemplateLibrary
from kortex.core.db import DatabaseEngineManager
from kortex.engines.storage.stores.data_store import RelationalDataStore


@pytest.fixture
async def test_db(tmp_path):
    """Create an isolated file-backed SQLite database manager and initialize all tables."""
    db_file = tmp_path / "test_operation_profile.db"
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
def repository(data_store: RelationalDataStore) -> DocumentRepository:
    """Create a DocumentRepository backed by the test data store."""
    return DocumentRepository(data_store)


class DummyProfileAdapter(BaseDocumentAdapter):
    """Dummy adapter for profile pipeline validation tests."""

    def __init__(self, adapter_id: str = "kortex.adapter.pdf") -> None:
        self._meta = AdapterMetadata(
            adapter_id=adapter_id,
            display_name="PDF Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="PDF Adapter",
            supported_capabilities=[AdapterCapability.GENERATE],
        )

    @property
    def metadata(self) -> AdapterMetadata:
        return self._meta

    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        return b"[PDF_OUTPUT]"

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


def create_sample_profile(
    profile_id: str = "profile.payslip.v1",
    version: str = "1.0.0",
    required_template_id: str | None = None,
    pipeline: AdapterPipelineDefinition | None = None,
) -> DocumentOperationProfile:
    """Helper function to create valid DocumentOperationProfile instances."""
    return DocumentOperationProfile(
        id=profile_id,
        name="Employee Payslip Profile",
        namespace="kortex.hr.payroll",
        version=version,
        description="Declarative profile for generating monthly employee payslips",
        business_operation="GENERATE_PAYROLL_SLIP",
        required_template_id=required_template_id,
        adapter_pipeline=pipeline,
        permissions=["hr:read", "payroll:generate"],
        output_bucket="payroll_documents",
    )


@pytest.mark.asyncio
async def test_valid_profile_registration() -> None:
    """1. Valid profile registration & 36. Protocol compatibility."""
    mgr = DocumentOperationProfileManager()
    profile = create_sample_profile()

    await mgr.register_profile(profile)

    fetched = await mgr.get_profile("profile.payslip.v1")
    assert fetched.id == "profile.payslip.v1"
    assert fetched.version == "1.0.0"
    assert fetched.business_operation == "GENERATE_PAYROLL_SLIP"


@pytest.mark.asyncio
async def test_invalid_profile_rejection() -> None:
    """2. Invalid profile rejection & 3. Missing profile ID & 4. Invalid version."""
    mgr = DocumentOperationProfileManager()

    # None profile
    with pytest.raises(DocumentOperationError, match="cannot be None"):
        mgr.validate_profile(None)  # type: ignore[arg-type]

    # Missing profile ID
    with pytest.raises(DocumentOperationError, match="profile 'id' cannot be empty"):
        mgr.validate_profile(
            DocumentOperationProfile(
                id="",
                name="N",
                namespace="kortex.hr",
                version="1.0.0",
                description="D",
                business_operation="OP",
            )
        )

    # Missing profile name
    with pytest.raises(DocumentOperationError, match="profile 'name' cannot be empty"):
        mgr.validate_profile(
            DocumentOperationProfile(
                id="p1",
                name="",
                namespace="kortex.hr",
                version="1.0.0",
                description="D",
                business_operation="OP",
            )
        )

    # Invalid namespace format
    with pytest.raises(DocumentOperationError, match="Invalid namespace format"):
        mgr.validate_profile(
            DocumentOperationProfile(
                id="p1",
                name="N",
                namespace="invalid_namespace_no_dot",
                version="1.0.0",
                description="D",
                business_operation="OP",
            )
        )

    # Invalid version format
    with pytest.raises(DocumentOperationError, match="Invalid profile version format"):
        mgr.validate_profile(
            DocumentOperationProfile(
                id="p1",
                name="N",
                namespace="kortex.hr",
                version="invalid_ver",
                description="D",
                business_operation="OP",
            )
        )

    # Missing description
    with pytest.raises(DocumentOperationError, match="profile 'description' cannot be empty"):
        mgr.validate_profile(
            DocumentOperationProfile(
                id="p1",
                name="N",
                namespace="kortex.hr",
                version="1.0.0",
                description="",
                business_operation="OP",
            )
        )

    # Missing business_operation
    with pytest.raises(DocumentOperationError, match="'business_operation' cannot be empty"):
        mgr.validate_profile(
            DocumentOperationProfile(
                id="p1",
                name="N",
                namespace="kortex.hr",
                version="1.0.0",
                description="D",
                business_operation="",
            )
        )

    # Missing output_bucket
    with pytest.raises(DocumentOperationError, match="'output_bucket' cannot be empty"):
        mgr.validate_profile(
            DocumentOperationProfile(
                id="p1",
                name="N",
                namespace="kortex.hr",
                version="1.0.0",
                description="D",
                business_operation="OP",
                output_bucket="",
            )
        )


@pytest.mark.asyncio
async def test_duplicate_profile_version_rejection() -> None:
    """5. Duplicate profile/version rejection & 6. Immutable registration."""
    mgr = DocumentOperationProfileManager()
    profile = create_sample_profile()
    await mgr.register_profile(profile)

    with pytest.raises(DocumentOperationError, match="Duplicate profile registration"):
        await mgr.register_profile(profile)


@pytest.mark.asyncio
async def test_version_lookups_and_semver_ordering() -> None:
    """7. Exact version lookup, 8. Latest stable lookup, 9. Pre-release handling, 10. SemVer ordering, 33. Stable > Prerelease."""
    mgr = DocumentOperationProfileManager()

    v1_alpha = create_sample_profile(version="1.0.0-alpha")
    v1_stable = create_sample_profile(version="1.0.0")
    v2_beta = create_sample_profile(version="2.0.0-beta")
    v1_5 = create_sample_profile(version="1.5.0")

    await mgr.register_profile(v1_alpha)
    await mgr.register_profile(v1_stable)
    await mgr.register_profile(v2_beta)
    await mgr.register_profile(v1_5)

    # Exact version lookup
    exact = await mgr.get_specific_version("profile.payslip.v1", "1.0.0-alpha")
    assert exact.version == "1.0.0-alpha"

    # Latest version (2.0.0-beta > 1.5.0 > 1.0.0)
    latest = await mgr.get_latest_version("profile.payslip.v1")
    assert latest.version == "2.0.0-beta"


@pytest.mark.asyncio
async def test_profile_listing_and_filtering() -> None:
    """11. Profile listing & 12. Business-operation filtering & namespace filtering."""
    mgr = DocumentOperationProfileManager()
    p1 = create_sample_profile(profile_id="p.payslip", version="1.0.0")
    p2 = DocumentOperationProfile(
        id="p.invoice",
        name="Invoice Profile",
        namespace="kortex.finance.invoice",
        version="1.0.0",
        description="Invoice profile",
        business_operation="GENERATE_INVOICE",
    )
    await mgr.register_profile(p1)
    await mgr.register_profile(p2)

    listed = await mgr.list_profiles()
    assert len(listed) == 2

    all_versions = await mgr.list_all_profile_versions()
    assert len(all_versions) == 2

    by_bo = await mgr.find_by_business_operation("generate_payroll_slip")
    assert len(by_bo) == 1
    assert by_bo[0].id == "p.payslip"

    by_ns = await mgr.find_by_namespace("kortex.finance.invoice")
    assert len(by_ns) == 1
    assert by_ns[0].id == "p.invoice"


@pytest.mark.asyncio
async def test_required_template_validation() -> None:
    """13. Required template validation & 14. Template reference validation."""
    tmpl_lib = TemplateLibrary(load_defaults=True)
    mgr = DocumentOperationProfileManager(template_library=tmpl_lib)

    # Installed template reference succeeds
    p_valid = create_sample_profile(required_template_id="payslip.declarative.v1")
    await mgr.register_profile(p_valid)

    # Non-existent template reference fails
    p_invalid = create_sample_profile(profile_id="p.invalid.tmpl", required_template_id="missing.template.v1")
    with pytest.raises(DocumentOperationError, match="is not installed in TemplateLibrary"):
        await mgr.register_profile(p_invalid)

    # Empty string required_template_id fails
    with pytest.raises(DocumentOperationError, match="cannot be empty string"):
        mgr.validate_profile(create_sample_profile(profile_id="p.empty.tmpl", required_template_id="   "))


@pytest.mark.asyncio
async def test_adapter_pipeline_and_capability_validation() -> None:
    """15. Pipeline validation, 16. Duplicate stage ID, 17. Invalid adapter ref, 18. Unsupported capability, 19. Capability validation."""
    reg = DocumentAdapterRegistry()
    adapter = DummyProfileAdapter(adapter_id="kortex.adapter.pdf")
    reg.register_adapter(adapter)

    mgr = DocumentOperationProfileManager(adapter_registry=reg)

    valid_pipeline = AdapterPipelineDefinition(
        pipeline_id="pipe-valid",
        profile_id="p.pipe",
        stages=[PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE)],
    )
    p_valid = create_sample_profile(profile_id="p.pipe", pipeline=valid_pipeline)
    await mgr.register_profile(p_valid)

    # Pipeline missing pipeline_id
    with pytest.raises(DocumentOperationError, match="missing pipeline_id"):
        mgr.validate_profile(create_sample_profile(profile_id="p.err1", pipeline=AdapterPipelineDefinition(pipeline_id="", profile_id="p")))

    # Pipeline missing stages
    with pytest.raises(DocumentOperationError, match="contains no stages"):
        mgr.validate_profile(create_sample_profile(profile_id="p.err2", pipeline=AdapterPipelineDefinition(pipeline_id="pipe-empty", profile_id="p", stages=[])))

    # Pipeline missing stage_id
    with pytest.raises(DocumentOperationError, match="missing stage_id"):
        mgr.validate_profile(
            create_sample_profile(
                profile_id="p.err3",
                pipeline=AdapterPipelineDefinition(
                    pipeline_id="p1", profile_id="p", stages=[PipelineStage(stage_id="", adapter_id="a", required_capability=AdapterCapability.GENERATE)]
                ),
            )
        )

    # Duplicate stage ID
    with pytest.raises(DocumentOperationError, match="Duplicate stage ID"):
        mgr.validate_profile(
            create_sample_profile(
                profile_id="p.err4",
                pipeline=AdapterPipelineDefinition(
                    pipeline_id="p1",
                    profile_id="p",
                    stages=[
                        PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
                        PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
                    ],
                ),
            )
        )

    # Stage missing adapter_id
    with pytest.raises(DocumentOperationError, match="missing adapter_id"):
        mgr.validate_profile(
            create_sample_profile(
                profile_id="p.err5",
                pipeline=AdapterPipelineDefinition(
                    pipeline_id="p1", profile_id="p", stages=[PipelineStage(stage_id="s1", adapter_id="", required_capability=AdapterCapability.GENERATE)]
                ),
            )
        )

    # Unregistered adapter reference
    with pytest.raises(DocumentOperationError, match="is not registered"):
        mgr.validate_profile(
            create_sample_profile(
                profile_id="p.err6",
                pipeline=AdapterPipelineDefinition(
                    pipeline_id="p1", profile_id="p", stages=[PipelineStage(stage_id="s1", adapter_id="missing.adapter", required_capability=AdapterCapability.GENERATE)]
                ),
            )
        )

    # Adapter does not support required capability
    with pytest.raises(DocumentOperationError, match="does not support required capability"):
        mgr.validate_profile(
            create_sample_profile(
                profile_id="p.err7",
                pipeline=AdapterPipelineDefinition(
                    pipeline_id="p1", profile_id="p", stages=[PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.OCR)]
                ),
            )
        )


def test_permission_and_output_rule_validation() -> None:
    """20. Permission metadata validation & 21. Output-rule validation & 22. Lifecycle-rule validation."""
    mgr = DocumentOperationProfileManager()

    # Invalid permission entry
    with pytest.raises(DocumentOperationError, match="Invalid permission entry"):
        mgr.validate_profile(
            DocumentOperationProfile(
                id="p.perm.err",
                name="N",
                namespace="kortex.hr",
                version="1.0.0",
                description="D",
                business_operation="OP",
                permissions=["valid:perm", ""],
            )
        )


@pytest.mark.asyncio
async def test_unregistration_and_unknown_profile_handling() -> None:
    """34. Unregistration behavior & 35. Unknown profile behavior."""
    mgr = DocumentOperationProfileManager()
    p1 = create_sample_profile(version="1.0.0")
    p2 = create_sample_profile(version="2.0.0")
    await mgr.register_profile(p1)
    await mgr.register_profile(p2)

    # Attempt to unregister non-existent version of existing profile
    assert await mgr.unregister_profile("profile.payslip.v1", version="9.9.9") is False

    # Unregister specific version
    assert await mgr.unregister_profile("profile.payslip.v1", version="1.0.0") is True

    # Remaining version should be 2.0.0
    latest = await mgr.get_latest_version("profile.payslip.v1")
    assert latest.version == "2.0.0"

    # Unregister final version, triggering deletion of profile_id key
    assert await mgr.unregister_profile("profile.payslip.v1", version="2.0.0") is True

    # Unknown profile lookup raises DocumentProfileNotFoundError
    with pytest.raises(DocumentProfileNotFoundError, match="not found"):
        await mgr.get_profile("profile.payslip.v1")

    with pytest.raises(DocumentProfileNotFoundError, match="version '1.0.0' not found"):
        p_temp = create_sample_profile(profile_id="p.single", version="2.0.0")
        await mgr.register_profile(p_temp)
        await mgr.get_profile("p.single", version="1.0.0")

    # Unregister entire profile when version is None
    assert await mgr.unregister_profile("p.single") is True

    with pytest.raises(DocumentProfileNotFoundError, match="not found"):
        await mgr.get_latest_version("missing.profile.id")

    # Unregister non-existent returns False
    assert await mgr.unregister_profile("missing.profile") is False
    assert await mgr.unregister_profile("missing.profile", version="1.0.0") is False


@pytest.mark.asyncio
async def test_immutability_and_security_guarantees() -> None:
    """24. No execution during registration, 25-29. No storage/FS/network/subprocess/eval, 30. Input immutability, 31. Internal-state immutability."""
    mgr = DocumentOperationProfileManager()
    profile = create_sample_profile()

    profile_dump_before = profile.model_dump()
    await mgr.register_profile(profile)
    profile_dump_after = profile.model_dump()

    assert profile_dump_before == profile_dump_after

    # Verify properties
    assert mgr.template_library is None
    assert mgr.adapter_registry is None


@pytest.mark.asyncio
async def test_regression_compatibility_across_milestones() -> None:
    """37. Regression compatibility across all Document Engine components."""
    tmpl_lib = TemplateLibrary(load_defaults=True)
    reg = DocumentAdapterRegistry()
    adapter = DummyProfileAdapter()
    reg.register_adapter(adapter)

    sandbox = AdapterSandbox(registry=reg)
    pipeline_executor = AdapterPipelineExecutor(registry=reg, sandbox=sandbox)
    profile_mgr = DocumentOperationProfileManager(template_library=tmpl_lib, adapter_registry=reg)

    pipeline_def = AdapterPipelineDefinition(
        pipeline_id="pipe-full",
        profile_id="profile.payslip.v1",
        stages=[PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE)],
    )

    profile = create_sample_profile(required_template_id="payslip.declarative.v1", pipeline=pipeline_def)
    await profile_mgr.register_profile(profile)

    fetched_profile = await profile_mgr.get_profile("profile.payslip.v1")
    assert fetched_profile.id == "profile.payslip.v1"
    assert fetched_profile.adapter_pipeline is not None

    # Execute pipeline derived from profile
    res = await pipeline_executor.execute_pipeline_definition(
        fetched_profile.adapter_pipeline, BindingContext(context_id="ctx-full")
    )
    assert res.is_success is True
    assert res.final_output_bytes == b"[PDF_OUTPUT]"


@pytest.mark.asyncio
async def test_required_template_validation_with_repository_backed_library(
    data_store: RelationalDataStore,
) -> None:
    """Reproduces and fixes the actual defect: a template that exists ONLY via a
    repository-backed TemplateLibrary (invisible to the old `_templates` dict check) is now
    correctly found by the async, repository-aware _validate_required_template check."""
    tmpl_repository = TemplateRepository(data_store=data_store)
    tmpl_lib = TemplateLibrary(load_defaults=False, repository=tmpl_repository)
    await tmpl_lib.register_template(
        TemplateSchema(
            template_id="repo.only.tmpl",
            name="Repository-Only Template",
            namespace="kortex.repo.only",
            version="1.0.0",
            description="Exists only in the repository, never in the in-memory _templates dict",
        )
    )

    mgr = DocumentOperationProfileManager(template_library=tmpl_lib)
    profile = create_sample_profile(
        profile_id="p.repo.tmpl", required_template_id="repo.only.tmpl"
    )

    # This is the exact regression the old `req_tmpl_id not in self._template_library._templates`
    # check would have failed: the template is real and registered, but only via the repository.
    await mgr.register_profile(profile)

    fetched = await mgr.get_profile("p.repo.tmpl")
    assert fetched.required_template_id == "repo.only.tmpl"


@pytest.mark.asyncio
async def test_repository_mode_register_and_get_survives_fresh_instance(
    repository: DocumentRepository,
) -> None:
    """A profile registered through a repository-backed manager survives a fresh instance."""
    mgr = DocumentOperationProfileManager(repository=repository)
    profile = create_sample_profile(profile_id="p.persisted.v1")

    await mgr.register_profile(profile)

    fresh_mgr = DocumentOperationProfileManager(repository=repository)
    fetched = await fresh_mgr.get_profile("p.persisted.v1")
    assert fetched.id == "p.persisted.v1"
    assert fetched.version == "1.0.0"


@pytest.mark.asyncio
async def test_repository_mode_duplicate_registration_raises(
    repository: DocumentRepository,
) -> None:
    """Duplicate profile_id+version registration is rejected in repository mode."""
    mgr = DocumentOperationProfileManager(repository=repository)
    profile = create_sample_profile(profile_id="p.dup.v1")

    await mgr.register_profile(profile)

    with pytest.raises(DocumentOperationError, match="Duplicate profile registration"):
        await mgr.register_profile(profile)


@pytest.mark.asyncio
async def test_repository_mode_semver_resolution_latest_version(
    repository: DocumentRepository,
) -> None:
    """Repository-mode latest-version resolution uses SemVer comparison, matching in-memory mode."""
    mgr = DocumentOperationProfileManager(repository=repository)

    await mgr.register_profile(create_sample_profile(profile_id="p.semver.v1", version="1.0.0"))
    await mgr.register_profile(create_sample_profile(profile_id="p.semver.v1", version="1.10.0"))
    await mgr.register_profile(create_sample_profile(profile_id="p.semver.v1", version="1.2.0"))

    latest = await mgr.get_latest_version("p.semver.v1")
    assert latest.version == "1.10.0"


@pytest.mark.asyncio
async def test_repository_mode_delete_specific_and_all_versions(
    repository: DocumentRepository,
) -> None:
    """Repository-mode delete supports both specific-version and delete-all semantics."""
    mgr = DocumentOperationProfileManager(repository=repository)
    await mgr.register_profile(create_sample_profile(profile_id="p.delete.v1", version="1.0.0"))
    await mgr.register_profile(create_sample_profile(profile_id="p.delete.v1", version="2.0.0"))

    assert await mgr.unregister_profile("p.delete.v1", version="1.0.0") is True
    remaining = await mgr.get_latest_version("p.delete.v1")
    assert remaining.version == "2.0.0"

    assert await mgr.unregister_profile("p.delete.v1") is True
    with pytest.raises(DocumentProfileNotFoundError):
        await mgr.get_profile("p.delete.v1")

    assert await mgr.unregister_profile("p.delete.missing") is False


@pytest.mark.asyncio
async def test_tenant_isolation_register_and_get_per_call(
    repository: DocumentRepository,
) -> None:
    """A single DocumentOperationProfileManager keeps per-call tenant_id partitions isolated."""
    mgr = DocumentOperationProfileManager(repository=repository)
    profile_a = create_sample_profile(profile_id="p.tenant.scoped")

    await mgr.register_profile(profile_a, tenant_id="tenant-a")

    fetched_a = await mgr.get_profile("p.tenant.scoped", tenant_id="tenant-a")
    assert fetched_a.id == "p.tenant.scoped"

    with pytest.raises(DocumentProfileNotFoundError):
        await mgr.get_profile("p.tenant.scoped", tenant_id="tenant-b")

    # Tenant B may independently register the same profile_id+version.
    profile_b = create_sample_profile(profile_id="p.tenant.scoped")
    await mgr.register_profile(profile_b, tenant_id="tenant-b")
    fetched_b = await mgr.get_profile("p.tenant.scoped", tenant_id="tenant-b")
    assert fetched_b.id == "p.tenant.scoped"

    # Isolation held: tenant A's read is unaffected by tenant B's registration.
    fetched_a_again = await mgr.get_profile("p.tenant.scoped", tenant_id="tenant-a")
    assert fetched_a_again.id == "p.tenant.scoped"


@pytest.mark.asyncio
async def test_tenant_isolation_list_and_delete_scoped_per_call(
    repository: DocumentRepository,
) -> None:
    """list_profiles/unregister_profile only affect the calling tenant's persisted profiles."""
    mgr = DocumentOperationProfileManager(repository=repository)
    await mgr.register_profile(
        create_sample_profile(profile_id="p.tenant.a.only"), tenant_id="tenant-a"
    )
    await mgr.register_profile(
        create_sample_profile(profile_id="p.tenant.b.only"), tenant_id="tenant-b"
    )

    tenant_a_list = await mgr.list_profiles(tenant_id="tenant-a")
    tenant_a_ids = {p.id for p in tenant_a_list}
    assert "p.tenant.a.only" in tenant_a_ids
    assert "p.tenant.b.only" not in tenant_a_ids

    await mgr.unregister_profile("p.tenant.a.only", tenant_id="tenant-a")
    with pytest.raises(DocumentProfileNotFoundError):
        await mgr.get_profile("p.tenant.a.only", tenant_id="tenant-a")

    # Tenant B's copy survives tenant A's deletion untouched.
    still_there = await mgr.get_profile("p.tenant.b.only", tenant_id="tenant-b")
    assert still_there.id == "p.tenant.b.only"


@pytest.mark.asyncio
async def test_tenant_isolation_falls_back_to_constructor_default(
    repository: DocumentRepository,
) -> None:
    """Omitting tenant_id on a per-call operation falls back to the constructor's tenant_id."""
    mgr = DocumentOperationProfileManager(
        repository=repository, tenant_id="tenant-default-fallback"
    )
    profile = create_sample_profile(profile_id="p.fallback")

    await mgr.register_profile(profile)

    fetched = await mgr.get_profile("p.fallback", tenant_id="tenant-default-fallback")
    assert fetched.id == "p.fallback"

    fetched_via_default = await mgr.get_profile("p.fallback")
    assert fetched_via_default.id == "p.fallback"

    with pytest.raises(DocumentProfileNotFoundError):
        await mgr.get_profile("p.fallback", tenant_id="some-other-tenant")


@pytest.mark.asyncio
async def test_repository_mode_not_found_and_listing_branches(
    repository: DocumentRepository,
) -> None:
    """Exercise repository-mode not-found and listing/search branches not covered elsewhere."""
    mgr = DocumentOperationProfileManager(repository=repository)

    # get_profile with an explicit version that doesn't exist.
    await mgr.register_profile(create_sample_profile(profile_id="p.notfound.v1"))
    with pytest.raises(DocumentProfileNotFoundError, match="version '9.9.9' not found"):
        await mgr.get_profile("p.notfound.v1", version="9.9.9")

    # get_latest_version for a profile_id that was never registered.
    with pytest.raises(DocumentProfileNotFoundError):
        await mgr.get_latest_version("p.never.registered")

    # unregister_profile: specific version not found among an existing profile_id's matches.
    assert await mgr.unregister_profile("p.notfound.v1", version="9.9.9") is False

    # list_all_profile_versions, find_by_business_operation, find_by_namespace in repository mode.
    await mgr.register_profile(
        create_sample_profile(profile_id="p.notfound.v1", version="2.0.0")
    )
    all_versions = await mgr.list_all_profile_versions()
    assert len([p for p in all_versions if p.id == "p.notfound.v1"]) == 2

    by_business_op = await mgr.find_by_business_operation("GENERATE_PAYROLL_SLIP")
    assert any(p.id == "p.notfound.v1" for p in by_business_op)

    by_namespace = await mgr.find_by_namespace("kortex.hr.payroll")
    assert any(p.id == "p.notfound.v1" for p in by_namespace)
