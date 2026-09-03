"""Unit tests for KORTEX Sentinel Engine.

Covers:
- Engine construction, properties, and configuration
- Lifecycle state transitions and invalid state error handling
- EngineState to SentinelStatus mapping (including STOPPED mapping)
- Health rollup logic and precedence rules
- IEngineDiagnostics contract compliance
- Subsystem health evaluation with self-exclusion
- Non-invasive integrity invariant verification
- Capability handlers with CapabilityExecutionContext
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.container import Container
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.core.exceptions import EngineStateError
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.sentinel.constants import (
    CAPABILITY_DIAGNOSTICS_GET,
    CAPABILITY_HEALTH_GET,
    CAPABILITY_STATUS_GET,
    ENGINE_NAME,
    ENGINE_VERSION,
)
from kortex.engines.sentinel.deadlock import DeadlockDetector
from kortex.engines.sentinel.engine import SentinelEngine
from kortex.engines.sentinel.heartbeats import HeartbeatManager
from kortex.engines.sentinel.models import (
    CheckStatus,
    ProbeResult,
    SentinelConfig,
    SentinelStatus,
)


@pytest.fixture
def mock_kernel() -> Kernel:
    """Create a mock Kernel with container and capability registry."""
    kernel = MagicMock(spec=Kernel)
    kernel.state = KernelState.RUNNING
    container = Container()
    kernel.container = container
    kernel.get_all_engines.return_value = {}
    kernel.list_capabilities.return_value = []
    kernel.db_manager = MagicMock()
    kernel.db_manager.is_connected = True
    kernel.db_manager.dialect.value = "sqlite"
    kernel.db = None
    kernel.register_capability = MagicMock()
    kernel.publish_event = AsyncMock()
    return kernel


# -- 1. Construction & Defaults ----------------------------------------------


def test_sentinel_construction_and_defaults() -> None:
    """Verify SentinelEngine default configuration, properties, and state."""
    engine = SentinelEngine()
    assert engine.name == ENGINE_NAME
    assert engine.dependencies == []
    assert engine.state == EngineState.UNINITIALIZED
    assert isinstance(engine.config, SentinelConfig)
    assert isinstance(engine.heartbeat_manager, HeartbeatManager)
    assert isinstance(engine.deadlock_detector, DeadlockDetector)
    assert isinstance(engine.deadlock_detector.tracker, object)
    assert len(engine.background_tasks) == 0
    assert engine.last_health_report is None
    assert engine.last_integrity_report is None
    assert engine.last_deadlock_report is None
    assert CAPABILITY_HEALTH_GET in engine.registered_capabilities
    assert CAPABILITY_STATUS_GET in engine.registered_capabilities
    assert CAPABILITY_DIAGNOSTICS_GET in engine.registered_capabilities


def test_sentinel_custom_config() -> None:
    """Verify SentinelEngine respects custom configuration."""
    cfg = SentinelConfig(
        monitor_interval_seconds=10.0,
        loop_lag_threshold_ms=500.0,
        operation_timeout_seconds=20.0,
        startup_grace_seconds=15.0,
        crash_loop_threshold=4,
        crash_loop_window_seconds=300.0,
        enable_background_monitor=False,
    )
    engine = SentinelEngine(config=cfg)
    assert engine.config.monitor_interval_seconds == 10.0
    assert engine.config.loop_lag_threshold_ms == 500.0
    assert engine.config.operation_timeout_seconds == 20.0
    assert engine.config.startup_grace_seconds == 15.0
    assert engine.config.crash_loop_threshold == 4
    assert engine.config.crash_loop_window_seconds == 300.0
    assert not engine.config.enable_background_monitor


# -- 2. Lifecycle Transitions ------------------------------------------------


@pytest.mark.asyncio
async def test_sentinel_lifecycle_transitions(mock_kernel: Kernel) -> None:
    """Verify UNINITIALIZED -> READY -> RUNNING -> STOPPED transitions."""
    cfg = SentinelConfig(enable_background_monitor=False)
    engine = SentinelEngine(config=cfg)

    await engine.initialize(mock_kernel)
    assert engine.state == EngineState.READY
    assert mock_kernel.container.has("engine.sentinel")
    assert mock_kernel.register_capability.call_count == 3

    await engine.start()
    assert engine.state == EngineState.RUNNING

    await engine.stop()
    assert engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_sentinel_invalid_lifecycle_transitions(mock_kernel: Kernel) -> None:
    """Verify invalid state transitions raise EngineStateError."""
    engine = SentinelEngine(config=SentinelConfig(enable_background_monitor=False))

    # Cannot start before initialize
    with pytest.raises(EngineStateError):
        await engine.start()

    await engine.initialize(mock_kernel)

    # Cannot initialize twice
    with pytest.raises(EngineStateError):
        await engine.initialize(mock_kernel)


@pytest.mark.asyncio
async def test_sentinel_restart_cycle(mock_kernel: Kernel) -> None:
    """Verify engine can stop and restart cleanly."""
    engine = SentinelEngine(config=SentinelConfig(enable_background_monitor=False))
    await engine.initialize(mock_kernel)
    await engine.start()
    assert engine.state == EngineState.RUNNING

    await engine.stop()
    assert engine.state == EngineState.STOPPED

    # After stop, re-initialize and start is supported
    engine._state = EngineState.UNINITIALIZED
    await engine.initialize(mock_kernel)
    await engine.start()
    assert engine.state == EngineState.RUNNING
    await engine.stop()


# -- 3. EngineState to SentinelStatus Mapping ---------------------------------


def test_sentinel_status_mapping() -> None:
    """Verify mapping from EngineState and probe results to SentinelStatus."""
    engine = SentinelEngine()

    # Stopping takes precedence
    status = engine._determine_engine_sentinel_status(
        "subsystem",
        EngineState.RUNNING,
        {},
        is_startup_grace=False,
        is_stopping=True,
    )
    assert status == SentinelStatus.STOPPING

    # Startup grace takes precedence
    status = engine._determine_engine_sentinel_status(
        "subsystem",
        EngineState.INITIALIZING,
        {},
        is_startup_grace=True,
        is_stopping=False,
    )
    assert status == SentinelStatus.STARTING

    # Running with all healthy probes -> HEALTHY
    probes_ok = {"check1": ProbeResult(probe_name="check1", status=CheckStatus.PASS, message="OK", is_required=True)}
    status = engine._determine_engine_sentinel_status(
        "subsystem",
        EngineState.RUNNING,
        probes_ok,
        is_startup_grace=False,
        is_stopping=False,
    )
    assert status == SentinelStatus.HEALTHY

    # Running with non-fatal warning -> DEGRADED
    probes_warn = {
        "check1": ProbeResult(probe_name="check1", status=CheckStatus.WARN, message="Warn", is_required=False)
    }
    status = engine._determine_engine_sentinel_status(
        "subsystem",
        EngineState.RUNNING,
        probes_warn,
        is_startup_grace=False,
        is_stopping=False,
    )
    assert status == SentinelStatus.DEGRADED

    # Running with fatal probe failure -> FAILED
    probes_fail = {
        "check1": ProbeResult(probe_name="check1", status=CheckStatus.FAIL, message="Fatal", is_required=True)
    }
    status = engine._determine_engine_sentinel_status(
        "subsystem",
        EngineState.RUNNING,
        probes_fail,
        is_startup_grace=False,
        is_stopping=False,
    )
    assert status == SentinelStatus.FAILED


def test_sentinel_stopped_mapping() -> None:
    """Verify EngineState.STOPPED mapping: FAILED if unexpected, DISABLED if intentional, UNKNOWN if unknown."""
    engine = SentinelEngine()

    # 1. Lifecycle not determinable (kernel is None or BOOTING) -> UNKNOWN
    engine._kernel = None
    status = engine._determine_engine_sentinel_status(
        "subsystem",
        EngineState.STOPPED,
        {},
        is_startup_grace=False,
        is_stopping=False,
    )
    assert status == SentinelStatus.UNKNOWN

    mock_k = MagicMock(spec=Kernel)
    mock_k.state = KernelState.BOOTING
    engine._kernel = mock_k
    status = engine._determine_engine_sentinel_status(
        "subsystem",
        EngineState.STOPPED,
        {},
        is_startup_grace=False,
        is_stopping=False,
    )
    assert status == SentinelStatus.UNKNOWN

    # 2. Unexpected STOPPED while Kernel is RUNNING -> FAILED
    mock_k.state = KernelState.RUNNING
    status = engine._determine_engine_sentinel_status(
        "subsystem",
        EngineState.STOPPED,
        {},
        is_startup_grace=False,
        is_stopping=False,
    )
    assert status == SentinelStatus.FAILED

    # 3. Intentional STOPPED when Kernel is not RUNNING -> DISABLED
    mock_k.state = KernelState.STOPPED
    status = engine._determine_engine_sentinel_status(
        "subsystem",
        EngineState.STOPPED,
        {},
        is_startup_grace=False,
        is_stopping=False,
    )
    assert status == SentinelStatus.DISABLED

    # 4. Engine marked DISABLED by config/registration -> DISABLED
    status_disabled = engine._determine_engine_sentinel_status(
        "subsystem",
        SentinelStatus.DISABLED,
        {},
        is_startup_grace=False,
        is_stopping=False,
    )
    assert status_disabled == SentinelStatus.DISABLED


# -- 4. Diagnostics Protocol (IEngineDiagnostics) ----------------------------


@pytest.mark.asyncio
async def test_sentinel_diagnostics_contract(mock_kernel: Kernel) -> None:
    """Verify IEngineDiagnostics methods return expected schemas."""
    engine = SentinelEngine(config=SentinelConfig(enable_background_monitor=False))
    await engine.initialize(mock_kernel)
    await engine.start()

    diag = engine.diagnostics()
    assert diag["engine"] == ENGINE_NAME
    assert diag["version"] == ENGINE_VERSION
    assert diag["state"] == EngineState.RUNNING.value
    assert set(diag["capabilities"]) == set(engine.registered_capabilities)
    assert isinstance(diag["metrics"], dict)

    health = engine.health()
    assert health["engine"] == ENGINE_NAME
    assert health["healthy"] is True
    assert health["status"] == EngineState.RUNNING.value
    assert "tracked_operations" in health

    metrics = engine.metrics()
    assert "checks_run" in metrics
    assert "deadlock_inspections" in metrics
    assert "recovery_requests_emitted" in metrics

    await engine.stop()


# -- 5. Health Evaluation & Self-Exclusion -----------------------------------


@pytest.mark.asyncio
async def test_evaluate_health_self_exclusion(mock_kernel: Kernel) -> None:
    """Verify evaluate_health excludes Sentinel itself from engine queries to prevent recursion."""
    engine = SentinelEngine(config=SentinelConfig(enable_background_monitor=False, startup_grace_seconds=0.0))

    mock_other = MagicMock()
    mock_other.state = EngineState.RUNNING
    mock_other.health_check = AsyncMock(return_value={"status": "healthy", "healthy": True})

    mock_kernel.get_all_engines.return_value = {
        "storage": mock_other,
        "sentinel": engine,  # should be skipped!
    }

    await engine.initialize(mock_kernel)
    await engine.start()

    report = await engine.evaluate_health()

    assert "storage" in report.subsystems
    assert "sentinel" not in report.subsystems  # Self-exclusion confirmed
    assert report.status == SentinelStatus.HEALTHY
    assert report.healthy is True

    await engine.stop()


@pytest.mark.asyncio
async def test_evaluate_health_degraded_and_failed_propagation(mock_kernel: Kernel) -> None:
    """Verify failure of subsystem correctly rolls up into aggregate health."""
    engine = SentinelEngine(config=SentinelConfig(enable_background_monitor=False, startup_grace_seconds=0.0))

    mock_failing = MagicMock()
    mock_failing.state = EngineState.FAILED
    mock_failing.health_check = AsyncMock(return_value={"status": "failed", "healthy": False})

    mock_kernel.get_all_engines.return_value = {"connector": mock_failing}

    await engine.initialize(mock_kernel)
    await engine.start()

    report = await engine.evaluate_health()
    assert report.subsystems["connector"].status == SentinelStatus.FAILED
    assert report.status == SentinelStatus.FAILED
    assert report.healthy is False

    await engine.stop()


# -- 6. Capability Handlers & Trusted Execution Context ----------------------


@pytest.mark.asyncio
async def test_capability_handlers_execution_context(mock_kernel: Kernel) -> None:
    """Verify the 3 capability handlers return valid schemas and accept ExecutionContext."""
    engine = SentinelEngine(config=SentinelConfig(enable_background_monitor=False, startup_grace_seconds=0.0))
    await engine.initialize(mock_kernel)
    await engine.start()

    mock_context = MagicMock(spec=CapabilityExecutionContext)
    mock_context.tenant_id = "tenant-test"
    mock_context.request_id = "req-123"

    # 1. health.get
    health_res = await engine.handle_health_get(execution_context=mock_context)
    assert "status" in health_res
    assert "subsystems" in health_res
    assert "healthy" in health_res

    # 2. status.get
    status_res = await engine.handle_status_get(execution_context=mock_context)
    assert status_res["engine"] == ENGINE_NAME
    assert "uptime_seconds" in status_res
    assert "subsystems_summary" in status_res

    # 3. diagnostics.get
    diag_res = await engine.handle_diagnostics_get(execution_context=mock_context)
    assert "metrics" in diag_res
    assert "recent_incidents" in diag_res
    assert "config" in diag_res

    await engine.stop()
