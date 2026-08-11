"""Unit tests for Document Intelligence and AI Recommendation Providers (Milestone 6).

Target: 100% pass rate, 100% line coverage for intelligence.py.
"""

from __future__ import annotations

import pytest

from kortex.engines.document.intelligence import (
    DefaultDocumentIntelligenceProvider,
    DefaultDocumentRecommendationProvider,
    DocumentIntelligenceModel,
)
from kortex.engines.document.interfaces import (
    IDocumentIntelligenceProvider,
    IDocumentRecommendationProvider,
)
from kortex.engines.document.models import AdapterCapability, AdapterMetadata


@pytest.mark.asyncio
async def test_document_intelligence_model_and_protocol() -> None:
    """Test DocumentIntelligenceModel immutability and IDocumentIntelligenceProvider protocol implementation."""
    provider = DefaultDocumentIntelligenceProvider()
    assert isinstance(provider, IDocumentIntelligenceProvider)

    concepts = await provider.extract_concepts("doc-1", "v-1")
    assert concepts["document_id"] == "doc-1"
    assert concepts["status"] == "EXTRACTED"
    assert "entity_identification" in concepts["concepts"]

    model = await provider.analyze_document(
        document_id="doc-1",
        version_id="v-1",
        ontology={"entity": "Payslip", "fields": {"net_pay": 5000.0}},
    )
    assert isinstance(model, DocumentIntelligenceModel)
    assert model.document_id == "doc-1"
    assert model.field_values["net_pay"] == 5000.0
    assert model.confidence_scores["net_pay"] == 1.0
    assert len(model.knowledge_references) > 0

    # Immutability check
    with pytest.raises(Exception):
        model.document_id = "doc-mutated"  # type: ignore[misc]

    delta = await provider.update_intelligence_incrementally("doc-1", {"net_pay": 5500.0})
    assert delta["status"] == "INCREMENTALLY_UPDATED"
    assert "net_pay" in delta["updated_fields"]

    refs = await provider.extract_knowledge_references("doc-1")
    assert len(refs) == 2
    assert "knowledge.entity.doc-1" in refs


@pytest.mark.asyncio
async def test_default_document_recommendation_provider() -> None:
    """Test DefaultDocumentRecommendationProvider recommendations and protocol implementation."""
    recommender = DefaultDocumentRecommendationProvider()
    assert isinstance(recommender, IDocumentRecommendationProvider)

    # Template recommendations
    rec_pay = await recommender.recommend_template("generate employee payslip", {"employee_id": "str"})
    assert rec_pay == ["payslip.declarative.v1"]

    rec_inv = await recommender.recommend_template("generate sales invoice", {"invoice_id": "str"})
    assert rec_inv == ["invoice.declarative.v1"]

    rec_contract = await recommender.recommend_template("draft employment contract", {})
    assert rec_contract == ["contract.declarative.v1"]

    rec_default = await recommender.recommend_template("generic intent", {})
    assert "invoice.declarative.v1" in rec_default

    # Profile recommendations
    prof_pay = await recommender.recommend_operation_profile("GENERATE_PAYROLL_SLIP", {})
    assert prof_pay == "profile.payslip.v1"

    prof_inv = await recommender.recommend_operation_profile("GENERATE_INVOICE", {})
    assert prof_inv == "profile.invoice.v1"

    prof_gen = await recommender.recommend_operation_profile("UNKNOWN_OP", {})
    assert prof_gen == "profile.default.v1"

    # Pipeline recommendations
    adapter_meta = AdapterMetadata(
        adapter_id="kortex.adapter.pdf",
        display_name="PDF Adapter",
        vendor="Kortex",
        author="Dev",
        version="1.0.0",
        license="MIT",
        description="PDF",
        supported_capabilities=[AdapterCapability.GENERATE],
    )
    pipeline_rec = await recommender.recommend_adapter_pipeline("profile.payslip.v1", [adapter_meta])
    assert pipeline_rec == ["kortex.adapter.pdf"]
