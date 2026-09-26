"""Typed exception hierarchy for the `kortex.browser.*` capability layer.

Mirrors `kortex.engines.desktop_automation.exceptions`'s own convention
(one exception type per distinct failure mode, an `error_code` carried on
every instance, a single `error_for_code` decode point) rather than
inventing a new error-handling shape for Browser specifically. Every
`BrowserActionErrorCode` value (`models.py`) has exactly one exception type
below — this is the single source of truth mapping between them.
"""

from __future__ import annotations

from kortex.engines.browser.models import BrowserActionErrorCode


class BrowserCapabilityError(Exception):
    """Base exception for every capability-level Browser failure.

    Never raised directly — always one of the specific subclasses below, so
    a caller can `except` the exact failure mode it wants to handle rather
    than string-matching a message.
    """

    def __init__(self, message: str, code: BrowserActionErrorCode) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class BrowserUnauthorizedError(BrowserCapabilityError):
    """No verified principal — should never actually surface past
    `CapabilityDispatcher`'s own authentication gate, but every capability
    handler still fails closed rather than assuming it can't happen."""

    def __init__(self, message: str = "No authenticated principal for this Browser action.") -> None:
        super().__init__(message, BrowserActionErrorCode.UNAUTHORIZED)


class BrowserForbiddenError(BrowserCapabilityError):
    """RBAC/ABAC denied — should equally never surface past the dispatcher's
    own authorization gate for a well-formed request; retained for the same
    fail-closed-by-construction reason as `BrowserUnauthorizedError`."""

    def __init__(self, message: str = "Principal is not authorized for this Browser action.") -> None:
        super().__init__(message, BrowserActionErrorCode.FORBIDDEN)


class BrowserPolicyDeniedError(BrowserCapabilityError):
    """The action would violate Browser-B4's `BrowserPolicyEngine` — never
    raised by anything in this package today (B5.0-B5.4 mints grants, it
    does not execute), reserved for B5.5+ once the desktop redeem command
    can observe a real `PolicyDeniedEvent`."""

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message, BrowserActionErrorCode.POLICY_DENIED)
        self.reason = reason


class BrowserSurfaceNotFoundError(BrowserCapabilityError):
    def __init__(self, surface_id: str) -> None:
        super().__init__(
            f"Browser surface '{surface_id}' is not known or is no longer open.",
            BrowserActionErrorCode.SURFACE_NOT_FOUND,
        )
        self.surface_id = surface_id


class BrowserProfileNotFoundError(BrowserCapabilityError):
    def __init__(self, browser_profile_id: str) -> None:
        super().__init__(
            f"Browser profile '{browser_profile_id}' is not known or does not belong to this tenant.",
            BrowserActionErrorCode.PROFILE_NOT_FOUND,
        )
        self.browser_profile_id = browser_profile_id


class BrowserStaleReferenceError(BrowserCapabilityError):
    """The surface has navigated (or been destroyed and recreated) since the
    caller's reference (a `navigation_generation`, or a selector derived from
    an earlier `.read`/`.extract`) was obtained."""

    def __init__(self, message: str = "The referenced page state is stale.") -> None:
        super().__init__(message, BrowserActionErrorCode.STALE_REFERENCE)


class BrowserGrantExpiredError(BrowserCapabilityError):
    def __init__(self, message: str = "Capability Execution Grant has expired.") -> None:
        super().__init__(message, BrowserActionErrorCode.GRANT_EXPIRED)


class BrowserGrantInvalidError(BrowserCapabilityError):
    """Signature failure, shape mismatch, tenant/principal/target/parameter
    mismatch, or a grant already redeemed once — every one of these is
    reported identically (never distinguished to the caller) so a probing
    caller cannot use the error to learn *which* check failed."""

    def __init__(self, message: str = "Capability Execution Grant is invalid.") -> None:
        super().__init__(message, BrowserActionErrorCode.GRANT_INVALID)


class BrowserNavigationFailedError(BrowserCapabilityError):
    def __init__(self, message: str) -> None:
        super().__init__(message, BrowserActionErrorCode.NAVIGATION_FAILED)


class BrowserTimeoutError(BrowserCapabilityError):
    def __init__(self, message: str = "Browser action timed out.") -> None:
        super().__init__(message, BrowserActionErrorCode.TIMEOUT)


class BrowserCancelledError(BrowserCapabilityError):
    def __init__(self, message: str = "Browser action was cancelled.") -> None:
        super().__init__(message, BrowserActionErrorCode.CANCELLED)


class BrowserTargetNotFoundError(BrowserCapabilityError):
    def __init__(self, message: str = "No element matched the supplied selector.") -> None:
        super().__init__(message, BrowserActionErrorCode.TARGET_NOT_FOUND)


class BrowserTargetAmbiguousError(BrowserCapabilityError):
    def __init__(self, message: str = "More than one element matched the supplied selector.") -> None:
        super().__init__(message, BrowserActionErrorCode.TARGET_AMBIGUOUS)


class BrowserRefusedSensitiveInputError(BrowserCapabilityError):
    """`browser.type`'s value looked like a password/API key/OTP/payment or
    recovery-code value — refused outright for an AI-originated call. See
    `grant.py::looks_like_secret` and `browser_b5_architecture_gate.md` §7."""

    def __init__(self, message: str = "Refused: the supplied text is shaped like a secret value.") -> None:
        super().__init__(message, BrowserActionErrorCode.REFUSED_SENSITIVE_INPUT)


class BrowserNotYetSupportedError(BrowserCapabilityError):
    """`browser.download`'s permanent posture in B5, and any capability's
    posture before its real execution ships — mirrors Browser-B4's own
    `DenyReason::NotYetSupported` naming/intent exactly (D31), never
    conflated with `PolicyDenied` (this is "not implemented", not "denied
    by policy")."""

    def __init__(self, message: str) -> None:
        super().__init__(message, BrowserActionErrorCode.NOT_YET_SUPPORTED)


class BrowserInternalError(BrowserCapabilityError):
    """Catch-all for anything not represented above. Never carries a raw
    COM/WebView2 exception message or traceback — only ever constructed
    with a caller-safe, already-sanitized message."""

    def __init__(self, message: str = "Internal Browser capability error.") -> None:
        super().__init__(message, BrowserActionErrorCode.INTERNAL_ERROR)


__all__ = [
    "BrowserCancelledError",
    "BrowserCapabilityError",
    "BrowserForbiddenError",
    "BrowserGrantExpiredError",
    "BrowserGrantInvalidError",
    "BrowserInternalError",
    "BrowserNavigationFailedError",
    "BrowserNotYetSupportedError",
    "BrowserPolicyDeniedError",
    "BrowserProfileNotFoundError",
    "BrowserRefusedSensitiveInputError",
    "BrowserStaleReferenceError",
    "BrowserSurfaceNotFoundError",
    "BrowserTargetAmbiguousError",
    "BrowserTargetNotFoundError",
    "BrowserTimeoutError",
    "BrowserUnauthorizedError",
]
