"""Production Runtime Bootstrap & Dependency Assembly for KORTEX AI Orchestration Engine.

Governed by Milestone 9.4 and 9.5 architecture specifications:
docs/architecture/ai_engine_m9_production_runtime_spec.md

Implements:
- Production dependency graph construction and IoC assembly
- Explicit port-adapter wiring (RouterLLMExecutionPort, EngineAgentContextPort,
  KernelToolExecutionPort, KernelSecurityApprovalPolicy)
- Startup dependency order validation
- Tri-tier telemetry and diagnostics integration
- Support for development (SQLite/Local) and production (PostgreSQL/External) profiles
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final, Literal

from kortex.engines.ai.agent import (
    AgentOrchestrator,
    IAgentTaskStore,
    InMemoryAgentTaskStore,
)
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.diagnostics import AIDiagnostics
from kortex.engines.ai.engine import (
    AIOrchestrationEngine,
    EngineAgentContextPort,
    KernelSecurityApprovalPolicy,
    KernelToolExecutionPort,
    RouterLLMExecutionPort,
)
from kortex.engines.ai.exceptions import AIBootstrapError
from kortex.engines.ai.interfaces import IKernelBridge
from kortex.engines.ai.memory import (
    AIMemoryManager,
    IConversationStore,
    InMemoryConversationStore,
)
from kortex.engines.ai.persistence import (
    StorageAgentTaskStore,
    StorageConversationStore,
)
from kortex.engines.ai.pipeline import ContextComposer, PromptPipeline
from kortex.engines.ai.registry import ProviderRegistry
from kortex.engines.ai.resilience import (
    CircuitBreaker,
    ResilientAIProvider,
    RetryPolicy,
)
from kortex.engines.ai.router import ModelRouter, RoutingContext
from kortex.engines.ai.telemetry import AITelemetryEmitter
from kortex.engines.ai.telemetry_ports import ITelemetryExporter
from kortex.engines.ai.throttling import TenantConcurrencyThrottler
from kortex.engines.ai.tools import (
    DEFAULT_MAX_TOOL_RESULT_BYTES,
    AIToolInvoker,
    InMemoryToolExecutionPort,
    IToolExecutionPort,
    ToolRegistry,
)

logger = logging.getLogger("kortex.engines.ai.bootstrap")

REQUIRED_STARTUP_DEPENDENCIES: Final[frozenset[str]] = frozenset({
    "configuration",
    "registry",
    "event",
    "storage",
})


@dataclass(frozen=True)
class AIEngineRuntimeConfig:
    """Production runtime configuration for AI Orchestration Engine."""

    environment: Literal["development", "production"] = "development"
    storage_backend: Literal["sqlite", "postgres"] = "sqlite"
    default_provider: str | None = None
    enable_cloud_models: bool = False
    max_context_tokens: int = 8192
    max_tool_result_bytes: int = DEFAULT_MAX_TOOL_RESULT_BYTES
    default_generation_timeout_seconds: float = 60.0
    max_concurrent_generations_per_tenant: int = 10
    max_concurrent_agents_per_tenant: int = 5
    max_step_history_window: int = 10
    max_step_result_chars: int = 2000
    retry_max_attempts: int = 3
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_recovery_timeout: float = 30.0


class KernelProductionBootstrap:
    """Production runtime bootstrap assembler for AI Orchestration Engine."""

    def __init__(self, config: AIEngineRuntimeConfig | None = None) -> None:
        self._config = config or AIEngineRuntimeConfig()

    @property
    def config(self) -> AIEngineRuntimeConfig:
        """Active runtime configuration."""
        return self._config

    def validate_startup_dependencies(self, registered_engines: set[str] | list[str]) -> bool:
        """Validate that all prerequisite foundation engines are registered before AI Engine startup.

        Raises:
            AIBootstrapError: If prerequisite engines are missing.
        """
        registered_set = set(registered_engines)
        missing = REQUIRED_STARTUP_DEPENDENCIES - registered_set
        if missing:
            raise AIBootstrapError(
                f"Startup dependency order violation: AI Engine requires engines {sorted(missing)} "
                f"to be initialized first. Currently registered: {sorted(registered_set)}"
            )
        return True

    def create_ai_engine(
        self,
        kernel_bridge: IKernelBridge | None = None,
        data_store: Any | None = None,  # noqa: ANN401
        custom_providers: list[BaseAIProvider] | None = None,
        registered_engines: set[str] | list[str] | None = None,
        exporter: ITelemetryExporter | None = None,
    ) -> AIOrchestrationEngine:
        """Construct all subsystems, wire production ports, and return a production-ready AIOrchestrationEngine.

        Args:
            kernel_bridge: Optional Kernel bridge adapter for capability invocation.
            data_store: Optional relational DataStore for durable conversation turns.
            custom_providers: Optional pre-configured AI providers to register at boot.
            registered_engines: Optional list of available engine names for dependency order validation.
            exporter: Optional external telemetry and metric exporter.

        Returns:
            Fully assembled, production-wired AIOrchestrationEngine instance.
        """
        if registered_engines is not None:
            self.validate_startup_dependencies(registered_engines)

        logger.info(
            "Bootstrapping AI Orchestration Engine (environment=%s, storage_backend=%s)...",
            self._config.environment,
            self._config.storage_backend,
        )

        # 1. Provider Registry & Model Router
        provider_registry = ProviderRegistry()
        model_router = ModelRouter(registry=provider_registry)

        # 2. Conversation Storage & Memory Manager
        conversation_store: IConversationStore
        if data_store is not None:
            conversation_store = StorageConversationStore(
                data_store=data_store,
                max_retries=self._config.retry_max_attempts,
            )
        else:
            conversation_store = InMemoryConversationStore()

        memory_manager = AIMemoryManager(store=conversation_store)

        # 3. Context Pipeline & Composer
        pipeline = PromptPipeline(max_context_tokens=self._config.max_context_tokens)
        context_composer = ContextComposer(
            memory=memory_manager,
            pipeline=pipeline,
            max_context_tokens=self._config.max_context_tokens,
        )

        # 4. Diagnostics & Telemetry
        diagnostics = AIDiagnostics(
            provider_registry=provider_registry,
            model_router=model_router,
            memory_manager=memory_manager,
            tool_registry=None,  # Will be wired after ToolRegistry creation
        )
        telemetry = AITelemetryEmitter(
            kernel_bridge=kernel_bridge,
            diagnostics=diagnostics,
            exporter=exporter,
        )

        # Register custom providers with resilience and telemetry wrapping
        if custom_providers:
            for p in custom_providers:
                if not isinstance(p, ResilientAIProvider):
                    resilient_p = ResilientAIProvider(
                        provider=p,
                        retry_policy=RetryPolicy(max_attempts=self._config.retry_max_attempts),
                        circuit_breaker=CircuitBreaker(
                            failure_threshold=self._config.circuit_breaker_failure_threshold,
                            recovery_timeout=self._config.circuit_breaker_recovery_timeout,
                        ),
                        telemetry=telemetry,
                    )
                    provider_registry.register(resilient_p)
                else:
                    provider_registry.register(p)

        # 5. Tool Registry & Tool Invoker
        tool_registry = ToolRegistry()
        diagnostics._tool_registry = tool_registry

        tool_execution_port: IToolExecutionPort
        if kernel_bridge is not None:
            tool_execution_port = KernelToolExecutionPort(kernel_bridge=kernel_bridge)
        else:
            tool_execution_port = InMemoryToolExecutionPort()

        tool_invoker = AIToolInvoker(
            registry=tool_registry,
            execution_port=tool_execution_port,
            telemetry=telemetry,
            max_tool_result_bytes=self._config.max_tool_result_bytes,
        )

        # 6. Agent Task Store & Orchestrator with Production Ports
        agent_task_store: IAgentTaskStore
        if data_store is not None:
            agent_task_store = StorageAgentTaskStore(data_store=data_store)
        else:
            agent_task_store = InMemoryAgentTaskStore()

        llm_port = RouterLLMExecutionPort(
            router=model_router,
            registry=provider_registry,
            default_routing_context=RoutingContext(allow_cloud=self._config.enable_cloud_models),
        )
        context_port = EngineAgentContextPort(
            composer=context_composer,
            memory_manager=memory_manager,
            max_step_history_window=self._config.max_step_history_window,
            max_step_result_chars=self._config.max_step_result_chars,
        )
        approval_policy = KernelSecurityApprovalPolicy(
            tool_registry=tool_registry,
        )
        agent_orchestrator = AgentOrchestrator(
            tool_invoker=tool_invoker,
            llm_port=llm_port,
            context_port=context_port,
            approval_policy=approval_policy,
            telemetry=telemetry,
            task_store=agent_task_store,
        )

        throttler = TenantConcurrencyThrottler(
            max_concurrent_generations=self._config.max_concurrent_generations_per_tenant,
            max_concurrent_agents=self._config.max_concurrent_agents_per_tenant,
        )

        # 7. Core Facade Construction
        engine = AIOrchestrationEngine(
            provider_registry=provider_registry,
            model_router=model_router,
            memory_manager=memory_manager,
            context_composer=context_composer,
            tool_invoker=tool_invoker,
            tool_registry=tool_registry,
            agent_orchestrator=agent_orchestrator,
            diagnostics=diagnostics,
            telemetry=telemetry,
            throttler=throttler,
            default_generation_timeout_seconds=self._config.default_generation_timeout_seconds,
        )

        logger.info("AI Orchestration Engine bootstrap assembly complete.")
        return engine


__all__ = [
    "REQUIRED_STARTUP_DEPENDENCIES",
    "AIEngineRuntimeConfig",
    "KernelProductionBootstrap",
]
