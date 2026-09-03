"""
KORTEX Process Intelligence Engine Package.

Provides execution telemetry, process mining, Directly-Follows Graph (DFG) generation,
trace variant clustering, and step bottleneck diagnostics for business workflows.
"""

from __future__ import annotations

from kortex.engines.process_intelligence.engine import ProcessIntelligenceEngine
from kortex.engines.process_intelligence.exceptions import (
    GraphBoundingError,
    InvalidTimeRangeError,
    ProcessAnalyticsTimeoutError,
    ProcessDefinitionNotFoundError,
    ProcessIntelligenceError,
)
from kortex.engines.process_intelligence.interfaces import (
    IProcessAnalyticsRepository,
    IProcessAnalyzer,
    IProcessMiner,
)
from kortex.engines.process_intelligence.models import (
    BottlenecksResult,
    ProcessEdge,
    ProcessGraph,
    ProcessGraphMetadata,
    ProcessNode,
    ProcessSummaryKPIs,
    StepBottleneck,
    TraceVariant,
    VariantListResult,
)

__all__ = [
    "BottlenecksResult",
    "GraphBoundingError",
    "IProcessAnalyticsRepository",
    "IProcessAnalyzer",
    "IProcessMiner",
    "InvalidTimeRangeError",
    "ProcessAnalyticsTimeoutError",
    "ProcessDefinitionNotFoundError",
    "ProcessEdge",
    "ProcessGraph",
    "ProcessGraphMetadata",
    "ProcessIntelligenceEngine",
    "ProcessIntelligenceError",
    "ProcessNode",
    "ProcessSummaryKPIs",
    "StepBottleneck",
    "TraceVariant",
    "VariantListResult",
]
