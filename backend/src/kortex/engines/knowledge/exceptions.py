"""
KORTEX Knowledge Engine Exception Hierarchy (Milestone M1).

All Knowledge Engine exceptions inherit from `KortexError`
(`kortex.core.exceptions`), following the existing KORTEX exception
conventions.

Only the base class is declared in Milestone M1 — concrete subclasses are
added incrementally as the milestones that need them land (graph errors in
M2, lineage errors in M3, annotation errors in M4, source-provider errors
in M5, trust-promotion errors in M6, persistence errors in M7, search
errors in M8, pack-verification errors in M9, facade errors in M11),
matching the established convention in `kortex.engines.security.exceptions`.
"""

from __future__ import annotations

from kortex.core.exceptions import KortexError


class KnowledgeEngineError(KortexError):
    """Base exception for all Knowledge Engine errors."""
