"""Adversarial tests for the Phase 5 internal KORTEX PKI.

Covers spec S11-S14 and adversarial matrix tests 1-5 and 7: CA generation and
custody, the prohibition on silently regenerating a CA, fail-closed behaviour
when CA material is missing or corrupt, server certificate issuance and SAN
construction, and client CSR signing.

These exercise the real `cryptography` X.509 stack and the real `SecretStore`
over a real SQLite database. Nothing about the CA, the certificate profiles,
or the secret custody boundary is mocked — a mocked CA would prove only that
the test doubles agree with each other.
"""

from __future__ import annotations

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from kortex.engines.security.exceptions import (
    CaAlreadyExistsError,
    CaUnavailableError,
    CsrValidationError,
    ServerCertificateUnavailableError,
)
from kortex.engines.security.pki import (
    CA_COMMON_NAME,
    CA_KEY_SIZE_BITS,
    CA_PRIVATE_KEY_HANDLE,
    CA_PUBLIC_CERTIFICATE_HANDLE,
    GATEWAY_SERVER_CERTIFICATE_HANDLE,
    GATEWAY_SERVER_PRIVATE_KEY_HANDLE,
    LEAF_KEY_SIZE_BITS,
    SYSTEM_TENANT_ID,
    KortexPki,
)
from tests.security.conftest import Phase5Stack


def _make_csr(common_name: str = "ignored-by-the-backend") -> tuple[rsa.RSAPrivateKey, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=LEAF_KEY_SIZE_BITS)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    return key, csr.public_bytes(serialization.Encoding.PEM)


# -- Test 1: CA initialization ------------------------------------------------


async def test_pki_ca_initialization(phase5_stack: Phase5Stack) -> None:
    """TEST 1: `pki init` generates an RSA-4096 CA and stores it in SecretStore."""
    secret_store, pki, tmp_path = phase5_stack.secret_store, phase5_stack.pki, phase5_stack.tmp_path
    ca_path = tmp_path / "certs" / "kortex_root_ca.crt"

    certificate = await pki.initialize_ca(certificate_path=ca_path)

    # Subject is exactly the specified distinguished name.
    assert certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == CA_COMMON_NAME
    assert certificate.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value == "KORTEX"
    assert certificate.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)[0].value == "Security Engine"
    # Self-issued root.
    assert certificate.issuer == certificate.subject

    # RSA 4096.
    public_key = certificate.public_key()
    assert isinstance(public_key, rsa.RSAPublicKey)
    assert public_key.key_size == CA_KEY_SIZE_BITS

    # Basic Constraints: critical, CA=True.
    basic_constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    assert basic_constraints.critical is True
    assert basic_constraints.value.ca is True

    # Key Usage: critical, keyCertSign=True.
    key_usage = certificate.extensions.get_extension_for_class(x509.KeyUsage)
    assert key_usage.critical is True
    assert key_usage.value.key_cert_sign is True

    # 10-year lifetime.
    lifetime_days = (certificate.not_valid_after_utc - certificate.not_valid_before_utc).days
    assert lifetime_days == 3650

    # Private key is retrievable from the SecretStore under the system tenant,
    # and is a real RSA-4096 private key.
    stored_key_pem = await secret_store.get_secret(CA_PRIVATE_KEY_HANDLE, SYSTEM_TENANT_ID)
    stored_key = serialization.load_pem_private_key(stored_key_pem.encode("ascii"), password=None)
    assert isinstance(stored_key, rsa.RSAPrivateKey)
    assert stored_key.key_size == CA_KEY_SIZE_BITS

    # Public certificate stored too, and written to disk for installer packaging.
    stored_cert_pem = await secret_store.get_secret(CA_PUBLIC_CERTIFICATE_HANDLE, SYSTEM_TENANT_ID)
    assert "BEGIN CERTIFICATE" in stored_cert_pem
    assert ca_path.exists()
    assert "BEGIN CERTIFICATE" in ca_path.read_text(encoding="ascii")


