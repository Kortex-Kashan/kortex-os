"""
KORTEX Knowledge Engine Package.

Directed knowledge graph, versioned record lineage with trust-state
promotion, non-destructive annotations, source ingestion, multi-modal
search, and knowledge pack loading — orchestrated behind the `KnowledgeEngine`
facade (`engine.py`), a `BaseEngine` subclass registering
`kortex.knowledge.*` capabilities with the Kernel like every other KORTEX
System Engine.
"""

from __future__ import annotations

from kortex.engines.knowledge.engine import KnowledgeEngine
from kortex.engines.knowledge.interfaces import (
    IEngineDiagnostics,
    IKnowledgeAnnotationManager,
    IKnowledgeEngine,
    IKnowledgeGraph,
    IKnowledgeRecordManager,
    IKnowledgeSearchEngine,
    IKnowledgeSourceProvider,
)
from kortex.engines.knowledge.models import (
    KnowledgeActorType,
    KnowledgeAnnotation,
    KnowledgeAnnotationType,
    KnowledgeClassification,
    KnowledgeNode,
    KnowledgePack,
    KnowledgeQuery,
    KnowledgeQueryResult,
    KnowledgeRecord,
    KnowledgeRecordStatus,
    KnowledgeRecordType,
    KnowledgeRelationship,
    KnowledgeRelationshipType,
    KnowledgeTrustState,
)

__all__ = [
    "KnowledgeEngine",
    "IKnowledgeEngine",
    "IKnowledgeGraph",
    "IKnowledgeRecordManager",
    "IKnowledgeAnnotationManager",
    "IKnowledgeSourceProvider",
    "IKnowledgeSearchEngine",
    "IEngineDiagnostics",
    "KnowledgeNode",
    "KnowledgeRelationship",
    "KnowledgeRelationshipType",
    "KnowledgeRecord",
    "KnowledgeRecordType",
    "KnowledgeRecordStatus",
    "KnowledgeAnnotation",
    "KnowledgeAnnotationType",
    "KnowledgePack",
    "KnowledgeQuery",
    "KnowledgeQueryResult",
    "KnowledgeActorType",
    "KnowledgeTrustState",
    "KnowledgeClassification",
]
