"""Unit tests for KORTEX AI Engine Production Runtime Bootstrap (Milestone 9.4).

Tests adhere strictly to the ratified M9.4 specification:
- Bootstrap engine instantiation and subsystem assembly
- Production port and adapter wiring verification
- Startup dependency order validation
- Empty provider environment tolerance
- Development vs production runtime profiles
- AST import quarantine verification
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.bootstrap import (
    AIEngineRuntimeConfig,
    KernelProductionBootstrap,
)
from kortex.engines.ai.engine import (
    AIOrchestrationEngine,
    EngineAgentContextPort,
    KernelToolExecutionPort,
    RouterLLMExecutionPort,
)
from kortex.engines.ai.exceptions import AIBootstrapError
from kortex.engines.ai.governance import DurableAIApprovalPolicy
from kortex.engines.ai.models import (
    AIProviderMetadata,
    LLMRequest,
    LLMResponse,
)
from kortex.engines.ai.resilience import ResilientAIProvider

# ---------------------------------------------------------------------------
# Test Helpers & Fakes
# ---------------------------------------------------------------------------


class FakeProvider(BaseAIProvider):
    """Simple fake provider for bootstrap testing."""

    def __init__(self, provider_id: str = "mock-ollama") -> None:
        self._provider_id = provider_id
        self._metadata = AIProviderMetadata(
            provider_id=provider_id,
            display_name=f"Mock Provider ({provider_id})",
            vendor="mock-vendor",
            endpoint_type="local_host",
            supported_models=["llama-3-8b"],
            credential_requirement="none",
        )

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def supported_models(self) -> list[str]:
        return ["llama-3-8b"]

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            request_id=request.request_id,
            text_content="Bootstrap generated text",
            tool_calls=[],
            token_usage={},
            execution_time_ms=0.0,
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]

    async def health_check(self) -> bool:
        return True


class DummyBridge:
    """Minimal IKernelBridge for bootstrap port testing."""

    async def invoke_capability(
        self,
        name: str,
        arguments: dict[str, object],
        tenant_id: str,
        user_id: str | None = None,
        request_id: str | None = None,
        session_token: object | None = None,
    ) -> object:
        return {"status": "ok", "name": name, "tenant_id": tenant_id}

    def subscribe_event(self, topic: str, handler: object, subscriber_name: str = "anonymous") -> str:
        return "sub-dummy"

    def register_capability(self, **kwargs: object) -> None:
        pass


# ---------------------------------------------------------------------------
# §1 — Bootstrap Creation & Dependency Wiring Tests
# ---------------------------------------------------------------------------


def test_bootstrap_creates_fully_assembled_ai_engine() -> None:
    """Verify that create_ai_engine returns an operational AIOrchestrationEngine."""
    bootstrap = KernelProductionBootstrap()
    engine = bootstrap.create_ai_engine()

    assert isinstance(engine, AIOrchestrationEngine)
    assert engine.name == "ai"
    assert engine.provider_registry is not None
    assert engine.model_router is not None
    assert engine.memory_manager is not None
    assert engine.tool_registry is not None
    assert engine.tool_invoker is not None
    assert engine.agent_orchestrator is not None


def test_bootstrap_wires_production_ports() -> None:
    """Verify that AgentOrchestrator is wired with production port adapters.

    M5-A3: the approval policy must be the governance-aware
    `DurableAIApprovalPolicy` (via `AIGovernanceManager.create_approval_policy()`),
    not `KernelSecurityApprovalPolicy` — the latter only ever checked a
    tool's `is_mutation` flag and had no tenant-policy (blocklist/allowlist/
    quota) awareness at all, meaning a tenant's AI governance policy had
    zero effect on the production agent orchestrator's tool-call gating.
    """
    bridge = DummyBridge()
    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(enable_cloud_models=True)
    )
    engine = bootstrap.create_ai_engine(kernel_bridge=bridge)  # type: ignore[arg-type]

    orchestrator = engine.agent_orchestrator
    assert isinstance(orchestrator._llm_port, RouterLLMExecutionPort)
    assert isinstance(orchestrator._context_port, EngineAgentContextPort)
    assert isinstance(orchestrator._approval_policy, DurableAIApprovalPolicy)
    assert isinstance(engine.tool_invoker._execution_port, KernelToolExecutionPort)


# ---------------------------------------------------------------------------
# §2 — Startup Dependency Order Validation Tests
# ---------------------------------------------------------------------------


def test_dependency_validation_passes_when_all_prerequisites_present() -> None:
    """Verify dependency validation passes when required foundation engines are registered."""
    bootstrap = KernelProductionBootstrap()
    valid_engines = ["configuration", "registry", "event", "storage", "security"]
    assert bootstrap.validate_startup_dependencies(valid_engines) is True


def test_dependency_validation_fails_on_missing_prerequisite() -> None:
    """Verify dependency validation raises AIBootstrapError if prerequisite engine is missing."""
    bootstrap = KernelProductionBootstrap()
    incomplete_engines = ["configuration", "registry", "event"]  # Missing "storage"

    with pytest.raises(AIBootstrapError, match="Startup dependency order violation"):
        bootstrap.validate_startup_dependencies(incomplete_engines)

    with pytest.raises(AIBootstrapError, match="Startup dependency order violation"):
        bootstrap.create_ai_engine(registered_engines=incomplete_engines)


# ---------------------------------------------------------------------------
# §3 — Empty Provider Environment & Resilience Wrapping Tests
# ---------------------------------------------------------------------------


def test_empty_provider_environment_initializes_without_crash() -> None:
    """Verify that AI engine boots cleanly even if no providers are registered at startup."""
    bootstrap = KernelProductionBootstrap()
    engine = bootstrap.create_ai_engine()

    diag = engine.diagnostics()
    assert diag["engine"] == "ai"
    assert len(diag["providers"]) == 0

    health = engine.health()
    assert health["engine"] == "ai"
    assert health["providers_registered"] == 0
    assert health["status"] == "DEGRADED"


def test_custom_providers_wrapped_with_resilience_layer() -> None:
    """Verify custom providers are automatically wrapped in ResilientAIProvider."""
    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(retry_max_attempts=4)
    )
    raw_provider = FakeProvider("ollama-raw")
    engine = bootstrap.create_ai_engine(custom_providers=[raw_provider])

    registered = engine.provider_registry.get("ollama-raw")
    assert isinstance(registered, ResilientAIProvider)
    assert registered.retry_policy.max_attempts == 4


# ---------------------------------------------------------------------------
# §4 — Development vs Production Configurations
# ---------------------------------------------------------------------------


def test_development_profile_configuration() -> None:
    """Verify development runtime profile defaults."""
    dev_config = AIEngineRuntimeConfig(
        environment="development",
        storage_backend="sqlite",
        enable_cloud_models=False,
    )
    bootstrap = KernelProductionBootstrap(config=dev_config)
    engine = bootstrap.create_ai_engine()

    assert bootstrap.config.environment == "development"
    assert bootstrap.config.storage_backend == "sqlite"
    assert bootstrap.config.enable_cloud_models is False
    assert engine is not None


def test_production_profile_configuration() -> None:
    """Verify production runtime profile with external cloud capability.

    Supplies real production wiring (`data_store` + `kernel_bridge`) because
    the production profile refuses to assemble without it — see
    `test_production_profile_refuses_*` below.
    """
    prod_config = AIEngineRuntimeConfig(
        environment="production",
        storage_backend="postgres",
        enable_cloud_models=True,
        max_context_tokens=16384,
    )
    bootstrap = KernelProductionBootstrap(config=prod_config)
    engine = bootstrap.create_ai_engine(
        kernel_bridge=DummyBridge(),  # type: ignore[arg-type]
        data_store=object(),
    )

    assert bootstrap.config.environment == "production"
    assert bootstrap.config.storage_backend == "postgres"
    assert bootstrap.config.enable_cloud_models is True
    assert bootstrap.config.max_context_tokens == 16384
    assert engine is not None


# ---------------------------------------------------------------------------
# §4.5 — Production Wiring Fail-Closed Guard (M9 Attack 4)
# ---------------------------------------------------------------------------


def test_production_profile_refuses_to_assemble_without_data_store() -> None:
    """M9 'Production Engine Wiring Requirement': a production engine must never
    silently fall back to non-durable in-memory stores."""
    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(environment="production")
    )
    with pytest.raises(AIBootstrapError, match="data_store"):
        bootstrap.create_ai_engine(kernel_bridge=DummyBridge())  # type: ignore[arg-type]


def test_production_profile_refuses_to_assemble_without_kernel_bridge() -> None:
    """Without a kernel bridge the assembler substitutes InMemoryToolExecutionPort,
    so every tool call would bypass CapabilityDispatcher and Security Engine."""
    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(environment="production")
    )
    with pytest.raises(AIBootstrapError, match="kernel_bridge"):
        bootstrap.create_ai_engine(data_store=object())


def test_production_profile_reports_every_missing_dependency_at_once() -> None:
    """An operator fixing production wiring should see the full list, not one at a time."""
    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(environment="production")
    )
    with pytest.raises(AIBootstrapError) as exc_info:
        bootstrap.create_ai_engine()

    message = str(exc_info.value)
    assert "data_store" in message
    assert "kernel_bridge" in message


def test_development_profile_still_permits_in_memory_fallbacks() -> None:
    """The guard must be production-only: development stays runnable with no
    Kernel and no database, which is the entire point of that profile."""
    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(environment="development")
    )
    engine = bootstrap.create_ai_engine()
    assert engine is not None


# ---------------------------------------------------------------------------
# §5 — AST Import Quarantine
# ---------------------------------------------------------------------------


def _collect_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


FORBIDDEN_NAMESPACES = [
    "kortex.engines.security.engine",
    "kortex.core.kernel.Kernel",
    "openai",
    "anthropic",
    "sqlalchemy",
]


def test_bootstrap_py_quarantine_forbidden_imports() -> None:
    """Verify bootstrap.py does not import forbidden infrastructure or vendor SDKs."""
    target_path = (
        Path(__file__).parent.parent.parent
        / "src"
        / "kortex"
        / "engines"
        / "ai"
        / "bootstrap.py"
    )
    imports = _collect_imports(target_path)
    for forbidden in FORBIDDEN_NAMESPACES:
        violations = [
            imp for imp in imports if imp == forbidden or imp.startswith(forbidden + ".")
        ]
        assert violations == [], (
            f"bootstrap.py illegally imports {forbidden!r}: {violations}"
        )
