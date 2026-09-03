"""
Unit tests for ProcessAnalyzer: Percentiles, bottleneck ranking, and retry semantics.
"""

from __future__ import annotations

from kortex.engines.process_intelligence.analyzer import ProcessAnalyzer


def test_percentile_empty_and_single_element() -> None:
    analyzer = ProcessAnalyzer()

    # Empty
    assert analyzer.calculate_percentile([], 50.0) == 0.0
    assert analyzer.calculate_percentile([], 90.0) == 0.0

    # Single element
    assert analyzer.calculate_percentile([42.5], 50.0) == 42.5
    assert analyzer.calculate_percentile([42.5], 90.0) == 42.5
    assert analyzer.calculate_percentile([42.5], 99.0) == 42.5


def test_percentile_discards_negative_and_null_values() -> None:
    analyzer = ProcessAnalyzer()
    # List containing negative numbers or null-like items
    data = [-10.0, -5.0, 10.0, 20.0, 30.0]
    # Filtered dataset is [10.0, 20.0, 30.0], N=3
    # P=50: R = 0.5 * 2 = 1.0 -> 20.0
    assert analyzer.calculate_percentile(data, 50.0) == 20.0


def test_percentile_known_linear_interpolation() -> None:
    analyzer = ProcessAnalyzer()
    # N=5: [10.0, 20.0, 30.0, 40.0, 50.0]
    data = [50.0, 10.0, 40.0, 20.0, 30.0]

    # P=50: R = 0.5 * 4 = 2.0 -> index 2 = 30.0
    assert analyzer.calculate_percentile(data, 50.0) == 30.0

    # P=90: R = 0.9 * 4 = 3.6 -> index 3 + 0.6*(50 - 40) = 40 + 6 = 46.0
    assert analyzer.calculate_percentile(data, 90.0) == 46.0

    # P=99: R = 0.99 * 4 = 3.96 -> 40 + 0.96*10 = 49.6
    assert analyzer.calculate_percentile(data, 99.0) == 49.6


def test_bottleneck_ranking_and_truthful_retry_semantics() -> None:
    analyzer = ProcessAnalyzer()

    raw_metrics = [
        {
            "step_id": "fast_step",
            "total_executions": 10,
            "failed_executions": 0,
            "durations": [10.0, 12.0, 15.0, 11.0, 14.0],
        },
        {
            "step_id": "slow_step",
            "total_executions": 10,
            "failed_executions": 2,
            "durations": [500.0, 600.0, 450.0, 800.0, 950.0],
        },
        {
            "step_id": "approval_step",
            "total_executions": 5,
            "failed_executions": 0,
            "durations": [5.0, 6.0, 4.0],  # Internal dispatch latency only
        },
    ]

    approval_map = {
        "approval_step": 3600000.0,  # 1 hour in ms from approval_requests
    }

    ranked = analyzer.rank_bottlenecks(raw_metrics, approval_map, limit=10)
    assert len(ranked) == 3

    # slow_step has highest p90 and should be first
    assert ranked[0].step_id == "slow_step"
    assert ranked[0].total_executions == 10
    assert ranked[0].failure_count == 2
    assert ranked[0].failure_rate == 20.0
    assert ranked[0].p90_duration_ms > 500.0
    assert ranked[0].is_approval_step is False

    # Truthful retry invariant: retries are NOT fabricated
    assert ranked[0].retry_count is None

    # approval_step should have approval_wait_ms populated from approval_map
    app_b = next(b for b in ranked if b.step_id == "approval_step")
    assert app_b.is_approval_step is True
    assert app_b.approval_wait_ms == 3600000.0
    assert app_b.retry_count is None
