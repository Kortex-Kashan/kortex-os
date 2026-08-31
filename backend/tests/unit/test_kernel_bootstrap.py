"""Unit tests for `kortex.api.kernel_bootstrap.build_and_boot_kernel`.

M3 shipped this module with only Storage + Security registered (confirmed
during the M5 preflight audit — no test previously exercised it directly).
M5 adds the Connector Engine; M6 adds the Workflow Engine; M7 adds the
Marketplace Engine; Slice 4.6 adds the AI Orchestration Engine; Slice 4.7
adds Document + Knowledge Engines. These tests prove that wiring lands on
the real production boot path, not only on the hand-built test kernels
`test_capability_dispatch.py` / `test_connector_engine.py` /
`test_workflow_engine.py` / `test_marketplace_engine.py` / `test_ai_engine.py`
/ `test_document_diagnostics.py` / `test_knowledge_engine.py` construct for
themselves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.api.kernel_bootstrap import build_and_boot_kernel
from kortex.core.kernel import KernelState
from kortex.engines.ai.engine import AIOrchestrationEngine
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.exceptions import ConnectorProfileNotFoundError
from kortex.engines.connector.models import ActionRequest, ConnectorActionType, ConnectorProfile
from kortex.engines.document.engine import DocumentEngine
from kortex.engines.knowledge.engine import KnowledgeEngine
from kortex.engines.marketplace.engine import MarketplaceEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.workflow.engine import WorkflowEngine


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(tmp_path / "kernel_bootstrap_storage"))


@pytest.mark.asyncio
async def test_connector_engine_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        assert kernel.state == KernelState.RUNNING
        connector_engine = kernel.get_engine("connector")
        assert isinstance(connector_engine, ConnectorEngine)
        assert connector_engine.status() == "RUNNING"
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_connector_driver_list_capability_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        descriptor = kernel.get_capability("kortex.connector.driver.list")
        assert descriptor.provider == "connector"
        assert descriptor.required_permissions == ["connector:read"]
        assert descriptor.requires_authentication is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_connector_registry_starts_empty_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        connector_engine = kernel.get_engine("connector")
        assert isinstance(connector_engine, ConnectorEngine)
        assert connector_engine.list_drivers() == []
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_workflow_engine_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        assert kernel.state == KernelState.RUNNING
        workflow_engine = kernel.get_engine("workflow")
        assert isinstance(workflow_engine, WorkflowEngine)
        assert workflow_engine.status() == "RUNNING"
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_workflow_definition_list_capability_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        descriptor = kernel.get_capability("kortex.workflow.definition.list")
        assert descriptor.provider == "workflow"
        assert descriptor.required_permissions == ["workflow:read"]
        assert descriptor.requires_authentication is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_workflow_registry_starts_empty_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        workflow_engine = kernel.get_engine("workflow")
        assert isinstance(workflow_engine, WorkflowEngine)
        assert workflow_engine.list_definitions() == []
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_marketplace_engine_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        assert kernel.state == KernelState.RUNNING
        marketplace_engine = kernel.get_engine("marketplace")
        assert isinstance(marketplace_engine, MarketplaceEngine)
        assert marketplace_engine.status() == "RUNNING"
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_marketplace_listing_list_capability_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        descriptor = kernel.get_capability("kortex.marketplace.listing.list")
        assert descriptor.provider == "marketplace"
        assert descriptor.required_permissions == ["marketplace:read"]
        assert descriptor.requires_authentication is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_marketplace_registry_starts_empty_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        marketplace_engine = kernel.get_engine("marketplace")
        assert isinstance(marketplace_engine, MarketplaceEngine)
        assert marketplace_engine.list_listings() == []
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_ai_engine_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        assert kernel.state == KernelState.RUNNING
        ai_engine = kernel.get_engine("ai")
        assert isinstance(ai_engine, AIOrchestrationEngine)
        assert ai_engine.status() in ("READY", "RUNNING")
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_ai_provider_list_capability_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        descriptor = kernel.get_capability("kortex.ai.provider.list")
        assert descriptor.provider == "ai"
        assert descriptor.required_permissions == ["ai:read"]
        assert descriptor.requires_authentication is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_ai_model_list_capability_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        descriptor = kernel.get_capability("kortex.ai.model.list")
        assert descriptor.provider == "ai"
        assert descriptor.required_permissions == ["ai:read"]
        assert descriptor.requires_authentication is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_ai_provider_registry_has_real_ollama_provider_on_production_boot_path() -> None:
    """M6.1-2: unlike Connector/Workflow/Marketplace's genuinely empty starting
    registries, the AI Engine now registers one real `OllamaProvider`, sourced
    from `SystemSettings.ollama_url`/`ollama_default_model` -- this is not
    fabricated demo data, it is the production boot path's own real,
    unconditional wiring (whether or not an actual Ollama instance is
    reachable at boot time; reachability is a `health_check()`/circuit-
    breaker concern, not a registration-time one)."""
    kernel = await build_and_boot_kernel()
    try:
        ai_engine = kernel.get_engine("ai")
        assert isinstance(ai_engine, AIOrchestrationEngine)

        providers = ai_engine.list_providers()
        assert len(providers) == 1
        assert providers[0].provider_id == "ollama-llama3"
        assert providers[0].vendor == "ollama"
        assert providers[0].endpoint_type == "local_host"
        assert providers[0].url == "http://localhost:11434"
        assert providers[0].credential_requirement == "none"

        models = ai_engine.list_models()
        assert len(models) == 1
        assert models[0].model_id == "llama3"
        assert models[0].provider_id == "ollama-llama3"
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_document_engine_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        assert kernel.state == KernelState.RUNNING
        document_engine = kernel.get_engine("document")
        assert isinstance(document_engine, DocumentEngine)
        assert document_engine.status() in ("READY", "RUNNING")
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_document_adapter_and_template_list_capabilities_register_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        adapter_descriptor = kernel.get_capability("kortex.document.adapter.list")
        assert adapter_descriptor.provider == "document"
        assert adapter_descriptor.required_permissions == ["document:read"]
        assert adapter_descriptor.requires_authentication is True

        template_descriptor = kernel.get_capability("kortex.document.template.list")
        assert template_descriptor.provider == "document"
        assert template_descriptor.required_permissions == ["document:read"]
        assert template_descriptor.requires_authentication is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_document_adapter_registry_has_real_discovered_reference_adapters_on_production_boot_path() -> None:
    """Unlike Connector's genuinely empty starting registry, DocumentEngine's
    `initialize()` auto-discovers and registers real in-package reference
    adapters (`DocumentAdapterLoader.load_and_register_all()`) — not
    fabricated demo data, the engine's own existing, real behavior."""
    kernel = await build_and_boot_kernel()
    try:
        document_engine = kernel.get_engine("document")
        assert isinstance(document_engine, DocumentEngine)
        assert len(document_engine.list_adapters()) > 0
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_document_template_library_has_real_standard_templates_on_production_boot_path() -> None:
    """Unlike Connector/Workflow/Marketplace's genuinely empty starting
    registries, TemplateLibrary ships pre-seeded with real standard
    templates (`TemplateLibrary._load_standard_templates`) — this is not
    fabricated demo data, it is the engine's own existing, real behavior."""
    kernel = await build_and_boot_kernel()
    try:
        document_engine = kernel.get_engine("document")
        assert isinstance(document_engine, DocumentEngine)
        templates = await document_engine.list_templates()
        assert len(templates) > 0
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_knowledge_engine_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        assert kernel.state == KernelState.RUNNING
        knowledge_engine = kernel.get_engine("knowledge")
        assert isinstance(knowledge_engine, KnowledgeEngine)
        assert knowledge_engine.status() in ("READY", "RUNNING")
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_knowledge_search_and_traverse_capabilities_register_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        search_descriptor = kernel.get_capability("kortex.knowledge.query.search")
        assert search_descriptor.provider == "knowledge"
        assert search_descriptor.required_permissions == ["knowledge:read"]
        assert search_descriptor.requires_authentication is True

        traverse_descriptor = kernel.get_capability("kortex.knowledge.graph.traverse")
        assert traverse_descriptor.provider == "knowledge"
        assert traverse_descriptor.required_permissions == ["knowledge:read"]
        assert traverse_descriptor.requires_authentication is True

        list_descriptor = kernel.get_capability("kortex.knowledge.graph.list")
        assert list_descriptor.provider == "knowledge"
        assert list_descriptor.required_permissions == ["knowledge:read"]
        assert list_descriptor.requires_authentication is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_knowledge_graph_starts_empty_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        knowledge_engine = kernel.get_engine("knowledge")
        assert isinstance(knowledge_engine, KnowledgeEngine)
        assert knowledge_engine.graph.list_nodes("any-tenant") == []
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_connector_secret_resolver_wired_on_production_boot_path() -> None:
    """M6.0-2 regression test: prior to this fix, `ConnectorEngine()` was
    constructed with no `secret_resolver` anywhere on the production boot
    path (`kernel_bootstrap.py`), so any connector action against a profile
    with a real `secret_handle` failed with "Secret resolver unavailable."
    unconditionally — this is the actual bug, proven end-to-end through the
    real production bootstrap, not a proxy for it: a real secret is
    provisioned via the real, Kernel-registered `SecurityEngine`, a profile
    referencing that secret is registered on the real, Kernel-registered
    `ConnectorEngine`, and a real action is executed.
    """
    kernel = await build_and_boot_kernel()
    try:
        security_engine = kernel.get_engine("security")
        assert isinstance(security_engine, SecurityEngine)
        connector_engine = kernel.get_engine("connector")
        assert isinstance(connector_engine, ConnectorEngine)

        # Prior to the fix, this resolver is None even after a full boot.
        assert connector_engine.pipeline._secret_resolver is not None

        connector_engine.register_driver(DummyConnectorDriver())

        secret_handle = "vault:m6-0-2-regression-secret"
        tenant_id = "tenant-m6-0-2"
        await security_engine.put_secret(secret_handle, tenant_id, "resolved-plaintext-token")

        profile = ConnectorProfile(
            profile_id="prof-m6-0-2-regression",
            tenant_id=tenant_id,
            name="M6.0-2 Regression Profile",
            driver_id="connector-dummy",
            secret_handle=secret_handle,
        )
        await connector_engine.profile_manager.register_profile(profile)

        request = ActionRequest(
            request_id="req-m6-0-2-regression",
            profile_id="prof-m6-0-2-regression",
            action_type=ConnectorActionType.FETCH,
            tenant_id=tenant_id,
        )
        result = await connector_engine.execute_action(request)

        assert result.status == "SUCCESS"
        assert result.error_details is None
        assert result.response_payload["secret_authenticated"] is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_connector_secret_resolver_stays_tenant_scoped_on_production_boot_path() -> None:
    """A secret provisioned under one tenant must never resolve for a
    connector profile executed under a different tenant — the specific
    tenant-safety property the M6.0-2 fix is required to preserve (a
    single-argument, hardcoded-tenant resolver would not catch this).

    M6.3-1 closes this even earlier than M6.0-2 did: the profile itself is
    now tenant-scoped (`prof-m6-0-2-cross-tenant` genuinely belongs to
    `tenant-real-owner`, the same tenant that owns the secret it
    references), so an attacker-claimed `tenant_id="tenant-attacker"`
    request now fails at profile resolution — it can no longer even
    discover the profile exists — rather than reaching secret resolution
    and failing there. Both are fail-closed; this is strictly stronger.
    """
    kernel = await build_and_boot_kernel()
    try:
        security_engine = kernel.get_engine("security")
        connector_engine = kernel.get_engine("connector")
        connector_engine.register_driver(DummyConnectorDriver())

        secret_handle = "vault:m6-0-2-tenant-scoped-secret"
        await security_engine.put_secret(secret_handle, "tenant-real-owner", "owner-secret-token")

        profile = ConnectorProfile(
            profile_id="prof-m6-0-2-cross-tenant",
            tenant_id="tenant-real-owner",
            name="M6.0-2 Cross-Tenant Profile",
            driver_id="connector-dummy",
            secret_handle=secret_handle,
        )
        await connector_engine.profile_manager.register_profile(profile)

        request = ActionRequest(
            request_id="req-m6-0-2-cross-tenant",
            profile_id="prof-m6-0-2-cross-tenant",
            action_type=ConnectorActionType.FETCH,
            tenant_id="tenant-attacker",
        )

        with pytest.raises(ConnectorProfileNotFoundError):
            await connector_engine.execute_action(request)
    finally:
        await kernel.shutdown()
