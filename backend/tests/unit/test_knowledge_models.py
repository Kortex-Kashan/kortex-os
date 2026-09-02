"""Unit tests for Knowledge Engine domain models (Milestone M1 — redesigned scope).

Verifies model validation rejects malformed knowledge data and that
immutability/enum contracts hold, per the Chief Architect's redesigned
Milestone M1 domain model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

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
from kortex.engines.security.models import PrincipalType

# -- KnowledgeRelationshipType ------------------------------------------------


def test_knowledge_relationship_type_has_expected_members() -> None:
    assert set(KnowledgeRelationshipType) == {
        KnowledgeRelationshipType.DERIVED_FROM,
        KnowledgeRelationshipType.SUPERSEDES,
        KnowledgeRelationshipType.RELATES_TO,
        KnowledgeRelationshipType.CONTAINS,
        KnowledgeRelationshipType.REFERENCES,
    }


# -- KnowledgeActorType --------------------------------------------------------


def test_knowledge_actor_type_values_align_with_security_principal_type() -> None:
    """Verifies alignment with the real Security Engine vocabulary without a
    production-code cross-engine import (see models.py module docstring)."""
    assert {member.value for member in KnowledgeActorType} == {member.value for member in PrincipalType}


# -- KnowledgeRecordType / KnowledgeTrustState / KnowledgeClassification -------
# -- KnowledgeRecordStatus / KnowledgeAnnotationType ---------------------------


def test_knowledge_record_type_has_expected_members() -> None:
    assert set(KnowledgeRecordType) == {
        KnowledgeRecordType.FACT,
        KnowledgeRecordType.DECISION,
        KnowledgeRecordType.PROCEDURE,
        KnowledgeRecordType.HISTORICAL_STATE,
    }


def test_knowledge_trust_state_has_expected_members() -> None:
    assert set(KnowledgeTrustState) == {
        KnowledgeTrustState.SOURCE_EVIDENCE,
        KnowledgeTrustState.AI_CANDIDATE,
        KnowledgeTrustState.HUMAN_CONFIRMED,
        KnowledgeTrustState.HUMAN_CORRECTED,
    }


def test_knowledge_classification_has_expected_members() -> None:
    assert set(KnowledgeClassification) == {
        KnowledgeClassification.PUBLIC,
        KnowledgeClassification.INTERNAL,
        KnowledgeClassification.CONFIDENTIAL,
        KnowledgeClassification.RESTRICTED,
    }


def test_knowledge_record_status_has_expected_members() -> None:
    assert set(KnowledgeRecordStatus) == {
        KnowledgeRecordStatus.CURRENT,
        KnowledgeRecordStatus.SUPERSEDED,
        KnowledgeRecordStatus.ARCHIVED,
        KnowledgeRecordStatus.DEPRECATED,
    }


def test_knowledge_annotation_type_has_expected_members() -> None:
    assert set(KnowledgeAnnotationType) == {
        KnowledgeAnnotationType.REMARK,
        KnowledgeAnnotationType.CORRECTION,
        KnowledgeAnnotationType.CONTEXT,
    }


# -- KnowledgeNode -------------------------------------------------------------


def test_knowledge_node_valid_construction() -> None:
    node = KnowledgeNode(
        node_id="node-1",
        tenant_id="tenant-a",
        entity_type="document",
        label="Q3 Financial Report",
        properties={"department": "finance"},
        vector_embedding=[0.1, 0.2, 0.3],
    )
    assert node.node_id == "node-1"
    assert node.tenant_id == "tenant-a"
    assert node.properties == {"department": "finance"}
    assert node.vector_embedding == [0.1, 0.2, 0.3]


def test_knowledge_node_defaults_properties_and_embedding() -> None:
    node = KnowledgeNode(
        node_id="node-1",
        tenant_id="tenant-a",
        entity_type="document",
        label="Q3 Financial Report",
    )
    assert node.properties == {}
    assert node.vector_embedding is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("node_id", ""),
        ("tenant_id", ""),
        ("entity_type", ""),
        ("label", ""),
    ],
)
def test_knowledge_node_rejects_empty_required_strings(field: str, value: str) -> None:
    kwargs = {
        "node_id": "node-1",
        "tenant_id": "tenant-a",
        "entity_type": "document",
        "label": "Q3 Financial Report",
    }
    kwargs[field] = value
    with pytest.raises(ValidationError):
        KnowledgeNode(**kwargs)


def test_knowledge_node_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        KnowledgeNode.model_validate({"tenant_id": "tenant-a", "entity_type": "document", "label": "Report"})


def test_knowledge_node_serialization_round_trip() -> None:
    node = KnowledgeNode(
        node_id="node-1",
        tenant_id="tenant-a",
        entity_type="document",
        label="Q3 Financial Report",
        properties={"department": "finance"},
        vector_embedding=[0.1, 0.2],
    )
    restored = KnowledgeNode.model_validate(node.model_dump())
    assert restored == node


# -- KnowledgeRelationship -----------------------------------------------------


def test_knowledge_relationship_valid_construction() -> None:
    relationship = KnowledgeRelationship(
        relationship_id="rel-1",
        tenant_id="tenant-a",
        source_node_id="node-1",
        target_node_id="node-2",
        relationship_type=KnowledgeRelationshipType.RELATES_TO,
        weight=2.5,
        metadata={"reason": "reference"},
    )
    assert relationship.relationship_type == KnowledgeRelationshipType.RELATES_TO
    assert relationship.weight == 2.5
    assert relationship.metadata == {"reason": "reference"}


def test_knowledge_relationship_defaults_weight_and_metadata() -> None:
    relationship = KnowledgeRelationship(
        relationship_id="rel-1",
        tenant_id="tenant-a",
        source_node_id="node-1",
        target_node_id="node-2",
        relationship_type=KnowledgeRelationshipType.REFERENCES,
    )
    assert relationship.weight == 1.0
    assert relationship.metadata == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("relationship_id", ""),
        ("tenant_id", ""),
        ("source_node_id", ""),
        ("target_node_id", ""),
    ],
)
def test_knowledge_relationship_rejects_empty_required_strings(field: str, value: str) -> None:
    kwargs = {
        "relationship_id": "rel-1",
        "tenant_id": "tenant-a",
        "source_node_id": "node-1",
        "target_node_id": "node-2",
        "relationship_type": KnowledgeRelationshipType.RELATES_TO,
    }
    kwargs[field] = value
    with pytest.raises(ValidationError):
        KnowledgeRelationship(**kwargs)


def test_knowledge_relationship_rejects_invalid_relationship_type() -> None:
    with pytest.raises(ValidationError):
        KnowledgeRelationship(
            relationship_id="rel-1",
            tenant_id="tenant-a",
            source_node_id="node-1",
            target_node_id="node-2",
            relationship_type="NOT_A_TYPE",
        )


def test_knowledge_relationship_serialization_round_trip() -> None:
    relationship = KnowledgeRelationship(
        relationship_id="rel-1",
        tenant_id="tenant-a",
        source_node_id="node-1",
        target_node_id="node-2",
        relationship_type=KnowledgeRelationshipType.SUPERSEDES,
        weight=0.75,
        metadata={"note": "v2 supersedes v1"},
    )
    restored = KnowledgeRelationship.model_validate(relationship.model_dump())
    assert restored == relationship


# -- KnowledgeRecord ------------------------------------------------------------


def _make_record(**overrides: object) -> KnowledgeRecord:
    kwargs: dict[str, Any] = {
        "record_id": "rec-1",
        "tenant_id": "tenant-a",
        "version_id": "v1",
        "record_type": KnowledgeRecordType.DECISION,
        "trust_state": KnowledgeTrustState.HUMAN_CONFIRMED,
        "created_by": "user-1",
        "created_by_type": KnowledgeActorType.USER,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    kwargs.update(overrides)
    return KnowledgeRecord(**kwargs)  # type: ignore[arg-type]


def test_knowledge_record_valid_construction() -> None:
    record = _make_record(
        parent_version_id="v0",
        lineage_path=["v0"],
        content={"decision": "office closes at 5pm"},
        classification=KnowledgeClassification.CONFIDENTIAL,
        status=KnowledgeRecordStatus.CURRENT,
        successor_version_id=None,
    )
    assert record.record_id == "rec-1"
    assert record.record_type == KnowledgeRecordType.DECISION
    assert record.trust_state == KnowledgeTrustState.HUMAN_CONFIRMED
    assert record.classification == KnowledgeClassification.CONFIDENTIAL
    assert record.lineage_path == ["v0"]
    assert record.content == {"decision": "office closes at 5pm"}


def test_knowledge_record_defaults_classification_content_status_and_lineage() -> None:
    record = _make_record()
    assert record.classification == KnowledgeClassification.INTERNAL
    assert record.content == {}
    assert record.status == KnowledgeRecordStatus.CURRENT
    assert record.lineage_path == []
    assert record.parent_version_id is None
    assert record.successor_version_id is None


def test_knowledge_record_provenance_is_optional() -> None:
    """No field on `KnowledgeRecord` requires an external source — manually
    authored organizational knowledge with no source must remain
    representable (redesigned M1 scope, provenance requirement)."""
    record = _make_record()
    assert record.parent_version_id is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("record_id", ""),
        ("tenant_id", ""),
        ("version_id", ""),
        ("created_by", ""),
    ],
)
def test_knowledge_record_rejects_empty_required_strings(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _make_record(**{field: value})


def test_knowledge_record_rejects_missing_trust_state() -> None:
    with pytest.raises(ValidationError):
        KnowledgeRecord.model_validate(
            {
                "record_id": "rec-1",
                "tenant_id": "tenant-a",
                "version_id": "v1",
                "record_type": KnowledgeRecordType.FACT,
                "created_by": "user-1",
                "created_by_type": KnowledgeActorType.USER,
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            }
        )


def test_knowledge_record_rejects_invalid_trust_state() -> None:
    with pytest.raises(ValidationError):
        _make_record(trust_state="NOT_A_STATE")


def test_knowledge_record_is_frozen() -> None:
    record = _make_record()
    with pytest.raises(ValidationError):
        record.status = KnowledgeRecordStatus.SUPERSEDED


def test_knowledge_record_serialization_round_trip() -> None:
    record = _make_record(content={"decision": "office closes at 5pm"}, lineage_path=["v0"])
    restored = KnowledgeRecord.model_validate(record.model_dump())
    assert restored == record


# -- KnowledgeAnnotation --------------------------------------------------------


def _make_annotation(**overrides: object) -> KnowledgeAnnotation:
    kwargs: dict[str, Any] = {
        "annotation_id": "ann-1",
        "tenant_id": "tenant-a",
        "target_record_id": "rec-1",
        "annotation_type": KnowledgeAnnotationType.REMARK,
        "actor_id": "user-1",
        "actor_type": KnowledgeActorType.USER,
        "content": "This decision was made after the Q3 review.",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    kwargs.update(overrides)
    return KnowledgeAnnotation(**kwargs)  # type: ignore[arg-type]


def test_knowledge_annotation_valid_construction() -> None:
    annotation = _make_annotation(annotation_type=KnowledgeAnnotationType.CORRECTION)
    assert annotation.annotation_type == KnowledgeAnnotationType.CORRECTION
    assert annotation.actor_type == KnowledgeActorType.USER
    assert annotation.content == "This decision was made after the Q3 review."


def test_knowledge_annotation_defaults_supersedes_annotation_id() -> None:
    annotation = _make_annotation()
    assert annotation.supersedes_annotation_id is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("annotation_id", ""),
        ("tenant_id", ""),
        ("target_record_id", ""),
        ("actor_id", ""),
        ("content", ""),
    ],
)
def test_knowledge_annotation_rejects_empty_required_strings(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _make_annotation(**{field: value})


def test_knowledge_annotation_rejects_invalid_annotation_type() -> None:
    with pytest.raises(ValidationError):
        _make_annotation(annotation_type="NOT_A_TYPE")


def test_knowledge_annotation_is_frozen() -> None:
    annotation = _make_annotation()
    with pytest.raises(ValidationError):
        annotation.content = "changed"


def test_knowledge_annotation_serialization_round_trip() -> None:
    annotation = _make_annotation(supersedes_annotation_id="ann-0")
    restored = KnowledgeAnnotation.model_validate(annotation.model_dump())
    assert restored == annotation


# -- KnowledgePack --------------------------------------------------------------


def test_knowledge_pack_valid_construction() -> None:
    pack = KnowledgePack(
        asset_id="pack-1",
        tenant_id="tenant-a",
        manifest={"name": "hr-ontology"},
        checksum_sha256="a" * 64,
        digital_signature="sig-bytes",
        size_bytes=2048,
        mime_type="application/x-kortex-knowledge",
        storage_key="packs/hr-ontology.kortex-knowledge",
        bucket_name="custom-bucket",
    )
    assert pack.asset_id == "pack-1"
    assert pack.bucket_name == "custom-bucket"
    assert pack.digital_signature == "sig-bytes"


def test_knowledge_pack_defaults_bucket_name_and_signature() -> None:
    pack = KnowledgePack(
        asset_id="pack-1",
        tenant_id="tenant-a",
        checksum_sha256="a" * 64,
        size_bytes=1024,
        mime_type="application/x-kortex-knowledge",
        storage_key="packs/pack-1.kortex-knowledge",
    )
    assert pack.bucket_name == "knowledge"
    assert pack.digital_signature is None
    assert pack.manifest == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("asset_id", ""),
        ("tenant_id", ""),
        ("checksum_sha256", ""),
        ("mime_type", ""),
        ("storage_key", ""),
    ],
)
def test_knowledge_pack_rejects_empty_required_strings(field: str, value: str) -> None:
    kwargs = {
        "asset_id": "pack-1",
        "tenant_id": "tenant-a",
        "checksum_sha256": "a" * 64,
        "size_bytes": 1024,
        "mime_type": "application/x-kortex-knowledge",
        "storage_key": "packs/pack-1.kortex-knowledge",
    }
    kwargs[field] = value
    with pytest.raises(ValidationError):
        KnowledgePack(**kwargs)


def test_knowledge_pack_rejects_negative_size_bytes() -> None:
    with pytest.raises(ValidationError):
        KnowledgePack(
            asset_id="pack-1",
            tenant_id="tenant-a",
            checksum_sha256="a" * 64,
            size_bytes=-1,
            mime_type="application/x-kortex-knowledge",
            storage_key="packs/pack-1.kortex-knowledge",
        )


def test_knowledge_pack_serialization_round_trip() -> None:
    pack = KnowledgePack(
        asset_id="pack-1",
        tenant_id="tenant-a",
        manifest={"name": "hr-ontology"},
        checksum_sha256="a" * 64,
        size_bytes=1024,
        mime_type="application/x-kortex-knowledge",
        storage_key="packs/pack-1.kortex-knowledge",
    )
    restored = KnowledgePack.model_validate(pack.model_dump())
    assert restored == pack


# -- KnowledgeQuery -------------------------------------------------------------


def test_knowledge_query_valid_construction() -> None:
    query = KnowledgeQuery(
        query_id="q-1",
        tenant_id="tenant-a",
        query_text="quarterly invoices",
        filters={"status": "approved"},
        entity_types=["invoice"],
        max_results=10,
    )
    assert query.query_text == "quarterly invoices"
    assert query.filters == {"status": "approved"}
    assert query.entity_types == ["invoice"]
    assert query.max_results == 10


def test_knowledge_query_defaults_filters_entity_types_and_max_results() -> None:
    query = KnowledgeQuery(query_id="q-1", tenant_id="tenant-a", query_text="quarterly invoices")
    assert query.filters == {}
    assert query.entity_types == []
    assert query.max_results is None


def test_knowledge_query_defaults_trust_states_to_confirmed_and_corrected_only() -> None:
    """Excludes unverified content (`SOURCE_EVIDENCE`/`AI_CANDIDATE`) by
    default so a query never silently surfaces unconfirmed knowledge as
    current truth."""
    query = KnowledgeQuery(query_id="q-1", tenant_id="tenant-a", query_text="quarterly invoices")
    assert query.trust_states == [KnowledgeTrustState.HUMAN_CONFIRMED, KnowledgeTrustState.HUMAN_CORRECTED]


def test_knowledge_query_defaults_as_of_to_none() -> None:
    query = KnowledgeQuery(query_id="q-1", tenant_id="tenant-a", query_text="quarterly invoices")
    assert query.as_of is None


def test_knowledge_query_accepts_explicit_trust_states_and_as_of() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    query = KnowledgeQuery(
        query_id="q-1",
        tenant_id="tenant-a",
        query_text="quarterly invoices",
        trust_states=[KnowledgeTrustState.AI_CANDIDATE],
        as_of=as_of,
    )
    assert query.trust_states == [KnowledgeTrustState.AI_CANDIDATE]
    assert query.as_of == as_of


def test_knowledge_query_trust_states_default_is_not_a_shared_mutable_list() -> None:
    """Each `KnowledgeQuery` instance must get its own default list instance
    — verifies `default_factory` is used rather than a shared mutable
    default, matching the convention already used for `filters`/`entity_types`."""
    query_a = KnowledgeQuery(query_id="q-1", tenant_id="tenant-a", query_text="a")
    query_b = KnowledgeQuery(query_id="q-2", tenant_id="tenant-a", query_text="b")
    assert query_a.trust_states is not query_b.trust_states


@pytest.mark.parametrize(
    "field,value",
    [
        ("query_id", ""),
        ("tenant_id", ""),
        ("query_text", ""),
    ],
)
def test_knowledge_query_rejects_empty_required_strings(field: str, value: str) -> None:
    kwargs = {"query_id": "q-1", "tenant_id": "tenant-a", "query_text": "quarterly invoices"}
    kwargs[field] = value
    with pytest.raises(ValidationError):
        KnowledgeQuery(**kwargs)


def test_knowledge_query_is_frozen() -> None:
    query = KnowledgeQuery(query_id="q-1", tenant_id="tenant-a", query_text="quarterly invoices")
    with pytest.raises(ValidationError):
        query.query_text = "changed"


def test_knowledge_query_serialization_round_trip() -> None:
    query = KnowledgeQuery(
        query_id="q-1",
        tenant_id="tenant-a",
        query_text="quarterly invoices",
        filters={"status": "approved"},
        entity_types=["invoice"],
        max_results=5,
    )
    restored = KnowledgeQuery.model_validate(query.model_dump())
    assert restored == query


# -- KnowledgeQueryResult -------------------------------------------------------


def test_knowledge_query_result_valid_construction() -> None:
    node = KnowledgeNode(node_id="node-1", tenant_id="tenant-a", entity_type="document", label="Report")
    relationship = KnowledgeRelationship(
        relationship_id="rel-1",
        tenant_id="tenant-a",
        source_node_id="node-1",
        target_node_id="node-2",
        relationship_type=KnowledgeRelationshipType.CONTAINS,
    )
    result = KnowledgeQueryResult(
        query_id="q-1",
        matching_nodes=[node],
        graph_relationships=[relationship],
        execution_time_ms=12.5,
    )
    assert result.matching_nodes == [node]
    assert result.graph_relationships == [relationship]
    assert result.execution_time_ms == 12.5


def test_knowledge_query_result_defaults_nodes_and_relationships() -> None:
    result = KnowledgeQueryResult(query_id="q-1", execution_time_ms=1.0)
    assert result.matching_nodes == []
    assert result.graph_relationships == []


def test_knowledge_query_result_rejects_missing_query_id() -> None:
    with pytest.raises(ValidationError):
        KnowledgeQueryResult.model_validate({"execution_time_ms": 1.0})


def test_knowledge_query_result_rejects_negative_execution_time() -> None:
    with pytest.raises(ValidationError):
        KnowledgeQueryResult(query_id="q-1", execution_time_ms=-1.0)


def test_knowledge_query_result_is_frozen() -> None:
    result = KnowledgeQueryResult(query_id="q-1", execution_time_ms=1.0)
    with pytest.raises(ValidationError):
        result.execution_time_ms = 2.0


def test_knowledge_query_result_serialization_round_trip() -> None:
    node = KnowledgeNode(node_id="node-1", tenant_id="tenant-a", entity_type="document", label="Report")
    result = KnowledgeQueryResult(query_id="q-1", matching_nodes=[node], execution_time_ms=3.2)
    restored = KnowledgeQueryResult.model_validate(result.model_dump())
    assert restored == result


def test_knowledge_query_result_defaults_matching_records() -> None:
    result = KnowledgeQueryResult(query_id="q-1", execution_time_ms=1.0)
    assert result.matching_records == []


def test_knowledge_query_result_accepts_matching_records() -> None:
    record = KnowledgeRecord(
        record_id="rec-1",
        tenant_id="tenant-a",
        version_id="v1",
        record_type=KnowledgeRecordType.DECISION,
        trust_state=KnowledgeTrustState.HUMAN_CONFIRMED,
        created_by="user-1",
        created_by_type=KnowledgeActorType.USER,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = KnowledgeQueryResult(query_id="q-1", matching_records=[record], execution_time_ms=1.0)
    assert result.matching_records == [record]


def test_knowledge_query_result_serialization_round_trip_with_matching_records() -> None:
    record = KnowledgeRecord(
        record_id="rec-1",
        tenant_id="tenant-a",
        version_id="v1",
        record_type=KnowledgeRecordType.FACT,
        trust_state=KnowledgeTrustState.SOURCE_EVIDENCE,
        created_by="user-1",
        created_by_type=KnowledgeActorType.USER,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = KnowledgeQueryResult(query_id="q-1", matching_records=[record], execution_time_ms=1.0)
    restored = KnowledgeQueryResult.model_validate(result.model_dump())
    assert restored == result
