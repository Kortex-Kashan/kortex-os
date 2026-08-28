"""Document Engine Telemetry and Diagnostics Manager.

This module implements DocumentDiagnostics adhering to IEngineDiagnostics in accordance with
Section 17 and Milestone 8 of the Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import sys
from typing import Any

from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.lifecycle import DocumentLifecycleManager
from kortex.engines.document.operation_profile import DocumentOperationProfileManager
from kortex.engines.document.template_library import TemplateLibrary
from kortex.engines.storage.interfaces import IEngineDiagnostics


class DocumentDiagnostics(IEngineDiagnostics):
    """Telemetry and diagnostic metrics collector for KORTEX OS Document Engine."""

    def __init__(
        self,
        registry: DocumentAdapterRegistry | None = None,
        template_library: TemplateLibrary | None = None,
        profile_manager: DocumentOperationProfileManager | None = None,
        lifecycle_manager: DocumentLifecycleManager | None = None,
    ) -> None:
        """Initialize DocumentDiagnostics with engine component dependencies."""
        self._registry = registry
        self._template_library = template_library
        self._profile_manager = profile_manager
        self._lifecycle_manager = lifecycle_manager
        self._operation_count = 0
        self._failed_operation_count = 0

    def record_operation_executed(self, is_success: bool) -> None:
        """Record an operation execution attempt."""
        self._operation_count += 1
        if not is_success:
            self._failed_operation_count += 1

    def health(self) -> dict[str, Any]:
        """Return operational health status and diagnostic checks (IEngineDiagnostics protocol)."""
        adapter_count = len(self._registry.list_adapters()) if self._registry is not None else 0
        template_count = (
            len(getattr(self._template_library, "_templates", {}))
            if self._template_library is not None
            else 0
        )
        profile_count = (
            len(getattr(self._profile_manager, "_profiles", {}))
            if self._profile_manager is not None
            else 0
        )

        return {
            "status": "HEALTHY",
            "engine": "document",
            "adapters_registered": adapter_count,
            "templates_indexed": template_count,
            "profiles_registered": profile_count,
            "components": {
                "registry": "OK" if self._registry is not None else "UNINITIALIZED",
                "template_library": "OK" if self._template_library is not None else "UNINITIALIZED",
                "profile_manager": "OK" if self._profile_manager is not None else "UNINITIALIZED",
                "lifecycle_manager": "OK" if self._lifecycle_manager is not None else "UNINITIALIZED",
            },
        }

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and throughput metrics (IEngineDiagnostics protocol)."""
        return {
            "total_operations_executed": self._operation_count,
            "failed_operations_count": self._failed_operation_count,
            "success_rate_percentage": (
                100.0
                if self._operation_count == 0
                else round(
                    ((self._operation_count - self._failed_operation_count) / self._operation_count)
                    * 100.0,
                    2,
                )
            ),
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics and system environment details (IEngineDiagnostics protocol)."""
        return {
            "engine": "document",
            "version": "1.0.0",
            "python_version": sys.version,
            "platform": sys.platform,
            "architecture_version": "1.0.0",
            "capabilities": self.capabilities(),
        }

    def status(self) -> str:
        """Return current engine state name string (IEngineDiagnostics protocol)."""
        return "READY"

    def version(self) -> str:
        """Return semantic version string of the Document Engine (IEngineDiagnostics protocol)."""
        return "1.0.0"

    def capabilities(self) -> list[str]:
        """Return canonical list of capability strings registered by the engine (Section 17)."""
        return [
            "kortex.document.operation.execute",
            "kortex.document.lifecycle.transition",
            "kortex.document.template.bind",
            "kortex.document.preview.generate",
            "kortex.document.intelligence.analyze",
            "kortex.document.recommendation.get",
            "kortex.document.adapter.register",
            "kortex.document.adapter.list",
            "kortex.document.template.list",
        ]


__all__ = ["DocumentDiagnostics"]
