"""Deterministic local PDF parser implementing `IPDFParser`.

Uses `pdfplumber` (pure Python, built on `pdfminer.six` — no subprocess, no
system package, no native-binary dependency). Verified during the M3/M2
dependency spike: installs from a prebuilt wheel chain on Windows/Python
3.12 with zero build-from-source steps.

Runs the synchronous `pdfplumber` call in a thread executor so `parse()`
itself does not block the event loop — this is the provider's own
responsibility to be non-blocking; the *timeout* envelope around the whole
call is owned by `DocumentIntelligenceEngine`, not by this provider.
"""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING, Any

from kortex.engines.document_intelligence.exceptions import CorruptedDocumentError, EncryptedDocumentError
from kortex.engines.document_intelligence.models import ExtractedTable, ParsedDocumentResult

if TYPE_CHECKING:
    # Referenced via its defining submodule, not the `pdfplumber.PDF`
    # top-level re-export: mypy `strict` implies `--no-implicit-reexport`,
    # which does not resolve implicit re-exports from third-party packages.
    from pdfplumber.pdf import PDF


class PdfPlumberParser:
    """Concrete `IPDFParser` implementation backed by `pdfplumber`.

    `pdfplumber`/`pdfminer` are imported lazily (deferred to first real
    use) rather than at module load, so a missing or broken install of
    this one dependency cannot prevent the Kernel from booting every other
    engine — mirroring `RapidOcrProvider`'s identical deferred-import
    defense.
    """

    async def parse(self, content: bytes, options: dict[str, Any]) -> ParsedDocumentResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._parse_sync, content)

    def _parse_sync(self, content: bytes) -> ParsedDocumentResult:
        import pdfplumber
        from pdfminer.pdfdocument import PDFEncryptionError
        from pdfplumber.utils.exceptions import PdfminerException

        try:
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                return self._extract(pdf)
        except PdfminerException as exc:
            # `pdfplumber` wraps the original `pdfminer` exception into a
            # generic `PdfminerException`, but preserves the specific
            # original type via implicit exception chaining (`__context__`,
            # not `__cause__` — verified empirically during the dependency
            # spike). `PDFEncryptionError` (and its `PDFPasswordIncorrect`
            # subclass) is the only reliable, non-message-string-based way
            # to distinguish "encrypted, needs a password" from "genuinely
            # malformed."
            if isinstance(exc.__context__, PDFEncryptionError):
                raise EncryptedDocumentError(
                    "PDF is password-protected; content cannot be read without a password."
                ) from exc
            raise CorruptedDocumentError(f"PDF could not be parsed: {exc}") from exc

    def _extract(self, pdf: PDF) -> ParsedDocumentResult:
        text_parts: list[str] = []
        tables: list[ExtractedTable] = []
        table_counter = 0

        for page_number, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text:
                text_parts.append(page_text)

            for raw_table in page.extract_tables():
                table_counter += 1
                tables.append(
                    ExtractedTable(
                        table_id=f"table-{table_counter}",
                        page_number=page_number,
                        rows=[[cell if cell is not None else "" for cell in row] for row in raw_table],
                    )
                )

        metadata = {k: v for k, v in (pdf.metadata or {}).items() if v is not None}

        return ParsedDocumentResult(
            raw_text="\n".join(text_parts),
            structured_tables=tables,
            metadata_fields=metadata,
            page_count=len(pdf.pages),
            language=None,
        )
