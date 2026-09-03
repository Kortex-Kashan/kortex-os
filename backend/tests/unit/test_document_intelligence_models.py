"""Contract tests for Document Intelligence models (M1).

KORTEX Platform Security — Capability Identity Propagation: `DocumentParseRequest`
carries no credential field at all (no `session_token`, no `principal`) — see
`test_request_carries_no_credential_fields`. Tenant/identity authority is
delivered exclusively via the dispatcher-injected `CapabilityExecutionContext`
(see `test_document_intelligence_security.py` for the end-to-end proof).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kortex.engines.document_intelligence.models import (
    DocumentLayoutBlock,
    DocumentParseRequest,
    ExtractedTable,
    OCRResult,
    ParsedDocumentResult,
    StructureAnalysisRequest,
)


def test_request_with_object_reference_is_valid() -> None:
    req = DocumentParseRequest(bucket_name="documents", object_key="doc-1.pdf", mime_type="application/pdf")
    assert req.bucket_name == "documents"
    assert req.content is None


def test_request_with_raw_content_is_valid() -> None:
    req = DocumentParseRequest(content=b"%PDF-1.4", mime_type="application/pdf")
    assert req.content == b"%PDF-1.4"
    assert req.bucket_name is None


def test_request_rejects_both_object_reference_and_content() -> None:
    with pytest.raises(ValidationError, match="not both"):
        DocumentParseRequest(
            bucket_name="documents",
            object_key="doc-1.pdf",
            content=b"%PDF-1.4",
            mime_type="application/pdf",
        )


def test_request_rejects_neither_object_reference_nor_content() -> None:
    with pytest.raises(ValidationError, match="Exactly one of"):
        DocumentParseRequest(mime_type="application/pdf")


def test_request_rejects_partial_object_reference() -> None:
    with pytest.raises(ValidationError, match="must both be supplied together"):
        DocumentParseRequest(bucket_name="documents", mime_type="application/pdf")


def test_request_rejects_version_id_without_document_id() -> None:
    with pytest.raises(ValidationError, match="requires document_id"):
        DocumentParseRequest(content=b"%PDF-1.4", version_id="v1", mime_type="application/pdf")


def test_request_requires_mime_type() -> None:
    with pytest.raises(ValidationError):
        DocumentParseRequest(content=b"%PDF-1.4", mime_type="")


def test_request_document_id_and_version_id_are_correlation_only() -> None:
    req = DocumentParseRequest(
        document_id="doc-1",
        version_id="v1",
        content=b"%PDF-1.4",
        mime_type="application/pdf",
    )
    assert req.document_id == "doc-1"
    assert req.version_id == "v1"


def test_request_carries_no_credential_fields() -> None:
    """Security-critical: the model must not expose any field a handler
    could independently trust as identity — no `session_token`, no
    `principal`, no `tenant_id`. Identity comes exclusively from the
    dispatcher-injected `CapabilityExecutionContext` (see engine.py)."""
    assert "session_token" not in DocumentParseRequest.model_fields
    assert "principal" not in DocumentParseRequest.model_fields
    assert "tenant_id" not in DocumentParseRequest.model_fields


def test_result_models_carry_no_tenant_field() -> None:
    assert "tenant_id" not in ParsedDocumentResult.model_fields
    assert "tenant_id" not in OCRResult.model_fields
    assert "tenant_id" not in DocumentLayoutBlock.model_fields
    assert "tenant_id" not in ExtractedTable.model_fields


def test_structure_request_requires_at_least_one_input() -> None:
    with pytest.raises(ValidationError, match="At least one"):
        StructureAnalysisRequest()


def test_structure_request_accepts_parsed_result_only() -> None:
    req = StructureAnalysisRequest(parsed_result=ParsedDocumentResult(raw_text="hello"))
    assert req.parsed_result is not None
    assert req.ocr_result is None


def test_structure_request_accepts_ocr_result_only() -> None:
    req = StructureAnalysisRequest(ocr_result=OCRResult(text="hello", engine_used="rapidocr-onnxruntime"))
    assert req.ocr_result is not None


def test_structure_request_accepts_both() -> None:
    req = StructureAnalysisRequest(
        parsed_result=ParsedDocumentResult(raw_text="hello"),
        ocr_result=OCRResult(text="hello", engine_used="rapidocr-onnxruntime"),
    )
    assert req.parsed_result is not None
    assert req.ocr_result is not None


def test_models_are_frozen() -> None:
    result = ParsedDocumentResult(raw_text="hello")
    with pytest.raises(ValidationError):
        result.raw_text = "changed"  # type: ignore[misc]
