"""Pydantic v2 data models for the KORTEX OS AI Orchestration Engine.

This module defines the Milestone 1 domain model surface: provider metadata,
and the request/response shapes exchanged between the future engine facade
and any caller (Workflow Engine, other engines, direct tests). No provider
registry, router, memory, tool, or agent implementation lives here — see
`docs/architecture/ai_orchestration_engine_implementation_spec.md` for the
full Phase 2 scope and the approved Milestone 1 plan for what is deferred.

Security boundary: no field in this module may hold a plaintext credential.
`AIProviderMetadata.secret_handle` is the only credential-adjacent field,
and it references a Security Engine `SecretStore` handle — never a raw
API key, bearer token, or password.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EndpointType = Literal["local_host", "network", "cloud"]
"""Where the AI provider actually lives.

Replaces a boolean `is_local` flag (as originally proposed in
`ai_orchestration_engine_implementation_spec.md` section 4/6), which cannot
distinguish "installed on the same machine as KORTEX" from "installed on
another machine/server on the organization's network" — both of which are
local in the sense of "not a paid cloud API," but require different
endpoint configuration. This is an approved, intentional deviation from the
literal spec text (Milestone 1 planning decision), not an oversight.
"""

CredentialRequirement = Literal["none", "api_key", "bearer_token", "oauth", "custom"]
"""What kind of credential, if any, an endpoint requires.

`"none"` is a first-class value, not an omission — a local or LAN endpoint
is not assumed to require authentication.
"""


class AIProviderMetadata(BaseModel):
    """Immutable metadata describing a registered (or registerable) AI provider.

    Deliberately does not distinguish "provider" from "model" as separate
    models in Milestone 1: `supported_models` is a plain list of model
    identifiers, matching the approved specification's field
    (`ai_orchestration_engine_implementation_spec.md` section 5). A richer
    per-model metadata type is deferred to the Model Router milestone, which
    is the first consumer that actually needs it.
    """

    model_config = ConfigDict(frozen=True)

    provider_id: str
    display_name: str
    vendor: str
    endpoint_type: EndpointType
    url: str | None = None
    credential_requirement: CredentialRequirement = "none"
    secret_handle: str | None = None
    supported_models: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_credential_consistency(self) -> AIProviderMetadata:
        """Enforce that any provider requiring credentials references a secret handle.

        This does not resolve or validate the handle itself (that is Security
        Engine's `SecretStore` responsibility) — it only prevents a provider
        from declaring that it needs a credential while carrying none.
        """
        if self.credential_requirement != "none" and not self.secret_handle:
            raise ValueError("secret_handle is required when credential_requirement is not 'none'")
        return self


class AIModelSummary(BaseModel):
    """Read-only, derived view of one model declared by a registered provider.

    Not a first-class Model domain entity — `AIProviderMetadata`'s own
    docstring defers that to a future milestone. This is a flattening
    projection over `AIProviderMetadata.supported_models` (Slice 4.6's
    `kortex.ai.model.list` capability), carrying no field
    `AIProviderMetadata` doesn't already expose — in particular, no
    `secret_handle`/`credential_requirement`, since a model listing has no
    reason to carry its provider's credential material.
    """

    model_config = ConfigDict(frozen=True)

    model_id: str
    provider_id: str
    provider_display_name: str


class LLMRequest(BaseModel):
    """Request payload for a single AI generation call.

    `tenant_id` is required with no default (unlike some existing engine
    request models that default to `"default"`) — a request with no
    identifiable tenant must fail to construct rather than silently fall
    back to a shared tenant, per the multi-tenant isolation invariant
    (`multi_tenant_architecture.md`).

    `conversation_id` is the entire Milestone 1 contribution to making
    conversation history model/provider-independent: it must be assigned by
    KORTEX (the future AI Memory Manager or Workflow Engine), never derived
    from a provider's own session/thread identifier. No storage or
    retrieval logic for conversation history exists yet — that is a later
    milestone's responsibility.

    `tools` is intentionally typed as a generic mapping rather than a
    `ToolDefinition` model: tool invocation is out of scope for Milestone 1,
    and defining `ToolDefinition`'s shape now, before the milestone that
    consumes it exists, would be a guess.
    """

    model_config = ConfigDict(frozen=True)

    request_id: str
    tenant_id: str
    user_id: str
    conversation_id: str
    prompt: str
    system_instruction: str | None = None
    context_documents: list[str] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)


class LLMResponse(BaseModel):
    """Response payload returned from a single AI generation call.

    `tool_calls` mirrors `LLMRequest.tools` in being a generic mapping
    pending Milestone 5's richer tool-invocation contract.

    `degraded` is `True` only for the one case where generation itself
    succeeded but recording it durably did not (conversation-history
    persistence failed after the model already produced a response). Per
    the M9 architecture spec's Systematic Failure Recovery Matrix, that
    turn must still be returned to the caller rather than discarded — an
    `AIStorageWriteFailedEvent` is emitted separately so the durability gap
    is observable without forcing the caller to lose an already-generated
    answer.

    `provider_id`/`model_name` (M6.1-2): identify which registered provider
    and model actually served this response. Additive and optional —
    `ai_decision_records` already carries nullable columns for both, but
    nothing populated them before a real provider existed to report them.
    """

    model_config = ConfigDict(frozen=True)

    request_id: str
    text_content: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    execution_time_ms: float = 0.0
    degraded: bool = False
    provider_id: str | None = None
    model_name: str | None = None


class TokenUsage(BaseModel):
    """Aggregate token usage counts for LLM generation or multi-step agent workflows."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, int] | None) -> TokenUsage:
        """Construct a TokenUsage from an arbitrary mapping."""
        if not data:
            return cls()
        p = int(data.get("prompt_tokens", 0) or 0)
        c = int(data.get("completion_tokens", 0) or 0)
        t = int(data.get("total_tokens", p + c) or (p + c))
        return cls(prompt_tokens=p, completion_tokens=c, total_tokens=t)

    def add(self, other: TokenUsage | dict[str, int] | None) -> TokenUsage:
        """Return a new TokenUsage summing self and other."""
        if other is None:
            return self
        if isinstance(other, dict):
            other = TokenUsage.from_dict(other)
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


__all__ = [
    "AIProviderMetadata",
    "CredentialRequirement",
    "EndpointType",
    "LLMRequest",
    "LLMResponse",
    "TokenUsage",
]
