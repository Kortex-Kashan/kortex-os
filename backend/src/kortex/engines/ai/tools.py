"""Tool invocation engine for the KORTEX OS AI Orchestration Engine.

Governed by the approved Milestone 6 architecture specification.

This module provides:
- Strongly typed, immutable tool definitions and schemas (`ToolDefinition`).
- Validated tool requests (`ToolCall`) with byte size boundaries.
- Normalized, sanitized tool execution results (`ToolResult`) rendered under the
  reserved `[[tool]]` marker.
- An abstract port (`IToolExecutionPort`) decoupling tool invocation from concrete
  Kernel capability dispatching.
- The core orchestrator (`AIToolInvoker`) and local schema catalog (`ToolRegistry`).

Security boundaries:
- AI Engine NEVER executes business capabilities directly. Execution is delegated
  across the `IToolExecutionPort` boundary.
- Tool outputs are UNTRUSTED RUNTIME DATA and are sanitized to neutralize role marker sentinels.
- Tool execution is strictly single-invocation. Nested execution or recursive loops are forbidden.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kortex.engines.ai.exceptions import (
    ToolAuthorizationError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from kortex.engines.ai.interfaces import ToolAuthorizer
from kortex.engines.ai.memory import require_identifier, sanitize_context_content
from kortex.engines.ai.pipeline import TOOL_MARKER

MAX_TOOL_ARGUMENTS_BYTES: int = 65_536
"""Maximum allowed size for tool argument JSON payloads (64 KB)."""

MAX_TOOL_OUTPUT_CHARS: int = 50_000
"""Maximum character length for tool output rendered into context (~10,000 tokens)."""

MAX_BATCH_SIZE: int = 10
"""Maximum number of tool calls permitted in a single batch invocation."""

DEFAULT_TOOL_TIMEOUT_SECONDS: float = 30.0
MIN_TOOL_TIMEOUT_SECONDS: float = 0.1
MAX_TOOL_TIMEOUT_SECONDS: float = 300.0

TRUNCATION_SUFFIX: str = "\n[TRUNCATED: output exceeded 50000 chars]"

_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class ToolExecutionStatus(StrEnum):
    """Execution outcome status for a tool invocation."""

    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    NOT_FOUND = "NOT_FOUND"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    TIMEOUT = "TIMEOUT"


def validate_schema(schema: dict[str, object], data: object, path: str = "") -> None:
    """Validate data against a JSON schema definition.

    Supports core JSON schema primitives: type, required, properties,
    items, enum, minimum, maximum, minLength, maxLength.
    Raises ToolValidationError on any discrepancy.
    """
    if not schema:
        return

    expected_type = schema.get("type")
    if expected_type:
        if expected_type == "object":
            if not isinstance(data, dict):
                field_desc = f"at '{path}'" if path else "root"
                raise ToolValidationError(
                    f"Invalid argument {field_desc}: expected object, got {type(data).__name__}."
                )
        elif expected_type == "string":
            if not isinstance(data, str):
                field_desc = f"at '{path}'" if path else "root"
                raise ToolValidationError(
                    f"Invalid argument {field_desc}: expected string, got {type(data).__name__}."
                )
        elif expected_type == "integer":
            if isinstance(data, bool) or not isinstance(data, int):
                field_desc = f"at '{path}'" if path else "root"
                raise ToolValidationError(
                    f"Invalid argument {field_desc}: expected integer, got {type(data).__name__}."
                )
        elif expected_type == "number":
            if isinstance(data, bool) or not isinstance(data, (int, float)):
                field_desc = f"at '{path}'" if path else "root"
                raise ToolValidationError(
                    f"Invalid argument {field_desc}: expected number, got {type(data).__name__}."
                )
        elif expected_type == "boolean":
            if not isinstance(data, bool):
                field_desc = f"at '{path}'" if path else "root"
                raise ToolValidationError(
                    f"Invalid argument {field_desc}: expected boolean, got {type(data).__name__}."
                )
        elif expected_type == "array":
            if not isinstance(data, list):
                field_desc = f"at '{path}'" if path else "root"
                raise ToolValidationError(
                    f"Invalid argument {field_desc}: expected array, got {type(data).__name__}."
                )
        elif expected_type == "null" and data is not None:
            field_desc = f"at '{path}'" if path else "root"
            raise ToolValidationError(
                f"Invalid argument {field_desc}: expected null, got {type(data).__name__}."
            )

    if "enum" in schema and isinstance(schema["enum"], list) and data not in schema["enum"]:
        field_desc = f"at '{path}'" if path else "root"
        raise ToolValidationError(
            f"Invalid argument {field_desc}: value {data!r} is not in allowed enum {schema['enum']}."
        )

    if isinstance(data, (int, float)) and not isinstance(data, bool):
        min_val = schema.get("minimum")
        if isinstance(min_val, (int, float)) and data < min_val:
            field_desc = f"at '{path}'" if path else "root"
            raise ToolValidationError(
                f"Invalid argument {field_desc}: value {data} is less than minimum {min_val}."
            )
        max_val = schema.get("maximum")
        if isinstance(max_val, (int, float)) and data > max_val:
            field_desc = f"at '{path}'" if path else "root"
            raise ToolValidationError(
                f"Invalid argument {field_desc}: value {data} is greater than maximum {max_val}."
            )

    if isinstance(data, str):
        min_len = schema.get("minLength")
        if isinstance(min_len, int) and len(data) < min_len:
            field_desc = f"at '{path}'" if path else "root"
            raise ToolValidationError(
                f"Invalid argument {field_desc}: string length {len(data)} is less than minLength {min_len}."
            )
        max_len = schema.get("maxLength")
        if isinstance(max_len, int) and len(data) > max_len:
            field_desc = f"at '{path}'" if path else "root"
            raise ToolValidationError(
                f"Invalid argument {field_desc}: string length {len(data)} exceeds maxLength {max_len}."
            )

    if isinstance(data, dict):
        required_fields = schema.get("required")
        if isinstance(required_fields, list):
            for req in required_fields:
                if isinstance(req, str) and req not in data:
                    subpath = f"{path}.{req}" if path else req
                    raise ToolValidationError(f"Missing required argument '{subpath}'.")

        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, val in data.items():
                if key in properties and isinstance(properties[key], dict):
                    subpath = f"{path}.{key}" if path else key
                    validate_schema(properties[key], val, subpath)

    if isinstance(data, list) and "items" in schema and isinstance(schema["items"], dict):
        items_schema = schema["items"]
        for idx, item in enumerate(data):
            subpath = f"{path}[{idx}]"
            validate_schema(items_schema, item, subpath)


class ToolDefinition(BaseModel):
    """Immutable declaration of an AI-facing tool schema."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters_schema: dict[str, object] = Field(default_factory=dict)
    canonical_capability: str = Field(min_length=1)
    is_mutation: bool = False
    timeout_seconds: float = Field(
        default=DEFAULT_TOOL_TIMEOUT_SECONDS,
        ge=MIN_TOOL_TIMEOUT_SECONDS,
        le=MAX_TOOL_TIMEOUT_SECONDS,
    )

    @model_validator(mode="after")
    def _validate_name_pattern(self) -> ToolDefinition:
        if not _TOOL_NAME_PATTERN.match(self.name):
            raise ToolValidationError(
                f"Invalid tool name '{self.name}': must match pattern '^[a-zA-Z0-9_-]+$'."
            )
        return self

    def validate_arguments(self, arguments: dict[str, object]) -> None:
        """Validate input arguments against declared schema and byte boundaries."""
        try:
            raw_bytes = json.dumps(arguments).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ToolValidationError(f"Tool arguments could not be JSON serialized: {exc}") from exc

        if len(raw_bytes) > MAX_TOOL_ARGUMENTS_BYTES:
            raise ToolValidationError(
                f"Tool arguments payload of {len(raw_bytes)} bytes exceeds limit of {MAX_TOOL_ARGUMENTS_BYTES} bytes."
            )

        validate_schema(self.parameters_schema, arguments)


