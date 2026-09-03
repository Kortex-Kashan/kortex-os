"""KORTEX Document Intelligence Engine — Diagnostics Implementation.

Mirrors `kortex.engines.sentinel.diagnostics.SentinelDiagnostics` exactly:
a thin adapter over the engine instance implementing `IEngineDiagnostics`
(reused from `kortex.engines.storage.interfaces`, the same reuse Sentinel
already established).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import EngineState
from kortex.engines.storage.interfaces import IEngineDiagnostics

if TYPE_CHECKING:
    from kortex.engines.document_intelligence.engine import DocumentIntelligenceEngine


class DocumentIntelligenceDiagnostics(IEngineDiagnostics):
    """Diagnostics adapter for `DocumentIntelligenceEngine` adhering to `IEngineDiagnostics`."""

    def __init__(self, engine: DocumentIntelligenceEngine) -> None:
        self._engine = engine
        self._pdf_parses: int = 0
        self._ocr_extractions: int = 0
        self._structure_analyses: int = 0
        self._failures: int = 0

    def record_pdf_parse(self) -> None:
        self._pdf_parses += 1

    def record_ocr_extraction(self) -> None:
        self._ocr_extractions += 1

    def record_structure_analysis(self) -> None:
        self._structure_analyses += 1

    def record_failure(self) -> None:
        self._failures += 1

    def health(self) -> dict[str, Any]:
        is_healthy = self._engine.state in (EngineState.READY, EngineState.RUNNING)
        return {
            "engine": self._engine.name,
            "status": self._engine.state.value,
            "healthy": is_healthy,
        }

    def metrics(self) -> dict[str, Any]:
        return {
            "pdf_parses": self._pdf_parses,
            "ocr_extractions": self._ocr_extractions,
            "structure_analyses": self._structure_analyses,
            "failures": self._failures,
        }

    def diagnostics(self) -> dict[str, Any]:
        return {
            "engine": self._engine.name,
            "version": self.version(),
            "state": self._engine.state.value,
            "capabilities": self.capabilities(),
            "metrics": self.metrics(),
        }

    def status(self) -> str:
        return self._engine.state.value

    def version(self) -> str:
        return "1.0.0"

    def capabilities(self) -> list[str]:
        return list(self._engine.registered_capabilities)
