"""Unit tests for Security Engine domain models (Milestone M1).

Verifies model validation rejects malformed security data and that
immutability/enum contracts hold, per the frozen Security Engine
specification (S5).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from kortex.engines.security.models import (
    AccessDecision,
    ClassificationLevel,
    CryptographicSignature,
    PermissionRequirement,
    PrincipalType,
    SecretEntry,
    SecurityMetadata,
    SecurityPrincipal,
    TokenPayload,
)

# -- PrincipalType / ClassificationLevel -------------------------------------


def test_principal_type_has_expected_members() -> None:
    assert set(PrincipalType) == {PrincipalType.USER, PrincipalType.SERVICE_PRINCIPAL, PrincipalType.AGENT}


def test_classification_level_has_expected_members() -> None:
    assert set(ClassificationLevel) == {
        ClassificationLevel.PUBLIC,
        ClassificationLevel.INTERNAL,
        ClassificationLevel.CONFIDENTIAL,
        ClassificationLevel.RESTRICTED,
    }


# -- SecurityPrincipal --------------------------------------------------------


def test_security_principal_valid_construction() -> None:
    principal = SecurityPrincipal(
        principal_id="user-1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant-a",
        roles=["ADMIN"],
        attributes={"department": "finance"},
    )
    assert principal.principal_id == "user-1"
    assert principal.tenant_id == "tenant-a"
    assert principal.roles == ["ADMIN"]


def test_security_principal_defaults_roles_and_attributes_empty() -> None:
    principal = SecurityPrincipal(principal_id="user-1", principal_type=PrincipalType.USER, tenant_id="tenant-a")
    assert principal.roles == []
    assert principal.attributes == {}


@pytest.mark.parametrize("field,value", [("principal_id", ""), ("tenant_id", "")])
def test_security_principal_rejects_empty_required_strings(field: str, value: str) -> None:
    kwargs = {"principal_id": "user-1", "principal_type": PrincipalType.USER, "tenant_id": "tenant-a"}
    kwargs[field] = value
    with pytest.raises(ValidationError):
        SecurityPrincipal(**kwargs)


def test_security_principal_rejects_invalid_principal_type() -> None:
    with pytest.raises(ValidationError):
        SecurityPrincipal(principal_id="user-1", principal_type="NOT_A_TYPE", tenant_id="tenant-a")


def test_security_principal_rejects_missing_tenant_id() -> None:
    with pytest.raises(ValidationError):
        SecurityPrincipal.model_validate({"principal_id": "user-1", "principal_type": PrincipalType.USER})


# -- PermissionRequirement ----------------------------------------------------


def test_permission_requirement_valid_construction() -> None:
    req = PermissionRequirement(
        capability_name="kortex.security.secret.get",
        required_permissions=["security:secret:read"],
        security_classification=ClassificationLevel.CONFIDENTIAL,
    )
    assert req.capability_name == "kortex.security.secret.get"
    assert req.security_classification == ClassificationLevel.CONFIDENTIAL


def test_permission_requirement_defaults_classification_internal() -> None:
    req = PermissionRequirement(capability_name="kortex.security.secret.get")
    assert req.security_classification == ClassificationLevel.INTERNAL
    assert req.required_permissions == []


def test_permission_requirement_rejects_empty_capability_name() -> None:
    with pytest.raises(ValidationError):
        PermissionRequirement(capability_name="")


# -- AccessDecision -----------------------------------------------------------


def test_access_decision_valid_construction() -> None:
    decision = AccessDecision(
        is_allowed=False,
        decision_code="PERMISSION_DENIED",
        reason="Principal lacks required role.",
    )
    assert decision.is_allowed is False
    assert decision.decision_code == "PERMISSION_DENIED"
    assert isinstance(decision.evaluated_at_utc, datetime)


def test_access_decision_rejects_empty_reason() -> None:
    with pytest.raises(ValidationError):
        AccessDecision(is_allowed=True, decision_code="ALLOWED", reason="")


def test_access_decision_rejects_empty_decision_code() -> None:
    with pytest.raises(ValidationError):
        AccessDecision(is_allowed=True, decision_code="", reason="ok")


def test_access_decision_is_immutable() -> None:
    decision = AccessDecision(is_allowed=True, decision_code="ALLOWED", reason="test")
    with pytest.raises(ValidationError):
        setattr(decision, "is_allowed", False)  # noqa: B010 -- intentional: proves frozen-model enforcement


# -- SecretEntry ---------------------------------------------------------------


def test_secret_entry_valid_construction() -> None:
    entry = SecretEntry(
        secret_handle="secret:kortex/connectors/smtp_pass",
        encrypted_payload=b"\x00\x01\x02ciphertext",
        algorithm="aes-256-gcm",
    )
    assert entry.secret_handle == "secret:kortex/connectors/smtp_pass"
    assert entry.encrypted_payload == b"\x00\x01\x02ciphertext"


def test_secret_entry_rejects_empty_handle() -> None:
    with pytest.raises(ValidationError):
        SecretEntry(secret_handle="", encrypted_payload=b"x", algorithm="aes-256-gcm")


def test_secret_entry_rejects_empty_algorithm() -> None:
    with pytest.raises(ValidationError):
        SecretEntry(secret_handle="secret:kortex/x", encrypted_payload=b"x", algorithm="")


# -- TokenPayload --------------------------------------------------------------


def test_token_payload_valid_construction() -> None:
    now = datetime.now(timezone.utc)
    payload = TokenPayload(
        token_id="tok-1",
        principal_id="user-1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant-a",
        issued_at_utc=now,
        expires_at_utc=now,
    )
    assert payload.token_id == "tok-1"
    assert payload.principal_type == PrincipalType.USER


def test_token_payload_rejects_empty_token_id() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        TokenPayload(
            token_id="",
            principal_id="user-1",
            principal_type=PrincipalType.USER,
            tenant_id="tenant-a",
            issued_at_utc=now,
            expires_at_utc=now,
        )


# -- CryptographicSignature ----------------------------------------------------


def test_cryptographic_signature_valid_construction() -> None:
    sig = CryptographicSignature(algorithm="ed25519", signature=b"\x01" * 64, public_key=b"\x02" * 32)
    assert sig.algorithm == "ed25519"
    assert len(sig.signature) == 64
    assert not hasattr(sig, "private_key")


def test_cryptographic_signature_rejects_empty_algorithm() -> None:
    with pytest.raises(ValidationError):
        CryptographicSignature(algorithm="", signature=b"\x01" * 64, public_key=b"\x02" * 32)


def test_cryptographic_signature_is_immutable() -> None:
    sig = CryptographicSignature(algorithm="ed25519", signature=b"\x01" * 64, public_key=b"\x02" * 32)
    with pytest.raises(ValidationError):
        setattr(sig, "algorithm", "sha1")  # noqa: B010 -- intentional: proves frozen-model enforcement


def test_cryptographic_signature_never_carries_private_key_field() -> None:
    """Structural guarantee: the model has no field capable of holding a private key."""
    assert "private_key" not in CryptographicSignature.model_fields


# -- SecurityMetadata -----------------------------------------------------------


def test_security_metadata_defaults() -> None:
    meta = SecurityMetadata()
    assert meta.classification == ClassificationLevel.INTERNAL
    assert meta.compliance_flags == []
    assert meta.encryption_required is False


def test_security_metadata_valid_construction() -> None:
    meta = SecurityMetadata(
        classification=ClassificationLevel.RESTRICTED,
        compliance_flags=["GDPR"],
        encryption_required=True,
    )
    assert meta.classification == ClassificationLevel.RESTRICTED
    assert meta.compliance_flags == ["GDPR"]
    assert meta.encryption_required is True
