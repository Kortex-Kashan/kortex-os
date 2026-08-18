"""
KORTEX Kernel Capability Enforcement Boundary.

Implements the sanctioned execution path for capability invocation:
`Kernel.invoke_capability()` resolves the requested `CapabilityDescriptor`,
authenticates the caller via the unmodified Milestone-3 `AuthenticationManager`,
constructs a `PermissionRequirement` exclusively from the resolved
descriptor's own security metadata (never from caller-supplied request
data), and authorizes via the unmodified Milestone-4
`AuthorizationEngine.authorize_strict` before invoking the handler. All
RBAC/ABAC/authentication decision logic remains entirely inside Security
Engine — this module only coordinates calling it in the correct order, per
`engineering_constitution.md` Art. 7 ("The Kernel owns... execution
coordination... The Kernel owns no business logic").

Security Engine's own capability, `kortex.security.access.authorize`,
remains registered and independently callable exactly as Milestone 4 left
it. This dispatcher never calls it — it calls
`AuthorizationEngine.authorize_strict()` directly as an internal method on
the Security Engine instance, resolved via `kernel.get_engine("security")`.
Calling `access.authorize` yourself is therefore never mistaken for having
authorized execution through this boundary.

`required_permissions=None` on a `CapabilityDescriptor` means the capability
has never been explicitly classified: no RBAC permission check is performed
for it, but authentication (when `requires_authentication=True`, the
default) and every other fail-closed check — ABAC tenant matching,
classification — remain fully applicable. `None` does not mean unrestricted,
does not grant permissions, and does not disable any other check. This
milestone establishes the enforcement mechanism; authoring real permission
metadata for every existing engine's capabilities is separate, future,
engine-by-engine work — this milestone must not be described as achieving
least-privilege completion for currently-unclassified capabilities.

Core -> Security Engine imports here are consistent with `kernel.py`'s own
existing precedent of importing concrete classes directly from
`kortex.engines.boot`, `kortex.engines.configuration`, `kortex.engines.event`,
and `kortex.engines.registry` (see `kernel.py`'s own import block). Kernel is
not hermetically isolated from `kortex.engines.*`; the constraint is that it
must not contain business logic, which this module does not — it holds
references and calls existing methods, evaluating nothing itself.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Dict, Optional, cast

from pydantic import BaseModel, Field

from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError
from kortex.engines.security.models import ClassificationLevel, PermissionRequirement, TokenPayload

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel
    from kortex.engines.registry.engine import CapabilityDescriptor


class CapabilityRequest(BaseModel):
    """Untrusted, caller-constructed request to invoke a capability through
    the Kernel's sanctioned dispatch path.

    Nothing in this model is trusted as authoritative security state —
    `session_token` is verified (never assumed genuine), and
    `parameters`/`context` can never supply or override `required_permissions`,
    `requires_authentication`, `security_classification`, an authentication
    result, or an authorization decision. Those always come from the
    resolved `CapabilityDescriptor` and from calling Security Engine's own
    `AuthenticationManager`/`AuthorizationEngine`.
    """

    capability_name: str
    session_token: Optional[TokenPayload] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)


def _safe_classification(value: str) -> ClassificationLevel:
    """Convert a descriptor's plain `security_classification` string into a
    `ClassificationLevel`, defaulting any unparseable value to `RESTRICTED`.

    This is the opposite fail-closed direction from `abac.py`'s own
    `_classification_rank` fallback, which correctly defaults an unparseable
    *principal clearance* to `PUBLIC` so an unset clearance never grants
    elevation. Here we are defaulting the *requirement's* classification —
    the fail-closed direction for a requirement is the most restrictive
    rank, not the least. Defaulting to `PUBLIC` here would make a malformed
    classification trivially satisfiable by any principal, which is
    fail-open, not fail-closed.
    """
    try:
        return ClassificationLevel(value)
    except ValueError:
        return ClassificationLevel.RESTRICTED


class CapabilityDispatcher:
    """Coordinates the enforcement boundary for `Kernel.invoke_capability()`.

    A plain coordinating object — not a `BaseEngine`, not lifecycle-managed.
    Holds only a reference back to the owning `Kernel`; carries no
    per-request state as instance attributes, so concurrent `dispatch()`
    calls cannot leak identity or decision state into one another.
    """

    def __init__(self, kernel: "Kernel") -> None:
        self._kernel = kernel

    async def dispatch(self, request: CapabilityRequest) -> Any:
        """Resolve, authenticate, authorize, then invoke — in that exact order.

        Raises `CapabilityNotFoundError` if the capability is unregistered,
        `AuthenticationError` (or a subtype) if authentication is required
        and fails, `AuthorizationDeniedError` if `authorize_strict` denies,
        and `SecurityEngineError` if Security Engine itself is unreachable
        or an underlying operation fails. The handler is invoked only after
        every applicable check succeeds.
        """
        descriptor: "CapabilityDescriptor" = self._kernel.get_capability(request.capability_name)

        if not descriptor.requires_authentication:
            return await self._invoke_handler(descriptor, request)

        security_engine = cast(SecurityEngine, self._kernel.get_engine("security"))

        if request.session_token is None:
            raise AuthenticationError(
                f"A session token is required to invoke capability '{request.capability_name}'."
            )

        principal = await security_engine.authentication_manager.verify_token(request.session_token)

        requirement = PermissionRequirement(
            capability_name=descriptor.name,
            required_permissions=list(descriptor.required_permissions or []),
            security_classification=_safe_classification(descriptor.security_classification),
        )

        await security_engine.authorization_engine.authorize_strict(principal, requirement, dict(request.context))

        return await self._invoke_handler(descriptor, request)

    @staticmethod
    async def _invoke_handler(descriptor: "CapabilityDescriptor", request: CapabilityRequest) -> Any:
        """Invoke the resolved handler, awaiting the result only if it is
        actually awaitable.

        Checking `inspect.isawaitable(result)` *after* calling — rather
        than `asyncio.iscoroutinefunction(handler)` *before* calling —
        correctly handles a callable class instance with an `async def
        __call__` (which `iscoroutinefunction` misclassifies as sync, since
        it inspects the object itself, not what calling it produces), in
        addition to plain sync and async functions.
        """
        handler = descriptor.handler
        if handler is None:
            raise RuntimeError(f"Capability '{request.capability_name}' has no registered handler.")
        result = handler(**request.parameters)
        if inspect.isawaitable(result):
            return await result
        return result
