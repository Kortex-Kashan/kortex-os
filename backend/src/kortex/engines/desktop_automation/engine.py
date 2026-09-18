"""The KORTEX Desktop Automation Engine (Phase 6).

Registers the four native-Windows-UI-automation capabilities and reaches the
Desktop Agent exclusively through the existing, unmodified enforcement path:
`Kernel.invoke_capability` -> `CapabilityDispatcher` -> `SecurityEngine` ->
this engine's handler -> `AgentGatewayEngine.send_desktop_command` -> the
Phase 6 desktop-command transport -> mTLS -> Desktop Agent -> FlaUI/UIA.

This engine adds no second authorization authority and no second workflow
engine: every capability below is `requires_execution_context=True`, so
`tenant_id` and `principal` come exclusively from the dispatcher-verified
`CapabilityExecutionContext` — never from a caller-supplied parameter — and
every RBAC/ABAC decision is made by `SecurityEngine.authorize()` before a
handler here ever runs.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.container import Container
from kortex.engines.agent_gateway.engine import AgentGatewayEngine
from kortex.engines.agent_gateway.protos import agent_pb2
from kortex.engines.desktop_automation.exceptions import DesktopInvalidSelectorError, error_for_code
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import CaUnavailableError, ServerCertificateUnavailableError
from kortex.engines.security.pki import KortexPki

if TYPE_CHECKING:
    from collections.abc import Callable

    from kortex.core.dispatch import CapabilityExecutionContext
    from kortex.core.kernel import Kernel
    from kortex.engines.storage.interfaces import IDataStore

LAUNCH_CAPABILITY = "kortex.desktop.launch"
CLICK_CAPABILITY = "kortex.desktop.click"
TYPE_CAPABILITY = "kortex.desktop.type"
READ_TEXT_CAPABILITY = "kortex.desktop.read_text"

_DEFAULT_LAUNCH_TIMEOUT_SECONDS = 30
_DEFAULT_INTERACTION_TIMEOUT_SECONDS = 15
_DEFAULT_READ_MAX_LENGTH = 4096
# Grace added on top of a command's own agent-side timeout before the
# transport itself gives up waiting for a correlated result — covers network
# latency and gRPC/event-loop scheduling, never the UI operation itself.
_TRANSPORT_GRACE_SECONDS = 10.0


def _selector_field(selector: dict[str, Any], key: str) -> str:
    """Coerce one selector field to a string, treating only an absent or
    `None` value as "not supplied" — never a falsy-but-real value like `0`
    or `False`, which a caller could legitimately mean as a literal
    AutomationId. `"" or default"`-style coercion would silently drop such a
    value instead of matching it, weakening the exact selector this design
    exists to keep unambiguous."""
    value = selector.get(key)
    return "" if value is None else str(value)


def _build_selector(selector: dict[str, Any] | None) -> agent_pb2.UiElementSelector:
    selector = selector or {}
    automation_id = _selector_field(selector, "automation_id")
    name = _selector_field(selector, "name")
    control_type = _selector_field(selector, "control_type")
    if not (automation_id or name or control_type):
        raise DesktopInvalidSelectorError(
            "A UI element selector must supply at least one of automation_id, name, or control_type."
        )
    return agent_pb2.UiElementSelector(automation_id=automation_id, name=name, control_type=control_type)


class DesktopAutomationEngine(BaseEngine):
    """System Engine owning the `kortex.desktop.*` capability surface."""

    def __init__(
        self,
        *,
        data_store: IDataStore | None = None,
        host: str = "127.0.0.1",
        port: int = 50051,
    ) -> None:
        super().__init__()
        self._data_store = data_store
        self._host = host
        self._port = port
        self._gateway: AgentGatewayEngine | None = None
        self._gateway_running = False

    @property
    def name(self) -> str:
        return "desktop_automation"

    @property
    def dependencies(self) -> list[str]:
        return ["storage", "security"]

    @property
    def gateway(self) -> AgentGatewayEngine:
        if self._gateway is None:
            raise RuntimeError("DesktopAutomationEngine has not been initialized.")
        return self._gateway

    async def initialize(self, kernel: Kernel) -> None:
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Desktop Automation Engine...")

        try:
            self._resolve_data_store(kernel)
            security_engine = cast(SecurityEngine, kernel.get_engine("security"))
            pki = KortexPki(security_engine.secret_store)
            if self._data_store is None:
                raise RuntimeError("Desktop Automation Engine requires a Storage Engine data store.")
            self._gateway = AgentGatewayEngine(data_store=self._data_store, pki=pki, host=self._host, port=self._port)

            kernel.register_capability(
                name=LAUNCH_CAPABILITY,
                description="Launch an explicitly allow-listed Windows application via the Desktop Agent.",
                provider=self.name,
                handler=self.launch_application,
                required_permissions=["desktop:launch"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=False,
                is_idempotent=False,
            )
            kernel.register_capability(
                name=CLICK_CAPABILITY,
                description="Click a deterministically-resolved UI element in a previously launched application.",
                provider=self.name,
                handler=self.click_element,
                required_permissions=["desktop:click"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=False,
                is_idempotent=False,
            )
            kernel.register_capability(
                name=TYPE_CAPABILITY,
                description="Type text into a deterministically-resolved UI element.",
                provider=self.name,
                handler=self.type_text,
                required_permissions=["desktop:type"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=False,
                is_idempotent=False,
            )
            kernel.register_capability(
                name=READ_TEXT_CAPABILITY,
                description="Read bounded text from a deterministically-resolved UI element.",
                provider=self.name,
                handler=self.read_text,
                required_permissions=["desktop:read"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=True,
                is_idempotent=False,
            )

            self._set_state(EngineState.READY)
            self.logger.info("KORTEX Desktop Automation Engine initialized.")
        except Exception:
            self._set_state(EngineState.FAILED)
            raise

    def _resolve_data_store(self, kernel: Kernel) -> None:
        """Wire Storage Engine's `IDataStore`, matching `PythonExecutionEngine`'s
        established deferred-wiring pattern exactly."""
        if self._data_store is not None:
            return
        if not isinstance(getattr(kernel, "container", None), Container):
            return
        if not kernel.container.has("engine.storage"):
            return
        with contextlib.suppress(Exception):
            storage_engine = kernel.container.resolve("engine.storage")
            if storage_engine is not None and hasattr(storage_engine, "data"):
                self._data_store = storage_engine.data

    async def start(self) -> None:
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        try:
            await self.gateway.start()
            self._gateway_running = True
            self.logger.info("Desktop Agent Gateway listening for agent connections.")
        except (CaUnavailableError, ServerCertificateUnavailableError) as exc:
            # Fails open on *registration*, fails closed on *invocation*: the
            # capabilities stay registered and dispatchable, but with no
            # gateway listening, no agent session can ever exist, so every
            # invocation resolves zero matching sessions and is rejected by
            # `AgentGatewayEngine._resolve_target_session` — the same
            # fail-closed path an unavailable agent hits in production.
            # Requiring every KORTEX deployment to provision Desktop Agent
            # PKI material just to boot at all — most have no desktop agents
            # configured — would be the wrong direction to fail in.
            self._gateway_running = False
            self.logger.warning(
                "Desktop Agent Gateway did not start (%s); kortex.desktop.* capabilities remain "
                "registered but unreachable until PKI material is provisioned.",
                exc,
            )

    async def stop(self) -> None:
        self._set_state(EngineState.STOPPING)
        if self._gateway_running:
            await self.gateway.stop()
            self._gateway_running = False
        self._set_state(EngineState.STOPPED)

    async def health_check(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "state": self.state.value,
            "gateway_running": self._gateway_running,
            "active_agent_sessions": len(self.gateway.sessions) if self._gateway is not None else 0,
        }

    async def _dispatch(
        self,
        *,
        execution_context: CapabilityExecutionContext | None,
        agent_id: str | None,
        timeout_seconds: int,
        build_command: Callable[[str], agent_pb2.GatewayMessage],
    ) -> agent_pb2.DesktopCommandResult:
        """Shared plumbing behind every `kortex.desktop.*` handler: resolve
        the dispatcher-verified tenant, add the transport grace period, send,
        and decode a failure into its specific exception. Each handler below
        keeps only what's actually specific to it — its own parameters and
        its own wire message/return shape."""
        tenant_id = execution_context.tenant_id if execution_context is not None else "default"
        result = await self.gateway.send_desktop_command(
            tenant_id=tenant_id,
            agent_principal_id=agent_id,
            build_command=build_command,
            timeout_seconds=timeout_seconds + _TRANSPORT_GRACE_SECONDS,
        )
        _raise_if_failed(result)
        return result

    # -- Capability handlers -------------------------------------------------

    async def launch_application(
        self,
        application_id: str,
        arguments: list[str] | None = None,
        timeout_seconds: int = _DEFAULT_LAUNCH_TIMEOUT_SECONDS,
        agent_id: str | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.desktop.launch`."""
        result = await self._dispatch(
            execution_context=execution_context,
            agent_id=agent_id,
            timeout_seconds=timeout_seconds,
            build_command=lambda command_id: agent_pb2.GatewayMessage(
                desktop_launch=agent_pb2.DesktopLaunchCommand(
                    command_id=command_id,
                    application_id=application_id,
                    arguments=list(arguments or []),
                    timeout_seconds=timeout_seconds,
                )
            ),
        )
        return {"window_handle": result.window_handle, "process_id": result.process_id}

    async def click_element(
        self,
        window_handle: str,
        selector: dict[str, Any],
        agent_id: str | None = None,
        timeout_seconds: int = _DEFAULT_INTERACTION_TIMEOUT_SECONDS,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.desktop.click`."""
        proto_selector = _build_selector(selector)
        await self._dispatch(
            execution_context=execution_context,
            agent_id=agent_id,
            timeout_seconds=timeout_seconds,
            build_command=lambda command_id: agent_pb2.GatewayMessage(
                desktop_click=agent_pb2.DesktopClickCommand(
                    command_id=command_id, window_handle=window_handle, selector=proto_selector
                )
            ),
        )
        return {"success": True}

    async def type_text(
        self,
        window_handle: str,
        selector: dict[str, Any],
        ui_input_text: str,
        agent_id: str | None = None,
        timeout_seconds: int = _DEFAULT_INTERACTION_TIMEOUT_SECONDS,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.desktop.type`.

        `ui_input_text` — not `text` — deliberately: `SENSITIVE_KEY_NAMES`
        (`kortex.core.idempotency`) redacts this exact key from every audit
        record and cached idempotency response, closing the "sensitive values
        never written to logs" requirement at the one place `CapabilityDispatcher`
        already scrubs every other credential-shaped parameter, rather than
        adding a second, capability-specific scrubbing mechanism.
        """
        proto_selector = _build_selector(selector)
        await self._dispatch(
            execution_context=execution_context,
            agent_id=agent_id,
            timeout_seconds=timeout_seconds,
            build_command=lambda command_id: agent_pb2.GatewayMessage(
                desktop_type=agent_pb2.DesktopTypeCommand(
                    command_id=command_id,
                    window_handle=window_handle,
                    selector=proto_selector,
                    text=ui_input_text,
                )
            ),
        )
        return {"success": True}

    async def read_text(
        self,
        window_handle: str,
        selector: dict[str, Any],
        max_length: int = _DEFAULT_READ_MAX_LENGTH,
        agent_id: str | None = None,
        timeout_seconds: int = _DEFAULT_INTERACTION_TIMEOUT_SECONDS,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.desktop.read_text`. `max_length` bounds the agent's own
        response — never truncated only after an unbounded read, which would
        still have scraped and transmitted the full value first."""
        proto_selector = _build_selector(selector)
        result = await self._dispatch(
            execution_context=execution_context,
            agent_id=agent_id,
            timeout_seconds=timeout_seconds,
            build_command=lambda command_id: agent_pb2.GatewayMessage(
                desktop_read_text=agent_pb2.DesktopReadTextCommand(
                    command_id=command_id,
                    window_handle=window_handle,
                    selector=proto_selector,
                    max_length=max_length,
                )
            ),
        )
        return {"text": result.text, "truncated": result.truncated}


def _raise_if_failed(result: agent_pb2.DesktopCommandResult) -> None:
    if not result.success:
        raise error_for_code(result.error_code, result.error_message or "Desktop command failed.")


__all__ = [
    "CLICK_CAPABILITY",
    "LAUNCH_CAPABILITY",
    "READ_TEXT_CAPABILITY",
    "TYPE_CAPABILITY",
    "DesktopAutomationEngine",
]
