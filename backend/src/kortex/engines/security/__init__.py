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

# Imported first, for its side effect of fully initializing `kortex.core`
# before any module in this package begins executing.
#
# This package and `kortex.core` are mutually recursive at import time:
# `kortex.core.__init__` imports `dispatch`, which imports
# `kortex.engines.security.engine`; meanwhile the modules below import
# `kortex.engines.security.models`, which imports `kortex.core.db` and so
# triggers `kortex.core.__init__`. Whichever package an interpreter happens to
# load first decides whether that cycle resolves. Entering through
# `kortex.core` always resolves; entering through this package leaves `models`
# half-initialized when `interfaces` imports `AccessDecision` from it, and the
# import fails.
#
# In practice something always imported `kortex.core` first, so the cycle stayed
# latent — until Phase 5 added `python -m kortex.engines.security.cli`, which
# makes this package the interpreter's genuine entry point. Pinning the order
# here fixes it for every entry point at once rather than per caller.
#
# This is containment, not a cure: the real defect is that `kortex.core`
# depends on a specific engine at import time. Restructuring that is outside
# Phase 5's change surface and is flagged for the Chief Architect.
import kortex.core  # noqa: F401  (imported for import-order side effect only)
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
