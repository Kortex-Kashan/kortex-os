"""KORTEX Monitoring Engine implementation.

Fulfills Phase 7 Production Hardening requirements:
- Inherits BaseEngine and IEngineDiagnostics
- In-memory only telemetry (no DB migrations, no permanent storage)
- Strict cardinality limits and collision-safe series keys
- Approximate linear-rank histogram percentiles
- stdlib-only system telemetry with graceful platform degradation
- Bounded time-series buffer (360 points at 10s intervals = 60 minutes)
- Decoupled Sentinel integration (public event cache and IEngineDiagnostics contract)
- Operational threshold evaluation (2 cycles, 10% hysteresis, 60s cooldown)
- Exactly 4 authenticated, execution-context aware capabilities
- Direct dashboard composition without nested dispatcher invocation
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.monitoring.collector import MetricsCollector
from kortex.engines.monitoring.constants import (
    CAPABILITY_MONITORING_DASHBOARD_GET,
    CAPABILITY_MONITORING_DIAGNOSTICS_GET,
    CAPABILITY_MONITORING_METRICS_GET,
    CAPABILITY_MONITORING_TIMESERIES_GET,
    DEFAULT_COLLECT_INTERVAL_SECONDS,
    DEFAULT_RETENTION_POINTS,
    EVENT_SENTINEL_HEALTH_CHANGED,
    MAX_ACTIVE_SERIES,
    MAX_METRIC_NAMES,
    MONITORING_ENGINE_NAME,
    MONITORING_SECURITY_CLASSIFICATION,
    PER_ENGINE_TIMEOUT_SECONDS,
    PERMISSION_MONITORING_READ,
)
from kortex.engines.monitoring.diagnostics import MonitoringDiagnostics
from kortex.engines.monitoring.events import MonitoringEventPublisher
from kortex.engines.monitoring.models import (
    AlertRecord,
    DashboardData,
    MetricValue,
    MonitoringConfig,
    TimeSeriesQueryResponse,
)
from kortex.engines.monitoring.registry import MetricRegistry
from kortex.engines.monitoring.thresholds import ThresholdEvaluator
from kortex.engines.monitoring.timeseries import TimeSeriesBuffer
from kortex.engines.storage.interfaces import IEngineDiagnostics

if TYPE_CHECKING:
    from kortex.core.dispatch import CapabilityExecutionContext
    from kortex.core.kernel import Kernel
    from kortex.engines.event.engine import Event

logger = logging.getLogger("kortex.engines.monitoring")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class MonitoringEngine(BaseEngine, IEngineDiagnostics):
    """Production Monitoring Engine for operational telemetry, metrics, and alerting."""

    def __init__(self, config: MonitoringConfig | None = None) -> None:
        super().__init__()
        self._config = config or MonitoringConfig()
        self._kernel: Kernel | None = None
        self._sentinel_sub_id: str | None = None
        self._started_at_monotonic: float | None = None

        # Core in-memory telemetry subsystems
        self.registry = MetricRegistry(
            max_metric_names=MAX_METRIC_NAMES,
            max_active_series=MAX_ACTIVE_SERIES,
        )
        self.timeseries_buffer = TimeSeriesBuffer(
            max_points=self._config.buffer_max_points or DEFAULT_RETENTION_POINTS,
            max_series=MAX_ACTIVE_SERIES,
        )
        self.event_publisher = MonitoringEventPublisher()
        self.threshold_evaluator = ThresholdEvaluator(event_publisher=self.event_publisher)
        self.collector = MetricsCollector(
            registry=self.registry,
            timeseries_buffer=self.timeseries_buffer,
            collect_interval_seconds=self._config.collect_interval_seconds or DEFAULT_COLLECT_INTERVAL_SECONDS,
            probe_timeout_seconds=self._config.probe_timeout_seconds or PER_ENGINE_TIMEOUT_SECONDS,
        )
        self._diagnostics = MonitoringDiagnostics(self)

        # Operational state
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._cached_sentinel_health: dict[str, Any] = {}
        self._active_alerts: list[AlertRecord] = []

    @property
    def name(self) -> str:
        return MONITORING_ENGINE_NAME

    @property
    def dependencies(self) -> list[str]:
        # Ordering dependency: event and security should initialize before or with monitoring
        return []

    @property
    def background_tasks(self) -> frozenset[asyncio.Task[Any]]:
        return frozenset(self._background_tasks)

    @property
    def active_alerts(self) -> list[AlertRecord]:
        return list(self._active_alerts)

    # -- Lifecycle (BaseEngine) ----------------------------------------------

    async def initialize(self, kernel: Kernel | None = None) -> None:
        """Initialize resources and register capabilities with Kernel."""
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)

        try:
            self._kernel = kernel
            if kernel is not None:
                self.event_publisher.set_kernel(kernel)

                # Register in IoC container if available
                container = getattr(kernel, "container", None)
                if (
                    container is not None
                    and hasattr(container, "register_instance")
                    and hasattr(container, "has")
                    and not container.has("engine.monitoring")
                ):
                    container.register_instance("engine.monitoring", self)

                # Register the 4 approved capabilities
                if hasattr(kernel, "register_capability"):
                    kernel.register_capability(
                        name=CAPABILITY_MONITORING_METRICS_GET,
                        description="Retrieve real-time metrics across KORTEX subsystems.",
                        provider=self.name,
                        handler=self.handle_metrics_get,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_MONITORING_READ],
                        security_classification=MONITORING_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_MONITORING_TIMESERIES_GET,
                        description="Retrieve bounded historical time-series points for a metric.",
                        provider=self.name,
                        handler=self.handle_timeseries_get,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_MONITORING_READ],
                        security_classification=MONITORING_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_MONITORING_DASHBOARD_GET,
                        description="Retrieve consolidated operational dashboard state.",
                        provider=self.name,
                        handler=self.handle_dashboard_get,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_MONITORING_READ],
                        security_classification=MONITORING_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_MONITORING_DIAGNOSTICS_GET,
                        description="Retrieve technical self-diagnostics for the Monitoring Engine.",
                        provider=self.name,
                        handler=self.handle_diagnostics_get,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_MONITORING_READ],
                        security_classification=MONITORING_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )

                # Subscribe to public Sentinel health events
                if hasattr(kernel, "subscribe_event"):
                    try:
                        self._sentinel_sub_id = kernel.subscribe_event(
                            topic=EVENT_SENTINEL_HEALTH_CHANGED,
                            handler=self._on_sentinel_health_changed,
                            subscriber_name=self.name,
                        )
                    except Exception as exc:
                        logger.warning("Could not subscribe to Sentinel health events: %s", exc)

            self._set_state(EngineState.READY)
            logger.info("Monitoring Engine initialized successfully.")
        except Exception as exc:
            self._set_state(EngineState.FAILED)
            logger.error("Failed to initialize Monitoring Engine: %s", exc)
            raise

    async def start(self) -> None:
        """Start active background collection loop."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self._started_at_monotonic = time.monotonic()

        task = asyncio.create_task(self._monitor_loop(), name="kortex_monitoring_loop")
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)

        logger.info("Monitoring Engine started cleanly.")

    async def stop(self) -> None:
        """Gracefully stop background loop and release resources."""
        self.ensure_state(EngineState.RUNNING, EngineState.READY)
        self._set_state(EngineState.STOPPING)

        # Unsubscribe event
        if self._kernel is not None and self._sentinel_sub_id:
            try:
                self._kernel.unsubscribe_event(self._sentinel_sub_id)
            except Exception as exc:
                logger.debug("Error unsubscribing Sentinel health event: %s", exc)
            self._sentinel_sub_id = None

        # Cancel background tasks
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()

        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        self._set_state(EngineState.STOPPED)
        logger.info("Monitoring Engine stopped cleanly.")

    def _on_background_task_done(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Background task '%s' raised exception: %s", task.get_name(), task.exception())

    # -- Background Monitor Loop ---------------------------------------------

    async def _monitor_loop(self) -> None:
        """Periodic collection and threshold evaluation loop."""
        interval = self._config.collect_interval_seconds
        while self.state == EngineState.RUNNING:
            try:
                await self.collect_now()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Error in monitoring collection loop: %s", exc)

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def collect_now(self) -> dict[str, Any]:
        """Trigger an immediate collection cycle and threshold evaluation."""
        result = await self.collector.collect_cycle(self._kernel)
        # Evaluate operational thresholds
        all_metrics = self.registry.get_all_metrics()
        self._active_alerts = await self.threshold_evaluator.evaluate_metrics(all_metrics)
        return result

    # -- Sentinel Integration ------------------------------------------------

    async def _on_sentinel_health_changed(self, event: Event) -> None:
        """Event handler for public kortex.sentinel.health.changed."""
        payload = getattr(event, "payload", {})
        if isinstance(payload, dict):
            self._cached_sentinel_health = payload

    async def _resolve_sentinel_health(self) -> dict[str, Any]:
        """Resolve Sentinel health via cached event or on-demand IEngineDiagnostics."""
        if self._cached_sentinel_health:
            return dict(self._cached_sentinel_health)

        if self._kernel is not None:
            sentinel = self._kernel.get_engine("sentinel")
            if sentinel is not None and hasattr(sentinel, "health"):
                try:
                    h = sentinel.health()
                    if asyncio.iscoroutine(h):
                        h = await h
                    if isinstance(h, dict):
                        return h
                except Exception as exc:
                    logger.debug("Failed on-demand Sentinel health query: %s", exc)

        return {"status": "UNKNOWN", "healthy": None, "subsystems": {}}

    # -- IEngineDiagnostics --------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Return diagnostic health information (BaseEngine contract)."""
        return self.health()

    def health(self) -> dict[str, Any]:
        """Return operational health status (IEngineDiagnostics protocol)."""
        return self._diagnostics.health()

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and telemetry counters."""
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

    # -- Direct Query Methods ------------------------------------------------

    async def get_metrics(
        self,
        subsystem: str | None = None,
        metric_names: list[str] | None = None,
    ) -> list[MetricValue]:
        """Query real-time metric snapshots."""
        all_snapshots = self.registry.get_all_metrics(subsystem=subsystem)
        if metric_names is not None:
            name_set = set(metric_names)
            return [m for m in all_snapshots if m.name in name_set]
        return all_snapshots

    async def get_timeseries(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
        duration_seconds: int = 3600,
    ) -> TimeSeriesQueryResponse:
        """Query historical time-series points."""
        return self.timeseries_buffer.query(
            metric_name=metric_name,
            labels=labels,
            duration_seconds=duration_seconds,
        )

    async def get_dashboard(self) -> DashboardData:
        """Direct composition of operational dashboard state without nested capability dispatch."""
        sentinel_health = await self._resolve_sentinel_health()
        sys_telemetry = (
            self.collector.last_system_telemetry or await self.collector.system_collector.collect_system_telemetry()
        )
        all_metrics = self.registry.get_all_metrics()

        # Select top operational metrics for dashboard overview
        top_metric_snapshots: list[dict[str, Any]] = []
        for m in all_metrics:
            if m.name.startswith("system.") or "total" in m.name or "count" in m.name:
                top_metric_snapshots.append(m.model_dump(mode="json"))

        active_alerts_dump = [a.model_dump(mode="json") for a in self._active_alerts]

        return DashboardData(
            timestamp=_utc_now_iso(),
            sentinel_health=sentinel_health,
            system_resources=sys_telemetry,
            top_metrics=top_metric_snapshots[:50],  # Bounded to top 50
            active_alerts=active_alerts_dump,
            engines_monitored=self.collector.last_engines_polled,
        )

    # -- Capability Handlers -------------------------------------------------

    async def handle_metrics_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        subsystem: str | None = None,
        metric_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Capability handler for kortex.monitoring.metrics.get."""
        metrics = await self.get_metrics(subsystem=subsystem, metric_names=metric_names)
        return [m.model_dump(mode="json") for m in metrics]

    async def handle_timeseries_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        metric_name: str = "",
        labels: dict[str, str] | None = None,
        duration_seconds: int = 3600,
    ) -> dict[str, Any]:
        """Capability handler for kortex.monitoring.timeseries.get."""
        resp = await self.get_timeseries(
            metric_name=metric_name,
            labels=labels,
            duration_seconds=duration_seconds,
        )
        return resp.model_dump(mode="json")

    async def handle_dashboard_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
    ) -> dict[str, Any]:
        """Capability handler for kortex.monitoring.dashboard.get."""
        dashboard = await self.get_dashboard()
        return dashboard.model_dump(mode="json")

    async def handle_diagnostics_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
    ) -> dict[str, Any]:
        """Capability handler for kortex.monitoring.diagnostics.get."""
        return self.diagnostics()
