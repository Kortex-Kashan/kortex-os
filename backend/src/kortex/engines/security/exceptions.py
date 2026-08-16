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


# -- Authentication (interfaces declared in M1; implemented in M3) ----------


class AuthenticationError(SecurityEngineError):
    """Raised when identity or credential verification fails."""


class InvalidTokenError(AuthenticationError):
    """Raised when a session/identity token is malformed or fails verification."""


class TokenExpiredError(AuthenticationError):
    """Raised when a session/identity token has passed its expiration time."""


# -- Authorization (interfaces declared in M1; implemented in M4) -----------


class AuthorizationDeniedError(SecurityEngineError):
    """Raised when a caller is denied a requested permission or capability."""


# -- Secret Storage (interfaces declared in M1; implemented in M2) ---------


class SecretNotFoundError(SecurityEngineError):
    """Raised when a requested secret handle does not resolve to a stored entry."""


class SecretDecryptionError(SecurityEngineError):
    """Raised when a stored secret cannot be decrypted or fails integrity verification."""


# -- Cryptographic Verification (implemented in M1) -------------------------


class InvalidSignatureError(SecurityEngineError):
    """Raised when a cryptographic signature fails verification against its payload/public key."""


class CryptoProviderError(SecurityEngineError):
    """Raised when a cryptographic operation fails at the provider layer.

    Examples: invalid key length, malformed signing key material, or an
    AES-256-GCM authentication tag failure indicating tampered ciphertext,
    tag, or associated data.
    """
