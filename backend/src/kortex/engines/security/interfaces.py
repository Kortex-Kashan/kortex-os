"""
KORTEX Security Engine Abstract Interfaces & Protocols (Milestone M1).

Defines the Protocol contracts for the Security Engine per
`docs/architecture/security_engine_implementation_spec.md` v3.0.0 (S4).

`ICryptoProvider`, `IVerificationService`, and `IEngineDiagnostics` are
implemented as of M1 (see `providers/local_crypto.py`, `crypto.py`,
`diagnostics.py`). `ISecretStore` is implemented as of M2 (`secrets.py`).
`IAuthenticationManager` is implemented as of M3 (`auth.py`). `ISecurityEngine`
and `IAuthorizationEngine` remain declared here as forward contracts for
later milestones (M4/M6) — they carry no implementation logic and must not
be mistaken for functional behavior.

`IEngineDiagnostics` intentionally mirrors
`kortex.engines.storage.interfaces.IEngineDiagnostics` exactly: this codebase's
established convention is for each engine to declare its own local copy of this
Protocol rather than import a shared one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Protocol, runtime_checkable

from kortex.engines.security.models import (
    AccessDecision,
    CryptographicSignature,
    PermissionRequirement,
    SecretEntry,
    SecurityPrincipal,
    TokenPayload,
)


@runtime_checkable
class ICryptoProvider(Protocol):
    """Cryptographic primitive provider abstraction (SHA-256 / Ed25519 / AES-256-GCM).

    Implemented by `providers.local_crypto.LocalCrypto` in M1.
    """

    def hash_sha256(self, data: bytes) -> str:
        """Compute the SHA-256 digest of `data`, returned as a lowercase hex string."""
        ...

    def verify_sha256(self, data: bytes, expected_hash: str) -> bool:
        """Verify `data` matches `expected_hash` using a constant-time comparison."""
        ...

    def generate_ed25519_keypair(self) -> tuple[bytes, bytes]:
        """Generate a fresh Ed25519 keypair. Returns (private_key_bytes, public_key_bytes)."""
        ...

    def sign_ed25519(self, data: bytes, private_key: bytes) -> bytes:
        """Sign `data` using a raw 32-byte Ed25519 private key. Returns a 64-byte signature."""
        ...

    def derive_ed25519_public_key(self, private_key: bytes) -> bytes:
        """Derive the raw 32-byte public key corresponding to a raw 32-byte
        Ed25519 private key. Never generates or persists key material — a
        pure mathematical derivation over caller-supplied bytes.
        """
        ...

    def verify_ed25519(self, data: bytes, signature: bytes, public_key: bytes) -> bool:
        """Verify an Ed25519 `signature` over `data` against a raw public key.

        Must return False (never raise) for a normal verification failure.
        """
        ...

    def encrypt_aes_gcm(
        self,
        plaintext: bytes,
        key: bytes,
        associated_data: bytes | None = None,
    ) -> tuple[bytes, bytes, bytes]:
        """Encrypt `plaintext` under AES-256-GCM with a fresh random nonce.

        Returns (nonce, ciphertext, tag). `key` must be exactly 32 bytes.
        """
        ...

    def decrypt_aes_gcm(
        self,
        nonce: bytes,
        ciphertext: bytes,
        tag: bytes,
        key: bytes,
        associated_data: bytes | None = None,
    ) -> bytes:
        """Decrypt and authenticate AES-256-GCM `ciphertext`+`tag` under `key`/`nonce`.

        Must raise on any tampering (ciphertext, tag, or associated data) or
        invalid key/nonce length — never returns partial or garbage plaintext.
        """
        ...


@runtime_checkable
class IVerificationService(Protocol):
    """Signature and integrity verification protocol. Implemented in M1 (`crypto.py`)."""

    def compute_checksum(self, data: bytes) -> str:
        """Compute a SHA-256 checksum of `data`."""
        ...

    def verify_checksum(self, data: bytes, expected_checksum: str) -> bool:
        """Verify `data` matches `expected_checksum`."""
        ...

    def sign(self, data: bytes, private_key: bytes, public_key: bytes) -> CryptographicSignature:
        """Sign `data` with an Ed25519 keypair and return a `CryptographicSignature`."""
        ...

    def verify_signature(self, data: bytes, signature: CryptographicSignature) -> bool:
        """Verify `data` against a `CryptographicSignature`. Never raises for a normal failure."""
        ...


@runtime_checkable
class ISecretStore(Protocol):
    """Encrypted secret vault protocol. Implemented as of M2 (`secrets.py`)."""

    async def get_secret(self, secret_handle: str, tenant_id: str) -> str:
        """Resolve a secret handle to its decrypted plaintext value."""
        ...

    async def put_secret(self, secret_handle: str, tenant_id: str, plaintext: str) -> SecretEntry:
        """Encrypt and persist a secret under a handle."""
        ...

    async def delete_secret(self, secret_handle: str, tenant_id: str) -> bool:
        """Delete a secret entry. Returns True if deleted, False if not found."""
        ...


@runtime_checkable
class IAuthenticationManager(Protocol):
    """Local authentication protocol. Implemented as of M3 (`auth.py`).

    Deliberately has no `revoke_token` method — token revocation is an
    explicit M3 non-goal (short-lived tokens only).
    """

    async def authenticate(self, credentials: Dict[str, Any]) -> SecurityPrincipal:
        """Verify credentials and return the resulting `SecurityPrincipal`."""
        ...

    async def issue_token(self, principal: SecurityPrincipal) -> TokenPayload:
        """Issue a short-lived session token for an authenticated principal."""
        ...

    async def verify_token(self, token: TokenPayload) -> SecurityPrincipal:
        """Verify a session token and return the resolved `SecurityPrincipal`."""
        ...


@runtime_checkable
class IAuthorizationEngine(Protocol):
    """Permission and policy evaluation protocol. Implemented in a later milestone (M4)."""

    async def evaluate_rbac(
        self, principal: SecurityPrincipal, requirement: PermissionRequirement
    ) -> AccessDecision:
        """Evaluate a static role-to-permission matrix against `requirement`."""
        ...

    async def evaluate_abac(
        self,
        principal: SecurityPrincipal,
        requirement: PermissionRequirement,
        context: Dict[str, Any],
    ) -> AccessDecision:
        """Evaluate dynamic attribute-based rules against `requirement` and `context`."""
        ...


@runtime_checkable
class ISecurityEngine(Protocol):
    """Primary Security Engine facade protocol. Implemented in a later milestone (M6)."""

    async def authenticate(self, credentials: Dict[str, Any]) -> SecurityPrincipal:
        """Authenticate a caller."""
        ...

    async def authorize(self, principal: SecurityPrincipal, requirement: PermissionRequirement) -> AccessDecision:
        """Authorize a caller's requested capability."""
        ...

    async def verify_signature(self, data: bytes, signature: CryptographicSignature) -> bool:
        """Verify a cryptographic signature."""
        ...

    async def get_secret(self, secret_handle: str, tenant_id: str) -> str:
        """Resolve a secret handle to its plaintext value."""
        ...


@runtime_checkable
class IEngineDiagnostics(Protocol):
    """Standardized diagnostics interface exposed by all KORTEX System Engines."""

    def health(self) -> Dict[str, Any]:
        """Return operational health status and diagnostic checks."""
        ...

    def metrics(self) -> Dict[str, Any]:
        """Return runtime performance and throughput metrics."""
        ...

    def diagnostics(self) -> Dict[str, Any]:
        """Return detailed technical diagnostics and system environment details."""
        ...

    def status(self) -> str:
        """Return current engine state name string."""
        ...

    def version(self) -> str:
        """Return semantic version string of the engine."""
        ...

    def capabilities(self) -> List[str]:
        """Return list of capability strings registered by the engine."""
        ...