class ToolCall(BaseModel):
    """Structured representation of an LLM tool call request."""

    model_config = ConfigDict(frozen=True)

    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, object] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Normalized, immutable result of a tool invocation."""

    model_config = ConfigDict(frozen=True)

    call_id: str
    tool_name: str
    status: ToolExecutionStatus
    output: object = None
    error_message: str | None = None
    execution_time_ms: float = 0.0

    def to_context_entry(self) -> str:
        """Render into a safe, bounded, and neutralized [[tool]] context document."""
        if self.error_message:
            serialized_payload = json.dumps({"error": self.error_message}, sort_keys=True)
        elif self.output is None:
            serialized_payload = "null"
        elif isinstance(self.output, str):
            serialized_payload = self.output
        else:
            try:
                serialized_payload = json.dumps(self.output, default=str, sort_keys=True)
            except Exception as exc:
                serialized_payload = json.dumps({"serialization_error": str(exc)})

        if len(serialized_payload) > MAX_TOOL_OUTPUT_CHARS:
            allowed_len = MAX_TOOL_OUTPUT_CHARS - len(TRUNCATION_SUFFIX)
            serialized_payload = serialized_payload[:allowed_len] + TRUNCATION_SUFFIX

        sanitized_payload = sanitize_context_content(serialized_payload)

        return (
            f"{TOOL_MARKER}\n"
            f"call_id: {self.call_id}\n"
            f"tool: {self.tool_name}\n"
            f"status: {self.status.value}\n"
            f"payload: {sanitized_payload}"
        )


@runtime_checkable
class IToolExecutionPort(Protocol):
    """Port interface for executing capabilities across the engine boundary."""

    async def execute_tool(
        self,
        tenant_id: str,
        capability_name: str,
        arguments: dict[str, object],
        authorizer: ToolAuthorizer | None = None,
    ) -> object:
        """Execute capability handler with arguments for the given tenant."""
        ...


@runtime_checkable
class IToolRegistry(Protocol):
    """Registry protocol for managing client-facing tool definitions."""

    def register_tool(self, tool: ToolDefinition) -> None: ...
    def unregister_tool(self, name: str) -> bool: ...
    def get_tool(self, name: str) -> ToolDefinition: ...
    def list_tools(self) -> list[ToolDefinition]: ...
    def has_tool(self, name: str) -> bool: ...


class ToolRegistry(IToolRegistry):
    """In-memory client-side catalog of AI tool definitions."""

    def __init__(self, tools: list[ToolDefinition] | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        if tools:
            for tool in tools:
                self.register_tool(tool)

    def register_tool(self, tool: ToolDefinition) -> None:
        if not isinstance(tool, ToolDefinition):
            raise ToolValidationError(f"Expected ToolDefinition instance, got {type(tool).__name__}.")
        if tool.name in self._tools:
            raise ToolValidationError(f"Tool '{tool.name}' is already registered in ToolRegistry.")
        self._tools[tool.name] = tool

    def unregister_tool(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get_tool(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' is not registered in ToolRegistry.")
        return self._tools[name]

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def has_tool(self, name: str) -> bool:
        return name in self._tools


class InMemoryToolExecutionPort(IToolExecutionPort):
    """Reference test fake executing canned or mocked capability handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[dict[str, object]], object]] = {}

    def register_handler(
        self,
        capability_name: str,
        handler: Callable[[dict[str, object]], object],
    ) -> None:
        self._handlers[capability_name] = handler

    async def execute_tool(
        self,
        tenant_id: str,
        capability_name: str,
        arguments: dict[str, object],
        authorizer: ToolAuthorizer | None = None,
    ) -> object:
        require_identifier(tenant_id, "tenant_id")
        if authorizer is not None:
            is_allowed = await authorizer(capability_name, arguments)
            if not is_allowed:
                raise ToolAuthorizationError(
                    f"Authorization denied for capability '{capability_name}'."
                )

        if capability_name not in self._handlers:
            raise ToolExecutionError(
                f"No handler registered for capability '{capability_name}' in test port."
            )

        handler = self._handlers[capability_name]
        if asyncio.iscoroutinefunction(handler):
            return await handler(arguments)
        res = handler(arguments)
        if isinstance(res, Awaitable):
            return await res
        return res