async def test_ca_private_key_is_not_written_to_disk(phase5_stack: Phase5Stack) -> None:
    """The CA public certificate is written to disk; the private key never is.

    Walks every file the PKI is allowed to touch and asserts no PRIVATE KEY
    PEM header appears in any of them.
    """
    pki, tmp_path = phase5_stack.pki, phase5_stack.tmp_path
    await pki.initialize_ca(certificate_path=tmp_path / "certs" / "kortex_root_ca.crt")

    for path in tmp_path.rglob("*"):
        if not path.is_file():
            continue
        blob = path.read_bytes()
        assert b"BEGIN PRIVATE KEY" not in blob, f"private key PEM found on disk at {path}"
        assert b"BEGIN RSA PRIVATE KEY" not in blob, f"private key PEM found on disk at {path}"


# -- Test 2: CA overwrite prevention -----------------------------------------


async def test_pki_ca_cannot_be_silently_regenerated(phase5_stack: Phase5Stack) -> None:
    """TEST 2: a second `pki init` aborts and leaves the original CA intact."""
    secret_store, pki, tmp_path = phase5_stack.secret_store, phase5_stack.pki, phase5_stack.tmp_path
    ca_path = tmp_path / "certs" / "kortex_root_ca.crt"

    first = await pki.initialize_ca(certificate_path=ca_path)
    original_key_pem = await secret_store.get_secret(CA_PRIVATE_KEY_HANDLE, SYSTEM_TENANT_ID)

    with pytest.raises(CaAlreadyExistsError):
        await pki.initialize_ca(certificate_path=ca_path)

    # The stored CA is byte-for-byte the original — not merely "an" existing CA.
    assert await secret_store.get_secret(CA_PRIVATE_KEY_HANDLE, SYSTEM_TENANT_ID) == original_key_pem
    surviving = await pki.load_ca_certificate()
    assert surviving.serial_number == first.serial_number
    assert surviving.fingerprint(hashes.SHA256()) == first.fingerprint(hashes.SHA256())


async def test_ca_existence_check_does_not_mistake_an_outage_for_absence(phase5_stack: Phase5Stack) -> None:
    """A SecretStore failure must never be read as "no CA yet".

    If it were, a transient storage outage during `pki init` would silently
    mint a second CA and orphan every certificate already issued.
    """
    pki, tmp_path = phase5_stack.pki, phase5_stack.tmp_path
    await pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")

    class _FailingSecretStore:
        async def get_secret(self, secret_handle: str, tenant_id: str) -> str:
            raise RuntimeError("simulated SecretStore outage")

        async def put_secret(self, secret_handle: str, tenant_id: str, plaintext: str) -> object:
            raise AssertionError("must not write during an existence check")

        async def delete_secret(self, secret_handle: str, tenant_id: str) -> bool:
            raise AssertionError("must not delete during an existence check")

    failing_pki = KortexPki(_FailingSecretStore())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        await failing_pki.ca_exists()


# -- Test 3: missing CA fails closed -----------------------------------------


async def test_pki_missing_ca_fails_closed(phase5_stack: Phase5Stack) -> None:
    """TEST 3: with no CA present, every CA-dependent operation fails closed."""
    pki = phase5_stack.pki

    assert await pki.ca_exists() is False

    with pytest.raises(CaUnavailableError):
        await pki.load_ca_private_key()
    with pytest.raises(CaUnavailableError):
        await pki.load_ca_certificate()
    with pytest.raises(CaUnavailableError):
        await pki.issue_server_certificate("gateway.kortex.local")

    _key, csr_pem = _make_csr()
    with pytest.raises(CaUnavailableError):
        await pki.sign_client_csr(csr_pem, "some-principal-id")

    # And the Gateway's own material is equally unavailable.
    with pytest.raises(ServerCertificateUnavailableError):
        await pki.load_server_material()


# -- Test 4: corrupt CA fails closed -----------------------------------------


