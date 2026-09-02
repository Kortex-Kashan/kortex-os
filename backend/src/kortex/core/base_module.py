"""
KORTEX Core Base Module Contract.

Defines the minimal abstract base class for KORTEX Business Modules
(`docs/architecture/business_module_architecture.md`, Approved Architecture,
ratified under Architecture Version 1.0.0 / ADR-0000).

`BaseModule` is a NEW, SIBLING abstraction to `BaseEngine`
(`kortex.core.base_engine`) -- deliberately not a subclass, alias, or
replacement of it. `business_module_architecture.md` §1 draws an explicit
conceptual line between "system engines... provide reusable infrastructure"
and "business modules encapsulate domain business rules" -- collapsing the
two into one class would blur an approved architectural boundary this
module exists to preserve, even though the two classes share an identical,
proven lifecycle *shape* by design (there is no evidence a business module
needs a different lifecycle mechanism than the one already proven for 21
system engines).

Scope of this first implementation (`.kortex/roadmap.md` Phase 6 "Module
base contract", minimal-boundary decision recorded in the Finance-pilot
planning/boundary passes preceding this commit -- NOT part of M7.4, which
was the unrelated Document Engine <-> AI Studio Integration milestone and
explicitly listed Phase 6 pilot business modules as out of scope in its own
implementation report): only the lifecycle states needed to prove one
capability-registering, Kernel-dispatched module -- construction ->
initialized/loaded -> active -> stopped. `business_module_architecture.md`
§3's full 7-state machine (`Unloaded -> Installed -> Loaded -> Active ->
Disabled/Superseded/Uninstalled`) additionally describes package-based
discovery, upgrade, and rollback flows this slice does not build --
deliberately deferred, not silently dropped. Direct registration via
`kernel.register_engine()` in `kernel_bootstrap.py` (proven, this pass, to
require zero Kernel modification: `Kernel.register_engine`/`BootEngine.
boot_system` are pure duck-typed dispatch over `.dependencies`/
`.initialize`/`.start`/`.stop`/`.state`, with no `isinstance(..., BaseEngine)`
check anywhere) is the module's path to `ACTIVE` for this slice, mirroring
every existing engine's own boot path rather than building package
discovery/loading machinery this milestone does not need.

KNOWN LIMITATION (Finance Module certification pass, recorded rather than
silently presented as architecturally correct): reusing
`kernel.register_engine()` for a `BaseModule` registrant is a deliberate,
pragmatic reuse of the existing Kernel registration mechanism for this
first pilot module -- it works because `Kernel.register_engine`/
`BootEngine.boot_system` are pure duck-typed dispatch (see above), but it
has a real side effect this pass did not originally disclose:
`Kernel.register_engine` (`core/kernel.py`) unconditionally sets the
Registry Engine's resource description to
`f"KORTEX {name.title()} Engine"` and stores the instance in the IoC
container under the key `f"engine.{name}"`. For `FinanceModule` this means
Registry/diagnostics metadata currently reads "KORTEX Finance Engine" and
the IoC key is `engine.finance` -- both textually mislabel a `BaseModule`
as an "Engine". This does NOT mean `FinanceModule` *is* an Engine (the
class hierarchy, lifecycle contract, and `ModuleState` above remain
entirely distinct from `BaseEngine`/`EngineState`) -- it is purely a
cosmetic/diagnostic metadata artifact of the registration pathway chosen
for this minimal slice, with no functional consequence (capability
dispatch, tenant isolation, and RBAC are all unaffected).
`RegistryEngine` already has an unused `register_module()`/
`RegistryCategory.MODULE` pair that would register a module under
correct, distinct metadata (`registry/engine.py`) -- deliberately NOT
wired up in this or the correction pass that documented this limitation:
doing so would require a new `Kernel.register_module()` convenience
method, which is out of scope for a minimal pilot slice and a
certification-correction pass alike. Recorded here as an explicit,
disclosed architectural follow-up for a future pass, not fixed now.
"""

from __future__ import annotations

import abc
import enum
import logging
from typing import TYPE_CHECKING, Any

from kortex.core.exceptions import EngineStateError

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel


class ModuleState(str, enum.Enum):
    """Lifecycle states for a KORTEX Business Module (minimal subset -- see
    module docstring for why the full 7-state machine is deferred)."""

    UNINITIALIZED = "UNINITIALIZED"
    INITIALIZING = "INITIALIZING"
    ACTIVE = "ACTIVE"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class BaseModule(abc.ABC):
    """Abstract base class for all KORTEX Business Modules.

    Mirrors `BaseEngine`'s proven lifecycle contract shape (`name`,
    `dependencies`, `initialize`, `start`, `stop`, `health_check`) without
    being `BaseEngine` itself -- see module docstring. A module reaches
    `ModuleState.ACTIVE` via the same two-phase `initialize()` then
    `start()` sequence `BootEngine.boot_system` already runs for every
    engine, applied unchanged to any duck-typed registrant.
    """

    def __init__(self) -> None:
        self._state: ModuleState = ModuleState.UNINITIALIZED
        self._logger: logging.Logger = logging.getLogger(f"kortex.module.{self.name}")

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique identifier name for this module."""

    @property
    @abc.abstractmethod
    def namespace(self) -> str:
        """Canonical capability namespace this module owns (e.g. `kortex.finance`)."""

    @property
    def dependencies(self) -> list[str]:
        """Names of System Engines this module depends on for boot ordering."""
        return []

    @property
    def state(self) -> ModuleState:
        """Current operational state of the module."""
        return self._state

    @property
    def logger(self) -> logging.Logger:
        """Logger instance dedicated to this module."""
        return self._logger

    def _set_state(self, new_state: ModuleState) -> None:
        """Transition the module to a new state with logging."""
        self._logger.debug("Module '%s' state transition: %s -> %s", self.name, self._state, new_state)
        self._state = new_state

    @abc.abstractmethod
    async def initialize(self, kernel: Kernel) -> None:
        """Resolve dependencies and register this module's capabilities with the Kernel.

        Args:
            kernel: The running Kernel instance providing core services.
        """

    @abc.abstractmethod
    async def start(self) -> None:
        """Transition the module to ACTIVE. No background services are
        implied by this minimal contract -- a module with none simply
        transitions state."""

    @abc.abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Return diagnostic health information about the module status."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Gracefully transition the module out of ACTIVE and release any resources."""

    def ensure_state(self, *expected_states: ModuleState) -> None:
        """Validate that the module is currently in one of the expected states.

        Raises:
            EngineStateError: If the current state is not in expected_states.

        Reuses the existing `EngineStateError` rather than introducing a
        parallel `ModuleStateError` type -- both signal the identical
        condition (an operation attempted outside its valid lifecycle
        state), and `core/exceptions.py`'s own docstring already scopes
        this exception family to "KORTEX Core, System Engines, and Modules".
        """
        if self._state not in expected_states:
            expected_names = [s.name for s in expected_states]
            raise EngineStateError(
                f"Module '{self.name}' is in state {self._state.name}, expected one of: {expected_names}"
            )


__all__ = ["BaseModule", "ModuleState"]
