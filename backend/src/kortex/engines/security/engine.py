"""
KORTEX Security Engine Core Facade (Milestone M1 + M2 + M3 + M4).

Follows the exact lifecycle/registration conventions established by sibling
engines (see `kortex.engines.storage.engine.StorageEngine`,
`kortex.engines.registry.engine.RegistryEngine`).

Milestone M1 scope: engine lifecycle (`BaseEngine`), Kernel/Registry capability
registration, and the common diagnostics interface (`IEngineDiagnostics`).
Milestone M2 adds: `SecretStore` construction and activation of the
`kortex.security.secret.get` capability with real (encrypted, fail-closed)
behavior. Milestone M3 adds: `AuthenticationManager` construction and
activation of the `kortex.security.auth.authenticate` capability with real
(fail-closed) behavior. Milestone M4 adds: `AuthorizationEngine` construction
and activation of the `kortex.security.access.authorize` capability with real
hybrid RBAC+ABAC (fail-closed) behavior. `kortex.security.signature.verify`
remains exactly what it was in M1 — an explicit, non-functional placeholder
that fails closed with `NOT_IMPLEMENTED` under any input. Authentication does
not imply authorization, and vice versa — `AuthorizationEngine` evaluates
policy against a caller-supplied `SecurityPrincipal`; it does not itself
verify that principal was ever genuinely authenticated.

Authoritative Kernel capability interception/dispatch (i.e. actually routing
every capability invocation through Security Engine's authorization decision)
remains a later, platform-wide gap — nothing in this module creates a
dispatcher or authorization middleware. `kortex.security.access.authorize` is
real and directly callable, exactly like `secret.get`/`auth.authenticate`
before it, but nothing in the Kernel/Registry routes capability invocation
through it automatically.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, NoReturn, Optional

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.security.auth import AuthenticationManager
from kortex.engines.security.authorization import AuthorizationEngine
from kortex.engines.security.exceptions import MasterKeyError, SecurityEngineError, SigningKeyError
from kortex.engines.security.interfaces import ICryptoProvider, IEngineDiagnostics
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.security.secrets import SecretStore
from kortex.engines.storage.interfaces import ICacheStore, IDataStore

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.security")

_NOT_IMPLEMENTED_MESSAGE = (
    "Security Engine capability '{name}' is not implemented in Milestone M1. "
    "This capability is a structural placeholder only — it never authenticates, "
    "authorizes, decrypts secrets, or grants access. Real enforcement lands in "
    "a later milestone."
)

_MASTER_KEY_CONFIG_KEY = "KORTEX_MASTER_KEY"
_AUTH_SIGNING_KEY_CONFIG_KEY = "KORTEX_AUTH_SIGNING_PRIVATE_KEY"
_SECRET_GET_CAPABILITY = "kortex.security.secret.get"
_AUTH_AUTHENTICATE_CAPABILITY = "kortex.security.auth.authenticate"
_ACCESS_AUTHORIZE_CAPABILITY = "kortex.security.access.authorize"

# Canonical capability registration list, per Security Engine spec S15.
_CANONICAL_CAPABILITIES: List[tuple[str, str]] = [
    ("kortex.security.auth.authenticate", "Authenticate a caller identity."),
    ("kortex.security.access.authorize", "Authorize a caller's requested capability."),
    (_SECRET_GET_CAPABILITY, "Resolve a secret handle to its plaintext value."),
    ("kortex.security.signature.verify", "Verify a cryptographic signature."),
]

# RBAC permission requirements per capability. `kortex.security.auth.authenticate`
# is deliberately absent: it is the bootstrap-exempt capability
# (`requires_authentication=False`), so no RBAC permission requirement applies to
# it. `kortex.security.access.authorize` is intentionally given `security:read`
# rather than left unclassified — this capability only *exposes* an authorization
# decision to a caller; it does not perform the enforcement check on itself, so
# requiring `security:read` here is not circular with `Kernel.invoke_capability`'s
# own internal call to `AuthorizationEngine.authorize_strict`, which never goes
# through this capability lookup.
_CANONICAL_CAPABILITY_PERMISSIONS: Dict[str, List[str]] = {
    _ACCESS_AUTHORIZE_CAPABILITY: ["security:read"],
    _SECRET_GET_CAPABILITY: ["security:read"],
    "kortex.security.signature.verify": ["security:read"],
}


class SecurityEngine(BaseEngine, IEngineDiagnostics):
    """KORTEX Security Engine Core Facade providing M1 lifecycle, M2 SecretStore, M3 AuthenticationManager,
    M4 AuthorizationEngine, and the remaining capability placeholder (signature.verify)."""

    def __init__(
        self,
        crypto_provider: Optional[ICryptoProvider] = None,
        data_store: Optional[IDataStore] = None,
        master_key: Optional[bytes] = None,
        signing_private_key: Optional[bytes] = None,
    ) -> None:
        """Initialize SecurityEngine instance.

        Args:
            crypto_provider: Optional cryptographic provider override (defaults
                to `LocalCrypto()`).
            data_store: Optional explicit `IDataStore` injection, mirroring the
                "explicit injection always takes priority" convention used by
                sibling engines (e.g. Document Engine) — if omitted, resolved
                from the Kernel's registered `storage` engine during `initialize()`.
            master_key: Optional already-decoded 32-byte master key, for
                deterministic tests — if omitted, resolved from the
                `KORTEX_MASTER_KEY` configuration value during `initialize()`.
                Never logged, never stored anywhere except as this attribute.
            signing_private_key: Optional already-decoded 32-byte Ed25519
                signing key, for deterministic tests — if omitted, resolved
                from the `KORTEX_AUTH_SIGNING_PRIVATE_KEY` configuration value
                during `initialize()`. Cryptographically and operationally
                separate from `master_key` — never derived from it, never
                shared with `SecretStore`.
        """
        super().__init__()
        self._crypto_provider: ICryptoProvider = crypto_provider if crypto_provider is not None else LocalCrypto()
        self._data_store_override: Optional[IDataStore] = data_store
        self._master_key_override: Optional[bytes] = master_key
        self._signing_private_key_override: Optional[bytes] = signing_private_key
        self._secret_store: Optional[SecretStore] = None
        self._authentication_manager: Optional[AuthenticationManager] = None
        self._authorization_engine: Optional[AuthorizationEngine] = None
        self._registered_capabilities: List[str] = []
        self._metrics: Dict[str, Any] = {
            "capabilities_registered": 0,
            "not_implemented_invocations": 0,
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

        Exists so the Kernel's capability dispatcher (`kortex.core.dispatch`)
        can call `verify_token()` directly, without reaching into a private
        attribute. Raises `SecurityEngineError` if accessed before
        `initialize()` has completed — never returns `None` silently.
        """
        if self._authentication_manager is None:
            raise SecurityEngineError("AuthenticationManager is not initialized.")
        return self._authentication_manager

    @property
    def authorization_engine(self) -> AuthorizationEngine:
        """Return the initialized `AuthorizationEngine`.

        Exists so the Kernel's capability dispatcher (`kortex.core.dispatch`)
        can call `authorize_strict()` directly, without reaching into a
        private attribute. Raises `SecurityEngineError` if accessed before
        `initialize()` has completed — never returns `None` silently.
        """
        if self._authorization_engine is None:
            raise SecurityEngineError("AuthorizationEngine is not initialized.")
        return self._authorization_engine

    # -- Lifecycle Implementation ---------------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Build the M2 `SecretStore`, M3 `AuthenticationManager`, and M4
        `AuthorizationEngine`, then register the four canonical capabilities.

        `kortex.security.secret.get` delegates to a real, encrypted, fail-closed
        `SecretStore`. `kortex.security.auth.authenticate` delegates to a real,
        fail-closed `AuthenticationManager`. `kortex.security.access.authorize`
        delegates to a real, fail-closed `AuthorizationEngine` (hybrid RBAC+ABAC).
        `kortex.security.signature.verify` remains exactly what it was in M1 —
        an explicit, non-functional placeholder that fails closed with
        `NOT_IMPLEMENTED` regardless of input. If `SecretStore`,
        `AuthenticationManager`, or `AuthorizationEngine` cannot be constructed
        securely (missing/malformed key material, missing `IDataStore`), this
        method raises and the Kernel boot fails closed — there is no fallback
        path that starts Security Engine without them.
        """
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Security Engine (Milestone M1 + M2 + M3)...")

        try:
            self._secret_store = self._build_secret_store(kernel)
            self._authentication_manager = self._build_authentication_manager(kernel)
            self._authorization_engine = self._build_authorization_engine(kernel)

            for capability_name, description in _CANONICAL_CAPABILITIES:
                requires_authentication = True
                if capability_name == _SECRET_GET_CAPABILITY:
                    handler: Callable[..., Any] = self._secret_store.get_secret
                    capability_description = f"{description} Encrypted, fail-closed (Milestone M2)."
                elif capability_name == _AUTH_AUTHENTICATE_CAPABILITY:
                    handler = self._authentication_manager.authenticate
                    capability_description = f"{description} Fail-closed (Milestone M3)."
                    # The one bootstrap exception: must be reachable before any
                    # session token exists. Enforced (not just declared here) by
                    # RegistryEngine.register_capability's own allowlist check.
                    requires_authentication = False
                elif capability_name == _ACCESS_AUTHORIZE_CAPABILITY:
                    handler = self._authorization_engine.authorize
                    capability_description = f"{description} Fail-closed, hybrid RBAC+ABAC (Milestone M4)."
                else:
                    handler = self._make_not_implemented_handler(capability_name)
                    capability_description = (
                        f"{description} NOT IMPLEMENTED - Milestone M1 structural placeholder only."
                    )
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
                "(M2 SecretStore + M3 Authentication + M4 Authorization active)."
            )
        except Exception as e:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize Security Engine: %s", e, exc_info=True)
            raise

    def _build_secret_store(self, kernel: Kernel) -> SecretStore:
        """Resolve `IDataStore` and the master key, then construct `SecretStore`.

        Explicit constructor injection (`data_store`/`master_key` passed to
        `SecurityEngine.__init__`) always takes priority, mirroring the same
        convention used by Document/Connector Engine for `IDataStore` — this
        only resolves from the Kernel when neither was already configured.
        """
        data_store = self._data_store_override
        if data_store is None:
            storage_engine = kernel.get_engine("storage")
            resolved = getattr(storage_engine, "data", None)
            if resolved is None:
                raise SecurityEngineError("Storage Engine did not provide an IDataStore instance.")
            data_store = resolved

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
        """Resolve `IDataStore` and the Ed25519 signing key, then construct `AuthenticationManager`.

        Reuses `_build_secret_store`'s already-resolved `IDataStore` resolution
        convention (explicit constructor injection first, else the Kernel's
        registered `storage` engine) — `AuthenticationManager` has no
        dependency on `SecretStore` itself, only on the same `IDataStore`
        resolution path.
        """
        data_store = self._data_store_override
        if data_store is None:
            storage_engine = kernel.get_engine("storage")
            resolved = getattr(storage_engine, "data", None)
            if resolved is None:
                raise SecurityEngineError("Storage Engine did not provide an IDataStore instance.")
            data_store = resolved

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
        """Resolve `IDataStore` and the shared `ICacheStore`, then construct `AuthorizationEngine`.

        Reuses the same `IDataStore` resolution convention as
        `_build_secret_store`/`_build_authentication_manager`.
        `AuthorizationEngine` needs no key material — unlike `SecretStore`/
        `AuthenticationManager`, it has no boot-time bootstrap failure mode
        of its own beyond the already-covered missing-`IDataStore` case.

        `cache_store` is resolved via `getattr(storage_engine, "cache", None)`
        — the exact same optional-resolution pattern `document/engine.py`
        already uses for its own cache-backed sub-components. It is never
        required: a `None` cache disables the S16/S18 permission-matrix
        cache but never changes RBAC/ABAC correctness (see `rbac.py`). Only
        resolved when `data_store` itself is Kernel-resolved — mirroring
        the existing convention that a full constructor-injection override
        (test determinism) bypasses Kernel resolution entirely, including
        for the cache.
        """
        data_store = self._data_store_override
        cache_store: Optional[ICacheStore] = None
        if data_store is None:
            storage_engine = kernel.get_engine("storage")
            resolved = getattr(storage_engine, "data", None)
            if resolved is None:
                raise SecurityEngineError("Storage Engine did not provide an IDataStore instance.")
            data_store = resolved
            cache_store = getattr(storage_engine, "cache", None)

        return AuthorizationEngine(data_store=data_store, cache_store=cache_store)

    async def start(self) -> None:
        """Start the Security Engine. No background services run in M1."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Security Engine is RUNNING.")

    async def stop(self) -> None:
        """Gracefully shut down the Security Engine. No resources to release in M1."""
        if self._state in (EngineState.STOPPED, EngineState.UNINITIALIZED):
            return

        self._set_state(EngineState.STOPPING)
        self.logger.info("Stopping Security Engine...")
        self._set_state(EngineState.STOPPED)
        self.logger.info("Security Engine stopped cleanly.")

    async def health_check(self) -> Dict[str, Any]:
        """Perform diagnostic health check."""
        return self.health()

    # -- Capability Placeholder Handler ---------------------------------------

    def _make_not_implemented_handler(self, capability_name: str) -> Callable[..., Any]:
        """Build a handler that fails closed for `capability_name` under any input.

        Accepts arbitrary positional/keyword arguments — missing, malformed,
        empty, or unexpected input all produce the identical explicit failure.
        There is no success path and no ALLOW path.
        """

        async def _handler(*_args: Any, **_kwargs: Any) -> NoReturn:
            self._metrics["not_implemented_invocations"] += 1
            raise SecurityEngineError(
                _NOT_IMPLEMENTED_MESSAGE.format(name=capability_name),
                code="NOT_IMPLEMENTED",
            )

        return _handler

    # -- Common Diagnostics Interface (IEngineDiagnostics) -------------------

    def health(self) -> Dict[str, Any]:
        """Return diagnostic health checks.

        `secret_store_implemented`/`authentication_implemented`/
        `authorization_implemented` reflect reality (True once `SecretStore`/
        `AuthenticationManager`/`AuthorizationEngine` have been constructed in
        `initialize()`). Audit remains honestly reported as not implemented —
        it does not exist as of Milestone M4. Never exposes the master key,
        the authentication signing key, or any decrypted secret/credential
        material.
        """
        return {
            "engine": self.name,
            "status": self._state.value,
            "healthy": self._state in (EngineState.READY, EngineState.RUNNING),
            "crypto_provider_configured": self._crypto_provider is not None,
            "authentication_implemented": self._authentication_manager is not None,
            "authorization_implemented": self._authorization_engine is not None,
            "secret_store_implemented": self._secret_store is not None,
            "audit_implemented": False,
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
            "not_yet_implemented": [
                "audit_enforcement",
                "kernel_capability_dispatch",
            ],
        }

    def status(self) -> str:
        """Return current operational state name string."""
        return self._state.value

    def version(self) -> str:
        """Return semantic version string."""
        return "0.4.0-m4"

    def capabilities(self) -> List[str]:
        """Return list of capability strings registered by this engine."""
        return list(self._registered_capabilities)
