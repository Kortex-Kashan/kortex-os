"""KORTEX Document Intelligence Engine — Provider Protocols.

`IPDFParser` and `IOCREngine` are the exact, locked provider boundaries.
There is no separate `IDocumentIntelligenceEngine` provider — that name
refers to the engine facade itself (`engine.DocumentIntelligenceEngine`),
not a provider protocol.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kortex.engines.document_intelligence.models import OCRResult, ParsedDocumentResult


@runtime_checkable
class IPDFParser(Protocol):
    """Deterministic extraction of text, metadata, and tables from PDF bytes.

    Input is bytes; no stream or filesystem-path abstraction is accepted.
    The provider does not own the overall timeout policy — the owning
    engine wraps every call in its configured timeout envelope.
    """

    async def parse(self, content: bytes, options: dict[str, Any]) -> ParsedDocumentResult:
        """Parse PDF bytes into a `ParsedDocumentResult`.

        Raises:
            kortex.engines.document_intelligence.exceptions.CorruptedDocumentError:
                the payload is not a well-formed PDF.
            kortex.engines.document_intelligence.exceptions.EncryptedDocumentError:
                the PDF is password-protected and cannot be read without one.
        """
        ...


@runtime_checkable
class IOCREngine(Protocol):
    """Local OCR extraction of text, bounding boxes, and confidence from image bytes.

    Input is bytes; no stream or filesystem-path abstraction is accepted.
    The provider does not own the overall timeout policy — the owning
    engine wraps every call in its configured timeout envelope.
    """

    async def extract_text(self, image: bytes, options: dict[str, Any]) -> OCRResult:
        """Extract text from image bytes into an `OCRResult`.

        Raises:
            kortex.engines.document_intelligence.exceptions.UnsupportedImageError:
                the payload cannot be decoded as an image.
        """
        ...
