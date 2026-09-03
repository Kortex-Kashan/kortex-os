"""
KORTEX License Engine Configuration Constants (Milestone M5.7).
"""

from __future__ import annotations

# Official KORTEX Vendor Root Ed25519 Public Keys (compiled constants)
_OFFICIAL_ROOT_KID = "kortex-root-2026"
_OFFICIAL_ROOT_PUBLIC_KEY_HEX = "bcbef94a272662ebde4568416febeee95fb128e1d5ed7ec260ddcb19ede9937d"

COMPILED_VENDOR_ROOT_KEYS: dict[str, bytes] = {
    _OFFICIAL_ROOT_KID: bytes.fromhex(_OFFICIAL_ROOT_PUBLIC_KEY_HEX),
}

# Cryptographic token constraints
SUPPORTED_ALGORITHM = "Ed25519"
SUPPORTED_TOKEN_TYPE = "kortex-license"  # noqa: S105
SUPPORTED_SCHEMA_VERSION = 1
ED25519_SIGNATURE_LENGTH_BYTES = 64
ED25519_PUBLIC_KEY_LENGTH_BYTES = 32

# Clock rollback defense tolerance (1 hour in seconds)
CLOCK_SKEW_TOLERANCE_SECONDS = 3600

# Grace period boundaries
DEFAULT_GRACE_PERIOD_DAYS = 14
MAX_GRACE_PERIOD_DAYS = 90

# Authoritative Canonical Community Tier Defaults
CANONICAL_COMMUNITY_FEATURES: frozenset[str] = frozenset(
    [
        "core_workflows",
        "local_storage",
        "basic_search",
        "standard_documents",
    ]
)

CANONICAL_COMMUNITY_QUOTAS: dict[str, int] = {
    "max_users": 5,
    "max_connectors": 2,
    "max_monthly_documents": 100,
}
