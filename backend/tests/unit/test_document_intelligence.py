"""Unit tests for Document Intelligence and AI Recommendation Providers (Milestone 6).

Target: 100% pass rate, 100% line coverage for intelligence.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

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
    with pytest.raises(ValidationError):
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


@pytest.mark.asyncio
async def test_document_engine_intelligence_and_recommendation_di() -> None:
    """Test DocumentEngine constructor injection and properties for intelligence & recommendation providers."""
    from kortex.engines.document.engine import DocumentEngine

    # 1. Default engine instantiation provides default providers
    engine_default = DocumentEngine()
    assert isinstance(engine_default.intelligence_provider, IDocumentIntelligenceProvider)
    assert isinstance(engine_default.recommendation_provider, IDocumentRecommendationProvider)
    assert isinstance(engine_default.intelligence_provider, DefaultDocumentIntelligenceProvider)
    assert isinstance(engine_default.recommendation_provider, DefaultDocumentRecommendationProvider)

    # 2. Custom injected provider instances
    class CustomIntelligenceProvider(IDocumentIntelligenceProvider):
        async def extract_concepts(self, document_id: str, version_id: str) -> dict[str, Any]:
            return {"document_id": document_id, "custom": True}

        async def analyze_document(
            self, document_id: str, version_id: str, ontology: dict[str, Any] | None = None
        ) -> Any:
            return {"analyzed": True}

        async def update_intelligence_incrementally(
            self, document_id: str, delta_context: dict[str, Any]
        ) -> dict[str, Any]:
            return {"updated": True}

        async def extract_knowledge_references(self, document_id: str) -> list[str]:
            return ["custom.ref.1"]

    class CustomRecommendationProvider(IDocumentRecommendationProvider):
        async def recommend_template(self, user_intent: str, data_schema: dict[str, Any]) -> list[str]:
            return ["custom.template.v1"]

        async def recommend_operation_profile(self, business_operation: str, user_context: dict[str, Any]) -> str:
            return "profile.custom.v1"

        async def recommend_adapter_pipeline(
            self, profile_id: str, installed_adapters: list[AdapterMetadata]
        ) -> list[str]:
            return ["custom.adapter.v1"]

    custom_intel = CustomIntelligenceProvider()
    custom_rec = CustomRecommendationProvider()

    engine_custom = DocumentEngine(
        intelligence_provider=custom_intel,
        recommendation_provider=custom_rec,
    )

    assert engine_custom.intelligence_provider is custom_intel
    assert engine_custom.recommendation_provider is custom_rec

    # Execute custom methods
    c_res = await engine_custom.intelligence_provider.extract_concepts("doc-custom", "v-1")
    assert c_res["custom"] is True

    t_res = await engine_custom.recommendation_provider.recommend_template("any", {})
    assert t_res == ["custom.template.v1"]


@pytest.mark.asyncio
async def test_intelligence_provider_deterministic_without_ontology() -> None:
    """Test analyze_document when no ontology is supplied."""
    provider = DefaultDocumentIntelligenceProvider()
    model = await provider.analyze_document(document_id="doc-none", version_id="v-none", ontology=None)

    assert model.document_id == "doc-none"
    assert model.version_id == "v-none"
    assert model.extracted_concepts == {"ontology_type": "GenericDocument"}
    assert model.field_values == {}
    assert model.confidence_scores == {"overall": 1.0}
    assert model.knowledge_references == ["knowledge.entity.doc-none"]
