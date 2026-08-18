"""
KORTEX Security Engine Exception Hierarchy.

All Security Engine exceptions inherit from `KortexError` (kortex.core.exceptions),
following the existing KORTEX exception conventions.

No exception raised by this package may ever include plaintext secret material,
private key bytes, or other sensitive cryptographic material in its message,
`code`, or any other attribute.
"""

from __future__ import annotations

from kortex.core.exceptions import KortexError


class SecurityEngineError(KortexError):
    """Base exception for all Security Engine errors."""


# -- Authentication (Milestone M3) -------------------------------------------


class AuthenticationError(SecurityEngineError):
    """Raised when identity or credential verification fails."""


class InvalidTokenError(AuthenticationError):
    """Raised when a session/identity token is malformed or fails verification."""


class TokenExpiredError(AuthenticationError):
    """Raised when a session/identity token has passed its expiration time."""


class SigningKeyError(SecurityEngineError):
    """Raised when the AuthenticationManager's Ed25519 signing key is missing
    or malformed.

    Never includes the key material itself, valid or not, in its message.
    Cryptographically separate from `MasterKeyError` (SecretStore's AES-256-GCM
    root key) — the two keys are resolved from distinct configuration values
    and never derived from one another.
    """


# -- Authorization (Milestone M4) --------------------------------------------


class AuthorizationDeniedError(SecurityEngineError):
    """Raised by `AuthorizationEngine.authorize_strict` when a caller is denied
    a requested permission or capability. The non-strict `authorize`/
    `evaluate_rbac`/`evaluate_abac` paths return a denying `AccessDecision`
    instead of raising — a policy "no" is a normal, expected outcome, not an
    exceptional one."""


# -- Secret Storage (Milestone M2) -------------------------------------------


class SecretNotFoundError(SecurityEngineError):
    """Raised when a requested secret handle does not resolve to a stored entry."""


class SecretDecryptionError(SecurityEngineError):
    """Raised when a stored secret cannot be decrypted or fails integrity verification.

    Covers ciphertext/tag/AAD tampering, malformed or truncated envelopes,
    unsupported envelope version/algorithm, and key-identity mismatches.
    """


class MasterKeyError(SecurityEngineError):
    """Raised when the SecretStore root encryption key is missing or malformed.

    Never includes the key material itself, valid or not, in its message.
    """


class SecretStoreError(SecurityEngineError):
    """Raised when a SecretStore storage-layer operation fails for a reason other
    than a normal not-found/decryption-failure outcome (e.g. an underlying
    `IDataStore` failure). Never silently converted into `False`/`None`.
    """


# -- Cryptographic Verification (implemented in M1) -------------------------


class InvalidSignatureError(SecurityEngineError):
    """Raised when a cryptographic signature fails verification against its payload/public key."""


class CryptoProviderError(SecurityEngineError):
    """Raised when a cryptographic operation fails at the provider layer.

    Examples: invalid key length, malformed signing key material, or an
    AES-256-GCM authentication tag failure indicating tampered ciphertext,
    tag, or associated data.
    """


# -- Audit Enforcement (Milestone M6) ---------------------------------------


class AuditError(SecurityEngineError):
    """Raised when an audit log recording, persistence, or querying operation fails."""
