"""Fixture-driven tests for `RapidOcrProvider` (M3)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kortex.engines.document_intelligence.exceptions import UnsupportedImageError
from kortex.engines.document_intelligence.providers.ocr_provider import RapidOcrProvider

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "document_intelligence"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.mark.asyncio
async def test_clear_image_text_extraction() -> None:
    provider = RapidOcrProvider()
    result = await provider.extract_text(_read("clear_text.png"), {})
    assert "KORTEX" in result.text
    assert result.engine_used == "rapidocr-onnxruntime"


@pytest.mark.asyncio
async def test_bounding_boxes_present() -> None:
    provider = RapidOcrProvider()
    result = await provider.extract_text(_read("clear_text.png"), {})
    assert len(result.layout_blocks) >= 1
    block = result.layout_blocks[0]
    assert block.bounding_box is not None
    assert len(block.bounding_box) == 4
    assert block.source == "ocr"


@pytest.mark.asyncio
async def test_confidence_present() -> None:
    provider = RapidOcrProvider()
    result = await provider.extract_text(_read("clear_text.png"), {})
    assert result.average_confidence is not None
    assert 0.0 <= result.average_confidence <= 1.0
    assert all(block.confidence is not None for block in result.layout_blocks)


@pytest.mark.asyncio
async def test_non_text_image_returns_empty_result_not_error() -> None:
    provider = RapidOcrProvider()
    result = await provider.extract_text(_read("non_text.png"), {})
    assert result.text == ""
    assert result.layout_blocks == []


@pytest.mark.asyncio
async def test_malformed_image_raises_unsupported_image_error() -> None:
    provider = RapidOcrProvider()
    with pytest.raises(UnsupportedImageError):
        await provider.extract_text(_read("malformed.png"), {})


@pytest.mark.asyncio
async def test_timeout_is_enforced_by_caller_via_wait_for() -> None:
    """The provider does not own timeout policy (see interfaces.py) — this
    proves a slow provider call can be bounded by `asyncio.wait_for` at the
    call site, exactly as `DocumentIntelligenceEngine` does."""

    class _SlowProvider:
        async def extract_text(self, image: bytes, options: dict[str, object]) -> None:
            await asyncio.sleep(5)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(_SlowProvider().extract_text(b"x", {}), timeout=0.05)


@pytest.mark.asyncio
async def test_deterministic_behavior_on_repeated_extraction() -> None:
    provider = RapidOcrProvider()
    content = _read("clear_text.png")
    result1 = await provider.extract_text(content, {})
    result2 = await provider.extract_text(content, {})
    assert result1.text == result2.text
