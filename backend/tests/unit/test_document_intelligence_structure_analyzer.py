"""Tests for `StructureAnalyzer` (M4) — pure composition, no I/O, no semantics."""

from __future__ import annotations

from kortex.engines.document_intelligence.models import (
    DocumentLayoutBlock,
    ExtractedTable,
    OCRResult,
    ParsedDocumentResult,
)
from kortex.engines.document_intelligence.structure_analyzer import StructureAnalyzer


def test_pdf_result_input_produces_text_and_table_blocks() -> None:
    parsed = ParsedDocumentResult(
        raw_text="hello world",
        structured_tables=[ExtractedTable(table_id="t1", page_number=2, rows=[["a", "b"]])],
    )
    blocks = StructureAnalyzer().analyze(parsed_result=parsed, ocr_result=None)
    assert any(b.block_type == "text" and b.source == "pdf" for b in blocks)
    assert any(b.block_type == "table" and b.page_number == 2 and b.source == "pdf" for b in blocks)


def test_ocr_result_input_passes_through_layout_blocks() -> None:
    ocr = OCRResult(
        text="hi",
        engine_used="rapidocr-onnxruntime",
        layout_blocks=[
            DocumentLayoutBlock(
                block_type="text", page_number=1, text="hi", bounding_box=(0, 0, 1, 1), confidence=0.9, source="ocr"
            )
        ],
    )
    blocks = StructureAnalyzer().analyze(parsed_result=None, ocr_result=ocr)
    assert len(blocks) == 1
    assert blocks[0].source == "ocr"


def test_both_inputs_combine() -> None:
    parsed = ParsedDocumentResult(raw_text="pdf text")
    ocr = OCRResult(
        text="ocr text",
        engine_used="rapidocr-onnxruntime",
        layout_blocks=[
            DocumentLayoutBlock(block_type="text", page_number=1, text="ocr text", source="ocr", confidence=0.8)
        ],
    )
    blocks = StructureAnalyzer().analyze(parsed_result=parsed, ocr_result=ocr)
    sources = {b.source for b in blocks}
    assert sources == {"pdf", "ocr"}


def test_neither_input_produces_no_blocks() -> None:
    blocks = StructureAnalyzer().analyze(parsed_result=None, ocr_result=None)
    assert blocks == []


def test_deterministic_normalization() -> None:
    parsed = ParsedDocumentResult(
        raw_text="hello",
        structured_tables=[ExtractedTable(table_id="t1", page_number=1, rows=[["x"]])],
    )
    blocks1 = StructureAnalyzer().analyze(parsed_result=parsed, ocr_result=None)
    blocks2 = StructureAnalyzer().analyze(parsed_result=parsed, ocr_result=None)
    assert blocks1 == blocks2


def test_no_semantic_content_is_introduced() -> None:
    """block_type must remain a structural label (text/table), never a
    business classification like "invoice" or "contract"."""
    parsed = ParsedDocumentResult(raw_text="Invoice #123 Total: $500")
    blocks = StructureAnalyzer().analyze(parsed_result=parsed, ocr_result=None)
    assert all(b.block_type in ("text", "table") for b in blocks)
