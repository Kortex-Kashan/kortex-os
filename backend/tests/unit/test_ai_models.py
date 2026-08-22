"""Unit tests for AI Orchestration Engine models, exceptions, interfaces, and base provider (Milestone 1).

Target: 100% pass rate across Milestone 1 production files
(exceptions.py, models.py, events.py, interfaces.py, base_provider.py).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from kortex.core.exceptions import KortexError
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.events import (
    AIBaseEvent,
    AIGenerationCompletedEvent,
    AIGenerationStartedEvent,
    AIToolInvokedEvent,
    AgentTaskCompletedEvent,
)
from kortex.engines.ai.exceptions import AIOrchestrationError, AIProviderError
from kortex.engines.ai.interfaces import (
    IAIMemoryManager,
    IAIOrchestrationEngine,
    IAIToolInvoker,
    IBaseAIProvider,
    IModelRouter,
)
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse

# --- Exception Hierarchy Tests ---


def test_exception_hierarchy() -> None:
    """Verify AIOrchestrationError/AIProviderError inherit from KortexError."""
    assert issubclass(AIOrchestrationError, KortexError)
    assert issubclass(AIProviderError, AIOrchestrationError)
    assert issubclass(AIProviderError, KortexError)


def test_exception_message_and_code() -> None:
    """Verify exception message/code behavior matches the core KortexError convention."""
    err = AIProviderError("provider unreachable")
    assert err.message == "provider unreachable"
    assert err.code == "AIProviderError"
    assert str(err) == "[AIProviderError] provider unreachable"


def test_exception_never_requires_secret_material() -> None:
    """Exceptions must be constructible without any credential-shaped argument."""
    sig = inspect.signature(AIOrchestrationError.__init__)
    for name in sig.parameters:
        assert "secret" not in name.lower()
        assert "api_key" not in name.lower()
        assert "password" not in name.lower()


# --- LLMRequest Tests ---


def _request_kwargs(**overrides: Any) -> dict[str, Any]:
    base = {
        "request_id": "req-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "conversation_id": "conv-1",
        "prompt": "hello",
    }
    base.update(overrides)
    return base


def test_llm_request_requires_tenant_id() -> None:
    """tenant_id has no default — omitting it must fail construction, not fall back silently."""
    kwargs = _request_kwargs()
    del kwargs["tenant_id"]
    with pytest.raises(ValidationError):
        LLMRequest(**kwargs)


def test_llm_request_defaults() -> None:
    request = LLMRequest(**_request_kwargs())
    assert request.system_instruction is None
    assert request.context_documents == []
    assert request.tools == []
    assert request.temperature == 0.7
    assert request.max_tokens is None


def test_llm_request_temperature_bounds() -> None:
    with pytest.raises(ValidationError):
        LLMRequest(**_request_kwargs(temperature=-0.1))
    with pytest.raises(ValidationError):
        LLMRequest(**_request_kwargs(temperature=2.1))


def test_llm_request_is_frozen() -> None:
    request = LLMRequest(**_request_kwargs())
    with pytest.raises(ValidationError):
        request.prompt = "changed"  # type: ignore[misc]


def test_llm_request_json_roundtrip() -> None:
    request = LLMRequest(**_request_kwargs(system_instruction="be concise"))
    restored = LLMRequest.model_validate_json(request.model_dump_json())
    assert restored == request


# --- LLMResponse Tests ---


def test_llm_response_defaults_and_roundtrip() -> None:
    response = LLMResponse(request_id="req-1", text_content="hi there")
    assert response.tool_calls == []
    assert response.token_usage == {}
    assert response.execution_time_ms == 0.0
    restored = LLMResponse.model_validate_json(response.model_dump_json())
    assert restored == response


# --- AIProviderMetadata Tests ---


def test_provider_metadata_valid_construction_no_credential() -> None:
    provider = AIProviderMetadata(
        provider_id="ollama-local",
        display_name="Local Ollama",
        vendor="ollama",
        endpoint_type="local_host",
        supported_models=["qwen2.5:7b"],
    )
    assert provider.credential_requirement == "none"
    assert provider.secret_handle is None


def test_provider_metadata_network_endpoint_without_auth() -> None:
    """A LAN endpoint must not be forced to declare a credential."""
    provider = AIProviderMetadata(
        provider_id="lan-llm",
        display_name="Org LLM Server",
        vendor="vllm",
        endpoint_type="network",
        url="http://10.0.0.5:8000",
        supported_models=["deepseek-v3"],
    )
    assert provider.credential_requirement == "none"
    assert provider.secret_handle is None


def test_provider_metadata_requires_secret_handle_when_credential_required() -> None:
    with pytest.raises(ValidationError):
        AIProviderMetadata(
            provider_id="cloud-openai-compatible",
            display_name="Cloud Provider",
            vendor="openai-compatible",
            endpoint_type="cloud",
            credential_requirement="api_key",
        )


def test_provider_metadata_accepts_secret_handle_when_credential_required() -> None:
    provider = AIProviderMetadata(
        provider_id="cloud-openai-compatible",
        display_name="Cloud Provider",
        vendor="openai-compatible",
        endpoint_type="cloud",
        credential_requirement="api_key",
        secret_handle="secret:kortex/ai/cloud-openai-compatible",
    )
    assert provider.secret_handle == "secret:kortex/ai/cloud-openai-compatible"


def test_provider_metadata_invalid_endpoint_type_rejected() -> None:
    with pytest.raises(ValidationError):
        AIProviderMetadata(
            provider_id="p",
            display_name="p",
            vendor="p",
            endpoint_type="same_machine",  # type: ignore[arg-type]
        )


def test_provider_metadata_is_frozen() -> None:
    provider = AIProviderMetadata(
        provider_id="p", display_name="p", vendor="p", endpoint_type="local_host"
    )
    with pytest.raises(ValidationError):
        provider.provider_id = "changed"  # type: ignore[misc]


def test_provider_metadata_no_is_local_field() -> None:
    """endpoint_type replaces is_local entirely per the approved Milestone 1 decision."""
    assert "is_local" not in AIProviderMetadata.model_fields


# --- Security: no plaintext-credential-capable fields anywhere ---

_ALLOWED_CREDENTIAL_FIELD_NAMES = {"secret_handle", "credential_requirement"}
_CREDENTIAL_PATTERNS = ("api_key", "apikey", "password", "bearer_token", "access_token", "oauth_token")


@pytest.mark.parametrize("model_cls", [AIProviderMetadata, LLMRequest, LLMResponse])
def test_no_plaintext_credential_fields(model_cls: type) -> None:
    for field_name in model_cls.model_fields:
        if field_name in _ALLOWED_CREDENTIAL_FIELD_NAMES:
            continue
        lowered = field_name.lower()
        for pattern in _CREDENTIAL_PATTERNS:
            assert pattern not in lowered, (
                f"{model_cls.__name__}.{field_name} looks like a plaintext credential field; "
                "credentials must be referenced only via secret_handle"
            )


def test_secret_handle_is_optional_string_not_object() -> None:
    """secret_handle must remain a plain, optional handle string, never a nested credential object."""
    field = AIProviderMetadata.model_fields["secret_handle"]
    assert field.annotation == (str | None)


# --- Event Tests ---


@pytest.mark.parametrize(
    ("event_cls", "expected_type", "extra_kwargs"),
    [
        (AIGenerationStartedEvent, "ai.generation.started", {"request_id": "r1", "tenant_id": "t1", "conversation_id": "c1"}),
        (
            AIGenerationCompletedEvent,
            "ai.generation.completed",
            {"request_id": "r1", "tenant_id": "t1", "conversation_id": "c1", "execution_time_ms": 12.5},
        ),
        (AIToolInvokedEvent, "ai.tool.invoked", {"request_id": "r1", "tenant_id": "t1", "tool_name": "kortex.workflow.instance.approve"}),
        (AgentTaskCompletedEvent, "ai.agent.completed", {"task_id": "task-1", "tenant_id": "t1", "status": "completed"}),
    ],
)
def test_event_type_matches_spec_naming(event_cls: type[AIBaseEvent], expected_type: str, extra_kwargs: dict[str, Any]) -> None:
    """Event type strings match ai_orchestration_engine_implementation_spec.md section 16 verbatim."""
    event = event_cls(**extra_kwargs)
    assert event.event_type == expected_type
    assert event.event_id.startswith("evt-")


def test_event_is_frozen() -> None:
    event = AIGenerationStartedEvent(request_id="r1", tenant_id="t1", conversation_id="c1")
    with pytest.raises(ValidationError):
        event.request_id = "changed"  # type: ignore[misc]


# --- BaseAIProvider ABC Tests ---


def test_base_ai_provider_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        BaseAIProvider()  # type: ignore[abstract]


class _StubAIProvider(BaseAIProvider):
    """Minimal concrete subclass used only to test ABC conformance, not a real provider."""

    @property
    def metadata(self) -> AIProviderMetadata:
        return AIProviderMetadata(
            provider_id="stub",
            display_name="Stub",
            vendor="stub",
            endpoint_type="local_host",
            supported_models=["stub-model"],
        )

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(request_id=request.request_id, text_content="stub")

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return True


def test_base_ai_provider_derived_properties_delegate_to_metadata() -> None:
    provider = _StubAIProvider()
    assert provider.provider_id == "stub"
    assert provider.supported_models == ["stub-model"]


@pytest.mark.asyncio
async def test_base_ai_provider_concrete_methods_are_callable() -> None:
    provider = _StubAIProvider()
    request = LLMRequest(**_request_kwargs())
    response = await provider.generate_text(request)
    assert response.request_id == "req-1"
    embeddings = await provider.generate_embeddings(["a", "b"])
    assert len(embeddings) == 2
    assert await provider.health_check() is True


def test_base_ai_provider_no_is_local_property() -> None:
    """is_local is not reintroduced as a derived property; endpoint_type is the sole source of truth."""
    assert not hasattr(BaseAIProvider, "is_local")


# --- Protocol Conformance Tests ---


def test_stub_provider_satisfies_ibaseaiprovider_protocol() -> None:
    provider = _StubAIProvider()
    assert isinstance(provider, IBaseAIProvider)


def test_interfaces_declare_expected_methods() -> None:
    assert hasattr(IModelRouter, "select_model")
    assert hasattr(IAIMemoryManager, "get_context")
    assert hasattr(IAIMemoryManager, "append_history")
    assert hasattr(IAIToolInvoker, "invoke")
    for method_name in ("generate_response", "orchestrate_agent", "invoke_tool", "register_provider"):
        assert hasattr(IAIOrchestrationEngine, method_name)


def test_tool_invoker_authorizer_parameter_is_mandatory() -> None:
    """Regression guard: IAIToolInvoker.invoke's authorizer parameter must have no default,
    so a future implementation cannot make authorization silently optional."""
    sig = inspect.signature(IAIToolInvoker.invoke)
    authorizer_param = sig.parameters["authorizer"]
    assert authorizer_param.default is inspect.Parameter.empty


def test_orchestration_engine_invoke_tool_authorizer_is_mandatory() -> None:
    sig = inspect.signature(IAIOrchestrationEngine.invoke_tool)
    authorizer_param = sig.parameters["authorizer"]
    assert authorizer_param.default is inspect.Parameter.empty
