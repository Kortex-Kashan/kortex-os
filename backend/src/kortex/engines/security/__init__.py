"""KORTEX Security Engine — RBAC, encryption, API keys, and audit logging.

Milestone M1 + M2 + M3 + M4 (current package state): domain models,
exception hierarchy, Protocol interfaces, a local cryptographic provider
(SHA-256, Ed25519, AES-256-GCM) exposed via `VerificationService`, an
encrypted `SecretStore` (single master key + AAD-bound envelope, no key
derivation, no key rotation), an `AuthenticationManager` (uniform Argon2id
credential verification for USER/SERVICE_PRINCIPAL/AGENT, Ed25519-signed
short-lived tokens, no revocation, no cache), and an `AuthorizationEngine`
(hybrid RBAC + ABAC — static IDataStore-persisted role/permission grants;
ABAC evaluates tenant_id and security_classification only). Audit
enforcement and Kernel capability dispatch middleware are NOT implemented
yet — see the Security Engine milestone plan.
"""

from kortex.engines.security.abac import ABACEvaluator
from kortex.engines.security.auth import AuthenticationManager
from kortex.engines.security.authorization import AuthorizationEngine
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
    SigningKeyError,
    TokenExpiredError,
)
from kortex.engines.security.models import (
    AccessDecision,
    ClassificationLevel,
    CryptographicSignature,
    PermissionRequirement,
    PrincipalRecord,
    PrincipalType,
    RolePermissionRecord,
    SecretEntry,
    SecurityMetadata,
    SecurityPrincipal,
    TokenPayload,
)
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.security.rbac import RBACEvaluator
from kortex.engines.security.secrets import SecretStore

__all__ = [
    "ABACEvaluator",
    "AccessDecision",
    "AuthenticationError",
    "AuthenticationManager",
    "AuthorizationDeniedError",
    "AuthorizationEngine",
    "ClassificationLevel",
    "CryptoProviderError",
    "CryptographicSignature",
    "InvalidSignatureError",
    "InvalidTokenError",
    "LocalCrypto",
    "MasterKeyError",
    "PermissionRequirement",
    "PrincipalRecord",
    "PrincipalType",
    "RBACEvaluator",
    "RolePermissionRecord",
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
    "SigningKeyError",
    "TokenExpiredError",
    "TokenPayload",
    "VerificationService",
]
