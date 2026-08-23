"""Production Runtime Bootstrap & Dependency Assembly for KORTEX AI Orchestration Engine.

Governed by Milestone 9.4 architecture specification:
docs/architecture/ai_engine_m9_production_runtime_spec.md

Implements:
- Production dependency graph construction and IoC assembly
- Explicit port-adapter wiring (RouterLLMExecutionPort, EngineAgentContextPort,
  KernelToolExecutionPort, KernelSecurityApprovalPolicy)
- Startup dependency order validation
- Support for development (SQLite/Local) and production (PostgreSQL/External) profiles
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Literal

from kortex.engines.ai.agent import AgentOrchestrator
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
from kortex.engines.ai.persistence import StorageConversationStore
from kortex.engines.ai.pipeline import ContextComposer, PromptPipeline
from kortex.engines.ai.registry import ProviderRegistry
from kortex.engines.ai.resilience import (
    CircuitBreaker,
    ResilientAIProvider,
    RetryPolicy,
)
from kortex.engines.ai.router import ModelRouter, RoutingContext
from kortex.engines.ai.tools import (
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
        data_store: object | None = None,
        custom_providers: list[BaseAIProvider] | None = None,
        registered_engines: set[str] | list[str] | None = None,
    ) -> AIOrchestrationEngine:
        """Construct all subsystems, wire production ports, and return a production-ready AIOrchestrationEngine.

        Args:
            kernel_bridge: Optional Kernel bridge adapter for capability invocation.
            data_store: Optional relational DataStore for durable conversation turns.
            custom_providers: Optional pre-configured AI providers to register at boot.
            registered_engines: Optional list of available engine names for dependency order validation.

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
                    )
                    provider_registry.register(resilient_p)
                else:
                    provider_registry.register(p)

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
        pipeline = PromptPipeline()
        context_composer = ContextComposer(
            memory=memory_manager,
            pipeline=pipeline,
        )

        # 4. Tool Registry & Tool Invoker
        tool_registry = ToolRegistry()
        tool_execution_port: IToolExecutionPort
        if kernel_bridge is not None:
            tool_execution_port = KernelToolExecutionPort(kernel_bridge=kernel_bridge)
        else:
            tool_execution_port = InMemoryToolExecutionPort()

        tool_invoker = AIToolInvoker(
            registry=tool_registry,
            execution_port=tool_execution_port,
        )

        # 5. Agent Orchestrator with Production Ports
        llm_port = RouterLLMExecutionPort(
            router=model_router,
            registry=provider_registry,
            default_routing_context=RoutingContext(allow_cloud=self._config.enable_cloud_models),
        )
        context_port = EngineAgentContextPort(
            composer=context_composer,
            memory_manager=memory_manager,
        )
        approval_policy = KernelSecurityApprovalPolicy(
            tool_registry=tool_registry,
        )
        agent_orchestrator = AgentOrchestrator(
            tool_invoker=tool_invoker,
            llm_port=llm_port,
            context_port=context_port,
            approval_policy=approval_policy,
        )

        # 6. Diagnostics Subsystem
        diagnostics = AIDiagnostics(
            provider_registry=provider_registry,
            model_router=model_router,
            memory_manager=memory_manager,
            tool_registry=tool_registry,
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
        )

        logger.info("AI Orchestration Engine bootstrap assembly complete.")
        return engine


__all__ = [
    "REQUIRED_STARTUP_DEPENDENCIES",
    "AIEngineRuntimeConfig",
    "KernelProductionBootstrap",
]
