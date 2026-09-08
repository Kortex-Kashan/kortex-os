"""
KORTEX Registry Engine.

Provides centralized runtime registration and capability discovery for Modules,
System Engines, Recipes, Templates, Connectors, Capabilities, and Services.
"""

from __future__ import annotations

import datetime
import enum
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, computed_field

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.exceptions import CapabilityNotFoundError, ResourceAlreadyExistsError, ResourceNotFoundError

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel


_BOOTSTRAP_EXEMPT_CAPABILITIES = frozenset(
    {
        "kortex.security.auth.authenticate",
        # M7.1: the first-run tenant/admin bootstrap capability is the second
        # (and, by design, still deliberately narrow) member of this
        # allowlist — it must be reachable before any session token exists
        # for the identical reason `auth.authenticate` is: there is no
        # principal yet to authenticate as. It is not a general bypass —
        # its own handler (`SecurityEngine.bootstrap_create_admin`) fails
        # closed the moment any principal already exists, exactly mirroring
        # how `authenticate` fails closed on any credential mismatch.
        "kortex.security.bootstrap.create_admin",
        # Phase A (post-RC authentication completion): forgot/reset password
        # must be reachable before any session token exists — that is the
        # entire point of the flow (a signed-out user who cannot authenticate
        # is exactly who needs it). Neither is a general bypass: the reset
        # token itself is the credential being verified
        # (`AuthenticationManager.reset_password`), and the request endpoint
        # never reveals whether an email matched a principal
        # (enumeration-resistant, mirroring `authenticate`'s own discipline).
        "kortex.security.auth.request_password_reset",
        "kortex.security.auth.reset_password",
        # Phase A: OAuth sign-in (Google/Microsoft) for an existing account.
        # `get_config`/`login_begin` are unauthenticated reads/redirects —
        # there is no session yet by definition on the signed-out login
        # screen. `login_complete` is likewise reachable pre-session (the
        # whole point of "sign in with Google"); it mints a real session
        # token itself, via the same generic "bootstrap-exempt capability
        # returning a SecurityPrincipal" mechanism `authenticate` already
        # uses (`kortex.api.main._invoke`), on successfully resolving an
        # existing linked account. It never creates a principal — an
        # external identity with no linked account fails closed with
        # `OAuthNoLinkedAccountError`, exactly mirroring how
        # `bootstrap.create_admin` fails closed once a principal exists.
        "kortex.security.oauth.get_config",
        "kortex.security.oauth.login_begin",
        "kortex.security.oauth.login_complete",
    }
)
"""Capabilities permitted to register with `requires_authentication=False` —
each must be reachable before any session token exists. Enforced in
`RegistryEngine.register_capability`, not merely documented as a convention."""


