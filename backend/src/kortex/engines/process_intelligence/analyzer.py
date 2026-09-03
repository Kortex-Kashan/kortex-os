"""
KORTEX Process Intelligence Statistical Analyzer.

Implements deterministic linear-interpolation percentile calculations, step-level
bottleneck ranking, and KPI transformations.
"""

from __future__ import annotations

import math
from typing import Any

from kortex.engines.process_intelligence.interfaces import IProcessAnalyzer
from kortex.engines.process_intelligence.models import StepBottleneck


class ProcessAnalyzer(IProcessAnalyzer):
    """Deterministic statistical analyzer for execution metrics."""

    def calculate_percentile(self, values: list[float], percentile: float) -> float:
        """Calculate NIST Method 8 linear interpolation percentile for non-negative values.

        Formula:
            R = (P / 100.0) * (N - 1)
            k = floor(R)
            d = R - k
            value = x[k] + d * (x[k+1] - x[k])
        """
        # Filter nulls and negative durations
        valid = [v for v in values if v is not None and v >= 0.0]
        if not valid:
            return 0.0

        valid.sort()
        n = len(valid)

        if n == 1:
            return round(valid[0], 2)

        p = max(0.0, min(100.0, percentile))
        r = (p / 100.0) * (n - 1)
        k = math.floor(r)
        d = r - k

        if k >= n - 1:
            return round(valid[-1], 2)

        interpolated = valid[k] + d * (valid[k + 1] - valid[k])
        return round(interpolated, 2)

    def rank_bottlenecks(
        self,
        step_metrics_raw: list[dict[str, Any]],
        approval_wait_map: dict[str, float],
        limit: int = 20,
    ) -> list[StepBottleneck]:
        """Rank workflow steps by bottleneck latency severity (p90 and average duration)."""
        bottlenecks: list[StepBottleneck] = []

        for row in step_metrics_raw:
            step_id = str(row["step_id"])
            total_execs = int(row["total_executions"])
            failed_execs = int(row.get("failed_executions", 0))
            durations: list[float] = row.get("durations", [])

            failure_rate = round((failed_execs / total_execs) * 100.0, 2) if total_execs > 0 else 0.0

            p50 = self.calculate_percentile(durations, 50.0)
            p90 = self.calculate_percentile(durations, 90.0)
            p99 = self.calculate_percentile(durations, 99.0)

            valid_durations = [d for d in durations if d is not None and d >= 0.0]
            avg_dur = round(sum(valid_durations) / len(valid_durations), 2) if valid_durations else 0.0

            approval_wait = approval_wait_map.get(step_id)
            is_approval = approval_wait is not None or bool(row.get("is_approval", False))

            bottlenecks.append(
                StepBottleneck(
                    step_id=step_id,
                    step_name=step_id,
                    is_approval_step=is_approval,
                    total_executions=total_execs,
                    failure_count=failed_execs,
                    failure_rate=failure_rate,
                    avg_duration_ms=avg_dur,
                    p50_duration_ms=p50,
                    p90_duration_ms=p90,
                    p99_duration_ms=p99,
                    approval_wait_ms=round(approval_wait, 2) if approval_wait is not None else None,
                    retry_count=None,  # Detailed retries not independently persisted
                )
            )

        # Sort: highest p90 duration first, then highest average, then step_id ascending
        bottlenecks.sort(key=lambda b: (-b.p90_duration_ms, -b.avg_duration_ms, b.step_id))

        clamped_limit = max(1, min(limit, 50))
        return bottlenecks[:clamped_limit]
