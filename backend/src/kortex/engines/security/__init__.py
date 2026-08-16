"""KORTEX Security Engine — RBAC, encryption, API keys, and audit logging.

Milestone M1 (current package state): domain models, exception hierarchy,
Protocol interfaces, and a local cryptographic provider (SHA-256, Ed25519,
AES-256-GCM) exposed via `VerificationService`. Authentication, authorization,
secret storage, audit enforcement, and Kernel capability registration are NOT
implemented yet — see the Security Engine milestone plan.
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
    SecretDecryptionError,
    SecretNotFoundError,
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
    "PermissionRequirement",
    "PrincipalType",
    "SecretDecryptionError",
    "SecretEntry",
    "SecretNotFoundError",
    "SecurityDiagnostics",
    "SecurityEngine",
    "SecurityEngineError",
    "SecurityMetadata",
    "SecurityPrincipal",
    "TokenExpiredError",
    "TokenPayload",
    "VerificationService",
]
