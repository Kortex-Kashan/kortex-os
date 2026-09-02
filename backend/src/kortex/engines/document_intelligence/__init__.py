"""KORTEX Document Intelligence Engine — Document parsing and extraction.

Standalone, capability-dispatch-driven, locally executed, deterministic
where applicable, stateless in Phase 4. See `engine.py` for the full
architectural rationale and `docs/architecture/document_intelligence_
engine_implementation_spec.md` for the ratified specification.
"""

from __future__ import annotations

from kortex.engines.document_intelligence.engine import DocumentIntelligenceEngine
from kortex.engines.document_intelligence.exceptions import (
    CorruptedDocumentError,
    DocumentIntelligenceError,
    EncryptedDocumentError,
    ExtractionTimeoutError,
    InvalidRequestError,
    OCRProviderUnavailableError,
    ResourceLimitExceededError,
    StorageAccessError,
    TenantAuthorityError,
    UnsupportedImageError,
)
from kortex.engines.document_intelligence.interfaces import IOCREngine, IPDFParser
from kortex.engines.document_intelligence.models import (
    DocumentLayoutBlock,
    DocumentParseRequest,
    ExtractedTable,
    OCRResult,
    ParsedDocumentResult,
    StructureAnalysisRequest,
)

__all__ = [
    "CorruptedDocumentError",
    "DocumentIntelligenceEngine",
    "DocumentIntelligenceError",
    "DocumentLayoutBlock",
    "DocumentParseRequest",
    "EncryptedDocumentError",
    "ExtractedTable",
    "ExtractionTimeoutError",
    "IOCREngine",
    "IPDFParser",
    "InvalidRequestError",
    "OCRProviderUnavailableError",
    "OCRResult",
    "ParsedDocumentResult",
    "ResourceLimitExceededError",
    "StorageAccessError",
    "StructureAnalysisRequest",
    "TenantAuthorityError",
    "UnsupportedImageError",
]
