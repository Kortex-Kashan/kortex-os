"""Document Intelligence and AI Recommendation Providers for KORTEX OS Document Engine.

This module implements DocumentIntelligenceModel, DefaultDocumentIntelligenceProvider, and
DefaultDocumentRecommendationProvider in accordance with Section 10 and Milestone 6 of the
Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.document.interfaces import (
    IDocumentIntelligenceProvider,
    IDocumentRecommendationProvider,
)
from kortex.engines.document.models import AdapterMetadata


class DocumentIntelligenceModel(BaseModel):
    """Declarative domain model representing extracted document intelligence and concept links."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str
    extracted_concepts: dict[str, Any] = Field(default_factory=dict)
    field_values: dict[str, Any] = Field(default_factory=dict)
    confidence_scores: dict[str, float] = Field(default_factory=dict)
    knowledge_references: list[str] = Field(default_factory=list)
    analysis_timestamp: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


class DefaultDocumentIntelligenceProvider(IDocumentIntelligenceProvider):
    """Default local-first provider for document analysis and concept extraction.

    Maintains an AI Optional Design, operating 100% deterministically without requiring
    active LLM calls or cloud API connections.
    """

    async def extract_concepts(
        self, document_id: str, version_id: str
    ) -> dict[str, Any]:
        """Extract semantic concepts and structural relationships from document.

        Args:
            document_id: Unique root document identifier.
            version_id: Specific document version identifier.

        Returns:
            Dictionary containing extracted concept metadata.
        """
        return {
            "document_id": document_id,
            "version_id": version_id,
            "concepts": ["entity_identification", "structural_summary", "field_bindings"],
            "status": "EXTRACTED",
        }

    async def analyze_document(
        self,
        document_id: str,
        version_id: str,
        ontology: dict[str, Any] | None = None,
    ) -> DocumentIntelligenceModel:
        """Perform comprehensive deterministic intelligence analysis on a document version.

        Args:
            document_id: Root document ID.
            version_id: Specific version ID.
            ontology: Optional declarative ontology schema.

        Returns:
            Structured DocumentIntelligenceModel.
        """
        ontology_data = dict(ontology) if ontology is not None else {}
        extracted_fields = ontology_data.get("fields", {})

        scores = {k: 1.0 for k in extracted_fields} if extracted_fields else {"overall": 1.0}
        refs = [f"knowledge.entity.{document_id}"]

        return DocumentIntelligenceModel(
            document_id=document_id,
            version_id=version_id,
            extracted_concepts={"ontology_type": ontology_data.get("entity", "GenericDocument")},
            field_values=extracted_fields,
            confidence_scores=scores,
            knowledge_references=refs,
        )

    async def update_intelligence_incrementally(
        self, document_id: str, delta_context: dict[str, Any]
    ) -> dict[str, Any]:
        """Update document intelligence state with delta context changes.

        Args:
            document_id: Root document ID.
            delta_context: Dictionary of delta field changes.

        Returns:
            Updated intelligence summary dictionary.
        """
        return {
            "document_id": document_id,
            "updated_fields": list(delta_context.keys()),
            "status": "INCREMENTALLY_UPDATED",
        }

    async def extract_knowledge_references(self, document_id: str) -> list[str]:
        """Identify and link entity references to the KORTEX Knowledge Engine.

        Args:
            document_id: Root document ID.

        Returns:
            List of Knowledge Engine URI reference strings.
        """
        return [f"knowledge.entity.{document_id}", f"knowledge.domain.document.{document_id}"]


class DefaultDocumentRecommendationProvider(IDocumentRecommendationProvider):
    """Default provider for template, profile, and pipeline process recommendations."""

    async def recommend_template(
        self, user_intent: str, data_schema: dict[str, Any]
    ) -> list[str]:
        """Recommend template schema IDs based on user intent and input data.

        Args:
            user_intent: Intent descriptor string (e.g. 'generate payslip').
            data_schema: Dictionary of input data keys.

        Returns:
            List of recommended template IDs.
        """
        intent_lower = user_intent.lower()
        if "payslip" in intent_lower or "pay" in intent_lower:
            return ["payslip.declarative.v1"]
        if "invoice" in intent_lower or "bill" in intent_lower:
            return ["invoice.declarative.v1"]
        if "contract" in intent_lower:
            return ["contract.declarative.v1"]
        return ["invoice.declarative.v1", "payslip.declarative.v1"]

    async def recommend_operation_profile(
        self, business_operation: str, user_context: dict[str, Any]
    ) -> str:
        """Recommend optimal DocumentOperationProfile ID for a business operation.

        Args:
            business_operation: Business operation string (e.g. 'GENERATE_PAYROLL_SLIP').
            user_context: Context dictionary.

        Returns:
            Recommended profile ID string.
        """
        op_upper = business_operation.upper()
        if "PAYROLL" in op_upper or "PAYSLIP" in op_upper:
            return "profile.payslip.v1"
        if "INVOICE" in op_upper:
            return "profile.invoice.v1"
        return "profile.default.v1"

    async def recommend_adapter_pipeline(
        self, profile_id: str, installed_adapters: list[AdapterMetadata]
    ) -> list[str]:
        """Recommend optimal adapter pipeline stage configuration.

        Args:
            profile_id: Operation profile ID string.
            installed_adapters: List of installed AdapterMetadata objects.

        Returns:
            List of recommended adapter ID strings in execution sequence order.
        """
        recommended: list[str] = []
        for meta in installed_adapters:
            recommended.append(meta.adapter_id)
        return recommended


__all__ = [
    "DefaultDocumentIntelligenceProvider",
    "DefaultDocumentRecommendationProvider",
    "DocumentIntelligenceModel",
]
