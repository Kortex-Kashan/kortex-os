"""KORTEX Sentinel Engine.

Production-Hardening engine responsible for:
- System-wide aggregate health evaluation layered over EngineState
- Architectural invariant integrity verification
- Non-invasive deadlock suspicion and event-loop starvation detection
- Explicit heartbeat & watchdog tracking
- Deterministic failure classification and crash-loop circuit breaking
- Event-driven recovery handoff (request emission without recovery execution)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.container import Container
from kortex.core.kernel import KernelState
from kortex.engines.sentinel.constants import (
    CAPABILITY_DIAGNOSTICS_GET,
    CAPABILITY_HEALTH_GET,
    CAPABILITY_STATUS_GET,
    ENGINE_NAME,
    SENTINEL_CAPABILITIES,
    SENTINEL_PERMISSION_READ,
    SENTINEL_SECURITY_CLASSIFICATION,
)
from kortex.engines.sentinel.deadlock import DeadlockDetector, OperationTracker
from kortex.engines.sentinel.diagnostics import SentinelDiagnostics
from kortex.engines.sentinel.events import SentinelEventPublisher
from kortex.engines.sentinel.heartbeats import HeartbeatManager
from kortex.engines.sentinel.incident import IncidentStore
from kortex.engines.sentinel.integrity import IntegrityVerifier
from kortex.engines.sentinel.interfaces import IHeartbeatSource
from kortex.engines.sentinel.models import (
    CheckStatus,
    DeadlockReport,
    FailureClass,
    IntegrityReport,
    ProbeResult,
    SentinelConfig,
    SentinelDiagnosticsReport,
    SentinelHealthReport,
    SentinelStatus,
    SentinelStatusReport,
    SubsystemHealth,
)
from kortex.engines.storage.interfaces import IEngineDiagnostics

if TYPE_CHECKING:
    from kortex.core.dispatch import CapabilityExecutionContext
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engine.sentinel")


class SentinelEngine(BaseEngine, IEngineDiagnostics):
    """Production Sentinel Engine for system health, integrity, and liveness observation."""

    def __init__(self, config: SentinelConfig | None = None) -> None:
        super().__init__()
        self._config = config or SentinelConfig()
        self._kernel: Kernel | None = None

        self._verifier = IntegrityVerifier(probe_timeout_seconds=self._config.probe_timeout_seconds)
        self._detector = DeadlockDetector(
            loop_lag_threshold_ms=self._config.loop_lag_threshold_ms,
            operation_timeout_threshold_seconds=self._config.operation_timeout_seconds,
        )
        self._heartbeat_manager = HeartbeatManager(
            warning_multiplier=self._config.heartbeat_warning_multiplier,
            failure_multiplier=self._config.heartbeat_failure_multiplier,
        )
        self._incident_store = IncidentStore(
            max_size=self._config.ring_buffer_size,
            crash_loop_threshold=self._config.crash_loop_threshold,
            crash_loop_window_seconds=self._config.crash_loop_window_seconds,
            recovery_cooldown_seconds=self._config.recovery_cooldown_seconds,
        )
        self._event_publisher = SentinelEventPublisher()
        self._diagnostics = SentinelDiagnostics(self)

        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._started_at_monotonic: float | None = None

        # Cached evaluation reports
        self._last_health_report: SentinelHealthReport | None = None
        self._last_integrity_report: IntegrityReport | None = None
        self._last_deadlock_report: DeadlockReport | None = None

        # Consecutive failure counters per subsystem
        self._subsystem_consecutive_failures: dict[str, int] = {}
        # Prior evaluated health status per subsystem
        self._subsystem_previous_status: dict[str, SentinelStatus] = {}

    @property
    def name(self) -> str:
        """Unique identifier name for this engine."""
        return ENGINE_NAME

    @property
    def dependencies(self) -> list[str]:
        """Declared dependencies for topological sort during boot.

        Sentinel dynamically resolves Kernel and engine services, requiring no hard
        boot dependencies during early initialization.
        """
        return []

    @property
    def config(self) -> SentinelConfig:
        """Configuration settings for Sentinel."""
        return self._config

    @property
    def heartbeat_manager(self) -> HeartbeatManager:
        """Access internal HeartbeatManager."""
        return self._heartbeat_manager

    @property
    def deadlock_detector(self) -> DeadlockDetector:
        """Access internal DeadlockDetector."""
        return self._detector

    @property
    def operation_tracker(self) -> OperationTracker:
        """Access internal OperationTracker."""
        return self._detector.tracker

    @property
    def incident_store(self) -> IncidentStore:
        """Access internal IncidentStore."""
        return self._incident_store

    @property
    def event_publisher(self) -> SentinelEventPublisher:
        """Access internal SentinelEventPublisher."""
        return self._event_publisher

    @property
    def background_tasks(self) -> frozenset[asyncio.Task[Any]]:
        """Active background tasks owned by Sentinel."""
        return frozenset(self._background_tasks)

    @property
    def last_health_report(self) -> SentinelHealthReport | None:
        """Most recent aggregate Sentinel health report."""
        return self._last_health_report

    @property
    def last_integrity_report(self) -> IntegrityReport | None:
        """Most recent integrity verification report."""
        return self._last_integrity_report

    @property
    def last_deadlock_report(self) -> DeadlockReport | None:
        """Most recent deadlock / lag inspection report."""
        return self._last_deadlock_report

    @property
    def registered_capabilities(self) -> tuple[str, ...]:
        """List of canonical capabilities registered by Sentinel."""
        return SENTINEL_CAPABILITIES

    # -- Lifecycle Implementation (BaseEngine) -------------------------------

    async def initialize(self, kernel: Kernel | None = None) -> None:
        """Initialize Sentinel resources and register capabilities with Kernel."""
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)

        try:
            self._kernel = kernel
            if kernel is not None:
                self._event_publisher.set_kernel(kernel)

                # Register Sentinel instance in Kernel IoC container if available
                container = getattr(kernel, "container", None)
                if isinstance(container, Container) and not container.has("engine.sentinel"):
                    container.register_instance("engine.sentinel", self)

                # Register the three canonical read capabilities
                if hasattr(kernel, "register_capability"):
                    kernel.register_capability(
                        name=CAPABILITY_HEALTH_GET,
                        description="Retrieve aggregate system health report across KORTEX subsystems.",
                        provider=self.name,
                        handler=self.handle_health_get,
                        requires_authentication=True,
                        required_permissions=[SENTINEL_PERMISSION_READ],
                        security_classification=SENTINEL_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_STATUS_GET,
                        description="Retrieve high-level operational status and component summary.",
                        provider=self.name,
                        handler=self.handle_status_get,
                        requires_authentication=True,
                        required_permissions=[SENTINEL_PERMISSION_READ],
                        security_classification=SENTINEL_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_DIAGNOSTICS_GET,
                        description="Retrieve detailed technical diagnostics and incident history.",
                        provider=self.name,
                        handler=self.handle_diagnostics_get,
                        requires_authentication=True,
                        required_permissions=[SENTINEL_PERMISSION_READ],
                        security_classification=SENTINEL_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )

            self._set_state(EngineState.READY)
            logger.info("Sentinel Engine initialized successfully.")
        except Exception as exc:
            self._set_state(EngineState.FAILED)
            logger.error("Sentinel Engine initialization failed: %s", exc, exc_info=True)
            raise

    async def start(self) -> None:
        """Start Sentinel operational monitoring and background task loop."""
        self.ensure_state(EngineState.READY)
        self._started_at_monotonic = time.monotonic()
        self._set_state(EngineState.RUNNING)

        if self._config.enabled and self._config.enable_background_monitor:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._monitor_loop(), name="sentinel_background_monitor")
                self._background_tasks.add(task)
                task.add_done_callback(self._on_background_task_done)
            except RuntimeError:
                logger.debug("No active event loop to schedule background monitor.")

        logger.info("Sentinel Engine started.")

    async def stop(self) -> None:
        """Gracefully shut down active background tasks and release resources."""
        self.ensure_state(EngineState.RUNNING, EngineState.READY)
        self._set_state(EngineState.STOPPING)

        tasks_to_cancel = [task for task in self._background_tasks if not task.done()]
        for task in tasks_to_cancel:
            task.cancel()

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        self._background_tasks.clear()

        self._set_state(EngineState.STOPPED)
        logger.info("Sentinel Engine stopped cleanly.")

    # -- Common Diagnostics Interface (IEngineDiagnostics) -------------------

    async def health_check(self) -> dict[str, Any]:
        """Return diagnostic health information (BaseEngine contract)."""
        return self.health()

    def health(self) -> dict[str, Any]:
        """Return operational health status (IEngineDiagnostics protocol)."""
        return self._diagnostics.health()

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and monitoring metrics."""
        return self._diagnostics.metrics()

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics."""
        return self._diagnostics.diagnostics()

    def status(self) -> str:
        """Return current engine state name string."""
        return self._diagnostics.status()

    def version(self) -> str:
        """Return semantic version string of the engine."""
        return self._diagnostics.version()

    def capabilities(self) -> list[str]:
        """Return list of capability strings registered by the engine."""
        return self._diagnostics.capabilities()

    # -- Heartbeat Source Public APIs ----------------------------------------

    def register_heartbeat_source(
        self,
        source: str | IHeartbeatSource,
        expected_interval_seconds: float | None = None,
        owner: str = "system",
        replace: bool = False,
    ) -> None:
        """Register an active heartbeat source."""
        self._heartbeat_manager.register_source(
            source=source,
            expected_interval_seconds=expected_interval_seconds,
            owner=owner,
            replace=replace,
        )

    def unregister_heartbeat_source(self, source_id: str) -> bool:
        """Unregister an active heartbeat source."""
        return self._heartbeat_manager.unregister_source(source_id)

    def record_heartbeat(self, source_id: str) -> bool:
        """Record a heartbeat ping from an active source."""
        return self._heartbeat_manager.record_heartbeat(source_id)

    # -- Health Evaluation Logic ---------------------------------------------

    def _determine_engine_sentinel_status(
        self,
        engine_name: str,
        engine_state: EngineState | None,
        probe_reports: dict[str, ProbeResult],
        is_startup_grace: bool,
        is_stopping: bool,
    ) -> SentinelStatus:
        """Map EngineState and probe results into a deterministic SentinelStatus."""
        if is_stopping:
            return SentinelStatus.STOPPING

        if engine_state == EngineState.STOPPING:
            return SentinelStatus.STOPPING

        if engine_state is None:
            return SentinelStatus.UNKNOWN

        if engine_state in ("DISABLED", SentinelStatus.DISABLED):
            return SentinelStatus.DISABLED

        if engine_state in (EngineState.INITIALIZING, "STARTING") or is_startup_grace:
            return SentinelStatus.STARTING

        if engine_state == EngineState.UNINITIALIZED:
            return SentinelStatus.STARTING if is_startup_grace else SentinelStatus.UNKNOWN

        if engine_state == EngineState.STOPPED:
            # STOPPED mapping per specification:
            # 1. if startup/shutdown lifecycle is not yet determinable -> UNKNOWN
            if self._kernel is None or self._kernel.state in (KernelState.CREATED, KernelState.BOOTING):
                return SentinelStatus.UNKNOWN
            # 2. if unexpectedly stopped while expected to be running -> FAILED
            if self._kernel.state == KernelState.RUNNING:
                return SentinelStatus.FAILED
            # 3. if intentionally stopped/disabled -> DISABLED
            return SentinelStatus.DISABLED

        if engine_state == EngineState.FAILED:
            return SentinelStatus.FAILED

        # For running or ready engines, evaluate probe results
        has_failed_probe = False
        has_warned_probe = False

        for probe in probe_reports.values():
            if probe.status == CheckStatus.FAIL:
                if probe.is_required:
                    has_failed_probe = True
                else:
                    has_warned_probe = True
            elif probe.status == CheckStatus.WARN:
                has_warned_probe = True

        if has_failed_probe:
            return SentinelStatus.FAILED
        if has_warned_probe:
            return SentinelStatus.DEGRADED
        if engine_state in (EngineState.READY, EngineState.RUNNING):
            return SentinelStatus.HEALTHY

        return SentinelStatus.UNKNOWN

    async def evaluate_health(self) -> SentinelHealthReport:
        """Evaluate aggregate health across KORTEX subsystems and architectural invariants.

        Explicitly excludes Sentinel itself from the queried subsystems to avoid
        circular health evaluation. Emits state transition events on health changes.
        """
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        self._diagnostics.record_check_run()

        now_mono = time.monotonic()

        # Determine startup / shutdown immunity states
        uptime = (now_mono - self._started_at_monotonic) if self._started_at_monotonic else 0.0
        is_startup_grace = uptime < self._config.startup_grace_seconds

        kernel_state = self._kernel.state if self._kernel is not None else KernelState.STOPPED
        is_stopping = kernel_state in (KernelState.SHUTTING_DOWN, KernelState.STOPPED)

        subsystems: dict[str, SubsystemHealth] = {}
        degraded_subsystems: list[str] = []
        failed_subsystems: list[str] = []

        # 1. Query registered engines
        if self._kernel is not None:
            registered_engines = self._kernel.get_all_engines()
            for name, engine in registered_engines.items():
                if name == self.name:
                    # Self-exclusion prevents circular recursion
                    continue

                probes: dict[str, ProbeResult] = {}
                try:
                    # Wrapped in strict timeout to prevent slow engines from blocking Sentinel
                    report = await asyncio.wait_for(
                        engine.health_check(),
                        timeout=self._config.probe_timeout_seconds,
                    )
                    is_h = (
                        report.get("healthy", True)
                        if "healthy" in report
                        else str(report.get("status", "")).lower() == "healthy"
                    )
                    probe_status = CheckStatus.PASS if is_h else CheckStatus.WARN
                    if report.get("status") == EngineState.FAILED.value:
                        probe_status = CheckStatus.FAIL

                    probes["engine.health_check"] = ProbeResult(
                        probe_name=f"{name}.health_check",
                        status=probe_status,
                        message=f"Subsystem '{name}' reported status {report.get('status')}",
                        details=report,
                        is_required=True,
                    )
                except TimeoutError:
                    probes["engine.health_check"] = ProbeResult(
                        probe_name=f"{name}.health_check",
                        status=CheckStatus.FAIL,
                        message=(
                            f"Subsystem '{name}' health check timed out after {self._config.probe_timeout_seconds}s"
                        ),
                        is_required=True,
                    )
                except Exception as exc:
                    logger.warning("Subsystem '%s' health check error: %s", name, exc)
                    probes["engine.health_check"] = ProbeResult(
                        probe_name=f"{name}.health_check",
                        status=CheckStatus.FAIL,
                        message=f"Subsystem '{name}' health check raised: {exc}",
                        details={"error": str(exc)},
                        is_required=True,
                    )

                e_state = getattr(engine, "state", None)
                sub_status = self._determine_engine_sentinel_status(
                    engine_name=name,
                    engine_state=e_state,
                    probe_reports=probes,
                    is_startup_grace=is_startup_grace,
                    is_stopping=is_stopping,
                )

                subsystem_health = SubsystemHealth(
                    name=name,
                    status=sub_status,
                    engine_state=e_state.value if e_state else None,
                    probes=probes,
                    details={"is_startup_grace": is_startup_grace},
                )
                subsystems[name] = subsystem_health

                if sub_status == SentinelStatus.FAILED:
                    failed_subsystems.append(name)
                elif sub_status == SentinelStatus.DEGRADED:
                    degraded_subsystems.append(name)

                # Process subsystem status transitions & recovery requests
                await self._process_subsystem_transition(name, sub_status, subsystem_health)

        # 2. Evaluate Heartbeat Watchdogs
        heartbeat_probes = self._heartbeat_manager.evaluate_all(
            now_monotonic=now_mono,
            is_starting=is_startup_grace,
            is_stopping=is_stopping,
        )
        for hp in heartbeat_probes:
            if hp.status == CheckStatus.FAIL or hp.status == CheckStatus.WARN:
                degraded_subsystems.append(f"heartbeat.{hp.details.get('source_id')}")

        # 3. Deadlock & Lag Probe
        deadlock_rep = await self._detector.inspect_deadlocks()
        self._last_deadlock_report = deadlock_rep
        self._diagnostics.record_deadlock_inspection(
            starvation=deadlock_rep.starvation_detected,
            deadlock=deadlock_rep.deadlock_suspected,
        )

        if deadlock_rep.deadlock_suspected:
            await self._event_publisher.emit_deadlock_detected(
                loop_lag_ms=deadlock_rep.loop_lag_ms,
                stalled_operations=[op.model_dump() for op in deadlock_rep.stalled_operations],
                active_operations_count=deadlock_rep.active_operations_count,
            )
            self._incident_store.record_incident(
                subsystem="runtime.event_loop",
                failure_class=FailureClass.DEADLOCK_SUSPECTED,
                health_status=SentinelStatus.DEGRADED,
                message=(
                    f"Deadlock suspected: loop lag {deadlock_rep.loop_lag_ms}ms "
                    f"with {len(deadlock_rep.stalled_operations)} stalled ops."
                ),
                details={"loop_lag_ms": deadlock_rep.loop_lag_ms},
            )
            degraded_subsystems.append("runtime.event_loop")
        elif deadlock_rep.starvation_detected:
            self._incident_store.record_incident(
                subsystem="runtime.event_loop",
                failure_class=FailureClass.EVENT_LOOP_STARVATION,
                health_status=SentinelStatus.DEGRADED,
                message=f"Event loop starvation: loop lag {deadlock_rep.loop_lag_ms}ms.",
                details={"loop_lag_ms": deadlock_rep.loop_lag_ms},
            )
            degraded_subsystems.append("runtime.event_loop")

        # 4. Database Liveness
        db_mgr = getattr(self._kernel, "db", None) or getattr(self._kernel, "db_manager", None)
        db_connected = getattr(db_mgr, "is_connected", False) if db_mgr is not None else True
        if not db_connected and not is_startup_grace and not is_stopping:
            failed_subsystems.append("database")

        # 5. Event Engine Availability
        event_engine_available = (
            getattr(self._kernel, "get_engine", lambda _: None)("event") is not None
            or getattr(self._kernel, "_event_engine", None) is not None
        )

        # 6. Determine Aggregate Overall Status
        if is_stopping:
            overall_status = SentinelStatus.STOPPING
        elif is_startup_grace and not failed_subsystems:
            overall_status = SentinelStatus.STARTING
        elif failed_subsystems:
            overall_status = SentinelStatus.FAILED
        elif degraded_subsystems:
            overall_status = SentinelStatus.DEGRADED
        else:
            overall_status = SentinelStatus.HEALTHY

        # 7. Check Aggregate Health Transition
        previous_overall = self._last_health_report.status if self._last_health_report else SentinelStatus.UNKNOWN
        if overall_status != previous_overall:
            await self._event_publisher.emit_health_changed(
                previous_status=previous_overall.value,
                current_status=overall_status.value,
                healthy=(overall_status == SentinelStatus.HEALTHY),
                degraded_subsystems=degraded_subsystems + failed_subsystems,
            )

        report_obj = SentinelHealthReport(
            status=overall_status,
            healthy=(overall_status == SentinelStatus.HEALTHY),
            kernel_state=kernel_state.value,
            subsystems=subsystems,
            database_connected=db_connected,
            event_engine_available=event_engine_available,
            loop_lag_ms=deadlock_rep.loop_lag_ms,
        )
        self._last_health_report = report_obj
        return report_obj

    async def _process_subsystem_transition(
        self,
        name: str,
        current_status: SentinelStatus,
        health: SubsystemHealth,
    ) -> None:
        """Process state changes for a subsystem, handle crash loops, and emit recovery requests."""
        prev_status = self._subsystem_previous_status.get(name, SentinelStatus.UNKNOWN)
        self._subsystem_previous_status[name] = current_status

        if current_status == SentinelStatus.FAILED:
            consecutive = self._subsystem_consecutive_failures.get(name, 0) + 1
            self._subsystem_consecutive_failures[name] = consecutive

            # Failure classification
            f_class = (
                FailureClass.TRANSIENT
                if consecutive == 1
                else FailureClass.REPEATED
                if consecutive == 2
                else FailureClass.PERSISTENT
            )

            # Record incident in bounded ring buffer
            self._incident_store.record_incident(
                subsystem=name,
                failure_class=f_class,
                health_status=current_status,
                message=f"Subsystem '{name}' entered FAILED state ({f_class.value}).",
                details={"consecutive_failures": consecutive},
            )

            # Emit subsystem.failed event
            await self._event_publisher.emit_subsystem_failed(
                subsystem_name=name,
                failure_class=f_class.value,
                health_status=current_status.value,
                error_message=f"Subsystem '{name}' reported failed.",
                consecutive_failures=consecutive,
            )

            # Check crash-loop detection
            if self._incident_store.should_emit_crash_loop_event(name):
                self._diagnostics.record_crash_loop()
                _, count = self._incident_store.check_crash_loop(name)
                await self._event_publisher.emit_crash_loop_detected(
                    subsystem_name=name,
                    failure_count=count,
                    window_seconds=self._config.crash_loop_window_seconds,
                )
                self._incident_store.record_incident(
                    subsystem=name,
                    failure_class=FailureClass.CRASH_LOOP,
                    health_status=SentinelStatus.FAILED,
                    message=(
                        f"Subsystem '{name}' crash-loop detected ({count} failures in window). Circuit breaker tripped."
                    ),
                )

            # Check recovery-request emission circuit breaker
            if consecutive >= 2 and self._incident_store.can_emit_recovery_request(name):
                # Formulate deterministic idempotency key
                epoch_window = int(time.time() // self._config.recovery_cooldown_seconds)
                idempotency_key = f"recovery:{name}:{consecutive}:{epoch_window}"

                self._incident_store.record_recovery_request_emitted(name)
                self._diagnostics.record_recovery_request()

                await self._event_publisher.emit_recovery_requested(
                    subsystem_name=name,
                    failure_class=f_class.value,
                    health_status=current_status.value,
                    idempotency_key=idempotency_key,
                    observed_evidence={"consecutive_failures": consecutive},
                    diagnostic_context={"probes": [p.model_dump() for p in health.probes.values()]},
                    suggested_action="RESTART_SUBSYSTEM",
                )

        elif current_status == SentinelStatus.HEALTHY and prev_status in (
            SentinelStatus.FAILED,
            SentinelStatus.DEGRADED,
        ):
            # Subsystem recovered!
            self._subsystem_consecutive_failures[name] = 0
            self._incident_store.reset_subsystem(name)

            await self._event_publisher.emit_subsystem_recovered(
                subsystem_name=name,
                previous_status=prev_status.value,
                current_status=current_status.value,
            )
            self._incident_store.record_incident(
                subsystem=name,
                failure_class="RECOVERY",
                health_status=current_status,
                message=f"Subsystem '{name}' successfully recovered to HEALTHY.",
            )

    async def verify_integrity(self, checks: list[str] | None = None) -> IntegrityReport:
        """Verify architectural invariants across Kernel, engine states, and dependencies."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        self._diagnostics.record_check_run()

        report = await self._verifier.verify(self._kernel, selected_checks=checks)
        self._last_integrity_report = report

        if report.overall_status != SentinelStatus.HEALTHY:
            self._diagnostics.record_integrity_failure()

        return report

    async def inspect_deadlocks(self, threshold_seconds: float | None = None) -> DeadlockReport:
        """Inspect event loop lag and detect stalled operations or suspected deadlocks."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        self._diagnostics.record_check_run()

        report = await self._detector.inspect_deadlocks(threshold_seconds=threshold_seconds)
        self._last_deadlock_report = report
        self._diagnostics.record_deadlock_inspection(
            starvation=report.starvation_detected,
            deadlock=report.deadlock_suspected,
        )
        return report

    # -- Capability Handlers -------------------------------------------------

    async def handle_health_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
    ) -> dict[str, Any]:
        """Capability handler for kortex.sentinel.health.get."""
        report = await self.evaluate_health()
        return report.model_dump(mode="json")

    async def handle_status_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
    ) -> dict[str, Any]:
        """Capability handler for kortex.sentinel.status.get."""
        last_rep = self._last_health_report or await self.evaluate_health()
        uptime = (time.monotonic() - self._started_at_monotonic) if self._started_at_monotonic else 0.0

        summary = {name: sub.status.value for name, sub in last_rep.subsystems.items()}

        status_report = SentinelStatusReport(
            status=last_rep.status,
            engine=self.name,
            version=self.version(),
            uptime_seconds=round(uptime, 2),
            active_tasks=len(self.background_tasks),
            tracked_operations=self.deadlock_detector.tracker.active_count,
            registered_heartbeats=self.heartbeat_manager.registered_count,
            subsystems_summary=summary,
        )
        return status_report.model_dump(mode="json")

    async def handle_diagnostics_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
    ) -> dict[str, Any]:
        """Capability handler for kortex.sentinel.diagnostics.get."""
        last_rep = self._last_health_report or await self.evaluate_health()
        recent_incidents = self._incident_store.get_recent_incidents(limit=20)

        diag_report = SentinelDiagnosticsReport(
            status=last_rep.status,
            version=self.version(),
            config=self.config.model_dump(),
            metrics=self.metrics(),
            recent_incidents=recent_incidents,
            deadlock_report=self._last_deadlock_report,
            integrity_report=self._last_integrity_report,
        )
        return diag_report.model_dump(mode="json")

    # -- Background Monitor Loop & Task Lifecycle ----------------------------

    def _on_background_task_done(self, task: asyncio.Task[Any]) -> None:
        """Handle completion of background tasks and clean up references."""
        self._background_tasks.discard(task)
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.warning("Unhandled exception in Sentinel background task: %s", type(exc).__name__)

    async def _monitor_loop(self) -> None:
        """Periodic background monitoring loop."""
        logger.debug("Sentinel background monitor loop started.")
        while self.state == EngineState.RUNNING:
            try:
                await self.evaluate_health()
                await self.verify_integrity()
                await self.inspect_deadlocks()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Error in Sentinel monitor loop cycle: %s", exc)

            try:
                await asyncio.sleep(self._config.monitor_interval_seconds)
            except asyncio.CancelledError:
                break
        logger.debug("Sentinel background monitor loop ended.")
