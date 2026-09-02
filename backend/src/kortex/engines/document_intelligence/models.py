"""KORTEX Document Intelligence Engine — Domain Models.

Pydantic v2, frozen where the platform convention calls for it (mirrors
`kortex.engines.document.models` and `kortex.engines.sentinel.models`).

Tenant identity is deliberately absent from every model in this module.
`DocumentParseRequest` carries no credential field of any kind — no
`session_token`, no `principal`. The authoritative tenant/principal is
delivered exclusively via the dispatcher-injected `CapabilityExecutionContext`
(see `engine.py`), never independently re-derived by this engine from a
token embedded in a caller-supplied request model. This is the corrected
design following KORTEX Platform Security — Capability Identity
Propagation: the previous `DocumentParseRequest.session_token` field was
exactly the vector an authenticated Tenant-A caller could use to smuggle a
separately-valid Tenant-B token and have this engine execute as Tenant B.
Result models carry no tenant field at all: they are transient values
returned to an already tenant-authorized caller, so duplicating tenant
identity onto them would be redundant, not defense-in-depth.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExtractedTable(BaseModel):
    """A single table extracted from a document page."""

    model_config = ConfigDict(frozen=True)

    table_id: str
    page_number: int = Field(ge=1)
    rows: list[list[str]] = Field(default_factory=list)


class DocumentLayoutBlock(BaseModel):
    """A single structural/spatial region of a document (paragraph, table, etc.).

    Deterministic, structural information only — never a semantic label
    (e.g. "invoice", "signature"). `source` records which provider produced
    the block (`pdf` or `ocr`), not what the block *means*.
    """

    model_config = ConfigDict(frozen=True)

    block_type: str = Field(description='Structural kind, e.g. "text" or "table". Never a business category.')
    page_number: int = Field(ge=1)
    text: str | None = None
    bounding_box: tuple[float, float, float, float] | None = Field(
        default=None, description="(x0, y0, x1, y1) in source coordinate space, when known."
    )
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: str = Field(description='Provenance of this block: "pdf" or "ocr".')


class ParsedDocumentResult(BaseModel):
    """Deterministic output of `IPDFParser.parse()`."""

    model_config = ConfigDict(frozen=True)

    document_id: str | None = None
    version_id: str | None = None
    raw_text: str = ""
    structured_tables: list[ExtractedTable] = Field(default_factory=list)
    metadata_fields: dict[str, Any] = Field(default_factory=dict)
    page_count: int = 0
    language: str | None = None


class OCRResult(BaseModel):
    """Output of `IOCREngine.extract_text()`."""

    model_config = ConfigDict(frozen=True)

    document_id: str | None = None
    version_id: str | None = None
    text: str = ""
    layout_blocks: list[DocumentLayoutBlock] = Field(default_factory=list)
    average_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    engine_used: str


class DocumentParseRequest(BaseModel):
    """Input envelope for `pdf.parse` and `ocr.extract`.

    Invariants (enforced below, not merely documented):
    - Exactly one of (`bucket_name` + `object_key`) or `content` is supplied.
    - `version_id` requires `document_id`.
    - `document_id`/`version_id` are correlation metadata only — the engine
      never resolves them to a storage location itself (Article 6); the
      caller is responsible for already knowing `bucket_name`/`object_key`.
    - Carries no credential of any kind. Tenant authority comes exclusively
      from the `CapabilityExecutionContext` the Kernel dispatcher injects
      into the handler — never from this model.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str | None = None
    version_id: str | None = None
    bucket_name: str | None = None
    object_key: str | None = None
    content: bytes | None = None
    mime_type: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_input_union(self) -> DocumentParseRequest:
        has_bucket = self.bucket_name is not None
        has_key = self.object_key is not None
        if has_bucket != has_key:
            raise ValueError("bucket_name and object_key must both be supplied together, or neither.")
        has_object_ref = has_bucket and has_key
        has_content = self.content is not None
        if has_object_ref and has_content:
            raise ValueError("Exactly one of (bucket_name+object_key) or content must be supplied, not both.")
        if not has_object_ref and not has_content:
            raise ValueError("Exactly one of (bucket_name+object_key) or content must be supplied.")
        if self.version_id is not None and self.document_id is None:
            raise ValueError("version_id requires document_id to also be set.")
        return self


class StructureAnalysisRequest(BaseModel):
    """Input envelope for `structure.analyze`.

    Pure composition over already-computed results — carries no storage
    reference and no `session_token`, because the handler performs no
    tenant-scoped Storage I/O of its own (see `structure_analyzer.py`).
    """

    model_config = ConfigDict(frozen=True)

    parsed_result: ParsedDocumentResult | None = None
    ocr_result: OCRResult | None = None

    @model_validator(mode="after")
    def _require_at_least_one(self) -> StructureAnalysisRequest:
        if self.parsed_result is None and self.ocr_result is None:
            raise ValueError("At least one of parsed_result or ocr_result must be supplied.")
        return self
