"""
KORTEX Security Engine — M1 Diagnostics.

Reports the state of the M1 subsystem (domain contracts + cryptographic
provider) only. Authentication, authorization, secret storage, audit
enforcement, and Kernel capability registration are NOT implemented as of
this milestone — this module MUST NOT report any of them as available or
operational.

This is not a Kernel-registered engine facade. That is `SecurityEngine`
(milestone M6, inheriting `BaseEngine`) — no such class exists yet, and this
module does not create one.
"""

from __future__ import annotations

from typing import Any

from kortex.engines.security.interfaces import ICryptoProvider

_ENGINE_VERSION = "0.1.0-m1"

# Explicit, honest declaration of what M1 actually provides. Nothing here
# implies Kernel capability registration, authentication, or authorization.
_M1_SUPPORTED_CRYPTO_OPERATIONS: list[str] = [
    "crypto.sha256.hash",
    "crypto.sha256.verify",
    "crypto.ed25519.sign",
    "crypto.ed25519.verify",
    "crypto.aes256gcm.encrypt",
    "crypto.aes256gcm.decrypt",
]

_NOT_YET_IMPLEMENTED: list[str] = [
    "authentication",
    "authorization",
    "secret_storage",
    "audit_enforcement",
    "kernel_capability_dispatch",
]


class SecurityDiagnostics:
    """Standalone M1 diagnostics reporter for the Security Engine's crypto subsystem.

    Implements `kortex.engines.security.interfaces.IEngineDiagnostics`.
    """

    def __init__(self, crypto_provider: ICryptoProvider | None) -> None:
        """Initialize with the crypto provider whose readiness this reports on.

        `crypto_provider` may be `None` to represent an unconfigured subsystem
        (e.g. before `LocalCrypto()` has been constructed) — `health()`/`status()`
        report this honestly rather than assuming a default.
        """
        self._crypto_provider = crypto_provider

    def health(self) -> dict[str, Any]:
        """Report M1 subsystem health. Only the crypto provider's presence is checked.

        No key material, secret material, or credential data ever appears here.
        """
        crypto_ready = self._crypto_provider is not None
        return {
            "engine": "security",
            "milestone": "M1",
            "status": "healthy" if crypto_ready else "unhealthy",
            "healthy": crypto_ready,
            "crypto_provider_configured": crypto_ready,
            "authentication_implemented": False,
            "authorization_implemented": False,
            "secret_store_implemented": False,
            "audit_implemented": False,
        }

    def metrics(self) -> dict[str, Any]:
        """Return M1 runtime metrics.

        M1 performs no stateful, counted operations, so this is intentionally
        static — no counters are fabricated to imply activity that doesn't occur.
        """
        return {
            "engine": "security",
            "milestone": "M1",
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics for the M1 subsystem."""
        return {
            "engine": "security",
            "version": self.version(),
            "milestone": "M1",
            "status": self.status(),
            "supported_crypto_operations": (
                list(_M1_SUPPORTED_CRYPTO_OPERATIONS) if self._crypto_provider is not None else []
            ),
            "capabilities": self.capabilities(),
            "not_yet_implemented": list(_NOT_YET_IMPLEMENTED),
        }

    def status(self) -> str:
        """Return a state name string.

        M1 has no engine lifecycle (`engine.py`/`BaseEngine` do not exist yet)
        — this reflects crypto-provider readiness only, not a Kernel-tracked
        `EngineState`.
        """
        return "READY" if self._crypto_provider is not None else "UNINITIALIZED"

    def version(self) -> str:
        """Return the semantic version string for the M1 subsystem."""
        return _ENGINE_VERSION

    def capabilities(self) -> list[str]:
        """Return Kernel-registered capability strings.

        Always empty in M1 — no capability registration with the Kernel
        Registry occurs until a later milestone (M7). This method must never
        claim otherwise.
        """
        return []
