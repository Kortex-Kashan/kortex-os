"""Production Kernel Runtime Bridge Adapter for KORTEX AI Orchestration Engine.

Governed by Milestone 9.1 architecture specification:
docs/architecture/ai_engine_m9_production_runtime_spec.md

Translates AI Engine port invocations (IKernelBridge) into typed CapabilityRequest
dispatches executed through the Kernel capability enforcement boundary.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from typing import Protocol, cast

from kortex.engines.ai.exceptions import BridgeValidationError
from kortex.engines.ai.interfaces import IKernelBridge

logger = logging.getLogger("kortex.engines.ai.bridge")


class _KernelRuntime(Protocol):
    """Structural protocol defining Kernel methods consumed by KernelBridgeAdapter."""

    def register_capability(
        self,
        name: str,
        description: str,
        provider: str,
        handler: Callable[..., object] | None = None,
        parameters_schema: dict[str, object] | None = None,
        returns_schema: dict[str, object] | None = None,
        required_permissions: list[str] | None = None,
        requires_authentication: bool = True,
        security_classification: str = "INTERNAL",
    ) -> object: ...

    async def publish_event(
        self,
        topic: str,
        payload: dict[str, object] | None = None,
        sender: str = "ai",
    ) -> object: ...

    async def invoke_capability(self, request: object) -> object: ...


def _get_capability_request_cls() -> Callable[..., object]:
    """Dynamically resolve CapabilityRequest to maintain AST isolation."""
    dispatch_module = importlib.import_module("kortex.core.dispatch")
    return cast(Callable[..., object], getattr(dispatch_module, "CapabilityRequest"))  # noqa: B009


class KernelBridgeAdapter(IKernelBridge):
    """Production adapter implementing `IKernelBridge` over KORTEX `Kernel`.

    Invariants:
    - Never bypasses Kernel or CapabilityDispatcher.
    - Never performs direct handler invocation or custom RBAC evaluation.
    - Converts raw capability parameters and identity context into validated `CapabilityRequest`.
    - Fails closed on missing or blank `tenant_id` or `capability_name`.
    - Decoupled from concrete Kernel type via duck typing and dependency injection.
    """

    def __init__(self, kernel: _KernelRuntime) -> None:
        if kernel is None:
            raise BridgeValidationError("Kernel instance must not be None.")
        self._kernel = kernel

    def register_capability(
        self,
        name: str,
        description: str,
        provider: str,
        handler: Callable[..., object] | None = None,
        parameters_schema: dict[str, object] | None = None,
        returns_schema: dict[str, object] | None = None,
        required_permissions: list[str] | None = None,
        requires_authentication: bool = True,
        security_classification: str = "INTERNAL",
    ) -> object:
        """Register a canonical system capability with the Kernel Registry."""
        return self._kernel.register_capability(
            name=name,
            description=description,
            provider=provider,
            handler=handler,
            parameters_schema=parameters_schema,
            returns_schema=returns_schema,
            required_permissions=required_permissions,
            requires_authentication=requires_authentication,
            security_classification=security_classification,
        )

    async def publish_event(
        self,
        topic: str,
        payload: dict[str, object] | None = None,
        sender: str = "ai",
    ) -> object:
        """Publish an asynchronous system event to the Kernel Event Engine."""
        return await self._kernel.publish_event(
            topic=topic,
            payload=payload,
            sender=sender,
        )

    async def invoke_capability(
        self,
        name: str,
        arguments: dict[str, object],
        tenant_id: str,
        user_id: str | None = None,
        request_id: str | None = None,
        session_token: object | None = None,
    ) -> object:
        """Translate and dispatch a capability invocation through Kernel enforcement boundary."""
        if not isinstance(name, str) or not name.strip():
            raise BridgeValidationError("capability_name must not be empty or whitespace-only.")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise BridgeValidationError("tenant_id must not be empty or whitespace-only.")
        if arguments is None:
            arguments = {}
        elif not isinstance(arguments, dict):
            raise BridgeValidationError("arguments must be a dict.")

        context: dict[str, object] = {"tenant_id": tenant_id.strip()}
        if user_id is not None:
            if not isinstance(user_id, str) or not user_id.strip():
                raise BridgeValidationError("user_id, if provided, must not be whitespace-only.")
            context["user_id"] = user_id.strip()

        if request_id is not None:
            if not isinstance(request_id, str) or not request_id.strip():
                raise BridgeValidationError("request_id, if provided, must not be whitespace-only.")
            context["request_id"] = request_id.strip()

        capability_request_cls = _get_capability_request_cls()
        capability_request = capability_request_cls(
            capability_name=name.strip(),
            parameters=arguments,
            context=context,
            session_token=session_token,
        )

        return await self._kernel.invoke_capability(capability_request)


__all__ = [
    "KernelBridgeAdapter",
]
