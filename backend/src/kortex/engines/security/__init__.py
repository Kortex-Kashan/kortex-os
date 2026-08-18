"""KORTEX Security Engine — RBAC, encryption, API keys, and audit logging.

Milestone M1 + M2 + M3 + M4 + M5 + M6: domain models, exception hierarchy,
Protocol interfaces, local cryptographic provider (SHA-256, Ed25519,
AES-256-GCM) exposed via `VerificationService`, encrypted `SecretStore`
(single master key + AAD-bound envelope), `AuthenticationManager` (uniform
Argon2id credential verification, Ed25519-signed short-lived tokens),
`AuthorizationEngine` (hybrid RBAC + ABAC), Kernel capability enforcement
boundary, and `AuditManager` (immutable `UniversalAuditEntry` persistence in
`IDataStore` and immutable security event dispatch to `EventEngine`).
"""

from kortex.engines.security.abac import ABACEvaluator
from kortex.engines.security.audit import AuditManager
from kortex.engines.security.auth import AuthenticationManager
from kortex.engines.security.authorization import AuthorizationEngine
from kortex.engines.security.crypto import VerificationService
from kortex.engines.security.diagnostics import SecurityDiagnostics
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.events import (
    SecurityAccessDeniedEvent,
    SecurityAccessGrantedEvent,
    SecurityAuditEvent,
    SecurityAuthFailureEvent,
    SecurityAuthSuccessEvent,
    SecurityBaseEvent,
    SecuritySecretAccessedEvent,
    SecuritySecretModifiedEvent,
    SecuritySignatureVerifiedEvent,
)
from kortex.engines.security.exceptions import (
    AuditError,
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
from kortex.engines.security.interfaces import (
    IAuditManager,
    IAuthenticationManager,
    IAuthorizationEngine,
    ICryptoProvider,
    IEngineDiagnostics,
    ISecretStore,
    ISecurityEngine,
    IVerificationService,
)
from kortex.engines.security.models import (
    AccessDecision,
    AuditRecord,
    ClassificationLevel,
    CryptographicSignature,
    PermissionRequirement,
    PrincipalRecord,
    PrincipalType,
    RolePermissionRecord,
    SecretEntry,
    SecretRecord,
    SecurityMetadata,
    SecurityPrincipal,
    TokenPayload,
    UniversalAuditEntry,
)
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.security.rbac import RBACEvaluator
from kortex.engines.security.secrets import SecretStore

__all__ = [
    "ABACEvaluator",
    "AccessDecision",
    "AuditError",
    "AuditManager",
    "AuditRecord",
    "AuthenticationError",
    "AuthenticationManager",
    "AuthorizationDeniedError",
    "AuthorizationEngine",
    "ClassificationLevel",
    "CryptoProviderError",
    "CryptographicSignature",
    "IAuditManager",
    "IAuthenticationManager",
    "IAuthorizationEngine",
    "ICryptoProvider",
    "IEngineDiagnostics",
    "ISecretStore",
    "ISecurityEngine",
    "IVerificationService",
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
    "SecretRecord",
    "SecretStore",
    "SecretStoreError",
    "SecurityAccessDeniedEvent",
    "SecurityAccessGrantedEvent",
    "SecurityAuditEvent",
    "SecurityAuthFailureEvent",
    "SecurityAuthSuccessEvent",
    "SecurityBaseEvent",
    "SecurityDiagnostics",
    "SecurityEngine",
    "SecurityEngineError",
    "SecurityMetadata",
    "SecurityPrincipal",
    "SecuritySecretAccessedEvent",
    "SecuritySecretModifiedEvent",
    "SecuritySignatureVerifiedEvent",
    "SigningKeyError",
    "TokenExpiredError",
    "TokenPayload",
    "UniversalAuditEntry",
    "VerificationService",
]
