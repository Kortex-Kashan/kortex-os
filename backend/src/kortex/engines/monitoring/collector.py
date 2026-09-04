"""Telemetry and engine metrics collection worker for KORTEX Monitoring Engine.

Collects portable system resources (memory, CPU, threads, tasks, loop lag)
using the standard library only, and polls registered engines via
IEngineDiagnostics with strict timeouts and failure isolation.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

from kortex.engines.monitoring.constants import (
    DEFAULT_COLLECT_INTERVAL_SECONDS,
    MONITORING_ENGINE_NAME,
    PER_ENGINE_TIMEOUT_SECONDS,
)
from kortex.engines.monitoring.models import TimeSeriesPoint
from kortex.engines.monitoring.normalizer import DiagnosticsNormalizer
from kortex.engines.monitoring.registry import MetricRegistry
from kortex.engines.monitoring.timeseries import TimeSeriesBuffer

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.monitoring.collector")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class SystemTelemetryCollector:
    """Portable standard-library-first host and process resource collector."""

    def __init__(self) -> None:
        self._last_cpu_times: Any | None = None
        self._last_cpu_wall_time: float | None = None
        self._win32_initialized: bool = False
        self._win32_psapi: Any = None
        self._win32_kernel32: Any = None
        self._win32_pmc_cls: Any = None

    def _init_win32(self) -> None:
        if sys.platform != "win32" or self._win32_initialized:
            return
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):  # noqa: N801
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            psapi = ctypes.WinDLL("psapi.dll", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

            self._win32_psapi = psapi
            self._win32_kernel32 = kernel32
            self._win32_pmc_cls = PROCESS_MEMORY_COUNTERS
            self._win32_initialized = True
        except Exception as exc:
            logger.debug("Failed to initialize Win32 psapi ctypes: %s", exc)

    def get_memory_working_set_bytes(self) -> int:
        """Get current process working set size in bytes across platforms."""
        if sys.platform == "win32":
            self._init_win32()
            if self._win32_psapi and self._win32_kernel32 and self._win32_pmc_cls:
                try:
                    import ctypes

                    handle = self._win32_kernel32.GetCurrentProcess()
                    counters = self._win32_pmc_cls()
                    counters.cb = ctypes.sizeof(self._win32_pmc_cls)
                    if self._win32_psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                        return int(counters.WorkingSetSize)
                except Exception as exc:
                    logger.debug("Win32 GetProcessMemoryInfo call failed: %s", exc)
        else:
            try:
                import resource

                usage = resource.getrusage(resource.RUSAGE_SELF)
                if sys.platform == "darwin":
                    return int(usage.ru_maxrss)
                return int(usage.ru_maxrss * 1024)
            except Exception as exc:
                logger.debug("POSIX getrusage call failed: %s", exc)

        # Fallback to tracemalloc if enabled
        try:
            import tracemalloc

            if tracemalloc.is_tracing():
                current, _ = tracemalloc.get_traced_memory()
                return int(current)
        except Exception as exc:
            logger.debug("tracemalloc measurement failed: %s", exc)

        return 0

    def get_cpu_percent(self) -> float:
        """Calculate process CPU usage percentage using os.times() deltas.

        Returns 0.0 on the first evaluation cycle (insufficient samples).
        """
        now_times = os.times()
        now_wall = time.monotonic()

        if self._last_cpu_times is None or self._last_cpu_wall_time is None:
            self._last_cpu_times = now_times
            self._last_cpu_wall_time = now_wall
            return 0.0

        user_delta = now_times.user - self._last_cpu_times.user
        sys_delta = now_times.system - self._last_cpu_times.system
        wall_delta = now_wall - self._last_cpu_wall_time

        self._last_cpu_times = now_times
        self._last_cpu_wall_time = now_wall

        if wall_delta <= 0.0:
            return 0.0

        total_cpu_time = user_delta + sys_delta
        pct = (total_cpu_time / wall_delta) * 100.0
        # Bound sensibly
        cpu_count = os.cpu_count() or 1
        return float(round(max(0.0, min(pct, float(cpu_count * 100))), 2))

    def get_thread_count(self) -> int:
        """Return active thread count in the process."""
        return threading.active_count()

    def get_asyncio_task_count(self) -> int:
        """Return active asyncio task count in the running loop."""
        try:
            return len(asyncio.all_tasks())
        except Exception:
            return 0

    async def measure_event_loop_lag_seconds(self) -> float:
        """Measure instantaneous event loop delay with an independent probe."""
        t0 = time.perf_counter()
        await asyncio.sleep(0.0)
        return round(max(0.0, time.perf_counter() - t0), 6)

    async def collect_system_telemetry(self) -> dict[str, Any]:
        """Gather consolidated host / process resource telemetry."""
        mem_bytes = self.get_memory_working_set_bytes()
        mem_mb = round(mem_bytes / (1024.0 * 1024.0), 2)
        cpu_pct = self.get_cpu_percent()
        threads = self.get_thread_count()
        tasks = self.get_asyncio_task_count()
        lag = await self.measure_event_loop_lag_seconds()

        return {
            "memory_working_set_bytes": mem_bytes,
            "memory_working_set_mb": mem_mb,
            "cpu_percent": cpu_pct,
            "thread_count": threads,
            "asyncio_task_count": tasks,
            "event_loop_lag_seconds": lag,
            "timestamp": _utc_now_iso(),
        }


class MetricsCollector:
    """Orchestrates periodic metrics collection across all registered engines."""

    def __init__(
        self,
        registry: MetricRegistry,
        timeseries_buffer: TimeSeriesBuffer,
        collect_interval_seconds: float = DEFAULT_COLLECT_INTERVAL_SECONDS,
        probe_timeout_seconds: float = PER_ENGINE_TIMEOUT_SECONDS,
    ) -> None:
        self.registry = registry
        self.timeseries_buffer = timeseries_buffer
        self.collect_interval_seconds = collect_interval_seconds
        self.probe_timeout_seconds = probe_timeout_seconds

        self.normalizer = DiagnosticsNormalizer()
        self.system_collector = SystemTelemetryCollector()

        # Telemetry stats
        self.collection_cycles_total: int = 0
        self.engine_timeouts_total: int = 0
        self.engine_failures_total: int = 0
        self.last_collection_duration_ms: float = 0.0
        self.last_system_telemetry: dict[str, Any] = {}
        self.last_engines_polled: list[str] = []

    async def collect_cycle(self, kernel: Kernel | None) -> dict[str, Any]:
        """Perform one complete collection cycle across system telemetry and engines."""
        t0 = time.perf_counter()
        now_iso = _utc_now_iso()
        self.collection_cycles_total += 1

        # 1. Collect and record system telemetry
        sys_telemetry = await self.system_collector.collect_system_telemetry()
        self.last_system_telemetry = sys_telemetry

        self._record_system_metrics(sys_telemetry, now_iso)

        # 2. Collect from all engines via Kernel
        polled_engines: list[str] = []
        if kernel is not None:
            engines = kernel.get_all_engines()
            for engine_name, engine in engines.items():
                # Self-exclusion rule: never monitor monitoring engine
                if engine_name == MONITORING_ENGINE_NAME:
                    continue

                polled_engines.append(engine_name)
                await self._collect_engine_diagnostics(engine_name, engine, now_iso)

        self.last_engines_polled = sorted(polled_engines)
        self.last_collection_duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        return {
            "cycle": self.collection_cycles_total,
            "duration_ms": self.last_collection_duration_ms,
            "system_telemetry": sys_telemetry,
            "engines_polled": self.last_engines_polled,
        }

    def _record_system_metrics(self, telemetry: dict[str, Any], timestamp: str) -> None:
        """Update system metrics in registry and time-series buffer."""
        labels = {"subsystem": "system"}

        # Memory MB
        mem_mb = float(telemetry.get("memory_working_set_mb", 0.0))
        g_mem = self.registry.gauge("system.memory.working_set_mb", labels)
        g_mem.set(mem_mb)
        k_mem = MetricRegistry.series_key("system.memory.working_set_mb", labels)
        self.timeseries_buffer.append(k_mem, TimeSeriesPoint(timestamp=timestamp, value=mem_mb))

        # CPU percent
        cpu_pct = float(telemetry.get("cpu_percent", 0.0))
        g_cpu = self.registry.gauge("system.cpu.percent", labels)
        g_cpu.set(cpu_pct)
        k_cpu = MetricRegistry.series_key("system.cpu.percent", labels)
        self.timeseries_buffer.append(k_cpu, TimeSeriesPoint(timestamp=timestamp, value=cpu_pct))

        # Event loop lag seconds
        lag = float(telemetry.get("event_loop_lag_seconds", 0.0))
        g_lag = self.registry.gauge("system.event_loop.lag_seconds", labels)
        g_lag.set(lag)
        k_lag = MetricRegistry.series_key("system.event_loop.lag_seconds", labels)
        self.timeseries_buffer.append(k_lag, TimeSeriesPoint(timestamp=timestamp, value=lag))

        # Async tasks
        tasks = float(telemetry.get("asyncio_task_count", 0))
        g_tasks = self.registry.gauge("system.asyncio.tasks_count", labels)
        g_tasks.set(tasks)

        # Threads
        threads = float(telemetry.get("thread_count", 0))
        g_threads = self.registry.gauge("system.threads.count", labels)
        g_threads.set(threads)

    async def _collect_engine_diagnostics(
        self,
        engine_name: str,
        engine: Any,
        timestamp: str,
    ) -> None:
        """Poll one engine with strict timeout and record normalized metrics."""
        raw_payload: dict[str, Any] = {}

        # Safe diagnostic probe with 1.0s timeout
        try:
            raw_payload = await asyncio.wait_for(
                self._probe_engine(engine),
                timeout=self.probe_timeout_seconds,
            )
        except TimeoutError:
            self.engine_timeouts_total += 1
            logger.warning(
                "Diagnostic probe timed out for engine '%s' after %ss",
                engine_name,
                self.probe_timeout_seconds,
            )
            return
        except Exception as exc:
            self.engine_failures_total += 1
            logger.warning("Diagnostic probe failed for engine '%s': %s", engine_name, exc)
            return

        # Normalize 3-tier diagnostics
        norm_result = self.normalizer.normalize(engine_name, raw_payload)

        # Record metrics into registry & timeseries
        for nm in norm_result.metrics:
            try:
                g = self.registry.gauge(nm.name, nm.labels)
                g.set(nm.value)

                s_key = MetricRegistry.series_key(nm.name, nm.labels)
                self.timeseries_buffer.append(
                    s_key,
                    TimeSeriesPoint(timestamp=timestamp, value=nm.value),
                )
            except Exception as exc:
                logger.debug("Failed to record metric '%s': %s", nm.name, exc)

    async def _probe_engine(self, engine: Any) -> dict[str, Any]:
        """Extract diagnostic fields from engine instance."""
        payload: dict[str, Any] = {}

        # 1. Check health()
        if hasattr(engine, "health"):
            h = engine.health()
            if asyncio.iscoroutine(h):
                h = await h
            if isinstance(h, dict):
                payload.update(h)

        # 2. Check metrics()
        if hasattr(engine, "metrics"):
            m = engine.metrics()
            if asyncio.iscoroutine(m):
                m = await m
            if isinstance(m, dict):
                payload.update(m)

        # 3. Check diagnostics()
        if hasattr(engine, "diagnostics"):
            d = engine.diagnostics()
            if asyncio.iscoroutine(d):
                d = await d
            if isinstance(d, dict):
                payload.update(d)

        # 4. Status, version, capabilities
        if hasattr(engine, "status"):
            try:
                s = engine.status()
                payload["status"] = str(s)
            except Exception as exc:
                logger.debug("status probe failed: %s", exc)

        if hasattr(engine, "version"):
            try:
                v = engine.version()
                payload["version"] = str(v)
            except Exception as exc:
                logger.debug("version probe failed: %s", exc)

        if hasattr(engine, "capabilities"):
            try:
                c = engine.capabilities()
                if isinstance(c, list):
                    payload["capabilities"] = c
            except Exception as exc:
                logger.debug("capabilities probe failed: %s", exc)

        return payload
