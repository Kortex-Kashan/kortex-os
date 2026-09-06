"""KORTEX Security Engine Core Facade (Milestone M1 + M2 + M3 + M4 + M5 + M6).

Follows the exact lifecycle/registration conventions established by sibling
engines (see `kortex.engines.storage.engine.StorageEngine`,
`kortex.engines.registry.engine.RegistryEngine`).

Milestone M1 scope: engine lifecycle (`BaseEngine`), Kernel/Registry capability
registration, and the common diagnostics interface (`IEngineDiagnostics`).
Milestone M2 adds: `SecretStore` construction and activation of the
`kortex.security.secret.get` capability with real (encrypted, fail-closed)
behavior.
Milestone M3 adds: `AuthenticationManager` construction and activation of the
`kortex.security.auth.authenticate` capability with real (fail-closed) behavior.
Milestone M4 adds: `AuthorizationEngine` construction and activation of the
`kortex.security.access.authorize` capability with real hybrid RBAC+ABAC
(fail-closed) behavior.
Milestone M5 adds: Capability Enforcement Boundary integration via
`CapabilityDispatcher` in `kortex.core.dispatch`.
Milestone M6 adds: `AuditManager` construction for immutable audit trail
generation (`UniversalAuditEntry`) persisted in `IDataStore` and published to
`EventEngine`, activation of the 4th canonical capability
`kortex.security.signature.verify` via `VerificationService`, and full
implementation of the `ISecurityEngine` protocol.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.security.audit import AuditManager
from kortex.engines.security.auth import AuthenticationManager
from kortex.engines.security.authorization import AuthorizationEngine
from kortex.engines.security.crypto import VerificationService
from kortex.engines.security.email.base import IEmailProvider
from kortex.engines.security.email.dev_log_provider import DevLogEmailProvider
from kortex.engines.security.events import (
    SecurityAccessDeniedEvent,
    SecurityAccessGrantedEvent,
    SecurityAuthFailureEvent,
    SecurityAuthSuccessEvent,
    SecuritySecretModifiedEvent,
)
from kortex.engines.security.exceptions import (
    AuthenticationError,
    MasterKeyError,
    OAuthNoLinkedAccountError,
    OAuthProviderNotConfiguredError,
    OAuthStateError,
    SecretStoreError,
    SecurityEngineError,
    SigningKeyError,
)
from kortex.engines.security.interfaces import (
    ICryptoProvider,
    IEngineDiagnostics,
    ISecurityEngine,
)
from kortex.engines.security.models import (
    AccessDecision,
    CryptographicSignature,
    OAuthStatePayload,
    PermissionRequirement,
    SecretEntry,
    SecurityPrincipal,
)
from kortex.engines.security.oauth.base import IOAuthProvider
from kortex.engines.security.oauth.google_provider import GoogleOAuthProvider
from kortex.engines.security.oauth.microsoft_provider import MicrosoftOAuthProvider
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.security.secrets import SecretStore
from kortex.engines.storage.interfaces import ICacheStore, IDataStore, IFileStore

if TYPE_CHECKING:
    from kortex.core.dispatch import CapabilityExecutionContext
    from kortex.core.kernel import Kernel
    from kortex.engines.event.engine import EventEngine

logger = logging.getLogger("kortex.engines.security")

# Identical wording to `auth.py`'s own `_GENERIC_AUTH_FAILURE_MESSAGE` — kept
# as a separate module-local constant rather than a cross-module import of a
# private name, used only where this module's own capability handlers reject
# a missing execution-context identity before ever reaching `auth.py`.
_GENERIC_AUTH_FAILURE_MESSAGE = "Authentication failed: invalid credentials."

_MASTER_KEY_CONFIG_KEY = "KORTEX_MASTER_KEY"
_AUTH_SIGNING_KEY_CONFIG_KEY = "KORTEX_AUTH_SIGNING_PRIVATE_KEY"
# S105 false positives: capability name strings, flagged only because the
# constant names contain "SECRET".
_SECRET_GET_CAPABILITY = "kortex.security.secret.get"  # noqa: S105
_SECRET_PUT_CAPABILITY = "kortex.security.secret.put"  # noqa: S105
_AUTH_AUTHENTICATE_CAPABILITY = "kortex.security.auth.authenticate"
_ACCESS_AUTHORIZE_CAPABILITY = "kortex.security.access.authorize"
_SIGNATURE_VERIFY_CAPABILITY = "kortex.security.signature.verify"
_BOOTSTRAP_CREATE_ADMIN_CAPABILITY = "kortex.security.bootstrap.create_admin"
# Phase A (post-RC authentication completion).
_AUTH_CHANGE_PASSWORD_CAPABILITY = "kortex.security.auth.change_password"  # noqa: S105
_AUTH_REQUEST_PASSWORD_RESET_CAPABILITY = "kortex.security.auth.request_password_reset"  # noqa: S105
_AUTH_RESET_PASSWORD_CAPABILITY = "kortex.security.auth.reset_password"  # noqa: S105
_PRINCIPAL_REGISTER_CAPABILITY = "kortex.security.principal.register"
_PRINCIPAL_SET_EMAIL_CAPABILITY = "kortex.security.principal.set_email"
_OAUTH_GET_CONFIG_CAPABILITY = "kortex.security.oauth.get_config"
_OAUTH_LOGIN_BEGIN_CAPABILITY = "kortex.security.oauth.login_begin"
_OAUTH_LOGIN_COMPLETE_CAPABILITY = "kortex.security.oauth.login_complete"
_OAUTH_LINK_BEGIN_CAPABILITY = "kortex.security.oauth.link_begin"
_OAUTH_LINK_COMPLETE_CAPABILITY = "kortex.security.oauth.link_complete"
_OAUTH_UNLINK_CAPABILITY = "kortex.security.oauth.unlink"
_OAUTH_LIST_LINKS_CAPABILITY = "kortex.security.oauth.list_links"

# Fixed, custom URL scheme the desktop app registers for the OAuth
# authorization-code callback (Tauri deep link, `apps/desktop/src-tauri/`).
# A single constant shared by every provider's `authorization_url()`/
# `exchange_code()` call — the redirect target is identical regardless of
# which provider is used.
_OAUTH_REDIRECT_URI = "kortex-auth://oauth-callback"

# The single role granted to the first, bootstrap-created administrator.
_BOOTSTRAP_ADMIN_ROLE = "admin"

# Canonical capability registration list, per Security Engine spec S15, plus
# the M7.1 first-run bootstrap capability, the M7.3 secret-provisioning
# capability (closes the connector-credential write-path gap identified
# during M7.3 planning: `SecretStore.put_secret` existed and was already
# used internally for the AI system credential, but no tenant-facing,
# RBAC-gated, audited capability ever exposed it), and the Phase A
# authentication-completion capabilities (password change/reset,
# admin-provisioned principal registration).
_CANONICAL_CAPABILITIES: list[tuple[str, str]] = [
    (_AUTH_AUTHENTICATE_CAPABILITY, "Authenticate a caller identity."),
    (_ACCESS_AUTHORIZE_CAPABILITY, "Authorize a caller's requested capability."),
    (_SECRET_GET_CAPABILITY, "Resolve a secret handle to its plaintext value."),
    (_SECRET_PUT_CAPABILITY, "Store or rotate a tenant-scoped secret under a handle."),
    (_SIGNATURE_VERIFY_CAPABILITY, "Verify a cryptographic signature."),
    (_BOOTSTRAP_CREATE_ADMIN_CAPABILITY, "Create the first tenant administrator on a fresh install."),
    (_AUTH_CHANGE_PASSWORD_CAPABILITY, "Change the caller's own password."),
    (_AUTH_REQUEST_PASSWORD_RESET_CAPABILITY, "Request a password-reset email for an account."),
    (_AUTH_RESET_PASSWORD_CAPABILITY, "Redeem a password-reset token to set a new password."),
    (_PRINCIPAL_REGISTER_CAPABILITY, "Create a new tenant user (admin-provisioned)."),
    (_PRINCIPAL_SET_EMAIL_CAPABILITY, "Set the caller's own contact email."),
    (_OAUTH_GET_CONFIG_CAPABILITY, "Report which OAuth sign-in providers are configured."),
    (_OAUTH_LOGIN_BEGIN_CAPABILITY, "Begin an OAuth sign-in flow for an existing account."),
    (_OAUTH_LOGIN_COMPLETE_CAPABILITY, "Complete an OAuth sign-in flow."),
    (_OAUTH_LINK_BEGIN_CAPABILITY, "Begin linking an OAuth identity to the caller's own account."),
    (_OAUTH_LINK_COMPLETE_CAPABILITY, "Complete linking an OAuth identity to the caller's own account."),
    (_OAUTH_UNLINK_CAPABILITY, "Remove the caller's own linked OAuth identity for a provider."),
    (_OAUTH_LIST_LINKS_CAPABILITY, "List the caller's own linked OAuth providers."),
]

# RBAC permission requirements per capability. `kortex.security.auth.authenticate`
# and `kortex.security.bootstrap.create_admin` are deliberately absent: both are
# bootstrap-exempt capabilities (`requires_authentication=False`), so no RBAC
# permission requirement applies to either — each instead fails closed via its
# own handler logic (wrong credentials / bootstrap already closed).
#
# `security:secret:write` (M7.3) is deliberately a distinct permission from
# `security:read` (which already gates the read-only `secret.get`) rather
# than reusing it or `security:read`'s write-capable sibling from another
# engine's taxonomy — write access to arbitrary tenant secrets is more
# sensitive than read access to Security Engine's own authorize/verify
# capabilities and must be independently grantable/revocable. A fresh
# install's bootstrap-created admin receives it automatically (M7.1's
# `bootstrap_create_admin` grants the union of every capability's
# `required_permissions` registered at that moment); an admin bootstrapped
# before M7.3 shipped must be granted it manually, the same manual step
# already required for any RBAC permission on this platform, since no
# permission-provisioning API exists yet (a pre-existing gap, not introduced
# here).
# Phase A: capabilities whose handler must learn "who is calling" only from
# the dispatcher-injected `CapabilityExecutionContext` (KORTEX Platform
# Security — Capability Identity Propagation) — never from a caller-supplied
# parameter. `change_password`/`set_email` act on the caller's own identity;
# `register` needs the caller's tenant to scope the new principal to.
#
# Phase B adds `secret.get`: it must decrypt under the caller's REAL tenant,
# never a tenant named in `request.parameters` (see `get_secret_capability`).
_EXECUTION_CONTEXT_REQUIRED_CAPABILITIES = frozenset(
    {
        _SECRET_GET_CAPABILITY,
        _AUTH_CHANGE_PASSWORD_CAPABILITY,
        _PRINCIPAL_REGISTER_CAPABILITY,
        _PRINCIPAL_SET_EMAIL_CAPABILITY,
        _OAUTH_LINK_BEGIN_CAPABILITY,
        _OAUTH_LINK_COMPLETE_CAPABILITY,
        _OAUTH_UNLINK_CAPABILITY,
        _OAUTH_LIST_LINKS_CAPABILITY,
    }
)

_CANONICAL_CAPABILITY_PERMISSIONS: dict[str, list[str]] = {
    _ACCESS_AUTHORIZE_CAPABILITY: ["security:read"],
    _SECRET_GET_CAPABILITY: ["security:read"],
    _SECRET_PUT_CAPABILITY: ["security:secret:write"],
    _SIGNATURE_VERIFY_CAPABILITY: ["security:read"],
    # `change_password`/`set_email` are self-service (any authenticated
    # principal acts on their own identity — the handler derives which one
    # from the injected `principal`, never a caller-supplied target) so, like
    # `authorize`/`signature.verify`, no RBAC permission beyond "is
    # authenticated" applies. `register` is admin-only: a fresh install's
    # bootstrap-created admin receives `security:principal:write`
    # automatically via `bootstrap_create_admin`'s existing dynamic
    # `_list_capabilities` permission-union mechanism — the same mechanism
    # that already grants `security:secret:write` (M7.3), no new code
    # required. `request_password_reset`/`reset_password` are deliberately
    # absent here too: both are bootstrap-exempt (`requires_authentication=
    # False`), matching `authenticate`'s own precedent.
    _PRINCIPAL_REGISTER_CAPABILITY: ["security:principal:write"],
}

# Maps `PrincipalType` (auth/RBAC vocabulary: USER/SERVICE_PRINCIPAL/AGENT) to
# `UniversalAuditEntry.actor_type`'s own, separate frozen vocabulary
# (shared_domain_models.md S11: HUMAN/AI_AGENT/SYSTEM_ENGINE/CONNECTOR).
# SERVICE_PRINCIPAL -> CONNECTOR is an implementation decision (closest
# available category for an external service credential), not a frozen
# mandate — flagged as such rather than silently assumed.
_PRINCIPAL_TYPE_TO_ACTOR_TYPE: dict[str, str] = {
    "USER": "HUMAN",
    "AGENT": "AI_AGENT",
    "SERVICE_PRINCIPAL": "CONNECTOR",
}


def _actor_type_for_principal_type(principal_type: str) -> str:
    """Fail-closed to `SYSTEM_ENGINE` for any unrecognized principal type
    string, rather than propagating an unknown value into the audit trail."""
    return _PRINCIPAL_TYPE_TO_ACTOR_TYPE.get(principal_type, "SYSTEM_ENGINE")


class SecurityEngine(BaseEngine, ISecurityEngine, IEngineDiagnostics):
    """KORTEX Security Engine Core Facade providing M1 lifecycle, M2 SecretStore,
    M3 AuthenticationManager, M4 AuthorizationEngine, M5 Capability Enforcement,
    and M6 AuditManager + Signature Verification."""

    def __init__(
        self,
        crypto_provider: ICryptoProvider | None = None,
        data_store: IDataStore | None = None,
        master_key: bytes | None = None,
        signing_private_key: bytes | None = None,
        event_engine: EventEngine | None = None,
        email_provider: IEmailProvider | None = None,
        oauth_providers: dict[str, IOAuthProvider] | None = None,
    ) -> None:
        """Initialize SecurityEngine instance.

        Args:
            crypto_provider: Optional cryptographic provider override (defaults
                to `LocalCrypto()`).
            data_store: Optional explicit `IDataStore` injection — if omitted,
                resolved from the Kernel's registered `storage` engine during
                `initialize()`.
            master_key: Optional already-decoded 32-byte master key, for
                deterministic tests — if omitted, resolved from the
                `KORTEX_MASTER_KEY` configuration value during `initialize()`.
            signing_private_key: Optional already-decoded 32-byte Ed25519
                signing key, for deterministic tests — if omitted, resolved
                from the `KORTEX_AUTH_SIGNING_PRIVATE_KEY` configuration value
                during `initialize()`.
            event_engine: Optional explicit `EventEngine` injection — if omitted,
                resolved from the Kernel during `initialize()`.
            email_provider: Optional explicit `IEmailProvider` injection
                (Phase A), for deterministic tests — if omitted, resolved
                from `KORTEX_EMAIL_PROVIDER` during `initialize()`, defaulting
                to `DevLogEmailProvider` (the only implementation that
                exists) when unset.
            oauth_providers: Optional explicit `{provider_id: IOAuthProvider}`
                injection (Phase A), for deterministic tests — if omitted,
                resolved from `GOOGLE_OAUTH_CLIENT_ID`/`_SECRET` and
                `MICROSOFT_OAUTH_CLIENT_ID`/`_SECRET` during `initialize()`.
                A provider is present in the resulting dict only when both
                its client ID and secret are configured.
        """
        super().__init__()
        self._crypto_provider: ICryptoProvider = crypto_provider if crypto_provider is not None else LocalCrypto()
        self._data_store_override: IDataStore | None = data_store
        self._master_key_override: bytes | None = master_key
        self._signing_private_key_override: bytes | None = signing_private_key
        self._event_engine_override: EventEngine | None = event_engine
        self._email_provider_override: IEmailProvider | None = email_provider
        self._oauth_providers_override: dict[str, IOAuthProvider] | None = oauth_providers
        self._secret_store: SecretStore | None = None
        self._authentication_manager: AuthenticationManager | None = None
        self._authorization_engine: AuthorizationEngine | None = None
        self._audit_manager: AuditManager | None = None
        self._email_provider: IEmailProvider | None = None
        self._oauth_providers: dict[str, IOAuthProvider] = {}
        # M7.1: bound `Kernel.list_capabilities` method only (never the full
        # `Kernel` instance) — the narrowest capture that lets
        # `bootstrap_create_admin` discover the union of every currently
        # registered capability's `required_permissions` at call time, so
        # the first bootstrap-created administrator is granted a working
        # permission set without hand-maintaining a duplicate list here that
        # would silently drift out of sync with every other engine's own
        # capability registrations.
        self._list_capabilities: Callable[[], list[Any]] | None = None
        self._verification_service: VerificationService = VerificationService(crypto_provider=self._crypto_provider)
        self._registered_capabilities: list[str] = []
        self._metrics: dict[str, Any] = {
            "capabilities_registered": 0,
            "signature_verifications": 0,
        }

    @property
    def name(self) -> str:
        """Unique identifier name for this engine."""
        return "security"

    @property
    def dependencies(self) -> list[str]:
        """Names of prerequisite foundation engines."""
        return ["storage", "registry"]

    @property
    def authentication_manager(self) -> AuthenticationManager:
        """Return the initialized `AuthenticationManager`.

        Raises `SecurityEngineError` if accessed before `initialize()` has
        completed.
        """
        if self._authentication_manager is None:
            raise SecurityEngineError("AuthenticationManager is not initialized.")
        return self._authentication_manager

    @property
    def authorization_engine(self) -> AuthorizationEngine:
        """Return the initialized `AuthorizationEngine`.

        Raises `SecurityEngineError` if accessed before `initialize()` has
        completed.
        """
        if self._authorization_engine is None:
            raise SecurityEngineError("AuthorizationEngine is not initialized.")
        return self._authorization_engine

    @property
    def secret_store(self) -> SecretStore:
        """Return the initialized `SecretStore`.

        Raises `SecurityEngineError` if accessed before `initialize()` has
        completed.
        """
        if self._secret_store is None:
            raise SecurityEngineError("SecretStore is not initialized.")
        return self._secret_store

    @property
    def audit_manager(self) -> AuditManager:
        """Return the initialized `AuditManager` (Milestone M6).

        Raises `SecurityEngineError` if accessed before `initialize()` has
        completed.
        """
        if self._audit_manager is None:
            raise SecurityEngineError("AuditManager is not initialized.")
        return self._audit_manager

    @property
    def verification_service(self) -> VerificationService:
        """Return the initialized `VerificationService`."""
        return self._verification_service

    # -- Lifecycle Implementation ---------------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Build the M2 `SecretStore`, M3 `AuthenticationManager`, M4
        `AuthorizationEngine`, and M6 `AuditManager`, then register the four
        canonical capabilities.
        """
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Security Engine (Milestones M1-M6)...")

        try:
            self._secret_store = self._build_secret_store(kernel)
            self._authentication_manager = self._build_authentication_manager(kernel)
            self._authorization_engine = self._build_authorization_engine(kernel)
            self._audit_manager = self._build_audit_manager(kernel)
            self._email_provider = self._build_email_provider(kernel)
            self._oauth_providers = self._build_oauth_providers(kernel)
            self._list_capabilities = kernel.list_capabilities

            for capability_name, description in _CANONICAL_CAPABILITIES:
                requires_authentication = True
                if capability_name == _SECRET_GET_CAPABILITY:
                    # Phase B / B-3: was `self._secret_store.get_secret`
                    # bound directly, which made the caller-supplied
                    # `tenant_id` authoritative. See `get_secret_capability`.
                    handler: Callable[..., Any] = self.get_secret_capability
                    capability_description = (
                        f"{description} Encrypted, fail-closed, tenant-bound to the verified caller (M2 + Phase B)."
                    )
                elif capability_name == _SECRET_PUT_CAPABILITY:
                    handler = self.put_secret_capability
                    capability_description = f"{description} Encrypted, tenant-scoped, audited (Milestone M7.3)."
                elif capability_name == _AUTH_AUTHENTICATE_CAPABILITY:
                    # `self.authenticate` (not the raw manager method) so this
                    # capability's real dispatch path is also audited (M6).
                    handler = self.authenticate
                    capability_description = f"{description} Fail-closed, audited (Milestones M3 + M6)."
                    # The bootstrap exception: reachable before any session token exists.
                    requires_authentication = False
                elif capability_name == _ACCESS_AUTHORIZE_CAPABILITY:
                    # `self.authorize` (not the raw engine method) so this
                    # capability's real dispatch path is also audited (M6).
                    handler = self.authorize
                    capability_description = (
                        f"{description} Fail-closed, hybrid RBAC+ABAC, audited (Milestones M4 + M6)."
                    )
                elif capability_name == _SIGNATURE_VERIFY_CAPABILITY:
                    handler = self._verify_signature_capability_handler
                    capability_description = f"{description} Cryptographic verification (Milestone M6)."
                elif capability_name == _BOOTSTRAP_CREATE_ADMIN_CAPABILITY:
                    handler = self.bootstrap_create_admin
                    capability_description = f"{description} Fail-closed after first use (Milestone M7.1)."
                    # The second (and still deliberately narrow) bootstrap
                    # exception: reachable before any session token exists.
                    requires_authentication = False
                elif capability_name == _AUTH_CHANGE_PASSWORD_CAPABILITY:
                    handler = self.change_password_capability
                    capability_description = f"{description} Self-service, audited (Phase A)."
                elif capability_name == _AUTH_REQUEST_PASSWORD_RESET_CAPABILITY:
                    handler = self.request_password_reset_capability
                    capability_description = f"{description} Enumeration-resistant, audited (Phase A)."
                    # Bootstrap-exempt: reachable from the (signed-out) login
                    # screen's "Forgot password?" link, exactly like
                    # `authenticate` itself.
                    requires_authentication = False
                elif capability_name == _AUTH_RESET_PASSWORD_CAPABILITY:
                    handler = self.reset_password_capability
                    capability_description = f"{description} Single-use token, audited (Phase A)."
                    # Bootstrap-exempt: the reset token itself is the
                    # credential — there is no session yet to authenticate.
                    requires_authentication = False
                elif capability_name == _PRINCIPAL_REGISTER_CAPABILITY:
                    handler = self.register_principal_capability
                    capability_description = f"{description} Admin-only, audited (Phase A)."
                elif capability_name == _PRINCIPAL_SET_EMAIL_CAPABILITY:
                    handler = self.set_email_capability
                    capability_description = f"{description} Self-service, audited (Phase A)."
                elif capability_name == _OAUTH_GET_CONFIG_CAPABILITY:
                    handler = self.oauth_get_config_capability
                    capability_description = f"{description} Never reveals secret material (Phase A)."
                    requires_authentication = False
                elif capability_name == _OAUTH_LOGIN_BEGIN_CAPABILITY:
                    handler = self.oauth_login_begin_capability
                    capability_description = f"{description} Bootstrap-exempt (Phase A)."
                    requires_authentication = False
                elif capability_name == _OAUTH_LOGIN_COMPLETE_CAPABILITY:
                    handler = self.oauth_login_complete_capability
                    capability_description = f"{description} Bootstrap-exempt; mints a session on success (Phase A)."
                    requires_authentication = False
                elif capability_name == _OAUTH_LINK_BEGIN_CAPABILITY:
                    handler = self.oauth_link_begin_capability
                    capability_description = f"{description} Self-service, audited (Phase A)."
                elif capability_name == _OAUTH_LINK_COMPLETE_CAPABILITY:
                    handler = self.oauth_link_complete_capability
                    capability_description = f"{description} Self-service, audited (Phase A)."
                elif capability_name == _OAUTH_UNLINK_CAPABILITY:
                    handler = self.oauth_unlink_capability
                    capability_description = f"{description} Self-service, audited (Phase A)."
                elif capability_name == _OAUTH_LIST_LINKS_CAPABILITY:
                    handler = self.oauth_list_links_capability
                    capability_description = f"{description} Self-service (Phase A)."
                else:
                    handler = self._make_not_implemented_handler(capability_name)
                    capability_description = f"{description} NOT IMPLEMENTED."

                kernel.register_capability(
                    name=capability_name,
                    description=capability_description,
                    provider=self.name,
                    handler=handler,
                    requires_authentication=requires_authentication,
                    required_permissions=_CANONICAL_CAPABILITY_PERMISSIONS.get(capability_name),
                    requires_execution_context=capability_name in _EXECUTION_CONTEXT_REQUIRED_CAPABILITIES,
                )
                self._registered_capabilities.append(capability_name)

            self._metrics["capabilities_registered"] = len(self._registered_capabilities)
            self._set_state(EngineState.READY)
            self.logger.info(
                "Security Engine initialized successfully "
                "(M2 SecretStore + M3 Auth + M4 Authorization + M6 Audit active)."
            )
        except Exception as e:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize Security Engine: %s", e, exc_info=True)
            raise

    def _resolve_data_store(self, kernel: Kernel) -> IDataStore:
        """Resolve `IDataStore` from override or Kernel `storage` engine."""
        data_store = self._data_store_override
        if data_store is None:
            storage_engine = kernel.get_engine("storage")
            resolved = getattr(storage_engine, "data", None)
            if resolved is None:
                raise SecurityEngineError("Storage Engine did not provide an IDataStore instance.")
            data_store = resolved
        return data_store

    def _build_secret_store(self, kernel: Kernel) -> SecretStore:
        """Resolve `IDataStore` and master key, then construct `SecretStore`."""
        data_store = self._resolve_data_store(kernel)
        master_key = self._master_key_override
        if master_key is None:
            raw_key = kernel.get_config(_MASTER_KEY_CONFIG_KEY)
            if not raw_key:
                raise MasterKeyError(
                    f"{_MASTER_KEY_CONFIG_KEY} is not configured. "
                    "Security Engine cannot initialize SecretStore without it."
                )
            master_key = SecretStore.decode_master_key(raw_key)

        return SecretStore(data_store=data_store, crypto_provider=self._crypto_provider, master_key=master_key)

    def _build_authentication_manager(self, kernel: Kernel) -> AuthenticationManager:
        """Resolve `IDataStore` and Ed25519 signing key, then construct `AuthenticationManager`."""
        data_store = self._resolve_data_store(kernel)
        signing_private_key = self._signing_private_key_override
        if signing_private_key is None:
            raw_key = kernel.get_config(_AUTH_SIGNING_KEY_CONFIG_KEY)
            if not raw_key:
                raise SigningKeyError(
                    f"{_AUTH_SIGNING_KEY_CONFIG_KEY} is not configured. "
                    "Security Engine cannot initialize AuthenticationManager without it."
                )
            signing_private_key = AuthenticationManager.decode_signing_key(raw_key)

        return AuthenticationManager(
            data_store=data_store, crypto_provider=self._crypto_provider, signing_private_key=signing_private_key
        )

    def _build_authorization_engine(self, kernel: Kernel) -> AuthorizationEngine:
        """Resolve `IDataStore` and shared `ICacheStore`, then construct `AuthorizationEngine`."""
        data_store = self._resolve_data_store(kernel)
        cache_store: ICacheStore | None = None
        if self._data_store_override is None:
            storage_engine = kernel.get_engine("storage")
            cache_store = getattr(storage_engine, "cache", None)

        return AuthorizationEngine(data_store=data_store, cache_store=cache_store)

    def _resolve_file_store(self, kernel: Kernel) -> IFileStore:
        """Resolve `IFileStore` from the Kernel's `storage` engine (Phase A:
        `DevLogEmailProvider`'s sandboxed outbox)."""
        storage_engine = kernel.get_engine("storage")
        resolved = getattr(storage_engine, "file", None)
        if resolved is None:
            raise SecurityEngineError("Storage Engine did not provide an IFileStore instance.")
        return cast(IFileStore, resolved)

    def _build_email_provider(self, kernel: Kernel) -> IEmailProvider:
        """Resolve `KORTEX_EMAIL_PROVIDER` (Phase A). Only `"dev_log"` (the
        default when unset) resolves to anything today — no real
        SMTP/API provider is configured yet. Never fails closed the way
        `SecretStore`/`AuthenticationManager` do for their own keys: email
        delivery is not a security boundary, and `DevLogEmailProvider`
        itself already logs a loud warning on first use so this is never
        silently mistaken for real delivery.
        """
        if self._email_provider_override is not None:
            return self._email_provider_override

        provider_name = (kernel.get_config("KORTEX_EMAIL_PROVIDER") or "dev_log").strip().lower()
        if provider_name not in ("", "dev_log"):
            self.logger.warning(
                "KORTEX_EMAIL_PROVIDER=%r is not a recognized provider; falling back to dev_log.", provider_name
            )
        return DevLogEmailProvider(file_store=self._resolve_file_store(kernel))

    def _build_oauth_providers(self, kernel: Kernel) -> dict[str, IOAuthProvider]:
        """Resolve configured OAuth providers (Phase A). A provider is
        present in the result only when *both* its client ID and secret are
        configured — never partially, never with a placeholder/generated
        credential. An unconfigured provider is simply absent, reported as
        such by `oauth.get_config`, and every OAuth capability that needs it
        fails closed with `OAuthProviderNotConfiguredError`.
        """
        if self._oauth_providers_override is not None:
            return self._oauth_providers_override

        providers: dict[str, IOAuthProvider] = {}

        google_client_id = kernel.get_config("GOOGLE_OAUTH_CLIENT_ID")
        google_client_secret = kernel.get_config("GOOGLE_OAUTH_CLIENT_SECRET")
        if google_client_id and google_client_secret:
            providers["google"] = GoogleOAuthProvider(client_id=google_client_id, client_secret=google_client_secret)

        microsoft_client_id = kernel.get_config("MICROSOFT_OAUTH_CLIENT_ID")
        microsoft_client_secret = kernel.get_config("MICROSOFT_OAUTH_CLIENT_SECRET")
        if microsoft_client_id and microsoft_client_secret:
            providers["microsoft"] = MicrosoftOAuthProvider(
                client_id=microsoft_client_id, client_secret=microsoft_client_secret
            )

        return providers

    def _build_audit_manager(self, kernel: Kernel) -> AuditManager:
        """Resolve `IDataStore` and optional `EventEngine`, then construct `AuditManager`."""
        data_store = self._resolve_data_store(kernel)
        event_engine = self._event_engine_override
        if event_engine is None:
            try:
                resolved_event = kernel.get_engine("event")
                if resolved_event is not None:
                    event_engine = cast("EventEngine", resolved_event)
            except Exception:
                event_engine = None

        return AuditManager(
            data_store=data_store,
            event_engine=event_engine,
            crypto_provider=self._crypto_provider,
        )

    async def start(self) -> None:
        """Start the Security Engine."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Security Engine is RUNNING.")

    async def stop(self) -> None:
        """Gracefully shut down the Security Engine."""
        if self._state in (EngineState.STOPPED, EngineState.UNINITIALIZED):
            return

        self._set_state(EngineState.STOPPING)
        self.logger.info("Stopping Security Engine...")
        self._set_state(EngineState.STOPPED)
        self.logger.info("Security Engine stopped cleanly.")

    async def health_check(self) -> dict[str, Any]:
        """Perform diagnostic health check."""
        return self.health()

    # -- Capability Handlers --------------------------------------------------

    async def _verify_signature_capability_handler(
        self,
        data: bytes | str,
        signature: bytes | str | dict[str, Any] | CryptographicSignature,
        public_key: bytes | str | None = None,
        algorithm: str = "ed25519",
        **_extra: Any,
    ) -> bool:
        """Capability handler for `kortex.security.signature.verify`."""
        self._metrics["signature_verifications"] += 1
        try:
            data_bytes = data.encode("utf-8") if isinstance(data, str) else data
            if not isinstance(data_bytes, bytes):
                return False

            if isinstance(signature, CryptographicSignature):
                return self._verification_service.verify_signature(data_bytes, signature)

            if isinstance(signature, dict):
                raw_sig = signature.get("signature")
                raw_pub = signature.get("public_key")
                alg = str(signature.get("algorithm", "ed25519"))

                sig_bytes = bytes.fromhex(raw_sig) if isinstance(raw_sig, str) else raw_sig
                pub_bytes = bytes.fromhex(raw_pub) if isinstance(raw_pub, str) else raw_pub

                if not isinstance(sig_bytes, bytes) or not isinstance(pub_bytes, bytes):
                    return False

                sig_obj = CryptographicSignature(
                    algorithm=alg,
                    signature=sig_bytes,
                    public_key=pub_bytes,
                )
                return self._verification_service.verify_signature(data_bytes, sig_obj)

            # Raw signature bytes or hex
            sig_b = bytes.fromhex(signature) if isinstance(signature, str) else signature
            if not isinstance(sig_b, bytes) or public_key is None:
                return False

            pub_b = bytes.fromhex(public_key) if isinstance(public_key, str) else public_key
            if not isinstance(pub_b, bytes):
                return False

            sig_obj = CryptographicSignature(
                algorithm=algorithm,
                signature=sig_b,
                public_key=pub_b,
            )
            return self._verification_service.verify_signature(data_bytes, sig_obj)
        except Exception:
            return False

    async def bootstrap_create_admin(
        self,
        tenant_id: str,
        principal_id: str,
        password: str,
    ) -> dict[str, Any]:
        """Capability handler for `kortex.security.bootstrap.create_admin` (M7.1).

        Creates the very first tenant/administrator on a fresh install (see
        `AuthenticationManager.bootstrap_first_admin` for the fail-closed,
        concurrency-safe transaction this delegates to). Grants the new
        administrator every RBAC permission currently declared by any
        registered capability — gathered dynamically from `_list_capabilities`
        rather than a hand-maintained list, so it never drifts out of sync
        with what other engines actually register. This is the only way a
        first-run desktop administrator can use the application immediately
        after signing in: RBAC otherwise fails closed for every
        unprovisioned role (`rbac.py`'s own documented behavior), and this is
        a single-tenant desktop install being bootstrapped by its own first
        (and, until it invites others, only) user.

        Records an audit entry mirroring `authenticate()`'s own pattern on
        both outcomes. Never audits, logs, or returns the submitted password.
        """
        permissions: list[str] = []
        if self._list_capabilities is not None:
            granted: set[str] = set()
            for descriptor in self._list_capabilities():
                if descriptor.required_permissions:
                    granted.update(descriptor.required_permissions)
            permissions = sorted(granted)

        safe_tenant_id = tenant_id if isinstance(tenant_id, str) and tenant_id else "unknown"
        safe_principal_id = principal_id if isinstance(principal_id, str) and principal_id else "unknown"

        try:
            await self.authentication_manager.bootstrap_first_admin(
                tenant_id=tenant_id,
                principal_id=principal_id,
                password=password,
                roles=[_BOOTSTRAP_ADMIN_ROLE],
                permissions=permissions,
            )
        except Exception as exc:
            await self._record_security_audit(
                action=_BOOTSTRAP_CREATE_ADMIN_CAPABILITY,
                actor_id=safe_principal_id,
                actor_type="HUMAN",
                tenant_id=safe_tenant_id,
                context={"result": "failure", "reason": type(exc).__name__},
            )
            raise

        await self._record_security_audit(
            action=_BOOTSTRAP_CREATE_ADMIN_CAPABILITY,
            actor_id=safe_principal_id,
            actor_type="HUMAN",
            tenant_id=safe_tenant_id,
            context={"result": "success"},
        )
        return {"created": True, "tenant_id": tenant_id, "principal_id": principal_id}

    async def is_bootstrap_required(self) -> bool:
        """Whether first-run bootstrap is still available (Milestone M7.1).

        Deliberately not its own Kernel capability — consulted directly by
        `Kernel.health_check()`, reusing the already-established
        unauthenticated `/health` diagnostic surface instead of widening the
        capability-registry's bootstrap-exemption allowlist a third time for
        a read that has an existing, more appropriate home. See
        `AuthenticationManager.is_bootstrap_required` for the query itself.
        """
        return await self.authentication_manager.is_bootstrap_required()

    def _make_not_implemented_handler(self, capability_name: str) -> Callable[..., Any]:
        """Build a handler that fails closed for unimplemented capabilities."""

        async def _handler(*_args: Any, **_kwargs: Any) -> Any:
            raise SecurityEngineError(
                f"Security Engine capability '{capability_name}' is not implemented.",
                code="NOT_IMPLEMENTED",
            )

        return _handler

    # -- ISecurityEngine Protocol Implementation ------------------------------

    async def authenticate(self, credentials: dict[str, Any]) -> SecurityPrincipal:
        """Authenticate a caller identity.

        Records an audit entry and publishes a typed success/failure event
        (Milestone M6) — identity for the audit record comes from the
        supplied `credentials` on failure (the only identity available
        before a `SecurityPrincipal` exists) and from the resulting
        `SecurityPrincipal` on success.
        """
        # `credentials` is untrusted, caller-supplied input — `AuthenticationManager.authenticate`
        # is required to fail closed with `AuthenticationError` even for non-dict shapes (`None`,
        # a bare string, etc.), so this audit-context extraction must never itself raise for those
        # shapes ahead of the real authentication check.
        safe_credentials: dict[str, Any] = credentials if isinstance(credentials, dict) else {}
        raw_tenant_id = safe_credentials.get("tenant_id")
        raw_principal_id = safe_credentials.get("principal_id")
        attempted_tenant_id = str(raw_tenant_id) if raw_tenant_id else "unknown"
        attempted_principal_id = str(raw_principal_id) if raw_principal_id else "unknown"
        attempted_principal_type = str(safe_credentials.get("principal_type") or "USER")

        try:
            principal = await self.authentication_manager.authenticate(credentials)
        except Exception as exc:
            await self._record_security_audit(
                action=_AUTH_AUTHENTICATE_CAPABILITY,
                actor_id=attempted_principal_id,
                actor_type=_actor_type_for_principal_type(attempted_principal_type),
                tenant_id=attempted_tenant_id,
                context={"result": "failure", "reason": type(exc).__name__},
            )
            if self._audit_manager is not None:
                await self._audit_manager.publish_security_event(
                    SecurityAuthFailureEvent(
                        tenant_id=attempted_tenant_id,
                        principal_id=attempted_principal_id,
                        reason=type(exc).__name__,
                    )
                )
            raise

        await self._record_security_audit(
            action=_AUTH_AUTHENTICATE_CAPABILITY,
            actor_id=principal.principal_id,
            actor_type=_actor_type_for_principal_type(principal.principal_type.value),
            tenant_id=principal.tenant_id,
            context={"result": "success"},
        )
        if self._audit_manager is not None:
            await self._audit_manager.publish_security_event(
                SecurityAuthSuccessEvent(
                    tenant_id=principal.tenant_id,
                    principal_id=principal.principal_id,
                    principal_type=principal.principal_type.value,
                )
            )
        return principal

    async def authorize(
        self,
        principal: SecurityPrincipal,
        requirement: PermissionRequirement,
        context: dict[str, Any] | None = None,
    ) -> AccessDecision:
        """Authorize a caller's requested capability.

        Records an audit entry and publishes a typed grant/deny event
        (Milestone M6) for every decision, not only denials — both are
        "security events" per the frozen audit requirement.
        """
        decision = await self.authorization_engine.authorize(principal, requirement, context)

        await self._record_security_audit(
            action=requirement.capability_name,
            actor_id=principal.principal_id,
            actor_type=_actor_type_for_principal_type(principal.principal_type.value),
            tenant_id=principal.tenant_id,
            context={"decision_code": decision.decision_code, "is_allowed": decision.is_allowed},
        )
        if self._audit_manager is not None:
            if decision.is_allowed:
                await self._audit_manager.publish_security_event(
                    SecurityAccessGrantedEvent(
                        tenant_id=principal.tenant_id,
                        principal_id=principal.principal_id,
                        capability_name=requirement.capability_name,
                        decision_code=decision.decision_code,
                    )
                )
            else:
                await self._audit_manager.publish_security_event(
                    SecurityAccessDeniedEvent(
                        tenant_id=principal.tenant_id,
                        principal_id=principal.principal_id,
                        capability_name=requirement.capability_name,
                        reason=decision.reason,
                        decision_code=decision.decision_code,
                    )
                )
        return decision

    async def _record_security_audit(
        self,
        action: str,
        actor_id: str,
        actor_type: str,
        tenant_id: str,
        resource_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort audit recording (Milestone M6).

        Audit-persistence failure is logged and swallowed rather than
        propagated — an audit-store outage must not itself become a
        fail-closed lockout of authentication/authorization, mirroring
        `AuditManager.record_audit_entry`'s own non-blocking treatment of
        event-publish failures. This is an implementation decision, not a
        frozen mandate; the spec does not state what should happen if audit
        persistence itself fails.
        """
        if self._audit_manager is None:
            return
        try:
            await self._audit_manager.record_event(
                action=action,
                actor_id=actor_id,
                actor_type=actor_type,
                tenant_id=tenant_id,
                resource_id=resource_id,
                context=context or {},
            )
        except Exception as exc:
            self.logger.warning("Failed to record security audit entry for '%s': %s", action, exc)

    async def verify_signature(self, data: bytes, signature: CryptographicSignature) -> bool:
        """Verify a cryptographic signature."""
        self._metrics["signature_verifications"] += 1
        return self._verification_service.verify_signature(data, signature)

    async def get_secret(self, secret_handle: str, tenant_id: str) -> str:
        """Resolve a secret handle to its plaintext value."""
        return await self.secret_store.get_secret(secret_handle, tenant_id)

    async def put_secret(self, secret_handle: str, tenant_id: str, plaintext: str) -> SecretEntry:
        """Encrypt and persist a secret under a handle."""
        return await self.secret_store.put_secret(secret_handle, tenant_id, plaintext)

    async def get_secret_capability(
        self,
        secret_handle: str,
        tenant_id: str | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        **_extra: Any,
    ) -> str:
        """Capability handler for `kortex.security.secret.get` (Phase B / B-3).

        Closes the gap `put_secret_capability`'s docstring flagged when M7.3
        shipped. Before this, the capability registered
        `SecretStore.get_secret` as its handler *directly*, so its
        `tenant_id` argument came straight from `request.parameters` and was
        fully authoritative: any principal holding `security:read` — a
        deliberately broad permission that also gates `authorize` and
        `signature.verify` — could decrypt ANY tenant's secret merely by
        naming that tenant. `SecretStore`'s own AES-GCM tenant binding did
        not help: it authenticates that the ciphertext belongs to the
        `tenant_id` it is given, and it was being given the attacker's.

        The tenant now comes from the dispatcher-verified
        `CapabilityExecutionContext` (`requires_execution_context=True`), so
        the AAD binding is finally checked against the caller's *real*
        tenant. A caller-supplied `tenant_id` survives only when no verified
        identity is present — in-process/system callers, which reach the
        engine method (`get_secret`) rather than this capability anyway.

        The plaintext is returned to the authorized caller, as before, but
        is never logged, never placed in an audit `context`, and never
        included in an exception message. `**_extra` absorbs any additional
        caller-supplied parameter (notably a `tenant_id` alias) rather than
        raising a `TypeError` that would leak handler shape.
        """
        resolved = self._tenant_from_context(execution_context, tenant_id)
        if not resolved:
            raise SecretStoreError("tenant_id is required to resolve a secret.")

        store = self.secret_store
        try:
            plaintext = await store.get_secret(secret_handle, resolved)
        except Exception:
            await self._record_security_audit(
                action=_SECRET_GET_CAPABILITY,
                actor_id=self._actor_id_from_context(execution_context),
                actor_type=self._actor_type_from_context(execution_context),
                tenant_id=resolved,
                resource_id=secret_handle,
                context={"result": "failure"},
            )
            raise

        await self._record_security_audit(
            action=_SECRET_GET_CAPABILITY,
            actor_id=self._actor_id_from_context(execution_context),
            actor_type=self._actor_type_from_context(execution_context),
            tenant_id=resolved,
            resource_id=secret_handle,
            context={"result": "success"},
        )
        return plaintext

    @staticmethod
    def _tenant_from_context(
        execution_context: CapabilityExecutionContext | None,
        claimed_tenant_id: str | None,
    ) -> str | None:
        """Authoritative tenant for one invocation: the verified one, or the
        caller's claim only when no identity was injected."""
        principal = execution_context.principal if execution_context is not None else None
        if principal is not None and principal.tenant_id:
            return principal.tenant_id
        return claimed_tenant_id

    @staticmethod
    def _actor_id_from_context(execution_context: CapabilityExecutionContext | None) -> str:
        principal = execution_context.principal if execution_context is not None else None
        return principal.principal_id if principal is not None else "system"

    @staticmethod
    def _actor_type_from_context(execution_context: CapabilityExecutionContext | None) -> str:
        principal = execution_context.principal if execution_context is not None else None
        if principal is None:
            return "SYSTEM_ENGINE"
        return _actor_type_for_principal_type(principal.principal_type.value)

    async def put_secret_capability(
        self,
        secret_handle: str,
        plaintext: str,
        tenant_id: str | None = None,
        principal: SecurityPrincipal | None = None,
    ) -> dict[str, Any]:
        """Capability handler for `kortex.security.secret.put` (M7.3).

        Derives tenant identity from the Kernel-verified `principal` whenever
        one is present, exactly like every other M7.3 capability. A
        caller-supplied `tenant_id` is used only when no principal was
        injected (trusted internal callers). Phase B gave the read side the
        same treatment via `get_secret_capability` — the gap this docstring
        used to flag as pre-existing and unclosed.

        Never returns the plaintext value, in the response, an exception, or
        the generic dispatch audit context (the Kernel dispatcher's own
        `sanitize_for_persistence` already redacts any parameter whose *key*
        matches `plaintext`/`secret`/etc., independent of this handler).
        Publishes `SecuritySecretModifiedEvent` (operation=PUT) -- the event
        class already existed but no code path had ever published it before
        this capability existed.
        """
        tid = principal.tenant_id if principal is not None else tenant_id
        if not tid:
            raise SecretStoreError("tenant_id is required to store a secret.")

        entry = await self.secret_store.put_secret(secret_handle, tid, plaintext)

        actor_id = principal.principal_id if principal is not None else "system"
        actor_type = (
            _actor_type_for_principal_type(principal.principal_type.value) if principal is not None else "SYSTEM_ENGINE"
        )
        await self._record_security_audit(
            action=_SECRET_PUT_CAPABILITY,
            actor_id=actor_id,
            actor_type=actor_type,
            tenant_id=tid,
            resource_id=secret_handle,
            context={"result": "success"},
        )
        if self._audit_manager is not None:
            await self._audit_manager.publish_security_event(
                SecuritySecretModifiedEvent(
                    tenant_id=tid,
                    secret_handle=secret_handle,
                    operation="PUT",
                )
            )

        return {
            "secret_handle": entry.secret_handle,
            "tenant_id": tid,
            "updated_at_utc": entry.updated_at_utc.isoformat(),
        }

    # -- Phase A: Password Change / Reset / Principal Registration ----------

    async def change_password_capability(
        self,
        current_password: str,
        new_password: str,
        execution_context: CapabilityExecutionContext | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Capability handler for `kortex.security.auth.change_password` (Phase A).

        Identity comes exclusively from the dispatcher-injected
        `execution_context` (KORTEX Platform Security — Capability Identity
        Propagation) — there is deliberately no `principal_id`/`tenant_id`
        parameter a caller could supply to target a different principal's
        password.
        """
        principal = execution_context.principal if execution_context is not None else None
        if principal is None:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)

        try:
            await self.authentication_manager.change_password(
                tenant_id=principal.tenant_id,
                principal_id=principal.principal_id,
                principal_type=principal.principal_type.value,
                current_password=current_password,
                new_password=new_password,
            )
        except Exception as exc:
            await self._record_security_audit(
                action=_AUTH_CHANGE_PASSWORD_CAPABILITY,
                actor_id=principal.principal_id,
                actor_type=_actor_type_for_principal_type(principal.principal_type.value),
                tenant_id=principal.tenant_id,
                context={"result": "failure", "reason": type(exc).__name__},
            )
            raise

        await self._record_security_audit(
            action=_AUTH_CHANGE_PASSWORD_CAPABILITY,
            actor_id=principal.principal_id,
            actor_type=_actor_type_for_principal_type(principal.principal_type.value),
            tenant_id=principal.tenant_id,
            context={"result": "success"},
        )
        return {"changed": True}

    async def set_email_capability(
        self,
        email: str,
        execution_context: CapabilityExecutionContext | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Capability handler for `kortex.security.principal.set_email` (Phase A).

        Identity comes exclusively from `execution_context`, matching
        `change_password_capability`'s exact precedent.
        """
        principal = execution_context.principal if execution_context is not None else None
        if principal is None:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)

        await self.authentication_manager.set_email(
            tenant_id=principal.tenant_id,
            principal_id=principal.principal_id,
            principal_type=principal.principal_type.value,
            email=email,
        )
        await self._record_security_audit(
            action=_PRINCIPAL_SET_EMAIL_CAPABILITY,
            actor_id=principal.principal_id,
            actor_type=_actor_type_for_principal_type(principal.principal_type.value),
            tenant_id=principal.tenant_id,
            context={"result": "success"},
        )
        return {"email": email}

    async def request_password_reset_capability(self, email: str, **_extra: Any) -> dict[str, Any]:
        """Capability handler for `kortex.security.auth.request_password_reset`
        (Phase A). Bootstrap-exempt (no session exists yet).

        Always returns the identical generic response regardless of whether
        `email` matched a principal — enumeration resistance, mirroring
        `authenticate()`'s own discipline. The real reset link/token is
        handed only to the configured `IEmailProvider`, never returned here.
        """
        safe_email = email if isinstance(email, str) else "unknown"
        raw_token: str | None = None
        try:
            raw_token = await self.authentication_manager.request_password_reset(safe_email)
            if raw_token is not None and self._email_provider is not None:
                await self._email_provider.send_email(
                    to=safe_email,
                    subject="Reset your KORTEX password",
                    body_text=(
                        "A password reset was requested for your KORTEX account.\n\n"
                        f"Reset token: {raw_token}\n\n"
                        "Enter this token on the Reset Password screen. "
                        "If you did not request this, you can ignore this message."
                    ),
                )
        except Exception as exc:
            # Never let a delivery/storage failure leak whether the email
            # matched a principal — logged, not surfaced to the caller.
            self.logger.warning("request_password_reset processing failed: %s", type(exc).__name__)

        await self._record_security_audit(
            action=_AUTH_REQUEST_PASSWORD_RESET_CAPABILITY,
            actor_id="unknown",
            actor_type="HUMAN",
            tenant_id="unknown",
            context={"result": "requested"},
        )
        return {"message": "If that email is registered, a password reset link has been sent."}

    async def reset_password_capability(self, token: str, new_password: str, **_extra: Any) -> dict[str, Any]:
        """Capability handler for `kortex.security.auth.reset_password`
        (Phase A). Bootstrap-exempt — the token itself is the credential."""
        try:
            await self.authentication_manager.reset_password(token=token, new_password=new_password)
        except Exception as exc:
            await self._record_security_audit(
                action=_AUTH_RESET_PASSWORD_CAPABILITY,
                actor_id="unknown",
                actor_type="HUMAN",
                tenant_id="unknown",
                context={"result": "failure", "reason": type(exc).__name__},
            )
            raise

        await self._record_security_audit(
            action=_AUTH_RESET_PASSWORD_CAPABILITY,
            actor_id="unknown",
            actor_type="HUMAN",
            tenant_id="unknown",
            context={"result": "success"},
        )
        return {"reset": True}

    async def register_principal_capability(
        self,
        principal_id: str,
        password: str,
        roles: list[str],
        email: str | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Capability handler for `kortex.security.principal.register` (Phase A).

        Admin-only via RBAC (`security:principal:write`, checked before this
        handler ever runs). The new principal's `tenant_id` comes exclusively
        from the admin's own authenticated `execution_context` — an admin can
        only ever create a user inside their own tenant, never an arbitrary
        one supplied as a parameter.
        """
        caller = execution_context.principal if execution_context is not None else None
        if caller is None:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)

        try:
            await self.authentication_manager.register_principal(
                tenant_id=caller.tenant_id,
                principal_id=principal_id,
                password=password,
                roles=roles,
                email=email,
            )
        except Exception as exc:
            await self._record_security_audit(
                action=_PRINCIPAL_REGISTER_CAPABILITY,
                actor_id=caller.principal_id,
                actor_type=_actor_type_for_principal_type(caller.principal_type.value),
                tenant_id=caller.tenant_id,
                resource_id=principal_id,
                context={"result": "failure", "reason": type(exc).__name__},
            )
            raise

        await self._record_security_audit(
            action=_PRINCIPAL_REGISTER_CAPABILITY,
            actor_id=caller.principal_id,
            actor_type=_actor_type_for_principal_type(caller.principal_type.value),
            tenant_id=caller.tenant_id,
            resource_id=principal_id,
            context={"result": "success"},
        )
        return {"created": True, "tenant_id": caller.tenant_id, "principal_id": principal_id}

    # -- Phase A: OAuth (Google/Microsoft sign-in for an existing account) --

    @staticmethod
    def _encode_oauth_state(state: OAuthStatePayload) -> str:
        """Encode a signed `OAuthStatePayload` into the opaque string carried
        through the provider's `state` query parameter. Not itself a secret
        — its integrity comes entirely from the embedded Ed25519 signature,
        verified on the way back in (`_decode_oauth_state` -> `verify_oauth_state`)."""
        payload = {
            "nonce": state.nonce,
            "provider": state.provider,
            "intent": state.intent,
            "tenant_id": state.tenant_id,
            "principal_id": state.principal_id,
            "principal_type": state.principal_type,
            "issued_at_utc": state.issued_at_utc.isoformat(),
            "expires_at_utc": state.expires_at_utc.isoformat(),
            "signature": state.signature.hex() if state.signature else None,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @staticmethod
    def _decode_oauth_state(encoded: str) -> OAuthStatePayload:
        try:
            raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
            payload = json.loads(raw)
            return OAuthStatePayload(
                nonce=payload["nonce"],
                provider=payload["provider"],
                intent=payload["intent"],
                tenant_id=payload.get("tenant_id"),
                principal_id=payload.get("principal_id"),
                principal_type=payload.get("principal_type"),
                issued_at_utc=datetime.fromisoformat(payload["issued_at_utc"]),
                expires_at_utc=datetime.fromisoformat(payload["expires_at_utc"]),
                signature=bytes.fromhex(payload["signature"]) if payload.get("signature") else None,
            )
        except Exception as exc:
            raise OAuthStateError("This sign-in attempt is invalid or has expired.") from exc

    async def oauth_get_config_capability(self, **_extra: Any) -> dict[str, Any]:
        """Capability handler for `kortex.security.oauth.get_config` (Phase A).

        Reports only which providers are configured — never client
        IDs/secrets or any other credential material. The desktop UI uses
        this to show a real "not configured" state (with a [Configure]
        note) rather than a button that silently fails.
        """
        return {"google": "google" in self._oauth_providers, "microsoft": "microsoft" in self._oauth_providers}

    def _require_oauth_provider(self, provider: str) -> IOAuthProvider:
        instance = self._oauth_providers.get(provider)
        if instance is None:
            raise OAuthProviderNotConfiguredError(f"{provider} sign-in is not configured.")
        return instance

    async def oauth_login_begin_capability(self, provider: str, **_extra: Any) -> dict[str, Any]:
        """Capability handler for `kortex.security.oauth.login_begin`
        (Phase A). Bootstrap-exempt — reachable from the signed-out login
        screen."""
        provider_instance = self._require_oauth_provider(provider)
        state = await self.authentication_manager.issue_oauth_state(provider=provider, intent="login")
        return {
            "authorization_url": provider_instance.authorization_url(
                state=self._encode_oauth_state(state), redirect_uri=_OAUTH_REDIRECT_URI
            ),
            "state": self._encode_oauth_state(state),
        }

    async def oauth_login_complete_capability(
        self, provider: str, code: str, state: str, **_extra: Any
    ) -> SecurityPrincipal:
        """Capability handler for `kortex.security.oauth.login_complete`
        (Phase A). Bootstrap-exempt. Returns the raw `SecurityPrincipal` on
        success — mirroring `authenticate()`'s own registered-capability
        shape exactly — so the API layer's existing, generic "mint a
        session token for a bootstrap-exempt capability that resolved to a
        SecurityPrincipal" mechanism (`kortex.api.main._invoke`) mints a
        real session the same way a password login does, with no changes
        needed there.

        Raises `OAuthNoLinkedAccountError` (never auto-creates an account)
        when the external identity resolves to no linked KORTEX principal —
        OAuth is a second sign-in method for an existing account only.
        """
        decoded_state = self._decode_oauth_state(state)
        verified_state = await self.authentication_manager.verify_oauth_state(decoded_state)
        if verified_state.intent != "login" or verified_state.provider != provider:
            raise OAuthStateError("This sign-in attempt is invalid or has expired.")

        provider_instance = self._require_oauth_provider(provider)
        user_info = await provider_instance.exchange_code(code=code, redirect_uri=_OAUTH_REDIRECT_URI)

        principal = await self.authentication_manager.resolve_oauth_link(provider, user_info.subject)
        if principal is None:
            await self._record_security_audit(
                action=_OAUTH_LOGIN_COMPLETE_CAPABILITY,
                actor_id="unknown",
                actor_type="HUMAN",
                tenant_id="unknown",
                context={"result": "no_linked_account", "provider": provider},
            )
            raise OAuthNoLinkedAccountError(
                f"No KORTEX account is linked to this {provider} identity. "
                f"Sign in with your username and password, then link {provider} from Account settings."
            )

        await self._record_security_audit(
            action=_OAUTH_LOGIN_COMPLETE_CAPABILITY,
            actor_id=principal.principal_id,
            actor_type=_actor_type_for_principal_type(principal.principal_type.value),
            tenant_id=principal.tenant_id,
            context={"result": "success", "provider": provider},
        )
        return principal

    async def oauth_link_begin_capability(
        self, provider: str, execution_context: CapabilityExecutionContext | None = None, **_extra: Any
    ) -> dict[str, Any]:
        """Capability handler for `kortex.security.oauth.link_begin`
        (Phase A). Identity comes exclusively from `execution_context`."""
        caller = execution_context.principal if execution_context is not None else None
        if caller is None:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)

        provider_instance = self._require_oauth_provider(provider)
        state = await self.authentication_manager.issue_oauth_state(
            provider=provider,
            intent="link",
            tenant_id=caller.tenant_id,
            principal_id=caller.principal_id,
            principal_type=caller.principal_type.value,
        )
        return {
            "authorization_url": provider_instance.authorization_url(
                state=self._encode_oauth_state(state), redirect_uri=_OAUTH_REDIRECT_URI
            ),
            "state": self._encode_oauth_state(state),
        }

    async def oauth_link_complete_capability(
        self,
        provider: str,
        code: str,
        state: str,
        execution_context: CapabilityExecutionContext | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Capability handler for `kortex.security.oauth.link_complete`
        (Phase A). Identity comes exclusively from `execution_context`; the
        signed `state`'s own embedded identity must match it exactly
        (defense in depth — a state token stolen from one session cannot be
        replayed to link an identity onto a different, currently-logged-in
        session, even though the state signature alone already proves it
        was issued for *some* legitimate flow)."""
        caller = execution_context.principal if execution_context is not None else None
        if caller is None:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)

        decoded_state = self._decode_oauth_state(state)
        verified_state = await self.authentication_manager.verify_oauth_state(decoded_state)
        if (
            verified_state.intent != "link"
            or verified_state.provider != provider
            or verified_state.tenant_id != caller.tenant_id
            or verified_state.principal_id != caller.principal_id
            or verified_state.principal_type != caller.principal_type.value
        ):
            raise OAuthStateError("This sign-in attempt is invalid or has expired.")

        provider_instance = self._require_oauth_provider(provider)
        user_info = await provider_instance.exchange_code(code=code, redirect_uri=_OAUTH_REDIRECT_URI)

        try:
            await self.authentication_manager.link_oauth_identity(
                tenant_id=caller.tenant_id,
                principal_id=caller.principal_id,
                principal_type=caller.principal_type.value,
                provider=provider,
                external_subject=user_info.subject,
            )
        except Exception as exc:
            await self._record_security_audit(
                action=_OAUTH_LINK_COMPLETE_CAPABILITY,
                actor_id=caller.principal_id,
                actor_type=_actor_type_for_principal_type(caller.principal_type.value),
                tenant_id=caller.tenant_id,
                context={"result": "failure", "provider": provider, "reason": type(exc).__name__},
            )
            raise

        await self._record_security_audit(
            action=_OAUTH_LINK_COMPLETE_CAPABILITY,
            actor_id=caller.principal_id,
            actor_type=_actor_type_for_principal_type(caller.principal_type.value),
            tenant_id=caller.tenant_id,
            context={"result": "success", "provider": provider},
        )
        return {"linked": True, "provider": provider}

    async def oauth_unlink_capability(
        self, provider: str, execution_context: CapabilityExecutionContext | None = None, **_extra: Any
    ) -> dict[str, Any]:
        """Capability handler for `kortex.security.oauth.unlink` (Phase A)."""
        caller = execution_context.principal if execution_context is not None else None
        if caller is None:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)

        unlinked = await self.authentication_manager.unlink_oauth_identity(
            tenant_id=caller.tenant_id,
            principal_id=caller.principal_id,
            principal_type=caller.principal_type.value,
            provider=provider,
        )
        await self._record_security_audit(
            action=_OAUTH_UNLINK_CAPABILITY,
            actor_id=caller.principal_id,
            actor_type=_actor_type_for_principal_type(caller.principal_type.value),
            tenant_id=caller.tenant_id,
            context={"result": "success", "provider": provider, "unlinked": unlinked},
        )
        return {"unlinked": unlinked}

    async def oauth_list_links_capability(
        self, execution_context: CapabilityExecutionContext | None = None, **_extra: Any
    ) -> dict[str, Any]:
        """Capability handler for `kortex.security.oauth.list_links` (Phase A)."""
        caller = execution_context.principal if execution_context is not None else None
        if caller is None:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)

        providers = await self.authentication_manager.list_oauth_links(
            tenant_id=caller.tenant_id, principal_id=caller.principal_id, principal_type=caller.principal_type.value
        )
        return {"providers": providers}

    async def delete_secret(self, secret_handle: str, tenant_id: str) -> bool:
        """Delete a secret entry."""
        return await self.secret_store.delete_secret(secret_handle, tenant_id)

    # -- Common Diagnostics Interface (IEngineDiagnostics) -------------------

    def health(self) -> dict[str, Any]:
        """Return diagnostic health checks."""
        return {
            "engine": self.name,
            "status": self._state.value,
            "healthy": self._state in (EngineState.READY, EngineState.RUNNING),
            "crypto_provider_configured": self._crypto_provider is not None,
            "authentication_implemented": self._authentication_manager is not None,
            "authorization_implemented": self._authorization_engine is not None,
            "secret_store_implemented": self._secret_store is not None,
            "audit_implemented": self._audit_manager is not None,
        }

    def metrics(self) -> dict[str, Any]:
        """Return operational runtime metrics."""
        return dict(self._metrics)

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics."""
        return {
            "engine": self.name,
            "version": self.version(),
            "state": self._state.value,
            "capabilities": self.capabilities(),
            "metrics": self.metrics(),
            "not_yet_implemented": [],
        }

    def status(self) -> str:
        """Return current operational state name string."""
        return self._state.value

    def version(self) -> str:
        """Return semantic version string."""
        return "0.6.0-m6"

    def capabilities(self) -> list[str]:
        """Return list of capability strings registered by this engine."""
        return list(self._registered_capabilities)
