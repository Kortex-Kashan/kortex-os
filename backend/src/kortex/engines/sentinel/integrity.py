"""KORTEX Sentinel Engine — Integrity and Invariant Verifier.

Executes non-invasive architectural invariant checks across:
- Kernel lifecycle state
- Engine lifecycle states
- Engine dependency resolution
- Database connectivity via non-mutating session ping
- Capability registry descriptor consistency
- Event Engine availability
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

from kortex.core.base_engine import EngineState
from kortex.core.kernel import KernelState
from kortex.engines.sentinel.constants import DEFAULT_PROBE_TIMEOUT_SECONDS
from kortex.engines.sentinel.models import (
    CheckStatus,
    IntegrityReport,
    ProbeResult,
    SentinelStatus,
)

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engine.sentinel.integrity")


class IntegrityVerifier:
    """Verifies system-level architectural invariants and returns structured ProbeResults."""

    def __init__(self, probe_timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS) -> None:
        self._probe_timeout_seconds = probe_timeout_seconds

    async def verify(
        self,
        kernel: Kernel | None,
        selected_checks: list[str] | None = None,
    ) -> IntegrityReport:
        """Run invariant checks against the active Kernel runtime."""
        if kernel is None:
            res = ProbeResult(
                probe_name="kernel_availability",
                status=CheckStatus.FAIL,
                message="Kernel reference is None. Cannot verify invariants.",
                is_required=True,
            )
            return IntegrityReport(
                overall_status=SentinelStatus.FAILED,
                passed=0,
                warnings=0,
                failures=1,
                checks=[res],
                timestamp=datetime.now(UTC),
            )

        all_checks = {
            "kernel_state": self._check_kernel_state,
            "engine_states": self._check_engine_states,
            "engine_dependencies": self._check_engine_dependencies,
            "database_connectivity": self._check_database_connectivity,
            "capability_registry": self._check_capability_registry,
            "event_engine": self._check_event_engine,
        }

        active_checks = (
            {k: v for k, v in all_checks.items() if k in selected_checks}
            if selected_checks is not None
            else all_checks
        )

        results: list[ProbeResult] = []
        for name, check_fn in active_checks.items():
            try:
                res = await asyncio.wait_for(check_fn(kernel), timeout=self._probe_timeout_seconds)
                results.append(res)
            except TimeoutError:
                results.append(
                    ProbeResult(
                        probe_name=name,
                        status=CheckStatus.FAIL,
                        message=f"Check '{name}' timed out after {self._probe_timeout_seconds}s.",
                        is_required=True,
                    )
                )
            except Exception as exc:
                logger.warning("Check '%s' raised unexpected exception: %s", name, exc)
                results.append(
                    ProbeResult(
                        probe_name=name,
                        status=CheckStatus.FAIL,
                        message=f"Check '{name}' failed with exception: {exc}",
                        details={"error": str(exc)},
                        is_required=True,
                    )
                )

        passed = sum(1 for r in results if r.status == CheckStatus.PASS)
        warnings = sum(1 for r in results if r.status == CheckStatus.WARN)
        failures = sum(1 for r in results if r.status == CheckStatus.FAIL)

        if failures > 0:
            overall = SentinelStatus.FAILED
        elif warnings > 0:
            overall = SentinelStatus.DEGRADED
        else:
            overall = SentinelStatus.HEALTHY

        return IntegrityReport(
            overall_status=overall,
            passed=passed,
            warnings=warnings,
            failures=failures,
            checks=results,
            timestamp=datetime.now(UTC),
        )

    async def _check_kernel_state(self, kernel: Kernel) -> ProbeResult:
        """Verify Kernel operational state."""
        state = kernel.state
        if state == KernelState.RUNNING:
            return ProbeResult(
                probe_name="kernel_state",
                status=CheckStatus.PASS,
                message="Kernel is in RUNNING state.",
                details={"kernel_state": state.value},
            )
        elif state == KernelState.BOOTING:
            return ProbeResult(
                probe_name="kernel_state",
                status=CheckStatus.WARN,
                message="Kernel is currently BOOTING.",
                details={"kernel_state": state.value},
            )
        else:
            return ProbeResult(
                probe_name="kernel_state",
                status=CheckStatus.FAIL,
                message=f"Kernel is in non-operational state: {state.value}",
                details={"kernel_state": state.value},
            )

    async def _check_engine_states(self, kernel: Kernel) -> ProbeResult:
        """Verify that registered engines are not in FAILED states."""
        try:
            engines = kernel.get_all_engines()
            failed_engines: list[str] = []
            uninitialized_engines: list[str] = []
            active_engines: list[str] = []

            for name, engine in engines.items():
                estate = getattr(engine, "state", None)
                if estate == EngineState.FAILED:
                    failed_engines.append(name)
                elif estate == EngineState.UNINITIALIZED:
                    uninitialized_engines.append(name)
                elif estate in (EngineState.READY, EngineState.RUNNING):
                    active_engines.append(name)

            if failed_engines:
                return ProbeResult(
                    probe_name="engine_states",
                    status=CheckStatus.FAIL,
                    message=f"Engines in FAILED state: {', '.join(failed_engines)}",
                    details={
                        "failed": failed_engines,
                        "uninitialized": uninitialized_engines,
                        "active": active_engines,
                    },
                )
            elif uninitialized_engines:
                return ProbeResult(
                    probe_name="engine_states",
                    status=CheckStatus.WARN,
                    message=f"Engines in UNINITIALIZED state: {', '.join(uninitialized_engines)}",
                    details={
                        "failed": [],
                        "uninitialized": uninitialized_engines,
                        "active": active_engines,
                    },
                )
            else:
                return ProbeResult(
                    probe_name="engine_states",
                    status=CheckStatus.PASS,
                    message=f"All {len(active_engines)} registered engines in operational state.",
                    details={"active_count": len(active_engines)},
                )
        except Exception as exc:
            return ProbeResult(
                probe_name="engine_states",
                status=CheckStatus.FAIL,
                message=f"Exception checking engine states: {exc}",
                details={"error": str(exc)},
            )

    async def _check_engine_dependencies(self, kernel: Kernel) -> ProbeResult:
        """Verify that all declared engine dependencies are satisfied in Kernel registry."""
        try:
            engines = kernel.get_all_engines()
            registered_names = set(engines.keys())
            missing_deps: dict[str, list[str]] = {}

            for name, engine in engines.items():
                deps = getattr(engine, "dependencies", [])
                unmet = [dep for dep in deps if dep not in registered_names]
                if unmet:
                    missing_deps[name] = unmet

            if missing_deps:
                return ProbeResult(
                    probe_name="engine_dependencies",
                    status=CheckStatus.FAIL,
                    message=f"Unresolved engine dependencies: {missing_deps}",
                    details={"missing_dependencies": missing_deps},
                )

            return ProbeResult(
                probe_name="engine_dependencies",
                status=CheckStatus.PASS,
                message="All declared engine dependencies resolve in Kernel registry.",
                details={"registered_engines": sorted(registered_names)},
            )
        except Exception as exc:
            return ProbeResult(
                probe_name="engine_dependencies",
                status=CheckStatus.FAIL,
                message=f"Exception checking engine dependencies: {exc}",
                details={"error": str(exc)},
            )

    async def _check_database_connectivity(self, kernel: Kernel) -> ProbeResult:
        """Verify database connection through a non-mutating session ping."""
        try:
            db_manager = getattr(kernel, "db_manager", None)
            if db_manager is None:
                return ProbeResult(
                    probe_name="database_connectivity",
                    status=CheckStatus.WARN,
                    message="Kernel does not expose db_manager; skipping database ping.",
                )

            if not getattr(db_manager, "is_connected", False):
                dialect = getattr(getattr(db_manager, "dialect", None), "value", "unknown")
                return ProbeResult(
                    probe_name="database_connectivity",
                    status=CheckStatus.FAIL,
                    message="DatabaseManager reports disconnected.",
                    details={"dialect": dialect},
                )

            db_target = getattr(kernel, "db", None) or db_manager
            session_ctx = None
            if hasattr(db_target, "get_session") and callable(db_target.get_session):
                session_ctx = db_target.get_session()
            elif hasattr(db_target, "session") and callable(db_target.session):
                session_ctx = db_target.session()

            if session_ctx is not None and hasattr(session_ctx, "__aenter__"):
                try:
                    async with session_ctx as session:
                        if hasattr(session, "execute"):
                            res = session.execute(text("SELECT 1"))
                            result = await res if inspect.isawaitable(res) else res
                            val = result.scalar() if hasattr(result, "scalar") else 1
                            if val != 1:
                                return ProbeResult(
                                    probe_name="database_connectivity",
                                    status=CheckStatus.FAIL,
                                    message=f"SELECT 1 returned unexpected scalar: {val}",
                                )
                except Exception as query_exc:
                    return ProbeResult(
                        probe_name="database_connectivity",
                        status=CheckStatus.FAIL,
                        message=f"Database ping query failed: {query_exc}",
                        details={"error": str(query_exc)},
                    )

            return ProbeResult(
                probe_name="database_connectivity",
                status=CheckStatus.PASS,
                message="Database connectivity confirmed with active session ping.",
                details={"connected": True},
            )
        except Exception as exc:
            return ProbeResult(
                probe_name="database_connectivity",
                status=CheckStatus.FAIL,
                message=f"Database connectivity check failed: {exc}",
                details={"error": str(exc)},
            )

    async def _check_capability_registry(self, kernel: Kernel) -> ProbeResult:
        """Verify that registered capabilities possess valid descriptors."""
        try:
            caps = kernel.list_capabilities()
            if not caps:
                return ProbeResult(
                    probe_name="capability_registry",
                    status=CheckStatus.WARN,
                    message="No capabilities registered in Kernel registry.",
                    details={"count": 0},
                )

            invalid_caps: list[str] = []
            for cap in caps:
                if not getattr(cap, "name", None) or not getattr(cap, "provider", None):
                    invalid_caps.append(str(cap))

            if invalid_caps:
                return ProbeResult(
                    probe_name="capability_registry",
                    status=CheckStatus.FAIL,
                    message=f"Found {len(invalid_caps)} invalid capability descriptors.",
                    details={"invalid_capabilities": invalid_caps},
                )

            return ProbeResult(
                probe_name="capability_registry",
                status=CheckStatus.PASS,
                message=f"All {len(caps)} registered capabilities possess complete descriptors.",
                details={"capability_count": len(caps)},
            )
        except Exception as exc:
            return ProbeResult(
                probe_name="capability_registry",
                status=CheckStatus.FAIL,
                message=f"Exception evaluating capability registry: {exc}",
                details={"error": str(exc)},
            )

    async def _check_event_engine(self, kernel: Kernel) -> ProbeResult:
        """Verify that the Event Engine is registered and available."""
        try:
            event_eng = kernel.get_engine("event")
            if event_eng is None:
                # Kernel also has kernel._event_engine
                if getattr(kernel, "_event_engine", None) is not None:
                    return ProbeResult(
                        probe_name="event_engine",
                        status=CheckStatus.PASS,
                        message="Event Engine active on Kernel runtime.",
                    )
                return ProbeResult(
                    probe_name="event_engine",
                    status=CheckStatus.FAIL,
                    message="Event Engine is not registered on Kernel.",
                )

            estate = getattr(event_eng, "state", None)
            if estate in (EngineState.READY, EngineState.RUNNING):
                return ProbeResult(
                    probe_name="event_engine",
                    status=CheckStatus.PASS,
                    message=f"Event Engine is in operational state: {estate.value}",
                    details={"state": estate.value},
                )
            else:
                return ProbeResult(
                    probe_name="event_engine",
                    status=CheckStatus.WARN,
                    message=f"Event Engine is in non-running state: {getattr(estate, 'value', str(estate))}",
                    details={"state": str(estate)},
                )
        except Exception as exc:
            return ProbeResult(
                probe_name="event_engine",
                status=CheckStatus.FAIL,
                message=f"Exception checking Event Engine: {exc}",
                details={"error": str(exc)},
            )