# KORTEX OS — Automation + Integration Fabric, Milestone F1 (Capability Model
# Completion): one-time, explicit risk classification for every capability
# that existed before this milestone, keyed by canonical name so a future
# accidental change to any engine's registration call is caught as a real
# test failure rather than silently drifting (mirrors
# `test_production_capability_permissions.py`'s `_EXPECTED_PERMISSIONS`
# convention exactly).
#
# Each value is `(is_read_only, is_idempotent)`. Every entry was derived by
# reading the actual handler implementation, never guessed from the
# capability's name or its HTTP-verb-shaped suffix alone — see the F1
# implementation report for the specific evidence behind every non-obvious
# entry (e.g. `connector.profile.delete` and `workflow.instance.cancel` are
# NOT idempotent because their handlers fetch-then-verify the target and
# raise on an already-absent/already-terminal one, while
# `security.oauth.unlink` and `ai.agent.cancel` ARE idempotent because their
# handlers return a safe `False`/no-op instead).
#
# This table is consulted by `register_capability()` ONLY as a fallback for
# a capability that registers without explicitly passing
# `is_read_only`/`is_idempotent` itself — any newly authored capability is
# expected to declare its own classification at its call site instead of
# growing this table. A capability found in neither place registers with the
# same fail-closed default `CapabilityDescriptor` itself declares (mutating,
# non-idempotent) rather than raising — existing capability registrations
# (production and test) must keep working unconditionally; the real guard
# against a *production* capability ever shipping with no real classification
# is `test_capability_metadata_completeness.py`'s expected-value comparison
# against the live-booted Kernel, not a runtime exception here.
_CAPABILITY_RISK_CLASSIFICATION: dict[str, tuple[bool, bool]] = {
    # -- AI --------------------------------------------------------------
    "kortex.ai.agent.cancel": (False, True),
    "kortex.ai.agent.list": (True, True),
    "kortex.ai.agent.orchestrate": (False, False),
    "kortex.ai.agent.resume": (False, False),
    "kortex.ai.agent.status": (True, True),
    "kortex.ai.conversation.history.get": (True, True),
    "kortex.ai.governance.approval.create": (False, False),
    "kortex.ai.governance.audit.query": (True, True),
    "kortex.ai.governance.guardrail.check": (True, True),
    "kortex.ai.governance.policy.evaluate": (True, True),
    "kortex.ai.governance.policy.get": (True, True),
    "kortex.ai.governance.policy.upsert": (False, True),
    "kortex.ai.governance.quota.get": (True, True),
    "kortex.ai.governance.quota.update": (False, True),
    "kortex.ai.model.list": (True, True),
    "kortex.ai.provider.config.list": (True, True),
    "kortex.ai.provider.config.remove": (False, True),
    "kortex.ai.provider.configure": (False, True),
    "kortex.ai.provider.list": (True, True),
    "kortex.ai.provider.register": (False, False),
    "kortex.ai.provider.test": (True, True),
    "kortex.ai.response.generate": (False, False),
    "kortex.ai.tool.invoke": (False, False),
    # -- Backup ------------------------------------------------------------
    "kortex.backup.create": (False, False),
    "kortex.backup.delete": (False, False),
    "kortex.backup.diagnostics.get": (True, True),
    "kortex.backup.get": (True, True),
    "kortex.backup.list": (True, True),
    "kortex.backup.verify": (True, True),
    # -- Connector -----------------------------------------------------------
    "kortex.connector.action.execute": (False, False),
    "kortex.connector.driver.list": (True, True),
    "kortex.connector.driver.register": (False, False),
    "kortex.connector.profile.delete": (False, False),
    "kortex.connector.profile.get": (True, True),
    "kortex.connector.profile.list": (True, True),
    "kortex.connector.profile.register": (False, True),
    # -- Document ------------------------------------------------------------
    "kortex.document.adapter.list": (True, True),
    "kortex.document.adapter.register": (False, False),
    "kortex.document.intelligence.analyze": (True, True),
    "kortex.document.lifecycle.transition": (False, False),
    "kortex.document.operation.execute": (False, False),
    "kortex.document.preview.generate": (True, True),
    "kortex.document.profile.list": (True, True),
    "kortex.document.recommendation.get": (True, True),
    "kortex.document.template.bind": (True, True),
    "kortex.document.template.list": (True, True),
    # -- Document Intelligence ------------------------------------------------
    "kortex.document_intelligence.ocr.extract": (True, True),
    "kortex.document_intelligence.pdf.parse": (True, True),
    "kortex.document_intelligence.structure.analyze": (True, True),
    # -- Finance ---------------------------------------------------------------
    "kortex.finance.invoice.create": (False, False),
    "kortex.finance.invoice.get": (True, True),
    # -- HR & Payroll ------------------------------------------------------------
    "kortex.hr_payroll.attendance.check_in": (False, False),
    "kortex.hr_payroll.attendance.check_out": (False, False),
    "kortex.hr_payroll.attendance.list": (True, True),
    "kortex.hr_payroll.employee.create": (False, False),
    "kortex.hr_payroll.employee.get": (True, True),
    "kortex.hr_payroll.employee.list": (True, True),
    "kortex.hr_payroll.leave.balance_get": (True, True),
    "kortex.hr_payroll.leave.decide": (False, False),
    "kortex.hr_payroll.leave.request": (False, False),
    "kortex.hr_payroll.payroll.calculate": (False, False),
    "kortex.hr_payroll.payroll.run_get": (True, True),
    "kortex.hr_payroll.payslip.get": (True, True),
    # -- Knowledge -----------------------------------------------------------
    "kortex.knowledge.graph.list": (True, True),
    "kortex.knowledge.graph.traverse": (True, True),
    "kortex.knowledge.pack.load": (False, False),
    "kortex.knowledge.query.search": (True, True),
    "kortex.knowledge.source.index": (False, False),
    # -- License -------------------------------------------------------------
    "kortex.license.activation.apply": (False, True),
    "kortex.license.activation.revoke": (False, True),
    "kortex.license.status.get": (True, True),
    "kortex.license.token.verify": (True, True),
    # -- Marketplace ---------------------------------------------------------
    "kortex.marketplace.listing.list": (True, True),
    # -- Monitoring ------------------------------------------------------------
    "kortex.monitoring.dashboard.get": (True, True),
    "kortex.monitoring.diagnostics.get": (True, True),
    "kortex.monitoring.metrics.get": (True, True),
    "kortex.monitoring.timeseries.get": (True, True),
    # -- Operations ------------------------------------------------------------
    "kortex.operations.incident.close": (False, False),
    "kortex.operations.incident.get": (True, True),
    "kortex.operations.incident.list": (True, True),
    "kortex.operations.incident.report": (False, False),
    "kortex.operations.incident.resolve": (False, False),
    "kortex.operations.incident.status_update": (False, False),
    "kortex.operations.vehicle.assign": (False, False),
    "kortex.operations.vehicle.create": (False, False),
    "kortex.operations.vehicle.get": (True, True),
    "kortex.operations.vehicle.list": (True, True),
    "kortex.operations.vehicle.status_update": (False, False),
    "kortex.operations.vehicle.tracking_history": (True, True),
    "kortex.operations.vehicle.tracking_record": (False, False),
    "kortex.operations.vehicle.unassign": (False, False),
    # -- Security --------------------------------------------------------------
    "kortex.security.access.authorize": (True, True),
    "kortex.security.auth.authenticate": (False, False),
    "kortex.security.auth.change_password": (False, True),
    "kortex.security.auth.request_password_reset": (False, False),
    "kortex.security.auth.reset_password": (False, False),
    "kortex.security.bootstrap.create_admin": (False, False),
    "kortex.security.oauth.get_config": (True, True),
    "kortex.security.oauth.link_begin": (False, False),
    "kortex.security.oauth.link_complete": (False, False),
    "kortex.security.oauth.list_links": (True, True),
    "kortex.security.oauth.login_begin": (False, False),
    "kortex.security.oauth.login_complete": (False, False),
    "kortex.security.oauth.unlink": (False, True),
    "kortex.security.principal.register": (False, False),
    "kortex.security.principal.set_email": (False, True),
    "kortex.security.secret.get": (True, True),
    "kortex.security.secret.put": (False, True),
    "kortex.security.signature.verify": (True, True),
    # -- Sentinel --------------------------------------------------------------
    "kortex.sentinel.diagnostics.get": (True, True),
    "kortex.sentinel.health.get": (True, True),
    "kortex.sentinel.status.get": (True, True),
    # -- Storage ---------------------------------------------------------------
    "kortex.storage.cache.set": (False, True),
    "kortex.storage.data.session": (True, True),
    "kortex.storage.file.store": (False, True),
    "kortex.storage.object.put": (False, True),
    # -- Workflow --------------------------------------------------------------
    "kortex.workflow.approval.create": (False, False),
    "kortex.workflow.approval.decide": (False, False),
    "kortex.workflow.approval.delegate": (False, False),
    "kortex.workflow.approval.get": (True, True),
    "kortex.workflow.approval.list": (True, True),
    "kortex.workflow.definition.list": (True, True),
    "kortex.workflow.external.cancel": (False, False),
    "kortex.workflow.external.execute": (False, False),
    "kortex.workflow.external.get": (True, True),
    "kortex.workflow.external.list": (True, True),
    "kortex.workflow.instance.approve": (False, False),
    "kortex.workflow.instance.cancel": (False, False),
    "kortex.workflow.instance.get": (True, True),
    "kortex.workflow.instance.list": (True, True),
    "kortex.workflow.instance.resume": (False, False),
    "kortex.workflow.instance.start": (False, False),
    "kortex.workflow.schedule.cancel": (False, True),
    "kortex.workflow.schedule.create": (False, False),
    "kortex.workflow.schedule.get": (True, True),
    "kortex.workflow.schedule.list": (True, True),
    "kortex.workflow.schedule.pause": (False, True),
    "kortex.workflow.schedule.resume": (False, True),
    "kortex.workflow.schedule.trigger": (False, False),
    "kortex.workflow.state.get": (True, True),
    # -- Recipe (registered by RecipeEngine; not currently wired into the
    # production boot path — see the F1 report's scope note — classified
    # here anyway for accuracy wherever RecipeEngine is booted independently,
    # e.g. in its own unit/integration tests) --------------------------------
    "kortex.recipe.compile": (True, True),
    "kortex.recipe.info": (True, True),
    "kortex.recipe.install": (False, False),
    "kortex.recipe.list": (True, True),
    "kortex.recipe.load": (True, True),
    "kortex.recipe.package": (True, True),
    "kortex.recipe.remove": (False, False),
    "kortex.recipe.search": (True, True),
    "kortex.recipe.upgrade": (False, False),
    "kortex.recipe.validate": (True, True),
}