class AIToolInvoker:
    """Validates, authorizes, and dispatches tool requests across the port boundary."""

    def __init__(
        self,
        registry: IToolRegistry,
        execution_port: IToolExecutionPort,
        default_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    ) -> None:
        self._registry = registry
        self._execution_port = execution_port
        self._default_timeout_seconds = max(
            MIN_TOOL_TIMEOUT_SECONDS, min(default_timeout_seconds, MAX_TOOL_TIMEOUT_SECONDS)
        )

    async def invoke(
        self,
        tool_call: dict[str, object],
        authorizer: ToolAuthorizer,
    ) -> dict[str, object]:
        """Backward-compatible invocation matching M1 IAIToolInvoker protocol signature."""
        tenant_id = str(tool_call.get("tenant_id", "default"))
        call_id = str(tool_call.get("call_id", "call-legacy"))
        tool_name = str(tool_call.get("name") or tool_call.get("tool_name", ""))
        raw_args = tool_call.get("arguments", {})
        arguments = raw_args if isinstance(raw_args, dict) else {}

        call = ToolCall(call_id=call_id, tool_name=tool_name, arguments=arguments)
        result = await self.invoke_tool(tenant_id, call, authorizer=authorizer)
        return result.model_dump()

    async def invoke_tool(
        self,
        tenant_id: str,
        tool_call: ToolCall,
        authorizer: ToolAuthorizer | None = None,
    ) -> ToolResult:
        """Validate, authorize, and invoke a single tool call with timeout and error handling."""
        start_time = time.perf_counter()
        try:
            require_identifier(tenant_id, "tenant_id")
        except Exception as exc:
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                status=ToolExecutionStatus.INVALID_ARGUMENTS,
                error_message=str(exc),
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        if not self._registry.has_tool(tool_call.tool_name):
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                status=ToolExecutionStatus.NOT_FOUND,
                error_message=f"Tool '{tool_call.tool_name}' is not registered.",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        tool = self._registry.get_tool(tool_call.tool_name)

        try:
            tool.validate_arguments(tool_call.arguments)
        except ToolValidationError as exc:
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                status=ToolExecutionStatus.INVALID_ARGUMENTS,
                error_message=str(exc),
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        if authorizer is not None:
            try:
                allowed = await authorizer(tool.canonical_capability, tool_call.arguments)
                if not allowed:
                    return ToolResult(
                        call_id=tool_call.call_id,
                        tool_name=tool_call.tool_name,
                        status=ToolExecutionStatus.DENIED,
                        error_message=f"Authorization denied for capability '{tool.canonical_capability}'.",
                        execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    )
            except Exception as exc:
                return ToolResult(
                    call_id=tool_call.call_id,
                    tool_name=tool_call.tool_name,
                    status=ToolExecutionStatus.DENIED,
                    error_message=f"Authorizer raised exception: {type(exc).__name__}",
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                )

        timeout = tool.timeout_seconds or self._default_timeout_seconds
        try:
            output = await asyncio.wait_for(
                self._execution_port.execute_tool(
                    tenant_id=tenant_id,
                    capability_name=tool.canonical_capability,
                    arguments=tool_call.arguments,
                    authorizer=authorizer,
                ),
                timeout=timeout,
            )
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                status=ToolExecutionStatus.SUCCESS,
                output=output,
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )
        except TimeoutError:
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                status=ToolExecutionStatus.TIMEOUT,
                error_message=f"Tool '{tool_call.tool_name}' timed out after {timeout}s.",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )
        except ToolAuthorizationError as exc:
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                status=ToolExecutionStatus.DENIED,
                error_message=str(exc),
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )
        except Exception as exc:
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                status=ToolExecutionStatus.EXECUTION_ERROR,
                error_message=f"Tool execution failed: {type(exc).__name__}",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

    async def invoke_all(
        self,
        tenant_id: str,
        tool_calls: list[ToolCall],
        authorizer: ToolAuthorizer | None = None,
        sequential: bool = True,
    ) -> list[ToolResult]:
        """Invoke a batch of tool calls with ordering guarantee."""
        if len(tool_calls) > MAX_BATCH_SIZE:
            raise ToolValidationError(
                f"Batch size {len(tool_calls)} exceeds MAX_BATCH_SIZE ({MAX_BATCH_SIZE})."
            )

        if sequential:
            results: list[ToolResult] = []
            for call in tool_calls:
                res = await self.invoke_tool(tenant_id, call, authorizer=authorizer)
                results.append(res)
            return results

        tasks = [self.invoke_tool(tenant_id, call, authorizer=authorizer) for call in tool_calls]
        return list(await asyncio.gather(*tasks))


__all__ = [
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "MAX_BATCH_SIZE",
    "MAX_TOOL_ARGUMENTS_BYTES",
    "MAX_TOOL_OUTPUT_CHARS",
    "MAX_TOOL_TIMEOUT_SECONDS",
    "MIN_TOOL_TIMEOUT_SECONDS",
    "TRUNCATION_SUFFIX",
    "AIToolInvoker",
    "IToolExecutionPort",
    "IToolRegistry",
    "InMemoryToolExecutionPort",
    "ToolCall",
    "ToolDefinition",
    "ToolExecutionStatus",
    "ToolRegistry",
    "ToolResult",
    "validate_schema",
]
