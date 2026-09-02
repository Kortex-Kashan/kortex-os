"""Fixture-driven tests for `PdfPlumberParser` (M2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.engines.document_intelligence.exceptions import CorruptedDocumentError, EncryptedDocumentError
from kortex.engines.document_intelligence.providers.pdf_parser import PdfPlumberParser

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "document_intelligence"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.mark.asyncio
async def test_normal_text_extraction() -> None:
    parser = PdfPlumberParser()
    result = await parser.parse(_read("normal_text.pdf"), {})
    assert "KORTEX Document Intelligence Fixture" in result.raw_text
    assert "deterministic text extraction fixture" in result.raw_text
    assert result.page_count == 1


@pytest.mark.asyncio
async def test_metadata_extraction() -> None:
    parser = PdfPlumberParser()
    result = await parser.parse(_read("normal_text.pdf"), {})
    assert result.metadata_fields.get("Author") == "KORTEX Test Suite"
    assert result.metadata_fields.get("Title") == "KORTEX Fixture Document"


@pytest.mark.asyncio
async def test_multipage_extraction() -> None:
    parser = PdfPlumberParser()
    result = await parser.parse(_read("multipage.pdf"), {})
    assert result.page_count == 3
    assert "Page 1 of 3" in result.raw_text
    assert "Page 2 of 3" in result.raw_text
    assert "Page 3 of 3" in result.raw_text


@pytest.mark.asyncio
async def test_table_extraction() -> None:
    parser = PdfPlumberParser()
    result = await parser.parse(_read("table.pdf"), {})
    assert len(result.structured_tables) == 1
    table = result.structured_tables[0]
    assert table.page_number == 1
    assert table.rows[0] == ["Item", "Quantity", "Price"]
    assert ["Widget", "10", "5.00"] in table.rows


@pytest.mark.asyncio
async def test_empty_pdf_extraction() -> None:
    parser = PdfPlumberParser()
    result = await parser.parse(_read("empty.pdf"), {})
    assert result.raw_text == ""
    assert result.page_count == 1
    assert result.structured_tables == []


@pytest.mark.asyncio
async def test_malformed_pdf_raises_corrupted_document_error() -> None:
    parser = PdfPlumberParser()
    with pytest.raises(CorruptedDocumentError):
        await parser.parse(_read("malformed.pdf"), {})


@pytest.mark.asyncio
async def test_encrypted_pdf_raises_encrypted_document_error() -> None:
    parser = PdfPlumberParser()
    with pytest.raises(EncryptedDocumentError):
        await parser.parse(_read("encrypted.pdf"), {})


@pytest.mark.asyncio
async def test_deterministic_repeated_parsing() -> None:
    parser = PdfPlumberParser()
    content = _read("normal_text.pdf")
    result1 = await parser.parse(content, {})
    result2 = await parser.parse(content, {})
    assert result1.raw_text == result2.raw_text
    assert result1.metadata_fields == result2.metadata_fields
    assert result1.page_count == result2.page_count