class RegistryCategory(str, enum.Enum):
    """Resource registration categories."""

    ENGINE = "ENGINE"
    MODULE = "MODULE"
    RECIPE = "RECIPE"
    TEMPLATE = "TEMPLATE"
    CONNECTOR = "CONNECTOR"
    CAPABILITY = "CAPABILITY"
    SERVICE = "SERVICE"


class ResourceMetadata(BaseModel):
    """Metadata descriptor for registered resources."""

    name: str = Field(description="Unique name identifier")
    category: RegistryCategory = Field(description="Category classification")
    version: str = Field(default="0.1.0", description="Resource version string")
    description: str = Field(default="", description="Human readable description")
    provider: str = Field(default="system", description="Provider or author module name")
    attributes: dict[str, Any] = Field(default_factory=dict, description="Custom attribute metadata")
    registered_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
        description="Timestamp of registration",
    )


class CapabilityDescriptor(BaseModel):
    """Specification for an AI or system capability registered by a module or engine."""

    name: str = Field(description="Unique capability name, e.g., 'payroll.calculate'")
    description: str = Field(description="Description of what this capability performs")
    provider: str = Field(description="Name of the providing module or engine")
    parameters_schema: dict[str, Any] = Field(default_factory=dict, description="JSON schema for parameters")
    returns_schema: dict[str, Any] = Field(default_factory=dict, description="JSON schema for return value")
    required_permissions: list[str] | None = Field(
        default=None,
        description=(
            "RBAC permission keys required to execute this capability, sourced exclusively from this "
            "descriptor by the Kernel dispatcher (`kortex.core.dispatch`) — never from a caller's "
            "request. `None` means this capability has never been explicitly classified: it still "
            "requires authentication when `requires_authentication` is True, but no RBAC permission "
            "check is performed for it. `None` does not mean unrestricted, does not grant permissions, "
            "and does not disable ABAC or classification checks. An empty list `[]` means explicitly "
            "classified as requiring zero specific permissions."
        ),
    )
    requires_authentication: bool = Field(
        default=True,
        description=(
            "Whether the Kernel dispatcher requires a verified session token before invoking this "
            "capability's handler. Only the small, fixed allowlist in `_BOOTSTRAP_EXEMPT_CAPABILITIES` "
            "may register with this set to False — enforced in `register_capability` below, not merely "
            "a convention. Every other capability defaults to True."
        ),
    )
    security_classification: str = Field(
        default="INTERNAL",
        description=(
            "Minimum security classification governing this capability, stored as a plain string so "
            "the Registry stays independent of Security Engine's `ClassificationLevel` enum. "
            "Interpreted by the Kernel dispatcher, which fails closed to RESTRICTED on any unparseable "
            "value."
        ),
    )
    requires_execution_context: bool = Field(
        default=False,
        description=(
            "KORTEX Platform Security — Capability Identity Propagation: when True, the Kernel "
            "dispatcher injects a trusted `CapabilityExecutionContext` (containing the dispatcher-"
            "authenticated principal and authoritative tenant_id) as the `execution_context` keyword "
            "argument on every invocation of this capability's handler — never conditionally, never "
            "from caller data. Defaults to False so the hundreds of pre-existing capabilities across "
            "every other engine are entirely unaffected; only handlers that need to know 'who is "
            "calling' opt in. Validated once, at registration time, against the handler's actual "
            "signature (see `register_capability` below) rather than re-inspected on every request."
        ),
    )
    legacy_principal_bridge: bool = Field(
        default=False,
        description=(
            "Transitional-migration flag only, defaulting False. When True (alongside "
            "requires_execution_context=True), the dispatcher additionally injects "
            "`execution_context.principal` as a `principal` keyword argument, for handlers not yet "
            "migrated to read `execution_context.principal` directly. The injected value is always "
            "the dispatcher-authenticated principal, never caller-suppliable — a caller-supplied "
            "`principal` key in `parameters` is rejected outright, not overridden."
        ),
    )
    is_read_only: bool = Field(
        default=False,
        description=(
            "KORTEX OS — Automation + Integration Fabric, Milestone F1 (Capability Model "
            "Completion): True iff a successful invocation never changes the authoritative state "
            "this capability represents. Authoritative registry data set exclusively at "
            "registration time by `register_capability()` — never caller-suppliable via "
            "`CapabilityRequest.parameters` or any other request-shaped input, so a caller can never "
            "make a mutating capability report itself as read-only. Defaults to False (fail-closed): "
            "a capability that does not explicitly classify itself is treated as mutating until "
            "proven otherwise, exactly mirroring `required_permissions=None`'s existing "
            "fail-closed-elsewhere convention on this same model."
        ),
    )
    is_idempotent: bool = Field(
        default=False,
        description=(
            "KORTEX OS — Automation + Integration Fabric, Milestone F1: True iff invoking this "
            "capability twice with the same parameters converges to the same authoritative end "
            "state without accumulating additional effects beyond the first call. Not implied by "
            "`is_read_only=True` being False, and not implied by the presence of a caller-supplied "
            "idempotency key elsewhere in the platform (`CapabilityRequest.idempotency_key` is an "
            "orthogonal, opt-in duplicate-suppression mechanism a caller may or may not use — it "
            "does not make the underlying capability's own contract idempotent). Authoritative "
            "registry data, same non-caller-suppliable guarantee as `is_read_only`. Defaults to "
            "False (fail-closed) for the identical reason."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def owner_domain(self) -> str:
        """The `<domain>` segment of this capability's canonical `kortex.<domain>...` name.

        Derived, never independently settable — deriving from `name` (which is itself immutable
        once registered) means this can never drift out of sync with the capability it describes,
        unlike a second, separately-populated field would. See `capability_registry.md` §1's
        canonical naming convention, which this only reads back, not reinterprets.
        """
        return _capability_name_segments(self.name)[0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resource_type(self) -> str:
        """The resource segment(s) of this capability's canonical name, between `owner_domain` and
        `action` — dot-joined verbatim when more than one segment exists (e.g. `governance.approval`
        for `kortex.ai.governance.approval.create`), and equal to `owner_domain` itself when the
        name carries no separate resource segment at all (e.g. `kortex.recipe.compile`, where the
        recipe engine's own catalog is the operated-on resource). Never fabricated: this is exactly
        the structure `capability_registry.md` §1's naming convention already encodes in `name`."""
        return _capability_name_segments(self.name)[1]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def action(self) -> str:
        """The final `<action>` segment of this capability's canonical name, verbatim."""
        return _capability_name_segments(self.name)[2]


def _capability_name_segments(name: str) -> tuple[str, str, str]:
    """Deterministically split a capability name into `(owner_domain, resource_type, action)`.

    For a canonical `kortex.<domain>.<resource>.<action>` name (`capability_registry.md` §1) — true
    of every one of the 140 capabilities registered by the production boot path
    (`kernel_bootstrap.build_and_boot_kernel`), verified directly against a live-booted
    `Kernel.list_capabilities()` during F1's implementation — this is a readback of a naming
    convention the platform already enforces, not a new inference: the leading `kortex` segment is
    dropped, the next segment is `owner_domain`, the last is `action`, and anything in between
    (dot-joined verbatim when more than one segment, or repeating `owner_domain` when there is none)
    is `resource_type`.

    Deliberately never raises: a capability registered under a non-canonical name (many pre-F1 unit
    tests register synthetic names like `"dispatch.test.allowed"`, with no `kortex.` prefix at all,
    and F1 must not break a single one of them — see `register_capability`'s own "existing
    registrations must continue to work" requirement) instead gets the same best-effort split
    applied to its own segments, with no leading-segment drop — an honest, degraded-but-non-crashing
    decomposition, never a fabricated one.
    """
    segments = name.split(".")
    if len(segments) > 1 and segments[0] == "kortex":
        segments = segments[1:]
    if not segments:
        return "", "", ""
    if len(segments) == 1:
        return segments[0], segments[0], segments[0]
    domain = segments[0]
    if len(segments) == 2:
        return domain, domain, segments[1]
    return domain, ".".join(segments[1:-1]), segments[-1]


class RegistryEngine(BaseEngine):
    """Central Capability and System Registry Engine."""

    def __init__(self) -> None:
        super().__init__()
        self._stores: dict[RegistryCategory, dict[str, ResourceMetadata]] = {cat: {} for cat in RegistryCategory}
        self._handlers: dict[str, Any] = {}
        self._capabilities: dict[str, CapabilityDescriptor] = {}
        self._capability_handlers: dict[str, Callable[..., Any] | None] = {}
        """Milestone M8: the SOLE store of real capability handler callables.

        Deliberately separate from `_capabilities` (which holds the public
        `CapabilityDescriptor` returned by `get_capability()`/
        `list_capabilities()`). A `CapabilityDescriptor` never carries a
        reference to its handler — only this private dict does, and it is
        resolved exclusively via `_resolve_handler()` (dispatcher-internal)
        or `get_raw_handler_for_testing()` (test-only). This closes the
        direct `descriptor.handler(...)` bypass of `Kernel.invoke_capability()`
        found during M8 adversarial hardening.
        """

    @property
    def name(self) -> str:
        return "registry"

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize the Registry Engine."""
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing Registry Engine...")
        self._set_state(EngineState.READY)

    async def start(self) -> None:
        """Start the Registry Engine."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Registry Engine running.")

    async def health_check(self) -> dict[str, Any]:
        """Diagnostic health check."""
        counts = {cat.value: len(store) for cat, store in self._stores.items()}
        return {
            "engine": self.name,
            "status": "healthy" if self.state == EngineState.RUNNING else "unhealthy",
            "registered_counts": counts,
            "capabilities_count": len(self._capabilities),
        }

    async def stop(self) -> None:
        """Stop the Registry Engine."""
        self._set_state(EngineState.STOPPING)
        self._set_state(EngineState.STOPPED)
        self.logger.info("Registry Engine stopped.")

    # -- Core Registration APIs ---------------------------------------------

    def register_resource(
        self,
        name: str,
        category: RegistryCategory,
        target_object: Any = None,
        description: str = "",
        version: str = "0.1.0",
        provider: str = "system",
        attributes: dict[str, Any] | None = None,
        allow_overwrite: bool = False,
    ) -> ResourceMetadata:
        """Register any named resource into a registry category."""
        store = self._stores[category]
        if name in store and not allow_overwrite:
            raise ResourceAlreadyExistsError(f"Resource '{name}' is already registered in category '{category.value}'.")

        meta = ResourceMetadata(
            name=name,
            category=category,
            version=version,
            description=description,
            provider=provider,
            attributes=attributes or {},
        )
        store[name] = meta
        if target_object is not None:
            self._handlers[f"{category.value}:{name}"] = target_object

        self.logger.info("Registered %s: '%s' (Provider: %s)", category.value, name, provider)
        return meta

    def get_resource(self, name: str, category: RegistryCategory) -> ResourceMetadata:
        """Fetch metadata for a registered resource."""
        store = self._stores[category]
        if name not in store:
            raise ResourceNotFoundError(f"Resource '{name}' not found in category '{category.value}'.")
        return store[name]

    def get_target_object(self, name: str, category: RegistryCategory) -> Any:
        """Fetch the registered target instance or handler object."""
        key = f"{category.value}:{name}"
        if key not in self._handlers:
            raise ResourceNotFoundError(
                f"Target instance object for '{name}' not found in category '{category.value}'."
            )
        return self._handlers[key]

    def list_resources(self, category: RegistryCategory) -> list[ResourceMetadata]:
        """List all registered metadata objects in a category."""
        return list(self._stores[category].values())

    # -- Specific Helper Methods --------------------------------------------

    def register_engine(self, engine_name: str, instance: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(engine_name, RegistryCategory.ENGINE, instance, description=description)

    def get_engine(self, engine_name: str) -> Any:
        return self.get_target_object(engine_name, RegistryCategory.ENGINE)

    def register_module(self, module_name: str, instance: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(module_name, RegistryCategory.MODULE, instance, description=description)

    def get_module(self, module_name: str) -> Any:
        return self.get_target_object(module_name, RegistryCategory.MODULE)

    def register_recipe(self, recipe_name: str, recipe_def: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(recipe_name, RegistryCategory.RECIPE, recipe_def, description=description)

    def get_recipe(self, recipe_name: str) -> Any:
        return self.get_target_object(recipe_name, RegistryCategory.RECIPE)

    def register_template(self, template_name: str, template_def: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(template_name, RegistryCategory.TEMPLATE, template_def, description=description)

    def get_template(self, template_name: str) -> Any:
        return self.get_target_object(template_name, RegistryCategory.TEMPLATE)

    def register_connector(self, connector_name: str, instance: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(connector_name, RegistryCategory.CONNECTOR, instance, description=description)

    def get_connector(self, connector_name: str) -> Any:
        return self.get_target_object(connector_name, RegistryCategory.CONNECTOR)

    def register_service(self, service_name: str, instance: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(service_name, RegistryCategory.SERVICE, instance, description=description)

    def get_service(self, service_name: str) -> Any:
        return self.get_target_object(service_name, RegistryCategory.SERVICE)

    # -- Capability Discovery & Lookup -------------------------------------

    def register_capability(
        self,
        name: str,
        description: str,
        provider: str,
        handler: Callable[..., Any] | None = None,
        parameters_schema: dict[str, Any] | None = None,
        returns_schema: dict[str, Any] | None = None,
        required_permissions: list[str] | None = None,
        requires_authentication: bool = True,
        security_classification: str = "INTERNAL",
        requires_execution_context: bool = False,
        legacy_principal_bridge: bool = False,
        is_read_only: bool | None = None,
        is_idempotent: bool | None = None,
    ) -> CapabilityDescriptor:
        """Register an AI-discoverable capability.

        `requires_authentication=False` is a bootstrap carve-out reserved
        exclusively for `kortex.security.auth.authenticate` — the one
        capability that must be reachable before any session token exists.
        Any other capability name attempting to register with
        `requires_authentication=False` is rejected here: this invariant
        was not enforced anywhere in the platform before this milestone, so
        it is enforced at this single, authoritative capability-registration
        choke point rather than left as an unenforced convention.

        KORTEX Platform Security — Capability Identity Propagation:
        `requires_execution_context`/`legacy_principal_bridge` are validated
        against `handler`'s actual signature exactly once, here, at
        registration time (trusted, boot-time code) — never re-inspected on
        every invocation. This is deliberate: per-call `inspect.signature()`
        is fragile against decorators without `functools.wraps`, dynamically
        constructed callables, etc., and a mis-detection at request time
        would fail silently, under attacker-influenced timing, in
        production. A mis-declaration here instead fails loudly, once, at
        boot, in a fully trusted context — see `_validate_execution_context_binding`.

        KORTEX OS — Automation + Integration Fabric, Milestone F1 (Capability
        Model Completion): `is_read_only`/`is_idempotent` are the two risk
        fields a capability cannot safely default to a name-derived guess
        (unlike `owner_domain`/`resource_type`/`action`, which
        `CapabilityDescriptor` derives from `name` itself). A caller passing
        either explicitly always wins — this is how every production
        capability's own registration call declares its real classification,
        and is the intended path for every future one. A caller passing
        neither (true of every pre-F1 call site, production and test alike —
        F1 must not force an edit onto every one of them just to keep
        registering) falls back to `_CAPABILITY_RISK_CLASSIFICATION`'s
        one-time, evidence-derived entry for `name` when one exists, and
        otherwise to the same fail-closed default `CapabilityDescriptor`
        itself declares (`False`, `False` — mutating, non-idempotent, until
        proven otherwise): never a raise, and never a silently *permissive*
        guess in either direction. The real enforcement that every
        production capability actually carries a *correct*, non-default
        classification is a test-level guard
        (`test_capability_metadata_completeness.py`), exactly mirroring how
        `test_production_capability_permissions.py` already enforces the
        production `required_permissions` mapping — not a runtime exception
        inside this shared, test-and-production-both method.
        """
        if name in self._capabilities:
            raise ResourceAlreadyExistsError(f"Capability '{name}' is already registered.")

        if not requires_authentication and name not in _BOOTSTRAP_EXEMPT_CAPABILITIES:
            raise ValueError(
                f"Capability '{name}' cannot register with requires_authentication=False; "
                f"only {sorted(_BOOTSTRAP_EXEMPT_CAPABILITIES)} may bypass authentication."
            )

        if is_read_only is None or is_idempotent is None:
            fallback_read_only, fallback_idempotent = _CAPABILITY_RISK_CLASSIFICATION.get(name, (False, False))
            if is_read_only is None:
                is_read_only = fallback_read_only
            if is_idempotent is None:
                is_idempotent = fallback_idempotent

        if handler is not None:
            try:
                sig = inspect.signature(handler)
                handler_params = sig.parameters
                if not requires_execution_context and "execution_context" in handler_params:
                    requires_execution_context = True
                if not legacy_principal_bridge and "principal" in handler_params:
                    legacy_principal_bridge = True
            except (TypeError, ValueError):
                pass

        if handler is not None and (requires_execution_context or legacy_principal_bridge):
            self._validate_execution_context_binding(
                name=name,
                handler=handler,
                requires_execution_context=requires_execution_context,
                legacy_principal_bridge=legacy_principal_bridge,
            )

        descriptor = CapabilityDescriptor(
            name=name,
            description=description,
            provider=provider,
            parameters_schema=parameters_schema or {},
            returns_schema=returns_schema or {},
            required_permissions=required_permissions,
            requires_authentication=requires_authentication,
            security_classification=security_classification,
            requires_execution_context=requires_execution_context,
            legacy_principal_bridge=legacy_principal_bridge,
            is_read_only=is_read_only,
            is_idempotent=is_idempotent,
        )
        self._capabilities[name] = descriptor
        self._capability_handlers[name] = handler
        self.register_resource(name, RegistryCategory.CAPABILITY, handler, description=description, provider=provider)
        self.logger.info("Registered Capability: '%s' (Provider: %s)", name, provider)
        return descriptor

    @staticmethod
    def _validate_execution_context_binding(
        name: str,
        handler: Callable[..., Any],
        requires_execution_context: bool,
        legacy_principal_bridge: bool,
    ) -> None:
        """One-time, registration-time (never per-invocation) signature check.

        Fails loudly at boot if a handler declaring `requires_execution_context=True`
        cannot actually receive the keyword arguments the dispatcher will inject —
        catching an authoring mistake immediately, in trusted code, rather than as a
        `TypeError` on the first real (possibly attacker-influenced-timing) invocation.
        """
        try:
            signature = inspect.signature(handler)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Capability '{name}' declares requires_execution_context=True but its handler's "
                f"signature could not be inspected at registration time: {exc}"
            ) from exc

        params = signature.parameters
        accepts_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

        if requires_execution_context and "execution_context" not in params and not accepts_var_kwargs:
            raise ValueError(
                f"Capability '{name}' declares requires_execution_context=True but its handler "
                f"accepts neither an 'execution_context' parameter nor **kwargs — it would raise "
                f"TypeError on every real invocation."
            )
        if legacy_principal_bridge and "principal" not in params and not accepts_var_kwargs:
            raise ValueError(
                f"Capability '{name}' declares legacy_principal_bridge=True but its handler accepts "
                f"neither a 'principal' parameter nor **kwargs — it would raise TypeError on every "
                f"real invocation."
            )

    def get_capability(self, name: str) -> CapabilityDescriptor:
        """Fetch capability descriptor by name.

        The returned `CapabilityDescriptor` never contains, references, or
        otherwise provides execution access to the real capability handler
        (Milestone M8) — it is pure introspection metadata. The only
        sanctioned production execution path remains
        `Kernel.invoke_capability()`.
        """
        if name not in self._capabilities:
            raise CapabilityNotFoundError(f"Capability '{name}' not found in registry.")
        return self._capabilities[name]

    def list_capabilities(self) -> list[CapabilityDescriptor]:
        """List all discoverable capabilities."""
        return list(self._capabilities.values())

    def search_capabilities(
        self,
        *,
        owner_domain: str | None = None,
        resource_type: str | None = None,
        action: str | None = None,
        is_read_only: bool | None = None,
        is_idempotent: bool | None = None,
        keyword: str | None = None,
    ) -> list[CapabilityDescriptor]:
        """Filter discoverable capabilities by canonical metadata (F1, `capability_registry.md` §3/§13).

        Pure in-memory filtering over the same catalog `list_capabilities()` already returns — no
        additional store, no database query, and therefore no scan cost beyond what a caller's own
        filter predicates require. Every parameter is optional and additive (AND-combined); omitting
        all of them is equivalent to `list_capabilities()`.

        `keyword` matches case-insensitively against `name` or `description` — deliberately not
        stemmed/fuzzy, since the intended near-term consumers (a future capability catalog, an AI
        tool-schema generator, a workflow node library) need deterministic, explainable results, not
        best-effort ranking.
        """
        results = list(self._capabilities.values())
        if owner_domain is not None:
            results = [c for c in results if c.owner_domain == owner_domain]
        if resource_type is not None:
            results = [c for c in results if c.resource_type == resource_type]
        if action is not None:
            results = [c for c in results if c.action == action]
        if is_read_only is not None:
            results = [c for c in results if c.is_read_only == is_read_only]
        if is_idempotent is not None:
            results = [c for c in results if c.is_idempotent == is_idempotent]
        if keyword:
            needle = keyword.lower()
            results = [c for c in results if needle in c.name.lower() or needle in c.description.lower()]
        return results

    def _resolve_handler(self, name: str) -> Callable[..., Any] | None:
        """Internal, dispatcher-only handler resolution (Milestone M8).

        NOT part of the public contract — not exposed on `Kernel`, not
        intended for any caller other than
        `kortex.core.dispatch.CapabilityDispatcher._invoke_handler`, reached
        via `Kernel`'s own already-private `_registry_engine` attribute
        (the dispatcher lives inside the same trust boundary as `Kernel`
        itself; this is not a new public API). Deliberately never returns
        anything derived from a caller-supplied `CapabilityDescriptor`.
        """
        if name not in self._capabilities:
            raise CapabilityNotFoundError(f"Capability '{name}' not found in registry.")
        return self._capability_handlers[name]

    def get_raw_handler_for_testing(self, name: str) -> Callable[..., Any] | None:
        """TEST-ONLY accessor for a capability's raw handler (Milestone M8).

        Exists solely so existing unit/integration tests that deliberately
        exercise a capability handler in isolation — without full Kernel
        dispatch machinery — keep working after `CapabilityDescriptor.handler`
        was removed. Bypasses authentication, RBAC, ABAC, tenant, and
        classification enforcement entirely, exactly like the pre-M8
        `descriptor.handler` did.

        Production code must never call this. Doing so reintroduces the
        exact bypass this milestone closed.
        """
        return self._resolve_handler(name)

    def set_raw_handler_for_testing(self, name: str, handler: Callable[..., Any]) -> None:
        """TEST-ONLY: substitute a registered capability's handler (Milestone M8).

        Preserves the existing test pattern of swapping a capability's
        handler for a spy/counting handler while the capability's real
        metadata (`required_permissions`, `requires_authentication`,
        `security_classification`) and the full `Kernel.invoke_capability`
        enforcement path remain exactly as registered — the substituted
        handler still only runs after authentication/RBAC/ABAC succeed.

        Production code must never call this.
        """
        if name not in self._capabilities:
            raise CapabilityNotFoundError(f"Capability '{name}' not found in registry.")
        self._capability_handlers[name] = handler
