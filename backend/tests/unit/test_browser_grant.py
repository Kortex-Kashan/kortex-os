"""Unit tests for Browser-B5.3's Capability Execution Grant primitive.

Exercises `mint_grant`/`verify_grant` purely (no live Kernel, no WebView2,
no Tauri process) — proving the cryptographic and shape half of grant
verification is correct in isolation, per `grant.py`'s own module doc on
why this split exists. Single-use/live-surface-binding checks are the
desktop redeem command's own responsibility (Browser-B5.4, Rust) and are
tested there, not here.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from kortex.engines.browser import grant as grant_module
from kortex.engines.browser.models import BrowserCapabilityTarget, BrowserElementSelector
from kortex.engines.security.providers.local_crypto import LocalCrypto


@pytest.fixture
def crypto_provider() -> LocalCrypto:
    return LocalCrypto()


@pytest.fixture
def keypair(crypto_provider: LocalCrypto) -> tuple[bytes, bytes]:
    """(private_key, public_key) — the exact order `ICryptoProvider.generate_ed25519_keypair` documents."""
    return crypto_provider.generate_ed25519_keypair()


def _target() -> BrowserCapabilityTarget:
    return BrowserCapabilityTarget(browser_profile_id="profile-1", surface_id="surface-1", navigation_generation=0)


def _mint(crypto_provider: LocalCrypto, keypair: tuple[bytes, bytes], **overrides):
    private_key, public_key = keypair
    kwargs = {
        "crypto_provider": crypto_provider,
        "signing_private_key": private_key,
        "signing_public_key": public_key,
        "tenant_id": "tenant-a",
        "principal_id": "ai-system",
        "capability_name": "kortex.browser.navigate",
        "target": _target(),
        "parameters": {"target": _target().model_dump(mode="json"), "url": "https://example.com"},
    }
    kwargs.update(overrides)
    return grant_module.mint_grant(**kwargs)


# -- canonicalize_and_hash ----------------------------------------------------


def test_hash_changes_when_parameters_change():
    h1 = grant_module.canonicalize_and_hash("kortex.browser.navigate", {"url": "https://a.example/"})
    h2 = grant_module.canonicalize_and_hash("kortex.browser.navigate", {"url": "https://b.example/"})
    assert h1 != h2


def test_hash_changes_when_capability_changes():
    params = {"url": "https://a.example/"}
    h1 = grant_module.canonicalize_and_hash("kortex.browser.navigate", params)
    h2 = grant_module.canonicalize_and_hash("kortex.browser.click", params)
    assert h1 != h2


def test_hash_is_deterministic_regardless_of_key_order():
    h1 = grant_module.canonicalize_and_hash("kortex.browser.click", {"a": 1, "b": 2})
    h2 = grant_module.canonicalize_and_hash("kortex.browser.click", {"b": 2, "a": 1})
    assert h1 == h2


# -- mint_grant / verify_grant roundtrip --------------------------------------


def test_valid_grant_verifies(crypto_provider: LocalCrypto, keypair: tuple[bytes, bytes]):
    _, public_key = keypair
    issued = _mint(crypto_provider, keypair)
    result = grant_module.verify_grant(issued, crypto_provider=crypto_provider, verification_public_key=public_key)
    assert result.is_valid


def test_expired_grant_rejected(crypto_provider: LocalCrypto, keypair: tuple[bytes, bytes]):
    _, public_key = keypair
    issued = _mint(crypto_provider, keypair, ttl_seconds=1)
    future = datetime.fromisoformat(issued.expires_at) + timedelta(seconds=1)
    result = grant_module.verify_grant(
        issued, crypto_provider=crypto_provider, verification_public_key=public_key, now=future
    )
    assert not result.is_valid
    assert result.reason == "expired"


def test_not_yet_valid_grant_rejected(crypto_provider: LocalCrypto, keypair: tuple[bytes, bytes]):
    issued = _mint(crypto_provider, keypair)
    _, public_key = keypair
    before_issuance = datetime.fromisoformat(issued.issued_at) - timedelta(seconds=5)
    result = grant_module.verify_grant(
        issued, crypto_provider=crypto_provider, verification_public_key=public_key, now=before_issuance
    )
    assert not result.is_valid


def test_invalid_signature_rejected(crypto_provider: LocalCrypto, keypair: tuple[bytes, bytes]):
    issued = _mint(crypto_provider, keypair)
    _, public_key = keypair
    tampered = issued.model_copy(update={"signature": "00" * 64})
    result = grant_module.verify_grant(tampered, crypto_provider=crypto_provider, verification_public_key=public_key)
    assert not result.is_valid
    assert result.reason == "signature verification failed"


def test_wrong_verification_key_rejected(crypto_provider: LocalCrypto, keypair: tuple[bytes, bytes]):
    issued = _mint(crypto_provider, keypair)
    _other_private, other_public = crypto_provider.generate_ed25519_keypair()
    result = grant_module.verify_grant(issued, crypto_provider=crypto_provider, verification_public_key=other_public)
    assert not result.is_valid


def test_non_hex_signature_rejected(crypto_provider: LocalCrypto, keypair: tuple[bytes, bytes]):
    issued = _mint(crypto_provider, keypair)
    _, public_key = keypair
    tampered = issued.model_copy(update={"signature": "not-hex!!"})
    result = grant_module.verify_grant(tampered, crypto_provider=crypto_provider, verification_public_key=public_key)
    assert not result.is_valid
    assert result.reason == "signature is not valid hex"


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("tenant_id", "tenant-b"),
        ("principal_id", "someone-else"),
        ("capability_name", "kortex.browser.click"),
        ("browser_profile_id", "profile-2"),
        ("surface_id", "surface-2"),
        ("navigation_generation", 99),
        ("canonicalized_parameters_hash", "0" * 64),
    ],
)
def test_modified_grant_field_fails_signature_verification(
    crypto_provider: LocalCrypto, keypair: tuple[bytes, bytes], field: str, new_value
):
    """Any single-field tamper changes the canonical payload the signature
    covers, so verification fails — proving the grant is bound to every one
    of these fields, not merely carrying them as inert metadata."""
    issued = _mint(crypto_provider, keypair)
    _, public_key = keypair
    tampered = issued.model_copy(update={field: new_value})
    result = grant_module.verify_grant(tampered, crypto_provider=crypto_provider, verification_public_key=public_key)
    assert not result.is_valid, f"expected tamper on '{field}' to invalidate the signature"


# -- Sensitive-input gate for browser.type ------------------------------------


@pytest.mark.parametrize(
    "accessible_name",
    ["Password", "Confirm Password", "PIN", "One-Time Code", "Security Answer", "CVV", "Recovery Code", "TOTP"],
)
def test_sensitive_target_names_are_detected(accessible_name: str):
    selector = BrowserElementSelector(accessible_name=accessible_name)
    assert grant_module.is_sensitive_type_target(selector)


@pytest.mark.parametrize("accessible_name", ["Search", "First Name", "Email Address", "Comment", "Subject"])
def test_ordinary_target_names_are_not_flagged(accessible_name: str):
    selector = BrowserElementSelector(accessible_name=accessible_name)
    assert not grant_module.is_sensitive_type_target(selector)


@pytest.mark.parametrize(
    "value",
    [
        "sk-abcdef0123456789ABCDEF",
        "Bearer abcdefgh12345678",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ",
        "4111 1111 1111 1111",
        "123456",
    ],
)
def test_secret_shaped_values_are_detected(value: str):
    assert grant_module.looks_like_secret_value(value)


@pytest.mark.parametrize("value", ["hello world", "user@example.com", "555-1234", "New York", "quarterly report"])
def test_ordinary_text_is_not_flagged(value: str):
    assert not grant_module.looks_like_secret_value(value)


def test_empty_value_is_not_flagged():
    assert not grant_module.looks_like_secret_value("   ")
