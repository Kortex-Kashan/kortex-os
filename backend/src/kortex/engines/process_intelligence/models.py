"""
KORTEX Process Intelligence Domain Models.

Provides pure Pydantic models for process graph representation, directly-follows
graph mining, trace variants, bottleneck diagnostics, and summary execution KPIs.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field

# Canonical Workflow State String Constants (zero imports from workflow engine)
STATE_CREATED = "CREATED"
STATE_READY = "READY"
STATE_RUNNING = "RUNNING"
STATE_WAITING = "WAITING"
STATE_APPROVED = "APPROVED"
STATE_COMPLETED = "COMPLETED"
STATE_FAILED = "FAILED"
STATE_CANCELLED = "CANCELLED"

TERMINAL_STATES = {STATE_COMPLETED, STATE_FAILED, STATE_CANCELLED}
ACTIVE_STATES = {STATE_CREATED, STATE_READY, STATE_RUNNING, STATE_WAITING, STATE_APPROVED}

# Virtual Graph Nodes
NODE_START = "[START]"
NODE_END_SUCCESS = "[END_SUCCESS]"
NODE_END_FAILED = "[END_FAILED]"
NODE_END_CANCELLED = "[END_CANCELLED]"
NODE_OTHER_STEPS = "[__OTHER_STEPS__]"
TERMINAL_NODES = {NODE_END_SUCCESS, NODE_END_FAILED, NODE_END_CANCELLED}


class ProcessNode(BaseModel):
    """Represents an execution step or terminal state in the process graph."""

    id: str = Field(description="Unique node identifier (step ID or virtual terminal name)")
    name: str = Field(description="Human-readable node label")
    is_approval: bool = Field(default=False, description="True if step requires human approval")
    is_terminal: bool = Field(default=False, description="True if node represents a terminal state")
    is_grouped: bool = Field(default=False, description="True if node represents collapsed low-frequency steps")
    total_visitations: int = Field(default=0, description="Total observed executions of this step")


class ProcessEdge(BaseModel):
    """Represents a directed transition between two steps in the process graph."""

    source: str = Field(description="Source node ID")
    target: str = Field(description="Target node ID")
    transition_count: int = Field(description="Observed transition frequency")
    transition_probability: float = Field(description="Outgoing transition probability from source node (0.0 to 1.0)")
    avg_latency_ms: float = Field(default=0.0, description="Average transition latency in milliseconds")
    median_latency_ms: float = Field(default=0.0, description="Median transition latency in milliseconds")


class ProcessGraphMetadata(BaseModel):
    """Telemetry and bounding metadata for process graph construction."""

    sample_size: int = Field(description="Number of instances analyzed")
    total_instances_matching: int = Field(description="Total instances matching the query filters")
    is_sampled: bool = Field(default=False, description="True if results are bounded by instance sample limit")
    nodes_collapsed: bool = Field(default=False, description="True if step nodes were collapsed into OTHER")
    collapsed_node_count: int = Field(default=0, description="Count of distinct step nodes collapsed")
    edges_pruned: bool = Field(default=False, description="True if edges were pruned to fit 500 edge limit")
    pruned_edge_count: int = Field(default=0, description="Count of low-frequency edges pruned")
    pruned_transitions_total: int = Field(default=0, description="Sum of transition counts in pruned edges")
    window_clamped: bool = Field(default=False, description="True if requested time window was clamped to 90 days")
    version_analyzed: str | None = Field(default=None, description="Workflow definition version analyzed")
    available_versions: list[str] = Field(
        default_factory=list, description="All workflow versions discovered in query window"
    )


class ProcessGraph(BaseModel):
    """Directly-Follows Graph (DFG) representing observed workflow transitions."""

    definition_id: str = Field(description="Workflow definition ID analyzed")
    nodes: list[ProcessNode] = Field(description="List of graph nodes (max 100)")
    edges: list[ProcessEdge] = Field(description="List of directed graph edges (max 500)")
    metadata: ProcessGraphMetadata = Field(description="Graph construction telemetry and bounding metadata")


class TraceVariant(BaseModel):
    """Represents a unique sequential execution path observed across instances."""

    variant_id: str = Field(description="Unique deterministic hash/signature of the path")
    steps: list[str] = Field(description="Ordered sequence of step IDs executed")
    frequency: int = Field(description="Number of instances following this exact path")
    percentage: float = Field(description="Percentage share of total analyzed traces (0.0 to 100.0)")
    avg_duration_ms: float = Field(description="Average end-to-end cycle duration for this variant")


class VariantListResult(BaseModel):
    """Result container for trace variant mining."""

    definition_id: str = Field(description="Workflow definition ID analyzed")
    total_variants_discovered: int = Field(description="Total unique paths discovered before top-K truncation")
    returned_variants: list[TraceVariant] = Field(description="List of top trace variants (max 50)")
    metadata: ProcessGraphMetadata = Field(description="Sampling and bounding metadata")


class StepBottleneck(BaseModel):
    """Performance and bottleneck diagnostics for an individual workflow step."""

    step_id: str = Field(description="Unique step identifier")
    step_name: str = Field(description="Human-readable step name")
    is_approval_step: bool = Field(default=False, description="True if step requires human approval")
    total_executions: int = Field(description="Total observed step executions")
    failure_count: int = Field(default=0, description="Total step runs ending in FAILED status")
    failure_rate: float = Field(default=0.0, description="Percentage failure rate (0.0 to 100.0)")
    avg_duration_ms: float = Field(default=0.0, description="Average execution duration in milliseconds")
    p50_duration_ms: float = Field(default=0.0, description="50th percentile (median) duration in milliseconds")
    p90_duration_ms: float = Field(default=0.0, description="90th percentile duration in milliseconds")
    p99_duration_ms: float = Field(default=0.0, description="99th percentile duration in milliseconds")
    approval_wait_ms: float | None = Field(
        default=None,
        description="Average human decision turnaround time in milliseconds (approval steps only)",
    )
    retry_count: int | None = Field(
        default=None,
        description="Detailed retry attempts are not independently persisted in the Phase 2 workflow ledger.",
    )


class BottlenecksResult(BaseModel):
    """Result container for step bottleneck diagnostics."""

    definition_id: str | None = Field(default=None, description="Workflow definition ID filtered, if any")
    steps: list[StepBottleneck] = Field(description="List of steps ranked by duration / bottleneck severity")
    window_clamped: bool = Field(default=False, description="True if query window was clamped to 90 days")
    effective_since: datetime.datetime = Field(description="Effective query window start timestamp")
    effective_until: datetime.datetime = Field(description="Effective query window end timestamp")


class ProcessSummaryKPIs(BaseModel):
    """High-level execution KPIs and throughput summary for business workflows."""

    definition_id: str | None = Field(default=None, description="Workflow definition ID filtered, if any")
    total_instances: int = Field(description="Total workflow executions matching filter")
    completed_runs: int = Field(description="Executions completed successfully")
    failed_runs: int = Field(description="Executions terminated in failure")
    cancelled_runs: int = Field(description="Executions cancelled prior to completion")
    active_runs: int = Field(description="Executions currently running, waiting, or pending (WIP)")
    throughput_per_day: float = Field(description="Average completed workflows per day over query window")
    success_rate: float = Field(description="Completed runs as percentage of terminal runs (0.0 to 100.0)")
    failure_rate: float = Field(description="Failed runs as percentage of terminal runs (0.0 to 100.0)")
    avg_cycle_time_ms: float = Field(description="Average end-to-end duration for completed instances")
    p50_cycle_time_ms: float = Field(description="50th percentile cycle duration for completed instances")
    p90_cycle_time_ms: float = Field(description="90th percentile cycle duration for completed instances")
    p99_cycle_time_ms: float = Field(description="99th percentile cycle duration for completed instances")
    avg_approval_turnaround_ms: float | None = Field(
        default=None,
        description="Average human approval decision latency in milliseconds across all tickets",
    )
    versions_included: list[str] = Field(
        default_factory=list, description="All workflow definition versions aggregated in this summary"
    )
    window_clamped: bool = Field(default=False, description="True if requested window was clamped to 90 days")
    effective_since: datetime.datetime = Field(description="Effective query window start timestamp")
    effective_until: datetime.datetime = Field(description="Effective query window end timestamp")
    is_sampled: bool = Field(default=False, description="Always False; scalar summary KPIs are complete aggregates")


# Internal Data Transport Structures
class RawStepExecution(BaseModel):
    """Internal representation of a single persisted step run."""

    id: str
    instance_id: str
    step_id: str
    status: str
    started_at: datetime.datetime
    completed_at: datetime.datetime | None = None
    attempt: int = 1


class RawInstanceTrace(BaseModel):
    """Internal representation of a complete instance execution trace."""

    instance_id: str
    definition_id: str
    definition_version: str
    state: str
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    steps: list[RawStepExecution] = Field(default_factory=list)
