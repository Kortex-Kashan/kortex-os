"""
KORTEX License Engine — Commercial license validation and feature gating (Milestone M5.7).
"""

from kortex.engines.license.config import (
    CANONICAL_COMMUNITY_FEATURES,
    CANONICAL_COMMUNITY_QUOTAS,
    COMPILED_VENDOR_ROOT_KEYS,
)
from kortex.engines.license.crypto import LicenseCryptoEngine
from kortex.engines.license.engine import LicenseEngine
from kortex.engines.license.exceptions import (
    ConcurrentActivationError,
    InvalidKeyFormatError,
    InvalidTokenSignatureError,
    LicenseConflictError,
    LicenseEngineError,
    LicenseExpiredError,
    LicenseNotYetValidError,
    LicenseRevokedError,
    LicenseSecurityError,
    LicenseStateError,
    LicenseStorageError,
    LicenseValidationError,
    MalformedTokenError,
    SecurityConfigurationError,
    StorageOperationError,
    TenantMismatchError,
    TerminalLicenseError,
    UnknownKeyIdentifierError,
    UnsupportedAlgorithmError,
    UnsupportedScopeError,
)
from kortex.engines.license.interfaces import ILicenseProvider, ILicenseRepository
from kortex.engines.license.models import (
    EntitlementSnapshot,
    LicenseActivateRequest,
    LicenseRevokeRequest,
    LicenseScopeEnum,
    LicenseStatusEnum,
    LicenseStatusResponse,
    LicenseTier,
    LicenseTokenClaims,
    TokenVerifyRequest,
    TokenVerifyResponse,
)
from kortex.engines.license.repository import TenantScopedLicenseRepository
from kortex.engines.license.tables import LicenseRecord

__all__ = [
    "CANONICAL_COMMUNITY_FEATURES",
    "CANONICAL_COMMUNITY_QUOTAS",
    "COMPILED_VENDOR_ROOT_KEYS",
    "ConcurrentActivationError",
    "EntitlementSnapshot",
    "ILicenseProvider",
    "ILicenseRepository",
    "InvalidKeyFormatError",
    "InvalidTokenSignatureError",
    "LicenseActivateRequest",
    "LicenseConflictError",
    "LicenseCryptoEngine",
    "LicenseEngine",
    "LicenseEngineError",
    "LicenseExpiredError",
    "LicenseNotYetValidError",
    "LicenseRecord",
    "LicenseRevokeRequest",
    "LicenseRevokedError",
    "LicenseScopeEnum",
    "LicenseSecurityError",
    "LicenseStateError",
    "LicenseStatusEnum",
    "LicenseStatusResponse",
    "LicenseStorageError",
    "LicenseTier",
    "LicenseTokenClaims",
    "LicenseValidationError",
    "MalformedTokenError",
    "SecurityConfigurationError",
    "StorageOperationError",
    "TenantMismatchError",
    "TenantScopedLicenseRepository",
    "TerminalLicenseError",
    "TokenVerifyRequest",
    "TokenVerifyResponse",
    "UnknownKeyIdentifierError",
    "UnsupportedAlgorithmError",
    "UnsupportedScopeError",
]
