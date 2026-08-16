"""
KORTEX Security Engine Core Facade (Milestone M1).

Follows the exact lifecycle/registration conventions established by sibling
engines (see `kortex.engines.storage.engine.StorageEngine`,
`kortex.engines.registry.engine.RegistryEngine`).

Milestone M1 scope: engine lifecycle (`BaseEngine`), Kernel/Registry capability
registration, and the common diagnostics interface (`IEngineDiagnostics`). The
four canonical Security capabilities are registered as explicit,
non-functional placeholders — every invocation fails closed with a
`NOT_IMPLEMENTED` error regardless of input. They never authenticate,
authorize, decrypt secrets, issue tokens, or grant access.

Authoritative Kernel capability interception/dispatch (i.e. actually routing
every capability invocation through Security Engine's authorization decision)
is a later milestone (M7) — nothing in this module creates a dispatcher or
authorization middleware.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, NoReturn, Optional

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.security.exceptions import SecurityEngineError
from kortex.engines.security.interfaces import ICryptoProvider, IEngineDiagnostics
from kortex.engines.security.providers.local_crypto import LocalCrypto

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.security")

_NOT_IMPLEMENTED_MESSAGE = (
    "Security Engine capability '{name}' is not implemented in Milestone M1. "
    "This capability is a structural placeholder only — it never authenticates, "
    "authorizes, decrypts secrets, or grants access. Real enforcement lands in "
    "a later milestone."
)

# Canonical capability registration list, per Security Engine spec S15.
_CANONICAL_CAPABILITIES: List[tuple[str, str]] = [
    ("kortex.security.auth.authenticate", "Authenticate a caller identity."),
    ("kortex.security.access.authorize", "Authorize a caller's requested capability."),
    ("kortex.security.secret.get", "Resolve a secret handle to its plaintext value."),
    ("kortex.security.signature.verify", "Verify a cryptographic signature."),
]


class SecurityEngine(BaseEngine, IEngineDiagnostics):
    """KORTEX Security Engine Core Facade providing M1 lifecycle and capability placeholders."""

    def __init__(self, crypto_provider: Optional[ICryptoProvider] = None) -> None:
        """Initialize SecurityEngine instance.

        Args:
            crypto_provider: Optional cryptographic provider override (defaults
                to `LocalCrypto()`). Held for diagnostic reporting and future
                milestones — M1's capability placeholders never invoke it.
        """
        super().__init__()
        self._crypto_provider: ICryptoProvider = crypto_provider if crypto_provider is not None else LocalCrypto()
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

    # -- Lifecycle Implementation ---------------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Register the four canonical M1 capability placeholders with the Kernel.

        None of these capabilities authenticate, authorize, decrypt secrets, or
        grant access — every invocation fails closed with an explicit
        `NOT_IMPLEMENTED` error, regardless of input.
        """
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Security Engine (Milestone M1)...")

        try:
            for capability_name, description in _CANONICAL_CAPABILITIES:
                kernel.register_capability(
                    name=capability_name,
                    description=f"{description} NOT IMPLEMENTED - Milestone M1 structural placeholder only.",
                    provider=self.name,
                    handler=self._make_not_implemented_handler(capability_name),
                )
                self._registered_capabilities.append(capability_name)

            self._metrics["capabilities_registered"] = len(self._registered_capabilities)
            self._set_state(EngineState.READY)
            self.logger.info("Security Engine initialized successfully (M1 placeholders only).")
        except Exception as e:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize Security Engine: %s", e, exc_info=True)
            raise

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

        Never reports authentication, authorization, secret storage, or audit
        as implemented — those do not exist in M1.
        """
        return {
            "engine": self.name,
            "status": self._state.value,
            "healthy": self._state in (EngineState.READY, EngineState.RUNNING),
            "crypto_provider_configured": self._crypto_provider is not None,
            "authentication_implemented": False,
            "authorization_implemented": False,
            "secret_store_implemented": False,
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
                "authentication",
                "authorization",
                "secret_storage",
                "audit_enforcement",
                "kernel_capability_dispatch",
            ],
        }

    def status(self) -> str:
        """Return current operational state name string."""
        return self._state.value

    def version(self) -> str:
        """Return semantic version string."""
        return "0.1.0-m1"

    def capabilities(self) -> List[str]:
        """Return list of capability strings registered by this engine."""
        return list(self._registered_capabilities)
