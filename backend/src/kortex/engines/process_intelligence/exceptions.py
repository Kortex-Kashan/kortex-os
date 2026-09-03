"""
KORTEX Process Intelligence Engine Exceptions.

Defines the domain-specific exception hierarchy for execution telemetry,
process mining, and analytical query processing.
"""

from __future__ import annotations


class ProcessIntelligenceError(Exception):
    """Base exception for all Process Intelligence engine errors."""


class ProcessAnalyticsTimeoutError(ProcessIntelligenceError):
    """Raised when an analytical query exceeds the application-level operation timeout."""


class InvalidTimeRangeError(ProcessIntelligenceError):
    """Raised when query time range boundaries are invalid."""


class ProcessDefinitionNotFoundError(ProcessIntelligenceError):
    """Raised when a specified workflow definition is not found or has no records."""


class GraphBoundingError(ProcessIntelligenceError):
    """Raised if graph reduction invariants are violated."""
