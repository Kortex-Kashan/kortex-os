"""Unit tests for Tier 2 Event Engine Telemetry & Tier 3 External Observability (Milestone 9.5).

Tests adhere strictly to the ratified M9.5 specification:
- Event emission for generation, provider, agent, security, and tool lifecycles
- Non-blocking & exception-isolated telemetry execution
- Secret sanitization (redaction of tokens, keys, passwords, credentials)
- Multi-tenant safety (tenant metadata preserved, zero conversation content leakage)
- Tier 3 ITelemetryExporter and InMemoryTelemetryExporter integration
- AST quarantine against forbidden infrastructure and external vendor SDKs
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kortex.engines.ai.diagnostics import AIDiagnostics
from kortex.engines.ai.telemetry import (
    AITelemetryEmitter,
    sanitize_telemetry_payload,
)
from kortex.engines.ai.telemetry_ports import InMemoryTelemetryExporter


class FakeKernelBridge:
    """Fake bridge capturing published events for testing."""

    def __init__(self, should_fail: bool = False) -> None:
        self.published_events: list[dict[str, object]] = []
        self.should_fail = should_fail

    async def publish_event(
        self,
        topic: str,
        payload: dict[str, object],
        sender: str = "ai",
    ) -> None:
        if self.should_fail:
            raise RuntimeError("Event engine connection dropped!")
        self.published_events.append({
            "topic": topic,
            "payload": payload,
            "sender": sender,
        })

    async def invoke_capability(
        self,
        name: str,
        arguments: dict[str, object],
        tenant_id: str,
        user_id: str | None = None,
        request_id: str | None = None,
        session_token: object | None = None,
    ) -> object:
        return {"status": "ok"}

    def subscribe_event(self, topic: str, handler: object, subscriber_name: str = "anonymous") -> str:
        return "sub-dummy"

    def register_capability(self, **kwargs: object) -> None:
        pass


# ---------------------------------------------------------------------------
# §1 — Event Emission Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_events_emission() -> None:
    """Verify started, completed, and failed generation events are emitted with sanitized metadata."""
    bridge = FakeKernelBridge()
    diag = AIDiagnostics()
    exporter = InMemoryTelemetryExporter()
    telemetry = AITelemetryEmitter(
        kernel_bridge=bridge, diagnostics=diag, exporter=exporter
    )

    # 1. Started
    await telemetry.emit_generation_started(
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        request_id="req-1",
    )
    assert len(bridge.published_events) == 1
    assert bridge.published_events[0]["topic"] == "ai.generation.started"
    assert bridge.published_events[0]["payload"]["tenant_id"] == "tenant-1"
    assert exporter.get_counter_value("ai.generation.requests") == 1

    # 2. Completed
    await telemetry.emit_generation_completed(
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        request_id="req-1",
        latency_ms=120.0,
        token_usage={"prompt_tokens": 15, "completion_tokens": 30},
    )
    assert len(bridge.published_events) == 2
    assert bridge.published_events[1]["topic"] == "ai.generation.completed"
    assert bridge.published_events[1]["payload"]["execution_time_ms"] == 120.0
    assert exporter.get_counter_value("ai.generation.success") == 1
    assert exporter.get_counter_value("ai.tokens.total") == 45
    assert diag.metrics()["tokens"]["total_tokens_used"] == 45

    # 3. Failed
    await telemetry.emit_generation_failed(
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        request_id="req-2",
        latency_ms=50.0,
        error_category="RateLimitError",
    )
    assert len(bridge.published_events) == 3
    assert bridge.published_events[2]["topic"] == "ai.generation.failed"
    assert exporter.get_counter_value("ai.generation.failure") == 1


@pytest.mark.asyncio
async def test_provider_resilience_events_emission() -> None:
    """Verify provider failure, timeout, and fallback events."""
    bridge = FakeKernelBridge()
    diag = AIDiagnostics()
    exporter = InMemoryTelemetryExporter()
    telemetry = AITelemetryEmitter(
        kernel_bridge=bridge, diagnostics=diag, exporter=exporter
    )

    await telemetry.emit_provider_timeout(
        provider_id="ollama-local",
        timeout_seconds=30.0,
        tenant_id="tenant-1",
    )
    await telemetry.emit_provider_fallback(
        primary_provider_id="ollama-local",
        fallback_provider_id="vllm-backup",
        reason="Timeout",
        tenant_id="tenant-1",
    )
    await telemetry.emit_provider_failure(
        provider_id="ollama-local",
        error_category="ConnectionRefused",
        is_transient=True,
        tenant_id="tenant-1",
    )

    assert len(bridge.published_events) == 3
    topics = [e["topic"] for e in bridge.published_events]
    assert topics == [
        "ai.provider.timeout",
        "ai.provider.fallback",
        "ai.provider.failure",
    ]
    assert exporter.get_counter_value("ai.provider.timeout") == 1
    assert exporter.get_counter_value("ai.provider.fallback") == 1
    assert exporter.get_counter_value("ai.provider.failure") == 1


@pytest.mark.asyncio
async def test_agent_orchestration_events_emission() -> None:
    """Verify agent task completed, failed, and loop detected events."""
    bridge = FakeKernelBridge()
    telemetry = AITelemetryEmitter(kernel_bridge=bridge)

    await telemetry.emit_agent_completed(
        task_id="task-1",
        tenant_id="tenant-1",
        user_id="user-1",
        total_steps=4,
        latency_ms=450.0,
    )
    await telemetry.emit_agent_loop_detected(
        task_id="task-2",
        tenant_id="tenant-1",
        user_id="user-1",
        tool_name="search_db",
        step_count=3,
    )
    await telemetry.emit_agent_failed(
        task_id="task-3",
        tenant_id="tenant-1",
        user_id="user-1",
        total_steps=1,
        latency_ms=100.0,
        error_category="OrchestrationError",
    )

    topics = [e["topic"] for e in bridge.published_events]
    assert topics == [
        "ai.agent.completed",
        "ai.agent.loop_detected",
        "ai.agent.failed",
    ]


@pytest.mark.asyncio
async def test_security_and_tool_events_emission() -> None:
    """Verify security denied, validation failed, tool invoked, and tool denied events."""
    bridge = FakeKernelBridge()
    telemetry = AITelemetryEmitter(kernel_bridge=bridge)

    await telemetry.emit_security_denied(
        tenant_id="tenant-1",
        user_id="user-unauthorized",
        action="execute_tool",
        reason="Missing permission ai:execute",
    )
    await telemetry.emit_security_validation_failed(
        tenant_id="tenant-1",
        validation_type="tenant_id",
        reason="Invalid tenant format",
    )
    await telemetry.emit_tool_invoked(
        tenant_id="tenant-1",
        tool_name="file_read",
        request_id="req-tool-1",
    )
    await telemetry.emit_tool_denied(
        tenant_id="tenant-1",
        tool_name="file_delete",
        request_id="req-tool-2",
        reason="Mutating action forbidden",
    )

    topics = [e["topic"] for e in bridge.published_events]
    assert topics == [
        "ai.security.denied",
        "ai.security.validation_failed",
        "ai.tool.invoked",
        "ai.tool.denied",
    ]


@pytest.mark.asyncio
async def test_tool_completed_event_emission_carries_latency() -> None:
    """M7.6-W3: a successful tool invocation must emit a domain event and
    exporter counter, symmetric with emit_tool_failed/emit_tool_denied --
    previously it recorded latency only into AIDiagnostics directly,
    publishing no event and incrementing no counter at all."""
    bridge = FakeKernelBridge()
    diag = AIDiagnostics()
    exporter = InMemoryTelemetryExporter()
    telemetry = AITelemetryEmitter(kernel_bridge=bridge, diagnostics=diag, exporter=exporter)

    await telemetry.emit_tool_completed(
        tenant_id="tenant-1",
        tool_name="knowledge_search",
        request_id="req-tool-3",
        latency_ms=42.5,
    )

    # 1. successful execution emits latency
    assert len(bridge.published_events) == 1
    assert bridge.published_events[0]["topic"] == "ai.tool.completed"
    assert bridge.published_events[0]["payload"]["execution_time_ms"] == 42.5
    assert bridge.published_events[0]["payload"]["tool_name"] == "knowledge_search"

    # 2. latency is non-negative and structurally valid
    assert bridge.published_events[0]["payload"]["execution_time_ms"] >= 0.0
    assert diag.metrics()["tool_invocations"]["successful"] == 1
    assert exporter.get_counter_value("ai.tool.completed") == 1

    # 4. no duplicate telemetry event is emitted for one call
    assert len(bridge.published_events) == 1


@pytest.mark.asyncio
async def test_tool_failed_and_denied_telemetry_unaffected_by_completed_event() -> None:
    """3. existing failure/timeout telemetry remains unchanged by the
    M7.6-W3 addition -- emit_tool_failed/emit_tool_denied still behave
    exactly as `test_security_and_tool_events_emission` already proves,
    with no change to their own topics, payloads, or counters."""
    bridge = FakeKernelBridge()
    diag = AIDiagnostics()
    exporter = InMemoryTelemetryExporter()
    telemetry = AITelemetryEmitter(kernel_bridge=bridge, diagnostics=diag, exporter=exporter)

    await telemetry.emit_tool_failed(
        tenant_id="tenant-1",
        tool_name="knowledge_search",
        request_id="req-tool-4",
        error_category="TimeoutError",
        latency_ms=999.0,
        is_timeout=True,
    )
    await telemetry.emit_tool_denied(
        tenant_id="tenant-1",
        tool_name="knowledge_search",
        request_id="req-tool-5",
        reason="Missing permission",
        latency_ms=1.0,
    )

    topics = [e["topic"] for e in bridge.published_events]
    assert topics == ["ai.tool.failed", "ai.tool.denied"]
    assert exporter.get_counter_value("ai.tool.failed") == 1
    assert exporter.get_counter_value("ai.tool.denied") == 1
    # The new SUCCESS-path counter must not be incremented by failure/denial.
    assert exporter.get_counter_value("ai.tool.completed") == 0


# ---------------------------------------------------------------------------
# §2 — Non-Blocking & Exception Isolation Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telemetry_event_failure_never_crashes_caller() -> None:
    """Verify telemetry emission catches all underlying bridge exceptions safely."""
    broken_bridge = FakeKernelBridge(should_fail=True)
    telemetry = AITelemetryEmitter(kernel_bridge=broken_bridge)

    # These should complete without raising any exceptions
    await telemetry.emit_generation_started(
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        request_id="req-1",
    )
    await telemetry.emit_generation_completed(
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        request_id="req-1",
        latency_ms=100.0,
    )
    await telemetry.emit_provider_failure(
        provider_id="ollama-local",
        error_category="Timeout",
        is_transient=True,
    )
    await telemetry.emit_agent_loop_detected(
        task_id="task-1",
        tenant_id="tenant-1",
        user_id="user-1",
        tool_name="tool_a",
        step_count=3,
    )


# ---------------------------------------------------------------------------
# §3 — Secret Sanitization & Privacy Tests
# ---------------------------------------------------------------------------


def test_secret_sanitization_removes_credentials() -> None:
    """Verify secrets and bearer tokens are redacted from telemetry payloads."""
    raw_payload = {
        "event_id": "evt-123",
        "tenant_id": "tenant-1",
        "api_key": "sk-proj-1234567890",
        "nested": {
            "token": "bearer eyJhbGciOi...",
            "password": "super-secret-password",
            "safe_metric": 42.0,
        },
        "credentials_list": [
            {"secret_key": "my-secret", "status": "active"},
            "plain-string",
        ],
    }

    sanitized = sanitize_telemetry_payload(raw_payload)
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["token"] == "[REDACTED]"  # noqa: S105
    assert sanitized["nested"]["password"] == "[REDACTED]"  # noqa: S105
    assert sanitized["nested"]["safe_metric"] == 42.0
    assert sanitized["credentials_list"][0]["secret_key"] == "[REDACTED]"  # noqa: S105
    assert sanitized["credentials_list"][0]["status"] == "active"
    assert sanitized["tenant_id"] == "tenant-1"


# ---------------------------------------------------------------------------
# §4 — AST Import Quarantine
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
    "openai",
    "anthropic",
    "google.generativeai",
    "sqlalchemy",
    "kortex.engines.security.engine",
    "kortex.core.kernel.Kernel",
]


@pytest.mark.parametrize(
    "filename",
    [
        "diagnostics.py",
        "telemetry.py",
        "telemetry_ports.py",
    ],
)
def test_telemetry_files_quarantine_forbidden_imports(filename: str) -> None:
    """Verify telemetry and diagnostics modules do not import forbidden SDKs or DB engines."""
    target_path = (
        Path(__file__).parent.parent.parent
        / "src"
        / "kortex"
        / "engines"
        / "ai"
        / filename
    )
    imports = _collect_imports(target_path)
    for forbidden in FORBIDDEN_NAMESPACES:
        violations = [
            imp for imp in imports if imp == forbidden or imp.startswith(forbidden + ".")
        ]
        assert violations == [], (
            f"{filename} illegally imports {forbidden!r}: {violations}"
        )
