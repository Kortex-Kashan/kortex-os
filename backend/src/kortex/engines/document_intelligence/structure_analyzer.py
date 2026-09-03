"""Pure structural composition for `kortex.document_intelligence.structure.analyze`.

Deliberately NOT a provider behind `IPDFParser`/`IOCREngine` — it consumes
their already-computed *outputs*, never invokes them, never performs I/O,
and never uses AI. See `engine.py::handle_structure_analyze` for why this
stays a capability rather than a hidden internal composition step.
"""

from __future__ import annotations

from kortex.engines.document_intelligence.models import DocumentLayoutBlock, OCRResult, ParsedDocumentResult


class StructureAnalyzer:
    """Deterministic, side-effect-free normalization into `DocumentLayoutBlock`s."""

    def analyze(
        self,
        parsed_result: ParsedDocumentResult | None,
        ocr_result: OCRResult | None,
    ) -> list[DocumentLayoutBlock]:
        blocks: list[DocumentLayoutBlock] = []

        if parsed_result is not None:
            if parsed_result.raw_text:
                blocks.append(
                    DocumentLayoutBlock(
                        block_type="text",
                        page_number=1,
                        text=parsed_result.raw_text,
                        bounding_box=None,
                        confidence=None,
                        source="pdf",
                    )
                )
            for table in parsed_result.structured_tables:
                blocks.append(
                    DocumentLayoutBlock(
                        block_type="table",
                        page_number=table.page_number,
                        text=None,
                        bounding_box=None,
                        confidence=None,
                        source="pdf",
                    )
                )

        if ocr_result is not None:
            blocks.extend(ocr_result.layout_blocks)

        return blocks
