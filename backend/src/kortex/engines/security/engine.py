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

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, cast

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.security.audit import AuditManager
from kortex.engines.security.auth import AuthenticationManager
from kortex.engines.security.authorization import AuthorizationEngine
from kortex.engines.security.crypto import VerificationService
from kortex.engines.security.events import (
    SecurityAccessDeniedEvent,
    SecurityAccessGrantedEvent,
    SecurityAuthFailureEvent,
    SecurityAuthSuccessEvent,
)
from kortex.engines.security.exceptions import (
    MasterKeyError,
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
    PermissionRequirement,
    SecretEntry,
    SecurityPrincipal,
)
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.security.secrets import SecretStore
from kortex.engines.storage.interfaces import ICacheStore, IDataStore

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel
    from kortex.engines.event.engine import EventEngine

logger = logging.getLogger("kortex.engines.security")

_MASTER_KEY_CONFIG_KEY = "KORTEX_MASTER_KEY"
_AUTH_SIGNING_KEY_CONFIG_KEY = "KORTEX_AUTH_SIGNING_PRIVATE_KEY"
_SECRET_GET_CAPABILITY = "kortex.security.secret.get"
_AUTH_AUTHENTICATE_CAPABILITY = "kortex.security.auth.authenticate"
_ACCESS_AUTHORIZE_CAPABILITY = "kortex.security.access.authorize"
_SIGNATURE_VERIFY_CAPABILITY = "kortex.security.signature.verify"
_BOOTSTRAP_CREATE_ADMIN_CAPABILITY = "kortex.security.bootstrap.create_admin"

# The single role granted to the first, bootstrap-created administrator.
_BOOTSTRAP_ADMIN_ROLE = "admin"

# Canonical capability registration list, per Security Engine spec S15, plus
# the M7.1 first-run bootstrap capability.
_CANONICAL_CAPABILITIES: List[tuple[str, str]] = [
    (_AUTH_AUTHENTICATE_CAPABILITY, "Authenticate a caller identity."),
    (_ACCESS_AUTHORIZE_CAPABILITY, "Authorize a caller's requested capability."),
    (_SECRET_GET_CAPABILITY, "Resolve a secret handle to its plaintext value."),
    (_SIGNATURE_VERIFY_CAPABILITY, "Verify a cryptographic signature."),
    (_BOOTSTRAP_CREATE_ADMIN_CAPABILITY, "Create the first tenant administrator on a fresh install."),
]

# RBAC permission requirements per capability. `kortex.security.auth.authenticate`
# and `kortex.security.bootstrap.create_admin` are deliberately absent: both are
# bootstrap-exempt capabilities (`requires_authentication=False`), so no RBAC
# permission requirement applies to either — each instead fails closed via its
# own handler logic (wrong credentials / bootstrap already closed).
_CANONICAL_CAPABILITY_PERMISSIONS: Dict[str, List[str]] = {
    _ACCESS_AUTHORIZE_CAPABILITY: ["security:read"],
    _SECRET_GET_CAPABILITY: ["security:read"],
    _SIGNATURE_VERIFY_CAPABILITY: ["security:read"],
}

