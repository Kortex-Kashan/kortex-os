"""Local OCR provider implementing `IOCREngine`.

Approved technology: `rapidocr-onnxruntime` using ONNXRuntime's CPU
execution provider. Verified during the dependency spike (this
implementation pass) on Windows/Python 3.12:
- installs from prebuilt wheels for every transitive dependency
  (onnxruntime, opencv-python, numpy, Pillow, shapely, pyclipper) — no
  build-from-source, no subprocess, no system package;
- the detection/classification/recognition ONNX models are bundled inside
  the `rapidocr_onnxruntime` wheel itself — engine construction performs
  no network access (confirmed empirically: construction completed in
  under one second with no download);
- accepts raw image bytes directly — no filesystem path is required.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from kortex.engines.document_intelligence.exceptions import UnsupportedImageError
from kortex.engines.document_intelligence.models import DocumentLayoutBlock, OCRResult

if TYPE_CHECKING:
    from rapidocr_onnxruntime import RapidOCR

# `RapidOCR`'s own import is deferred into `_get_engine()` rather than
# performed at module load time: constructing it loads ONNX models into
# memory, which should only happen lazily, on first real use, matching the
# "Lazy Adapter Loading" convention already established by the Document
# Engine's own adapter registry.
_RapidOCR: Any = None
_IMAGE_DECODE_ERRORS: tuple[type[Exception], ...] = (Exception,)


def _import_rapidocr() -> tuple[Any, tuple[type[Exception], ...]]:
    """Return the `RapidOCR` class and the tuple of exception types that
    mean "this byte payload could not be decoded as an image."

    Verified empirically during the dependency spike: `RapidOCR.__call__`
    raises its own `LoadImageError` only for an unsupported *type*
    (e.g. neither `bytes` nor `np.ndarray`); for genuinely corrupt *image
    bytes* it falls through into Pillow's `Image.open()`, which raises
    `PIL.UnidentifiedImageError` uncaught. Both must be mapped to
    `UnsupportedImageError`.
    """
    global _RapidOCR, _IMAGE_DECODE_ERRORS
    if _RapidOCR is None:
        from PIL import UnidentifiedImageError
        from rapidocr_onnxruntime import RapidOCR
        from rapidocr_onnxruntime.utils.load_image import LoadImageError

        _RapidOCR = RapidOCR
        _IMAGE_DECODE_ERRORS = (LoadImageError, UnidentifiedImageError)
    return _RapidOCR, _IMAGE_DECODE_ERRORS


class RapidOcrProvider:
    """Concrete `IOCREngine` implementation backed by `rapidocr-onnxruntime`."""

    def __init__(self) -> None:
        self._engine: RapidOCR | None = None

    def _get_engine(self) -> RapidOCR:
        rapid_ocr_cls, _ = _import_rapidocr()
        if self._engine is None:
            self._engine = rapid_ocr_cls()
        return self._engine

    async def extract_text(self, image: bytes, options: dict[str, Any]) -> OCRResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._extract_sync, image)

    def _extract_sync(self, image: bytes) -> OCRResult:
        _, image_decode_errors = _import_rapidocr()
        engine = self._get_engine()
        try:
            raw_result, _elapse = engine(image)
        except image_decode_errors as exc:
            raise UnsupportedImageError(f"Image could not be decoded: {exc}") from exc

        if raw_result is None:
            return OCRResult(text="", layout_blocks=[], average_confidence=None, engine_used="rapidocr-onnxruntime")

        blocks: list[DocumentLayoutBlock] = []
        texts: list[str] = []
        confidences: list[float] = []

        for bbox_points, text, confidence in raw_result:
            xs = [point[0] for point in bbox_points]
            ys = [point[1] for point in bbox_points]
            blocks.append(
                DocumentLayoutBlock(
                    block_type="text",
                    page_number=1,
                    text=text,
                    bounding_box=(min(xs), min(ys), max(xs), max(ys)),
                    confidence=float(confidence),
                    source="ocr",
                )
            )
            texts.append(text)
            confidences.append(float(confidence))

        return OCRResult(
            text="\n".join(texts),
            layout_blocks=blocks,
            average_confidence=(sum(confidences) / len(confidences)) if confidences else None,
            engine_used="rapidocr-onnxruntime",
        )
