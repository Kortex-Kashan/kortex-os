"""KORTEX Finance Business Module — Core Facade (`FinanceModule`).

Proves one genuine, tenant-isolated business operation through the new
minimal `BaseModule` foundation (`kortex.core.base_module`): a real Finance
Invoice, created in `DRAFT` state, dispatched through the real Kernel
`CapabilityDispatcher`, persisted via the existing `StorageEngine`/
`IDataStore` abstraction. See the Finance-pilot planning pass preceding
this commit for the full boundary this implementation follows exactly.

Deliberately implements exactly one capability
(`kortex.finance.invoice.create`) and nothing else -- no
`invoice.get`/`.list`/`.update`/`.delete`/`.publish`, no Purchase Orders,
Salary Sheets, customers, payments, taxes, or accounting ledger. See
module-level "Explicitly Out of Scope" accounting in the implementation
report for the full list of deferred `business_module_architecture.md`
platform-scale concerns (packaging, signing, Marketplace distribution,
DAG dependency resolution, IoC container, dynamic discovery, upgrade/
rollback) this module does not build.

Dependencies are exactly `["storage", "security"]` -- Workflow and
RecipeEngine are not required: creating one DRAFT invoice is a single,
synchronous capability call with no multi-step state machine or
approval gate, and this module's business logic is hand-coded Python
(the same pattern every existing engine already uses), not a declarative
recipe RecipeEngine would compile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from kortex.core.base_module import BaseModule, ModuleState
from kortex.core.exceptions import KortexError
from kortex.engines.security.models import SecurityPrincipal
from kortex.engines.storage.interfaces import IDataStore
from kortex.modules.finance.manager import FinanceInvoiceManager
from kortex.modules.finance.models import CreateInvoiceRequest, FinanceInvoice

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

_REGISTERED_CAPABILITIES: List[str] = [
    "kortex.finance.invoice.create",
]


class FinanceModule(BaseModule):
    """KORTEX Finance Business Module Facade — see module docstring."""

    def __init__(self) -> None:
        super().__init__()
        self._invoice_manager: FinanceInvoiceManager | None = None

    @property
    def name(self) -> str:
        """Unique identifier name for this module."""
        return "finance"

    @property
    def namespace(self) -> str:
        """Canonical capability namespace this module owns."""
        return "kortex.finance"

    @property
    def dependencies(self) -> List[str]:
        """Prerequisite System Engines this module depends on."""
        return ["storage", "security"]

    # -- Lifecycle Implementation ---------------------------------------

    async def initialize(self, kernel: "Kernel") -> None:
        """Resolve the Storage Engine's `IDataStore` and register this
        module's capability with the Kernel -- mirrors Knowledge Engine's
        own `initialize()` resolution pattern exactly."""
        self.ensure_state(ModuleState.UNINITIALIZED)
        self._set_state(ModuleState.INITIALIZING)
        self.logger.info("Initializing KORTEX Finance Module...")

        try:
            storage_engine = kernel.get_engine("storage")
            data_store: IDataStore | None = getattr(storage_engine, "data", None)
            if data_store is None:
                raise KortexError("Storage Engine did not provide an IDataStore instance.")

            self._invoice_manager = FinanceInvoiceManager(data_store=data_store)

            kernel.register_capability(
                name="kortex.finance.invoice.create",
                description="Create a commercial billing invoice in DRAFT state.",
                provider=self.name,
                handler=self.create_invoice,
                required_permissions=["finance:invoice:write"],
            )

            self._set_state(ModuleState.ACTIVE)
            self.logger.info("Finance Module initialized successfully.")
        except Exception as exc:
            self._set_state(ModuleState.FAILED)
            self.logger.error("Failed to initialize Finance Module: %s", exc, exc_info=True)
            raise

    async def start(self) -> None:
        """No background services -- ACTIVE is already reached in
        `initialize()`; this call is a state-consistency no-op mirroring
        `BaseEngine`'s own READY-then-RUNNING two-phase boot shape without
        introducing a distinct RUNNING state this module has no use for."""
        self.ensure_state(ModuleState.ACTIVE)

    async def stop(self) -> None:
        """Gracefully shut down. No background tasks or open resources to
        release."""
        self.ensure_state(ModuleState.ACTIVE)
        self._set_state(ModuleState.STOPPING)
        self._set_state(ModuleState.STOPPED)
        self.logger.info("Finance Module stopped.")

    async def health_check(self) -> Dict[str, Any]:
        """Return diagnostic health information."""
        return {
            "module": self.name,
            "status": "healthy" if self._state == ModuleState.ACTIVE else "unhealthy",
            "state": self._state.value,
        }

    # -- Capability Handler ------------------------------------------------

    async def create_invoice(
        self,
        request: CreateInvoiceRequest,
        principal: SecurityPrincipal | None = None,
    ) -> FinanceInvoice:
        """Backs the `kortex.finance.invoice.create` capability.

        Tenant ownership (non-negotiable): `tenant_id` is derived
        exclusively from `principal.tenant_id`, the Kernel-verified
        identity the dispatcher injects into any handler parameter
        literally named `principal`. `request` (`CreateInvoiceRequest`)
        has no `tenant_id` field to trust or ignore -- there is nothing
        for a caller to spoof in the first place, not merely a value that
        gets overridden after being accepted. A capability call with no
        verified `principal` at all (a bug elsewhere in the dispatch
        chain, not a caller-controllable state under real production
        authentication) fails closed rather than falling back to a
        default tenant.
        """
        self.ensure_state(ModuleState.ACTIVE)
        if principal is None:
            raise KortexError("kortex.finance.invoice.create requires a verified principal; none was provided.")

        assert self._invoice_manager is not None
        return await self._invoice_manager.create_invoice(request, tenant_id=principal.tenant_id)

    # -- Common Diagnostics (structurally matches IEngineDiagnostics) ------

    def metrics(self) -> Dict[str, Any]:
        """Return runtime metrics."""
        return {}

    def diagnostics(self) -> Dict[str, Any]:
        """Return detailed technical diagnostics."""
        return {
            "module": self.name,
            "version": self.version(),
            "state": self._state.value,
            "capabilities": self.capabilities(),
        }

    def status(self) -> str:
        """Return current operational state name string."""
        return self._state.value

    def version(self) -> str:
        """Return semantic version string."""
        return "1.0.0"

    def capabilities(self) -> List[str]:
        """Return list of registered capability strings."""
        return list(_REGISTERED_CAPABILITIES)


__all__ = ["FinanceModule"]
