"""KORTEX Document Intelligence Engine — Configuration.

Follows the `SentinelConfig`/`AIEngineRuntimeConfig` precedent: a standalone,
frozen Pydantic model passed as an optional constructor argument, not
registered with `ConfigurationEngine` and not read from `KORTEX_*` env vars
(those are reserved for platform-wide `SystemSettings`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DocumentIntelligenceConfig(BaseModel):
    """Resource and timeout limits for Document Intelligence operations."""

    model_config = ConfigDict(frozen=True)

    operation_timeout_seconds: float = Field(default=30.0, gt=0.0)
    max_input_size_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    max_pdf_pages: int = Field(default=200, gt=0)
    max_ocr_image_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
