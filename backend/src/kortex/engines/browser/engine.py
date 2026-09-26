"""The KORTEX Browser Capability Engine (Browser-B5.0-B5.4 foundation).

Registers the `kortex.browser.*` capability surface into the existing,
unmodified Kernel/`CapabilityDispatcher`/RBAC-ABAC/`ToolGovernanceEvaluator`
framework — no second dispatcher, no second authorization engine, no second
approval engine (Browser-B5 locked decisions #5/#6/#16). Every capability
here is `requires_execution_context=True`, so `tenant_id` and `principal`
come exclusively from the dispatcher-verified `CapabilityExecutionContext`
— never from a caller-supplied parameter — exactly mirroring
`DesktopAutomationEngine`'s own module doc for the identical reason.

**What this engine does NOT do, deliberately, for the B5.0-B5.4 foundation
phase**: no handler here executes a real browser action. `browser.navigate`,
`.read`, `.click`, `.type`, `.extract`, and `.screenshot` each mint a signed,
short-lived Capability Execution Grant (`grant.py`) and return it — the
Grant is an authorization artifact, not a transport (locked decision #7);
redeeming it against a live `BrowserRuntime` surface is the desktop-side
Rust redeem command's job (Browser-B5.4, `browser_grant.rs`), and *actually
turning on* that redemption for a live, AI-orchestrated end-to-end call is
Browser-B5.5+'s job, kept out of this phase on purpose (per the B5.0-B5.4
authorization's explicit "DO NOT implement browser.navigate execution").
`browser.download` never mints a grant at all — see `download()` below.

See `docs/architecture/browser_b5_architecture_gate.md` for the full
architecture rationale and `docs/architecture/browser_decision_log.md`
(D34+) for the locked B5 V1 decisions this module implements.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.browser import grant as grant_module
from kortex.engines.browser.audit import BROWSER_GRANT_MINTED, record_browser_audit_event
from kortex.engines.browser.exceptions import BrowserNotYetSupportedError, BrowserRefusedSensitiveInputError
from kortex.engines.browser.models import (
    BrowserCapabilityExecutionGrant,
    BrowserCapabilityTarget,
    BrowserElementSelector,
)
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.providers.local_crypto import LocalCrypto

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel
    from kortex.engines.security.interfaces import ICryptoProvider

# `CapabilityExecutionContext` is deliberately a REAL import, not
# TYPE_CHECKING-only (unlike `DesktopAutomationEngine`'s identical-looking
# pattern, which gets away with it) — every handler below declares
# `execution_context: CapabilityExecutionContext | None` in its signature,
# and `CapabilityDispatcher._coerce_model_parameters` (`core/dispatch.py`)
# resolves every handler's annotations via `inspect.signature(handler,
# eval_str=True)`, which `eval()`s each annotation string against the
# handler's own module globals. A name that resolves only under
# `TYPE_CHECKING` raises `NameError` there, which
# `_coerce_model_parameters` catches and silently treats as "skip
# coercion for every parameter on this handler" (its own documented
# fail-open behavior) — not just the one unresolvable parameter. Found
# by this module's own integration test suite failing with
# `AttributeError: 'dict' object has no attribute 'model_dump'` — several
# `BrowserCapabilityTarget`/`BrowserElementSelector`-typed parameters
# were silently arriving as raw dicts. `DesktopAutomationEngine` never
# surfaces this because none of its own handler parameters are
# `BaseModel`-typed, so it never needed coercion to begin with.

NAVIGATE_CAPABILITY = "kortex.browser.navigate"
READ_CAPABILITY = "kortex.browser.read"
CLICK_CAPABILITY = "kortex.browser.click"
TYPE_CAPABILITY = "kortex.browser.type"
EXTRACT_CAPABILITY = "kortex.browser.extract"
DOWNLOAD_CAPABILITY = "kortex.browser.download"
SCREENSHOT_CAPABILITY = "kortex.browser.screenshot"
GRANT_VERIFICATION_KEY_CAPABILITY = "kortex.browser.grant_verification_key"

BROWSER_CAPABILITY_NAMES = (
    NAVIGATE_CAPABILITY,
    READ_CAPABILITY,
    CLICK_CAPABILITY,
    TYPE_CAPABILITY,
    EXTRACT_CAPABILITY,
    DOWNLOAD_CAPABILITY,
    SCREENSHOT_CAPABILITY,
)
"""Every AI-tool-eligible `kortex.browser.*` capability name — deliberately
excludes `GRANT_VERIFICATION_KEY_CAPABILITY`, which is desktop-process
infrastructure (fetching a verification key), never something an AI agent
should itself invoke as a tool."""

_TARGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "browser_profile_id": {"type": "string", "minLength": 1},
        "surface_id": {"type": "string", "minLength": 1},
        "navigation_generation": {"type": "integer", "minimum": 0},
    },
    "required": ["browser_profile_id", "surface_id"],
}
_SELECTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "role": {"type": "string"},
        "accessible_name": {"type": "string"},
        "node_ref": {"type": "string"},
    },
}


def _grant_result(grant: BrowserCapabilityExecutionGrant) -> dict[str, Any]:
    return {"grant": grant.model_dump(mode="json")}


class BrowserCapabilityEngine(BaseEngine):
    """System Engine owning the `kortex.browser.*` capability surface."""

    def __init__(self, *, crypto_provider: ICryptoProvider | None = None, grant_ttl_seconds: int | None = None) -> None:
        super().__init__()
        # Independent from `SecurityEngine`'s own crypto provider instance
        # deliberately — Ed25519 sign/verify is a pure, stateless algorithm
        # with no shared state to keep in sync, and this keeps
        # `BrowserCapabilityEngine` from needing write access to
        # `SecurityEngine`'s internals beyond the one thing it actually
        # needs: `audit_manager`, for the one audit event this phase
        # records (`BROWSER_GRANT_MINTED`).
        self._crypto_provider: ICryptoProvider = crypto_provider if crypto_provider is not None else LocalCrypto()
        self._grant_ttl_seconds = (
            grant_ttl_seconds if grant_ttl_seconds is not None else grant_module.DEFAULT_GRANT_TTL_SECONDS
        )
        self._signing_private_key: bytes | None = None
        self._signing_public_key: bytes | None = None
        self._security_engine: SecurityEngine | None = None

    @property
    def name(self) -> str:
        return "browser"

    @property
    def dependencies(self) -> list[str]:
        return ["security"]

    async def initialize(self, kernel: Kernel) -> None:
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Browser Capability Engine...")

        try:
            self._security_engine = cast(SecurityEngine, kernel.get_engine("security"))
            # Disclosed limitation (not silently assumed): process-lifetime
            # only, does not survive a backend restart. See
            # `grant.py::generate_grant_signing_keypair`'s own doc for why
            # this mirrors an already-accepted pattern
            # (`AgentOrchestrator._DEFAULT_SIGNING_SECRET`) rather than
            # introducing a new risk.
            self._signing_private_key, self._signing_public_key = grant_module.generate_grant_signing_keypair(
                self._crypto_provider
            )

            kernel.register_capability(
                name=NAVIGATE_CAPABILITY,
                description=(
                    "Navigate a Browser surface to a URL. Mints a Capability Execution Grant; "
                    "does not execute — see engine module doc."
                ),
                provider=self.name,
                handler=self.navigate,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "target": _TARGET_SCHEMA,
                        "url": {"type": "string", "minLength": 1},
                        "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 300_000},
                    },
                    "required": ["target", "url"],
                },
                required_permissions=["browser:navigate"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=False,
                is_idempotent=False,
            )
            kernel.register_capability(
                name=READ_CAPABILITY,
                description=(
                    "Read the currently-loaded page's rendered text/title/URL. "
                    "Mints a Capability Execution Grant; does not execute."
                ),
                provider=self.name,
                handler=self.read,
                parameters_schema={"type": "object", "properties": {"target": _TARGET_SCHEMA}, "required": ["target"]},
                required_permissions=["browser:read"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=True,
                is_idempotent=True,
            )
            kernel.register_capability(
                name=CLICK_CAPABILITY,
                description=(
                    "Click one deterministically-targeted element. "
                    "Mints a Capability Execution Grant; does not execute."
                ),
                provider=self.name,
                handler=self.click,
                parameters_schema={
                    "type": "object",
                    "properties": {"target": _TARGET_SCHEMA, "selector": _SELECTOR_SCHEMA},
                    "required": ["target", "selector"],
                },
                required_permissions=["browser:click"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=False,
                is_idempotent=False,
            )
            kernel.register_capability(
                name=TYPE_CAPABILITY,
                description=(
                    "Type text into a deterministically-targeted element. "
                    "Refuses secret-shaped values/targets. "
                    "Mints a Capability Execution Grant; does not execute."
                ),
                provider=self.name,
                handler=self.type_text,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "target": _TARGET_SCHEMA,
                        "selector": _SELECTOR_SCHEMA,
                        "ui_input_text": {"type": "string", "minLength": 1},
                    },
                    "required": ["target", "selector", "ui_input_text"],
                },
                required_permissions=["browser:type"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=False,
                is_idempotent=False,
            )
            kernel.register_capability(
                name=EXTRACT_CAPABILITY,
                description=(
                    "Structured extraction of typed fields from the current page via deterministic selectors. "
                    "Mints a Capability Execution Grant; does not execute."
                ),
                provider=self.name,
                handler=self.extract,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "target": _TARGET_SCHEMA,
                        "schema_fields": {
                            "type": "object",
                            "additionalProperties": _SELECTOR_SCHEMA,
                            "minProperties": 1,
                        },
                    },
                    "required": ["target", "schema_fields"],
                },
                required_permissions=["browser:extract"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=True,
                is_idempotent=True,
            )
            kernel.register_capability(
                name=DOWNLOAD_CAPABILITY,
                description=(
                    "Download capability contract — registered for discovery only. "
                    "B4 denies all downloads; B5 does not implement execution. "
                    "Always returns NotYetSupported."
                ),
                provider=self.name,
                handler=self.download,
                parameters_schema={
                    "type": "object",
                    "properties": {"target": _TARGET_SCHEMA, "url": {"type": "string"}},
                    "required": ["target"],
                },
                required_permissions=["browser:download"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=False,
                is_idempotent=False,
            )
            kernel.register_capability(
                name=SCREENSHOT_CAPABILITY,
                description=(
                    "Capture a Browser surface's viewport (or full page). "
                    "Mints a Capability Execution Grant; does not execute."
                ),
                provider=self.name,
                handler=self.screenshot,
                parameters_schema={
                    "type": "object",
                    "properties": {"target": _TARGET_SCHEMA, "full_page": {"type": "boolean"}},
                    "required": ["target"],
                },
                required_permissions=["browser:screenshot"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=True,
                is_idempotent=False,
            )
            kernel.register_capability(
                name=GRANT_VERIFICATION_KEY_CAPABILITY,
                description=(
                    "Returns the current public key the desktop process must use to verify a "
                    "Capability Execution Grant's signature. Not a secret — publishing it does not weaken anything."
                ),
                provider=self.name,
                handler=self.grant_verification_key,
                parameters_schema={"type": "object", "properties": {}},
                required_permissions=[],
                requires_execution_context=True,
                security_classification="INTERNAL",
                is_read_only=True,
                is_idempotent=True,
            )

            self._set_state(EngineState.READY)
            self.logger.info("KORTEX Browser Capability Engine initialized.")
        except Exception:
            self._set_state(EngineState.FAILED)
            raise

    async def start(self) -> None:
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)

    async def stop(self) -> None:
        self._set_state(EngineState.STOPPING)
        self._set_state(EngineState.STOPPED)

    async def health_check(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "state": self.state.value,
            "grant_signing_key_configured": self._signing_public_key is not None,
        }

    # -- Shared grant-minting plumbing ---------------------------------------

    async def _mint_and_audit(
        self,
        *,
        capability_name: str,
        execution_context: CapabilityExecutionContext | None,
        target: BrowserCapabilityTarget,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        if execution_context is None or execution_context.principal is None:
            # `requires_execution_context=True` + `requires_authentication`
            # defaulting to `True` (never overridden below) should make this
            # unreachable in practice — `CapabilityDispatcher` never invokes
            # a handler without both. Fails closed anyway, per this
            # project's own "theoretically unreachable is not the same
            # claim as verified enforced" discipline (Browser-B4's own
            # doc convention, reused here deliberately).
            from kortex.engines.browser.exceptions import BrowserUnauthorizedError

            raise BrowserUnauthorizedError
        tenant_id = execution_context.tenant_id
        principal_id = execution_context.principal.principal_id
        assert self._signing_private_key is not None
        assert self._signing_public_key is not None
        issued_grant = grant_module.mint_grant(
            crypto_provider=self._crypto_provider,
            signing_private_key=self._signing_private_key,
            signing_public_key=self._signing_public_key,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability_name=capability_name,
            target=target,
            parameters=parameters,
            ttl_seconds=self._grant_ttl_seconds,
        )
        if self._security_engine is not None:
            await record_browser_audit_event(
                self._security_engine.audit_manager,
                BROWSER_GRANT_MINTED,
                tenant_id=tenant_id,
                actor_id=principal_id,
                resource_id=capability_name,
                context={
                    "grant_id": issued_grant.grant_id,
                    "surface_id": issued_grant.surface_id,
                    "browser_profile_id": issued_grant.browser_profile_id,
                    "canonicalized_parameters_hash": issued_grant.canonicalized_parameters_hash,
                    "expires_at": issued_grant.expires_at,
                },
            )
        return _grant_result(issued_grant)

    # -- Capability handlers --------------------------------------------------

    async def navigate(
        self,
        target: BrowserCapabilityTarget,
        url: str,
        timeout_ms: int = 30_000,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.browser.navigate`."""
        return await self._mint_and_audit(
            capability_name=NAVIGATE_CAPABILITY,
            execution_context=execution_context,
            target=target,
            parameters={"target": target.model_dump(mode="json"), "url": url, "timeout_ms": timeout_ms},
        )

    async def read(
        self,
        target: BrowserCapabilityTarget,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.browser.read`."""
        return await self._mint_and_audit(
            capability_name=READ_CAPABILITY,
            execution_context=execution_context,
            target=target,
            parameters={"target": target.model_dump(mode="json")},
        )

    async def click(
        self,
        target: BrowserCapabilityTarget,
        selector: BrowserElementSelector,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.browser.click`."""
        return await self._mint_and_audit(
            capability_name=CLICK_CAPABILITY,
            execution_context=execution_context,
            target=target,
            parameters={"target": target.model_dump(mode="json"), "selector": selector.model_dump(mode="json")},
        )

    async def type_text(
        self,
        target: BrowserCapabilityTarget,
        selector: BrowserElementSelector,
        ui_input_text: str,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.browser.type`.

        Refuses outright (never merely logs) if the target field is itself
        labeled like a credential input (`is_sensitive_type_target`) or the
        value itself is shaped like one (`looks_like_secret_value`) —
        before a Grant is ever minted, so a refused call never reaches the
        desktop at all. See `grant.py`'s own doc on why value-shape alone
        is an incomplete, disclosed-as-such defense, and why the
        target-name check is the stronger of the two.
        """
        if grant_module.is_sensitive_type_target(selector) or grant_module.looks_like_secret_value(ui_input_text):
            raise BrowserRefusedSensitiveInputError
        return await self._mint_and_audit(
            capability_name=TYPE_CAPABILITY,
            execution_context=execution_context,
            target=target,
            parameters={
                "target": target.model_dump(mode="json"),
                "selector": selector.model_dump(mode="json"),
                # Hashed into the Grant's `canonicalized_parameters_hash`
                # only — never placed in the audit context above, and
                # never returned in this handler's own result.
                "ui_input_text": ui_input_text,
            },
        )

    async def extract(
        self,
        target: BrowserCapabilityTarget,
        schema_fields: dict[str, Any],
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.browser.extract`.

        `schema_fields` values are validated as real `BrowserElementSelector`
        shapes (raising on an all-empty selector) before a Grant is minted
        — `dict[str, BrowserElementSelector]` is not one of the shapes
        `Kernel`'s parameter coercion auto-converts (only a bare
        `BaseModel`, `list[BaseModel]`, or `Optional[BaseModel]` are), so
        this handler validates each value explicitly rather than trusting
        an auto-coercion that does not actually happen for a dict-valued
        parameter.
        """
        validated = {key: BrowserElementSelector.model_validate(value) for key, value in schema_fields.items()}
        return await self._mint_and_audit(
            capability_name=EXTRACT_CAPABILITY,
            execution_context=execution_context,
            target=target,
            parameters={
                "target": target.model_dump(mode="json"),
                "schema_fields": {k: v.model_dump(mode="json") for k, v in validated.items()},
            },
        )

    async def download(
        self,
        target: BrowserCapabilityTarget,
        url: str | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.browser.download`.

        Always raises `BrowserNotYetSupportedError` — Browser-B4 denies
        every download unconditionally (D29), and B5 does not implement
        execution for this capability (B5.0-B5.4 authorization, explicit).
        This handler has **no reference anywhere** to `grant.py`'s minting
        function, `BrowserRuntime`, or any WebView2-adjacent primitive —
        not merely policy-configured to refuse, but structurally incapable
        of reaching further, matching `models.py::BrowserDownloadParams`'s
        own doc comment. `target`/`url`/`execution_context` are accepted
        only so the capability's real schema can be registered for
        discovery (Browser-B5 gate §7/§27) — none of them are read.
        """
        del target, url, execution_context
        raise BrowserNotYetSupportedError(
            "browser.download is registered for discovery only; execution is not implemented in Browser-B5."
        )

    async def screenshot(
        self,
        target: BrowserCapabilityTarget,
        full_page: bool = False,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.browser.screenshot`."""
        return await self._mint_and_audit(
            capability_name=SCREENSHOT_CAPABILITY,
            execution_context=execution_context,
            target=target,
            parameters={"target": target.model_dump(mode="json"), "full_page": full_page},
        )

    async def grant_verification_key(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.browser.grant_verification_key` — lets the desktop
        process fetch (and cache for its own process lifetime) the public
        key it needs to independently verify a Grant's signature, never
        trusting "it arrived over an authenticated connection" alone
        (`browser_b5_architecture_gate.md` §6)."""
        del execution_context
        assert self._signing_public_key is not None
        return {"public_key_hex": self._signing_public_key.hex(), "algorithm": "ed25519"}


__all__ = [
    "BROWSER_CAPABILITY_NAMES",
    "CLICK_CAPABILITY",
    "DOWNLOAD_CAPABILITY",
    "EXTRACT_CAPABILITY",
    "GRANT_VERIFICATION_KEY_CAPABILITY",
    "NAVIGATE_CAPABILITY",
    "READ_CAPABILITY",
    "SCREENSHOT_CAPABILITY",
    "TYPE_CAPABILITY",
    "BrowserCapabilityEngine",
]
