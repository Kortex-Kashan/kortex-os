"""Abstract Base Class for Document Adapters in the KORTEX OS Document Engine.

This module defines the BaseDocumentAdapter abstract base class that all Document Adapter
plugins MUST inherit. Technology-specific implementations remain 100% encapsulated behind
this abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    BindingContext,
    DocumentOperationType,
    TemplateSchema,
)


class BaseDocumentAdapter(ABC):
    """Abstract base class for all sandboxed document adapter plugins.

    All technology adapters (PDF renderers, spreadsheet engines, OCR processors,
    macro execution sandboxes) implement this contract.
    """

    @property
    @abstractmethod
    def metadata(self) -> AdapterMetadata:
        """Return immutable Marketplace-ready adapter metadata object."""

    @property
    def adapter_id(self) -> str:
        """Return canonical adapter identifier string."""
        return self.metadata.adapter_id

    @property
    def supported_capabilities(self) -> list[AdapterCapability]:
        """Return list of fine-grained capabilities advertised by this adapter."""
        return self.metadata.supported_capabilities

    def supports_capability(self, capability: AdapterCapability) -> bool:
        """Check whether this adapter advertises support for a specific capability."""
        return capability in self.supported_capabilities

    @abstractmethod
    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        """Execute a document operation in sandboxed context, returning output bytes."""

    @abstractmethod
    def validate_schema(self, schema: TemplateSchema) -> bool:
        """Validate whether a declarative template schema is compatible with this adapter."""


__all__ = ["BaseDocumentAdapter"]
