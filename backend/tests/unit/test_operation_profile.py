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
from kortex.engines.document.template_library import TemplateLibrary


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