async def test_pki_corrupt_ca_fails_closed(phase5_stack: Phase5Stack) -> None:
    """TEST 4: a corrupt CA key is rejected, never used and never regenerated."""
    secret_store, pki, tmp_path = phase5_stack.secret_store, phase5_stack.pki, phase5_stack.tmp_path
    await pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")

    # Overwrite the stored key with syntactically valid but non-key content.
    await secret_store.put_secret(CA_PRIVATE_KEY_HANDLE, SYSTEM_TENANT_ID, "-----BEGIN PRIVATE KEY-----\nnope\n")

    with pytest.raises(CaUnavailableError):
        await pki.load_ca_private_key()
    with pytest.raises(CaUnavailableError):
        await pki.issue_server_certificate("gateway.kortex.local")

    _key, csr_pem = _make_csr()
    with pytest.raises(CaUnavailableError):
        await pki.sign_client_csr(csr_pem, "some-principal-id")

    # A corrupt CA must still count as "exists" — the fail-closed path is an
    # error, never a silent regeneration.
    assert await pki.ca_exists() is True
    with pytest.raises(CaAlreadyExistsError):
        await pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")


async def test_pki_corrupt_ca_certificate_fails_closed(phase5_stack: Phase5Stack) -> None:
    """A corrupt CA *certificate* (not key) is equally fail-closed."""
    secret_store, pki, tmp_path = phase5_stack.secret_store, phase5_stack.pki, phase5_stack.tmp_path
    await pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")

    await secret_store.put_secret(CA_PUBLIC_CERTIFICATE_HANDLE, SYSTEM_TENANT_ID, "not a certificate")

    with pytest.raises(CaUnavailableError):
        await pki.load_ca_certificate()


# -- Test 5: server certificate issuance -------------------------------------


async def test_pki_server_cert_issuance(phase5_stack: Phase5Stack) -> None:
    """TEST 5: the server certificate carries the SAN and serverAuth EKU."""
    secret_store, pki, tmp_path = phase5_stack.secret_store, phase5_stack.pki, phase5_stack.tmp_path
    ca_certificate = await pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")

    hostname = "gateway.kortex.local"
    certificate, certificate_pem = await pki.issue_server_certificate(hostname, ip_address="127.0.0.1")

    # Issued by the KORTEX Root CA, not self-signed.
    assert certificate.issuer == ca_certificate.subject
    assert certificate.subject != certificate.issuer

    # RSA 2048.
    public_key = certificate.public_key()
    assert isinstance(public_key, rsa.RSAPublicKey)
    assert public_key.key_size == LEAF_KEY_SIZE_BITS

    # CN carries the hostname.
    assert certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == hostname

    # SAN carries the hostname and the configured IP.
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    assert san.value.get_values_for_type(x509.DNSName) == [hostname]
    assert [str(ip) for ip in san.value.get_values_for_type(x509.IPAddress)] == ["127.0.0.1"]

    # Not a CA.
    basic_constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    assert basic_constraints.critical is True
    assert basic_constraints.value.ca is False

    # EKU: critical, serverAuth — and specifically NOT clientAuth.
    eku = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    assert eku.critical is True
    assert list(eku.value) == [ExtendedKeyUsageOID.SERVER_AUTH]

    # Key Usage: critical, digitalSignature + keyEncipherment.
    key_usage = certificate.extensions.get_extension_for_class(x509.KeyUsage)
    assert key_usage.critical is True
    assert key_usage.value.digital_signature is True
    assert key_usage.value.key_encipherment is True
    assert key_usage.value.key_cert_sign is False

    # 1-year lifetime.
    assert (certificate.not_valid_after_utc - certificate.not_valid_before_utc).days == 365

    # The signature genuinely verifies against the CA's public key.
    ca_certificate.public_key().verify(
        certificate.signature,
        certificate.tbs_certificate_bytes,
        padding.PKCS1v15(),
        certificate.signature_hash_algorithm,
    )

    # Both halves land in the SecretStore under the system tenant.
    assert await secret_store.get_secret(GATEWAY_SERVER_CERTIFICATE_HANDLE, SYSTEM_TENANT_ID) == certificate_pem
    stored_key_pem = await secret_store.get_secret(GATEWAY_SERVER_PRIVATE_KEY_HANDLE, SYSTEM_TENANT_ID)
    assert isinstance(
        serialization.load_pem_private_key(stored_key_pem.encode("ascii"), password=None), rsa.RSAPrivateKey
    )

    # And load_server_material round-trips them.
    loaded_cert, loaded_key = await pki.load_server_material()
    assert loaded_cert == certificate_pem.encode("ascii")
    assert b"PRIVATE KEY" in loaded_key


