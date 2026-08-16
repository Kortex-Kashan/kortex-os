"""KORTEX Security Engine — RBAC, encryption, API keys, and audit logging.

Milestone M1 + M2 (current package state): domain models, exception
hierarchy, Protocol interfaces, a local cryptographic provider (SHA-256,
Ed25519, AES-256-GCM) exposed via `VerificationService`, and an encrypted
`SecretStore` (single master key + AAD-bound envelope, no key derivation, no
key rotation). Authentication, authorization, audit enforcement, and Kernel
capability dispatch middleware are NOT implemented yet — see the Security
Engine milestone plan.
"""

from kortex.engines.security.crypto import VerificationService
from kortex.engines.security.diagnostics import SecurityDiagnostics
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import (
    AuthenticationError,
    AuthorizationDeniedError,
    CryptoProviderError,
    InvalidSignatureError,
    InvalidTokenError,
    MasterKeyError,
    SecretDecryptionError,
    SecretNotFoundError,
    SecretStoreError,
    SecurityEngineError,
    TokenExpiredError,
)
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
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.security.secrets import SecretStore

__all__ = [
    "AccessDecision",
    "AuthenticationError",
    "AuthorizationDeniedError",
    "ClassificationLevel",
    "CryptoProviderError",
    "CryptographicSignature",
    "InvalidSignatureError",
    "InvalidTokenError",
    "LocalCrypto",
    "MasterKeyError",
    "PermissionRequirement",
    "PrincipalType",
    "SecretDecryptionError",
    "SecretEntry",
    "SecretNotFoundError",
    "SecretStore",
    "SecretStoreError",
    "SecurityDiagnostics",
    "SecurityEngine",
    "SecurityEngineError",
    "SecurityMetadata",
    "SecurityPrincipal",
    "TokenExpiredError",
    "TokenPayload",
    "VerificationService",
]
