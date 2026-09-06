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


# -- First-run Bootstrap (Milestone M7.1) ------------------------------------


class BootstrapClosedError(AuthenticationError):
    """Raised when `bootstrap_create_admin` is invoked but the system already
    has at least one principal.

    A subclass of `AuthenticationError` (not a fresh `SecurityEngineError`
    subtree) so it maps through the existing `PERMISSION_DENIED`/401
    exception taxonomy exactly like every other "you may not do this"
    identity-layer rejection — no new error category is introduced.
    """


class BootstrapValidationError(SecurityEngineError):
    """Raised when first-run bootstrap input fails validation (empty tenant
    ID/username, or a password below the minimum length). Never includes the
    submitted password in its message."""


# -- Principal Registration / Password Reset / Change (Phase A) -------------


class PrincipalAlreadyExistsError(SecurityEngineError):
    """Raised by `register_principal` when a principal already exists at the
    requested `(tenant_id, principal_id, principal_type)` — a genuine
    conflict an admin needs to see, unlike `provision_principal`'s
    deliberate silent idempotent no-op for its own, unrelated
    system-principal-bootstrap use case."""


class PrincipalRegistrationValidationError(SecurityEngineError):
    """Raised when `register_principal` input fails validation (empty
    tenant/username, empty roles, or a malformed email). Never includes the
    submitted password."""


class PasswordPolicyError(SecurityEngineError):
    """Raised whenever a submitted new password fails the minimum-length
    policy — shared across `change_password`, `reset_password`, and
    `register_principal`. Distinct from `BootstrapValidationError` (which
    remains scoped to `bootstrap_first_admin` only, unmodified) so this
    milestone's new methods don't silently repurpose bootstrap's own
    exception for an unrelated call path. Never includes the submitted
    password."""


class OAuthStateError(AuthenticationError):
    """Raised when a presented OAuth `state` parameter is missing, malformed,
    fails signature verification, or has expired (Phase A). A subclass of
    `AuthenticationError`, matching `PasswordResetError`'s exact precedent —
    the signed state *is* the credential being verified here."""


class OAuthExchangeError(SecurityEngineError):
    """Raised when exchanging an authorization code, or fetching the
    external identity, fails against the provider's own endpoints (network
    failure, invalid/expired code, malformed response). Never includes the
    authorization code or any token in its message."""


class OAuthProviderNotConfiguredError(SecurityEngineError):
    """Raised when an OAuth flow is attempted for a provider with no
    configured client ID/secret. The UI is expected to never reach this by
    disabling the corresponding button per `oauth.get_config`'s report, but
    the backend still fails closed rather than trusting the frontend gate."""


class OAuthLinkConflictError(SecurityEngineError):
    """Raised when linking would attach an external OAuth identity that is
    already linked to a *different* KORTEX principal — never silently
    reassigned or stolen."""


class OAuthNoLinkedAccountError(AuthenticationError):
    """Raised when an `intent="login"` OAuth callback resolves a real
    external identity that has no linked KORTEX principal. OAuth is a
    second sign-in method for an existing account, never a self-registration
    path — this is the honest, explicit outcome rather than silently
    creating an account or returning a generic authentication failure."""


class PasswordResetError(AuthenticationError):
    """Raised when a presented password-reset token is missing, malformed,
    expired, or already used.

    A subclass of `AuthenticationError` (not a fresh `SecurityEngineError`
    subtree), mapping through the existing `PERMISSION_DENIED`/401
    exception taxonomy exactly like every other "this credential does not
    grant you anything" identity-layer rejection — the reset token is
    itself the credential being verified here. Never distinguishes
    "missing" from "expired" from "already used" in its message
    (enumeration-resistance precedent, mirroring
    `_GENERIC_AUTH_FAILURE_MESSAGE`)."""


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
