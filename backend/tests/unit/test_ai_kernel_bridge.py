"""Unit & adversarial tests for KORTEX AI Kernel Bridge Adapter (Milestone 9.1).

Tests adhere strictly to the ratified M9.1 specification:
- Translation of IKernelBridge.invoke_capability() into CapabilityRequest
- Runtime protocol compliance with IKernelBridge (isinstance check)
- Strict identity validation and fail-closed behavior on missing identifiers
- Security boundary enforcement (no direct handler execution or bypass)
- AST import quarantine (zero forbidden imports)
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kortex.core.dispatch import CapabilityRequest
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.exceptions import BridgeValidationError
from kortex.engines.ai.interfaces import IKernelBridge
from kortex.engines.security.models import PrincipalType, TokenPayload

# ---------------------------------------------------------------------------
# Fakes & Mocks
# ---------------------------------------------------------------------------


class FakeKernel:
    """Fake Kernel capturing capability registrations, events, and dispatches."""

    def __init__(self) -> None:
        self.registered_capabilities: dict[str, dict[str, object]] = {}
        self.published_events: list[dict[str, object]] = []
        self.dispatched_requests: list[CapabilityRequest] = []
        self.mock_response: object = {"status": "success", "data": "test-data"}
        self.should_raise: Exception | None = None

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
    ) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "name": name,
            "description": description,
            "provider": provider,
            "handler": handler,
            "parameters_schema": parameters_schema,
            "returns_schema": returns_schema,
            "required_permissions": required_permissions,
            "requires_authentication": requires_authentication,
            "security_classification": security_classification,
        }
        self.registered_capabilities[name] = descriptor
        return descriptor

    async def publish_event(
        self,
        topic: str,
        payload: dict[str, object] | None = None,
        sender: str = "ai",
    ) -> dict[str, object]:
        event_record: dict[str, object] = {"topic": topic, "payload": payload, "sender": sender}
        self.published_events.append(event_record)
        return event_record

    async def invoke_capability(self, request: CapabilityRequest) -> object:
        if self.should_raise is not None:
            raise self.should_raise
        self.dispatched_requests.append(request)
        return self.mock_response


# ---------------------------------------------------------------------------
# §1 — Protocol Compliance Tests
# ---------------------------------------------------------------------------


def test_kernel_bridge_adapter_satisfies_ikernel_bridge_protocol() -> None:
    """Verify KernelBridgeAdapter satisfies IKernelBridge @runtime_checkable protocol."""
    fake_kernel = FakeKernel()
    adapter = KernelBridgeAdapter(fake_kernel)

    assert isinstance(adapter, IKernelBridge)


def test_kernel_bridge_adapter_rejects_none_kernel() -> None:
    """Verify constructor raises BridgeValidationError if Kernel is None."""
    with pytest.raises(BridgeValidationError, match="Kernel instance must not be None"):
        KernelBridgeAdapter(None)


# ---------------------------------------------------------------------------
# §2 — Translation & Invocation Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_capability_translation_to_capability_request() -> None:
    """Verify invoke_capability translates arguments into a typed CapabilityRequest."""
    fake_kernel = FakeKernel()
    adapter = KernelBridgeAdapter(fake_kernel)

    token = TokenPayload(
        token_id="session-id-123",  # noqa: S106
        principal_id="user-42",
        principal_type=PrincipalType.USER,
        tenant_id="tenant-alpha",
        issued_at_utc=datetime.now(UTC),
        expires_at_utc=datetime.now(UTC),
    )

    args = {"query": "Find latest Q3 invoices", "limit": 10}
    result = await adapter.invoke_capability(
        name="kortex.document.search",
        arguments=args,
        tenant_id="tenant-alpha",
        user_id="user-42",
        request_id="req-999",
        session_token=token,
    )

    assert result == fake_kernel.mock_response
    assert len(fake_kernel.dispatched_requests) == 1

    req = fake_kernel.dispatched_requests[0]
    assert isinstance(req, CapabilityRequest)
    assert req.capability_name == "kortex.document.search"
    assert req.parameters == {"query": "Find latest Q3 invoices", "limit": 10}
    assert req.context == {
        "tenant_id": "tenant-alpha",
        "user_id": "user-42",
        "request_id": "req-999",
    }
    assert req.session_token == token


@pytest.mark.asyncio
async def test_invoke_capability_optional_context_fields() -> None:
    """Verify invoke_capability cleanly handles omitted user_id and request_id."""
    fake_kernel = FakeKernel()
    adapter = KernelBridgeAdapter(fake_kernel)

    await adapter.invoke_capability(
        name="kortex.ai.response.generate",
        arguments={"prompt": "hello"},
        tenant_id="tenant-beta",
    )

    req = fake_kernel.dispatched_requests[0]
    assert req.context == {"tenant_id": "tenant-beta"}
    assert req.session_token is None


@pytest.mark.asyncio
async def test_register_capability_passthrough() -> None:
    """Verify register_capability forwards all metadata to Kernel."""
    fake_kernel = FakeKernel()
    adapter = KernelBridgeAdapter(fake_kernel)

    def dummy_handler() -> str:
        return "ok"

    adapter.register_capability(
        name="kortex.ai.test.run",
        description="Run AI test",
        provider="ai",
        handler=dummy_handler,
        required_permissions=["ai:test"],
        requires_authentication=True,
        security_classification="RESTRICTED",
    )

    assert "kortex.ai.test.run" in fake_kernel.registered_capabilities
    cap = fake_kernel.registered_capabilities["kortex.ai.test.run"]
    assert cap["description"] == "Run AI test"
    assert cap["provider"] == "ai"
    assert cap["handler"] is dummy_handler
    assert cap["required_permissions"] == ["ai:test"]
    assert cap["security_classification"] == "RESTRICTED"


@pytest.mark.asyncio
async def test_publish_event_passthrough() -> None:
    """Verify publish_event forwards event topic, payload, and sender to Kernel."""
    fake_kernel = FakeKernel()
    adapter = KernelBridgeAdapter(fake_kernel)

    await adapter.publish_event(
        topic="ai.generation.started",
        payload={"request_id": "req-123"},
        sender="ai",
    )

    assert len(fake_kernel.published_events) == 1
    event = fake_kernel.published_events[0]
    assert event["topic"] == "ai.generation.started"
    assert event["payload"] == {"request_id": "req-123"}
    assert event["sender"] == "ai"


# ---------------------------------------------------------------------------
# §3 — Missing Identity & Adversarial Validation Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank_tenant", ["", "   ", None])
@pytest.mark.asyncio
async def test_invoke_capability_fails_on_blank_tenant_id(blank_tenant: str | None) -> None:
    """Verify invoke_capability strictly rejects blank or missing tenant_id."""
    fake_kernel = FakeKernel()
    adapter = KernelBridgeAdapter(fake_kernel)

    with pytest.raises(BridgeValidationError, match="tenant_id"):
        await adapter.invoke_capability(
            name="kortex.ai.tool.invoke",
            arguments={},
            tenant_id=blank_tenant,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("blank_name", ["", "   ", None])
@pytest.mark.asyncio
async def test_invoke_capability_fails_on_blank_capability_name(blank_name: str | None) -> None:
    """Verify invoke_capability strictly rejects blank or missing capability name."""
    fake_kernel = FakeKernel()
    adapter = KernelBridgeAdapter(fake_kernel)

    with pytest.raises(BridgeValidationError, match="capability_name"):
        await adapter.invoke_capability(
            name=blank_name,  # type: ignore[arg-type]
            arguments={},
            tenant_id="tenant-1",
        )


@pytest.mark.parametrize("whitespace_val", ["", "   "])
@pytest.mark.asyncio
async def test_invoke_capability_fails_on_whitespace_optional_identities(
    whitespace_val: str,
) -> None:
    """Verify invoke_capability rejects whitespace-only user_id or request_id."""
    fake_kernel = FakeKernel()
    adapter = KernelBridgeAdapter(fake_kernel)

    with pytest.raises(BridgeValidationError, match="user_id"):
        await adapter.invoke_capability(
            name="kortex.ai.test",
            arguments={},
            tenant_id="tenant-1",
            user_id=whitespace_val,
        )

    with pytest.raises(BridgeValidationError, match="request_id"):
        await adapter.invoke_capability(
            name="kortex.ai.test",
            arguments={},
            tenant_id="tenant-1",
            request_id=whitespace_val,
        )


@pytest.mark.asyncio
async def test_invoke_capability_fails_on_invalid_arguments_type() -> None:
    """Verify invoke_capability rejects non-dict arguments."""
    fake_kernel = FakeKernel()
    adapter = KernelBridgeAdapter(fake_kernel)

    with pytest.raises(BridgeValidationError, match="arguments must be a dict"):
        await adapter.invoke_capability(
            name="kortex.ai.test",
            arguments="not-a-dict",  # type: ignore[arg-type]
            tenant_id="tenant-1",
        )


# ---------------------------------------------------------------------------
# §4 — Security Boundary & Exception Propagation Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_capability_propagates_kernel_exceptions() -> None:
    """Verify adapter propagates exceptions raised by Kernel / CapabilityDispatcher."""
    fake_kernel = FakeKernel()
    fake_kernel.should_raise = PermissionError("Access denied by SecurityEngine")
    adapter = KernelBridgeAdapter(fake_kernel)

    with pytest.raises(PermissionError, match="Access denied by SecurityEngine"):
        await adapter.invoke_capability(
            name="kortex.security.admin",
            arguments={},
            tenant_id="tenant-1",
        )


def test_adapter_does_not_execute_handlers_directly() -> None:
    """Verify adapter holds no direct execution logic or handler storage."""
    fake_kernel = FakeKernel()
    adapter = KernelBridgeAdapter(fake_kernel)

    assert not hasattr(adapter, "_handlers")
    assert not hasattr(adapter, "_execute_direct")


# ---------------------------------------------------------------------------
# §5 — AST Import Quarantine Test
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
    "kortex.engines.security",
    "kortex.core.container",
    "kortex.core.kernel",
    "sqlalchemy",
    "openai",
    "anthropic",
    "google.generativeai",
]


def test_bridge_py_quarantine_forbidden_imports() -> None:
    """Verify bridge.py does not import forbidden namespaces."""
    target_path = (
        Path(__file__).parent.parent.parent
        / "src"
        / "kortex"
        / "engines"
        / "ai"
        / "bridge.py"
    )
    imports = _collect_imports(target_path)
    for forbidden in FORBIDDEN_NAMESPACES:
        violations = [
            imp for imp in imports if imp == forbidden or imp.startswith(forbidden + ".")
        ]
        assert violations == [], (
            f"bridge.py illegally imports {forbidden!r}: {violations}"
        )