# Maps `PrincipalType` (auth/RBAC vocabulary: USER/SERVICE_PRINCIPAL/AGENT) to
# `UniversalAuditEntry.actor_type`'s own, separate frozen vocabulary
# (shared_domain_models.md S11: HUMAN/AI_AGENT/SYSTEM_ENGINE/CONNECTOR).
# SERVICE_PRINCIPAL -> CONNECTOR is an implementation decision (closest
# available category for an external service credential), not a frozen
# mandate — flagged as such rather than silently assumed.
_PRINCIPAL_TYPE_TO_ACTOR_TYPE: Dict[str, str] = {
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
        crypto_provider: Optional[ICryptoProvider] = None,
        data_store: Optional[IDataStore] = None,
        master_key: Optional[bytes] = None,
        signing_private_key: Optional[bytes] = None,
        event_engine: Optional[EventEngine] = None,
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
        """
        super().__init__()
        self._crypto_provider: ICryptoProvider = crypto_provider if crypto_provider is not None else LocalCrypto()
        self._data_store_override: Optional[IDataStore] = data_store
        self._master_key_override: Optional[bytes] = master_key
        self._signing_private_key_override: Optional[bytes] = signing_private_key
        self._event_engine_override: Optional[EventEngine] = event_engine
        self._secret_store: Optional[SecretStore] = None
        self._authentication_manager: Optional[AuthenticationManager] = None
        self._authorization_engine: Optional[AuthorizationEngine] = None
        self._audit_manager: Optional[AuditManager] = None
        # M7.1: bound `Kernel.list_capabilities` method only (never the full
        # `Kernel` instance) — the narrowest capture that lets
        # `bootstrap_create_admin` discover the union of every currently
        # registered capability's `required_permissions` at call time, so
        # the first bootstrap-created administrator is granted a working
        # permission set without hand-maintaining a duplicate list here that
        # would silently drift out of sync with every other engine's own
        # capability registrations.
        self._list_capabilities: Optional[Callable[[], List[Any]]] = None
        self._verification_service: VerificationService = VerificationService(crypto_provider=self._crypto_provider)
        self._registered_capabilities: List[str] = []
        self._metrics: Dict[str, Any] = {
            "capabilities_registered": 0,
            "signature_verifications": 0,
        }

    @property
    def name(self) -> str:
        """Unique identifier name for this engine."""
        return "security"

    @property
    def dependencies(self) -> List[str]:
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
            self._list_capabilities = kernel.list_capabilities

            for capability_name, description in _CANONICAL_CAPABILITIES:
                requires_authentication = True
                if capability_name == _SECRET_GET_CAPABILITY:
                    handler: Callable[..., Any] = self._secret_store.get_secret
                    capability_description = f"{description} Encrypted, fail-closed (Milestone M2)."
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
                    capability_description = f"{description} Fail-closed, hybrid RBAC+ABAC, audited (Milestones M4 + M6)."
                elif capability_name == _SIGNATURE_VERIFY_CAPABILITY:
                    handler = self._verify_signature_capability_handler
                    capability_description = f"{description} Cryptographic verification (Milestone M6)."
                elif capability_name == _BOOTSTRAP_CREATE_ADMIN_CAPABILITY:
                    handler = self.bootstrap_create_admin
                    capability_description = f"{description} Fail-closed after first use (Milestone M7.1)."
                    # The second (and still deliberately narrow) bootstrap
                    # exception: reachable before any session token exists.
                    requires_authentication = False
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
        cache_store: Optional[ICacheStore] = None
        if self._data_store_override is None:
            storage_engine = kernel.get_engine("storage")
            cache_store = getattr(storage_engine, "cache", None)

        return AuthorizationEngine(data_store=data_store, cache_store=cache_store)

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

    async def health_check(self) -> Dict[str, Any]:
        """Perform diagnostic health check."""
        return self.health()

    # -- Capability Handlers --------------------------------------------------

    async def _verify_signature_capability_handler(
        self,
        data: bytes | str,
        signature: bytes | str | Dict[str, Any] | CryptographicSignature,
        public_key: Optional[bytes | str] = None,
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
    ) -> Dict[str, Any]:
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
        permissions: List[str] = []
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

    async def authenticate(self, credentials: Dict[str, Any]) -> SecurityPrincipal:
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
        safe_credentials: Dict[str, Any] = credentials if isinstance(credentials, dict) else {}
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
        context: Optional[Dict[str, Any]] = None,
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
        resource_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
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

    async def delete_secret(self, secret_handle: str, tenant_id: str) -> bool:
        """Delete a secret entry."""
        return await self.secret_store.delete_secret(secret_handle, tenant_id)

    # -- Common Diagnostics Interface (IEngineDiagnostics) -------------------

    def health(self) -> Dict[str, Any]:
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

    def metrics(self) -> Dict[str, Any]:
        """Return operational runtime metrics."""
        return dict(self._metrics)

    def diagnostics(self) -> Dict[str, Any]:
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

    def capabilities(self) -> List[str]:
        """Return list of capability strings registered by this engine."""
        return list(self._registered_capabilities)