async def test_server_material_fails_closed_when_corrupt(phase5_stack: Phase5Stack) -> None:
    """A corrupt server certificate aborts rather than degrading."""
    secret_store, pki, tmp_path = phase5_stack.secret_store, phase5_stack.pki, phase5_stack.tmp_path
    await pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")
    await pki.issue_server_certificate("gateway.kortex.local")

    await secret_store.put_secret(GATEWAY_SERVER_CERTIFICATE_HANDLE, SYSTEM_TENANT_ID, "garbage")
    with pytest.raises(ServerCertificateUnavailableError):
        await pki.load_server_material()


# -- Test 7: client certificate issuance -------------------------------------


async def test_client_cert_issuance(phase5_stack: Phase5Stack) -> None:
    """TEST 7: the client certificate binds CN=principal_id and clientAuth."""
    pki, tmp_path = phase5_stack.pki, phase5_stack.tmp_path
    ca_certificate = await pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")

    principal_id = "11111111-2222-4333-8444-555555555555"
    _key, csr_pem = _make_csr(common_name="attacker-chosen-name")
    certificate, certificate_pem = await pki.sign_client_csr(csr_pem, principal_id)

    # CN is the backend's principal_id — NOT the name the CSR asked for.
    assert certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == principal_id
    assert "attacker-chosen-name" not in certificate.subject.rfc4514_string()

    assert certificate.issuer == ca_certificate.subject

    # EKU: critical, clientAuth only.
    eku = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    assert eku.critical is True
    assert list(eku.value) == [ExtendedKeyUsageOID.CLIENT_AUTH]

    # Not a CA — a CSR can never obtain signing authority.
    basic_constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    assert basic_constraints.critical is True
    assert basic_constraints.value.ca is False

    assert (certificate.not_valid_after_utc - certificate.not_valid_before_utc).days == 365
    assert b"BEGIN CERTIFICATE" in certificate_pem


async def test_client_csr_cannot_dictate_certificate_fields(phase5_stack: Phase5Stack) -> None:
    """A CSR requesting CA powers and serverAuth gets neither.

    The CSR contributes only its public key; every other field is imposed by
    the backend.
    """
    pki, tmp_path = phase5_stack.pki, phase5_stack.tmp_path
    await pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")

    key = rsa.generate_private_key(public_exponent=65537, key_size=LEAF_KEY_SIZE_BITS)
    hostile_csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "KORTEX Internal Root CA")]))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=True)
        .sign(key, hashes.SHA256())
    )

    certificate, _pem = await pki.sign_client_csr(
        hostile_csr.public_bytes(serialization.Encoding.PEM), "legitimate-principal-id"
    )

    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert list(certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) == [
        ExtendedKeyUsageOID.CLIENT_AUTH
    ]
    assert certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "legitimate-principal-id"


async def test_malformed_csr_is_rejected(phase5_stack: Phase5Stack) -> None:
    """A malformed CSR is rejected without touching the CA."""
    pki, tmp_path = phase5_stack.pki, phase5_stack.tmp_path
    await pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")

    for garbage in (b"", b"not a csr", b"-----BEGIN CERTIFICATE REQUEST-----\nbroken\n"):
        with pytest.raises(CsrValidationError):
            await pki.sign_client_csr(garbage, "some-principal-id")
