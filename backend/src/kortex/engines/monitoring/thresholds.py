"""Operational threshold evaluation engine for KORTEX Monitoring Engine.

Evaluates operational metrics against warning and critical thresholds with:
- Strict consecutive-cycle confirmation (default: 2 cycles)
- 10% hysteresis on recovery
- 60-second alert cooldown to avoid event spam
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from kortex.engines.monitoring.constants import (
    CONSECUTIVE_CYCLES_REQUIRED,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_HYSTERESIS_PERCENT,
)
from kortex.engines.monitoring.models import AlertRecord, MetricValue, ThresholdSeverity, ThresholdState

if TYPE_CHECKING:
    from kortex.engines.monitoring.events import MonitoringEventPublisher

logger = logging.getLogger("kortex.engines.monitoring.thresholds")


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


class ThresholdRule(BaseModel):
    """Configured operational threshold rule for a metric."""

    model_config = ConfigDict(frozen=True)

    metric_name: str
    subsystem: str = "system"
    warning_threshold: float
    critical_threshold: float
    hysteresis_pct: float = DEFAULT_HYSTERESIS_PERCENT
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    consecutive_cycles_required: int = CONSECUTIVE_CYCLES_REQUIRED


class _ThresholdEvaluationState:
    """Internal mutable state for tracking consecutive cycles and cooldowns."""

    def __init__(self, rule: ThresholdRule) -> None:
        self.rule = rule
        self.current_state = ThresholdState.NORMAL
        self.consecutive_breaches: int = 0
        self.first_breached_at: str | None = None
        self.last_evaluated_at: str = _utc_now_iso()
        self.last_event_emitted_at: datetime.datetime | None = None
        self.last_emitted_severity: ThresholdSeverity | None = None


class ThresholdEvaluator:
    """Evaluates metrics against operational threshold rules deterministically."""

    def __init__(
        self,
        event_publisher: MonitoringEventPublisher | None = None,
        rules: list[ThresholdRule] | None = None,
    ) -> None:
        self._event_publisher = event_publisher
        self._rules: dict[str, ThresholdRule] = {}
        self._states: dict[str, _ThresholdEvaluationState] = {}

        # Default rules if none provided
        default_rules = rules or [
            ThresholdRule(
                metric_name="system.memory.working_set_mb",
                subsystem="system",
                warning_threshold=1024.0,
                critical_threshold=2048.0,
            ),
            ThresholdRule(
                metric_name="system.event_loop.lag_seconds",
                subsystem="system",
                warning_threshold=0.5,
                critical_threshold=1.5,
            ),
            ThresholdRule(
                metric_name="system.cpu.percent",
                subsystem="system",
                warning_threshold=85.0,
                critical_threshold=95.0,
            ),
        ]
        for r in default_rules:
            self.add_rule(r)

    def add_rule(self, rule: ThresholdRule) -> None:
        """Register or update an operational threshold rule."""
        key = f"{rule.subsystem}:{rule.metric_name}"
        self._rules[key] = rule
        if key not in self._states:
            self._states[key] = _ThresholdEvaluationState(rule)
        else:
            self._states[key].rule = rule

    async def evaluate_metrics(self, metrics: list[MetricValue]) -> list[AlertRecord]:
        """Evaluate current metric values against all rules.

        Returns list of active AlertRecords. Emits exceeded and recovered events
        when state transitions occur.
        """
        now = _utc_now()
        now_iso = now.isoformat()
        active_alerts: list[AlertRecord] = []

        # Index metrics by subsystem:name
        metric_lookup: dict[str, float] = {}
        for m in metrics:
            sub = m.labels.get("subsystem", "system")
            if m.value is not None:
                metric_lookup[f"{sub}:{m.name}"] = m.value

        for key, state in self._states.items():
            rule = state.rule
            state.last_evaluated_at = now_iso

            val = metric_lookup.get(key)
            if val is None:
                # Metric not observed in this cycle
                continue

            # Check raw breach conditions
            target_severity: ThresholdSeverity | None = None
            threshold_val: float = rule.warning_threshold

            if val >= rule.critical_threshold:
                target_severity = ThresholdSeverity.CRITICAL
                threshold_val = rule.critical_threshold
            elif val >= rule.warning_threshold:
                target_severity = ThresholdSeverity.WARNING
                threshold_val = rule.warning_threshold

            # Evaluate state machine with consecutive cycle and hysteresis rules
            if target_severity is not None:
                # In breach condition
                state.consecutive_breaches += 1
                if state.first_breached_at is None:
                    state.first_breached_at = now_iso

                # Check if consecutive cycles met
                if state.consecutive_breaches >= rule.consecutive_cycles_required:
                    # Assert condition
                    prev_state = state.current_state
                    state.current_state = ThresholdState(target_severity.value)

                    # Determine if event should be emitted (escalation or cooldown elapsed)
                    should_emit = False
                    if prev_state == ThresholdState.NORMAL:
                        should_emit = True
                    elif state.last_emitted_severity != target_severity:
                        # Escalation or de-escalation between warning and critical
                        should_emit = True
                    elif state.last_event_emitted_at is not None:
                        elapsed = (now - state.last_event_emitted_at).total_seconds()
                        if elapsed >= rule.cooldown_seconds:
                            should_emit = True
                    else:
                        should_emit = True

                    if should_emit and self._event_publisher is not None:
                        await self._event_publisher.emit_threshold_exceeded(
                            metric_name=rule.metric_name,
                            subsystem=rule.subsystem,
                            current_value=val,
                            threshold_value=threshold_val,
                            severity=target_severity.value,
                            consecutive_breaches=state.consecutive_breaches,
                        )
                        state.last_event_emitted_at = now
                        state.last_emitted_severity = target_severity

                # If asserted, include in active alerts
                if state.current_state != ThresholdState.NORMAL:
                    active_alerts.append(
                        AlertRecord(
                            metric_name=rule.metric_name,
                            subsystem=rule.subsystem,
                            current_value=val,
                            threshold=threshold_val,
                            severity=ThresholdSeverity(state.current_state.value),
                            consecutive_breaches=state.consecutive_breaches,
                            first_breached_at=state.first_breached_at or now_iso,
                            last_evaluated_at=now_iso,
                            last_event_emitted_at=state.last_event_emitted_at.isoformat()
                            if state.last_event_emitted_at
                            else None,
                        )
                    )
            else:
                # Below warning threshold: check recovery with 10% hysteresis
                if state.current_state != ThresholdState.NORMAL:
                    recovery_ceiling = rule.warning_threshold * (1.0 - rule.hysteresis_pct)
                    if val <= recovery_ceiling:
                        prev_sev = state.current_state.value
                        state.current_state = ThresholdState.NORMAL
                        state.consecutive_breaches = 0
                        state.first_breached_at = None

                        if self._event_publisher is not None:
                            await self._event_publisher.emit_threshold_recovered(
                                metric_name=rule.metric_name,
                                subsystem=rule.subsystem,
                                current_value=val,
                                recovery_value=recovery_ceiling,
                                previous_severity=prev_sev,
                            )
                        state.last_emitted_severity = None
                    else:
                        # Value in hysteresis deadband: keep current alert state active
                        active_alerts.append(
                            AlertRecord(
                                metric_name=rule.metric_name,
                                subsystem=rule.subsystem,
                                current_value=val,
                                threshold=rule.warning_threshold,
                                severity=ThresholdSeverity(state.current_state.value),
                                consecutive_breaches=state.consecutive_breaches,
                                first_breached_at=state.first_breached_at or now_iso,
                                last_evaluated_at=now_iso,
                                last_event_emitted_at=state.last_event_emitted_at.isoformat()
                                if state.last_event_emitted_at
                                else None,
                            )
                        )
                else:
                    # In normal state, reset consecutive breaches
                    state.consecutive_breaches = 0
                    state.first_breached_at = None

        return active_alerts
