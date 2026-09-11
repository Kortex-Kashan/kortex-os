"""
KORTEX Workflow Definition Lifecycle Manager
(Milestone F4 — Workflow Definition Lifecycle & Authoring Foundation).

Implements the draft/publish/archive/clone lifecycle around `WorkflowDefinition` identity, backed
by `WorkflowStore`'s F4 persistence extensions (`persistence.py`). Mirrors the exact architecture of
this engine's other dedicated manager classes — `DurableApprovalManager`, `DurableWorkflowScheduler`,
`ExternalExecutionManager` — constructed once in `WorkflowEngine.initialize()` and owning its own
`_record_audit` helper (copied from `ExternalExecutionManager._record_audit`'s exact precedent, per
D16: reuse the existing `AuditManager`, never a new audit subsystem).

Scope discipline (Milestone F4 firewall):
- Zero Business Logic beyond lifecycle/version-safety orchestration: every structural check reuses
  F2's `graph_validation`/`graph_compat` and F3's `mapping_validation` verbatim; every authorization
  decision is delegated to the existing `SecurityEngine.authorize()`; every capability lookup goes
  through `Kernel.get_capability()`. Nothing here re-implements or shortcuts any of those systems.
- No visual canvas, no AI workflow builder, no MCP/connector surface — this module is pure lifecycle
  orchestration behind seven Kernel capabilities (`kortex.workflow.definition.*`).
- Never copies Registry-owned capability metadata (`is_read_only`/`owner_domain`/etc.) onto a
  definition; a `capability_name` on a node or step remains a reference, resolved dynamically.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from kortex.core.dispatch import CapabilityExecutionContext
from kortex.core.exceptions import CapabilityNotFoundError
from kortex.core.idempotency import sanitize_for_persistence
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.security.models import (
    ClassificationLevel,
    PermissionRequirement,
    SecurityPrincipal,
    UniversalAuditEntry,
)
from kortex.engines.workflow.exceptions import (
    WorkflowDefinitionAuthorizationError,
    WorkflowDefinitionConflictError,
    WorkflowDefinitionNotFoundError,
    WorkflowDefinitionStateError,
    WorkflowGraphConversionError,
    WorkflowGraphValidationError,
)
from kortex.engines.workflow.graph_compat import _STEP_METADATA_KEY, graph_to_steps
from kortex.engines.workflow.graph_validation import validate_graph
from kortex.engines.workflow.mapping_validation import validate_mapping
from kortex.engines.workflow.models import (
    WorkflowDefinitionStatus,
    WorkflowDefinitionValidationReport,
    WorkflowDefinitionVersion,
    WorkflowGraph,
    WorkflowMapping,
    WorkflowPriority,
    WorkflowStep,
    WorkflowTrigger,
)
from kortex.engines.workflow.persistence import WorkflowStore

logger = logging.getLogger("kortex.engines.workflow.lifecycle")


def _safe_classification(value: str) -> ClassificationLevel:
    """Convert a descriptor's plain `security_classification` string into a `ClassificationLevel`,
    defaulting any unparseable value to `RESTRICTED` (fail-closed for a *requirement*).

    Mirrors `kortex.core.dispatch._safe_classification` exactly — duplicated locally rather than
    imported, since that function is module-private (leading underscore) to `dispatch.py` and this
    module has no other reason to depend on the Kernel dispatcher's internals.
    """
    try:
        return ClassificationLevel(value)
    except ValueError:
        return ClassificationLevel.RESTRICTED


class WorkflowDefinitionLifecycleManager:
    """Orchestrates the F4 draft/publish/archive/clone lifecycle for `WorkflowDefinition` identity.

    Backed entirely by `WorkflowStore`'s additive persistence (D2/D20: exactly one new table,
    `workflow_definition_versions`). Never mutates `workflow_definitions` directly except through
    `WorkflowStore.publish_draft`'s own atomic projection refresh — this class has no other write
    path onto that legacy table.
    """

    def __init__(
        self,
        workflow_store: WorkflowStore,
        kernel: Any = None,
        security_engine: Any = None,
        outbox_store: Any = None,
    ) -> None:
        self._workflow_store = workflow_store
        self._kernel = kernel
        self._security_engine = security_engine
        self._outbox_store = outbox_store
        logger.debug("WorkflowDefinitionLifecycleManager initialized.")

    # -- Identity / Audit Helpers --------------------------------------------

    def _resolve_identity(
        self,
        execution_context: CapabilityExecutionContext | None,
        principal: SecurityPrincipal | None,
        tenant_id: str | None,
    ) -> tuple[SecurityPrincipal | None, str]:
        """Mirrors `WorkflowEngine.decide_approval_request`'s identity-resolution precedent exactly:
        `execution_context` (dispatcher-injected — always present via real capability dispatch,
        since every F4 lifecycle capability registers `requires_execution_context=True`) is
        authoritative over a directly-injected `principal`, itself authoritative over any
        caller-supplied `tenant_id` — a mismatched caller-supplied `tenant_id` is rejected outright,
        never silently overridden (KORTEX Platform Security — Capability Identity Propagation).
        """
        if principal is None and execution_context is not None:
            principal = execution_context.principal
        authoritative_tid: str | None = None
        if execution_context is not None:
            authoritative_tid = execution_context.tenant_id
        elif principal is not None:
            authoritative_tid = principal.tenant_id
        if authoritative_tid is not None:
            if tenant_id is not None and tenant_id != authoritative_tid:
                raise AuthorizationDeniedError(
                    f"Supplied tenant_id '{tenant_id}' does not match the authenticated tenant '{authoritative_tid}'."
                )
            return principal, authoritative_tid
        return principal, tenant_id or "default"

    @staticmethod
    def _actor_id(principal: SecurityPrincipal | None) -> str:
        return principal.principal_id if principal is not None else "SYSTEM"

    async def _record_audit(
        self,
        action: str,
        actor_id: str,
        tenant_id: str,
        resource_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record an immutable `UniversalAuditEntry` via SecurityEngine (D16). Copied verbatim from
        `ExternalExecutionManager._record_audit`'s established precedent — no new audit subsystem."""
        if self._security_engine is not None:
            audit_mgr = getattr(self._security_engine, "_audit_manager", None)
            if audit_mgr is None:
                try:
                    audit_mgr = getattr(self._security_engine, "audit_manager", None)
                except Exception:
                    audit_mgr = None
            if audit_mgr is not None:
                try:
                    entry = UniversalAuditEntry(
                        action=action,
                        actor_id=actor_id,
                        actor_type="HUMAN" if actor_id not in ("SYSTEM", "EXECUTOR") else "SYSTEM_ENGINE",
                        tenant_id=tenant_id,
                        resource_id=resource_id,
                        context=sanitize_for_persistence(context or {}),
                    )
                    await audit_mgr.record_audit_entry(entry)
                except Exception as exc:
                    logger.error("Failed to record workflow definition lifecycle audit entry for '%s': %s", action, exc)

    # -- Content Helpers ------------------------------------------------------

    @staticmethod
    def _coerce_trigger(value: str | WorkflowTrigger | None) -> WorkflowTrigger:
        if isinstance(value, WorkflowTrigger):
            return value
        if isinstance(value, str) and value in WorkflowTrigger._value2member_map_:
            return WorkflowTrigger(value)
        return WorkflowTrigger.MANUAL

    @staticmethod
    def _coerce_priority(value: str | WorkflowPriority | None) -> WorkflowPriority:
        if isinstance(value, WorkflowPriority):
            return value
        if isinstance(value, str) and value in WorkflowPriority._value2member_map_:
            return WorkflowPriority(value)
        return WorkflowPriority.NORMAL

    @staticmethod
    def _derive_steps_from_graph(graph: WorkflowGraph, fallback: list[WorkflowStep]) -> list[WorkflowStep]:
        """Best-effort derivation for DRAFT save (D9: informational, never blocking): a structurally
        invalid or non-linear graph is expected mid-edit (D5) — falls back to whatever flat `steps`
        were already known rather than raising. `publish()` re-derives and treats failure as
        blocking (D1/D3: the unmodified executor can only run what `graph_to_steps` can produce).
        """
        try:
            return graph_to_steps(graph)
        except (WorkflowGraphValidationError, WorkflowGraphConversionError):
            return fallback

    def _to_dict(self, dv: WorkflowDefinitionVersion) -> dict[str, Any]:
        return {
            "id": dv.id,
            "definition_id": dv.definition_id,
            "tenant_id": dv.tenant_id,
            "version": dv.version,
            "status": dv.status.value if isinstance(dv.status, WorkflowDefinitionStatus) else str(dv.status),
            "name": dv.name,
            "description": dv.description,
            "trigger": dv.trigger.value if isinstance(dv.trigger, WorkflowTrigger) else str(dv.trigger),
            "priority": dv.priority.value if isinstance(dv.priority, WorkflowPriority) else str(dv.priority),
            "timeout_seconds": dv.timeout_seconds,
            "steps": [step.model_dump(mode="json") for step in dv.steps],
            "graph": dv.graph.model_dump(mode="json") if dv.graph is not None else None,
            "created_by": dv.created_by,
            "lock_version": dv.lock_version,
            "created_at": dv.created_at.isoformat() if dv.created_at else None,
            "updated_at": dv.updated_at.isoformat() if dv.updated_at else None,
            "published_at": dv.published_at.isoformat() if dv.published_at else None,
        }

    @staticmethod
    def _collect_referenced_capabilities(dv: WorkflowDefinitionVersion) -> set[str]:
        names: set[str] = set()
        if dv.graph is not None:
            names.update(node.capability_name for node in dv.graph.nodes if node.capability_name)
        names.update(step.capability_name for step in dv.steps if step.capability_name)
        return names

    def _validate_content(self, dv: WorkflowDefinitionVersion) -> WorkflowDefinitionValidationReport:
        """D9 — structural/graph/mapping validation + capability existence checks, informational
        (errors block; capability-existence and non-linearity are warnings here — `publish()` alone
        promotes those to blocking, per D9's draft-vs-publish distinction)."""
        errors: list[str] = []
        warnings: list[str] = []

        if dv.graph is not None:
            try:
                validate_graph(dv.graph)
            except WorkflowGraphValidationError as exc:
                errors.append(f"Graph structural validation failed: {exc}")
            else:
                for node in dv.graph.nodes:
                    raw_mapping = node.config.get("mapping") if isinstance(node.config, dict) else None
                    if raw_mapping is None:
                        continue
                    try:
                        mapping = WorkflowMapping.model_validate(raw_mapping)
                        validate_mapping(dv.graph, node.node_id, mapping)
                    except Exception as exc:
                        errors.append(f"Mapping validation failed for node '{node.node_id}': {exc}")
                try:
                    graph_to_steps(dv.graph)
                except WorkflowGraphConversionError:
                    warnings.append(
                        "Graph is non-linear (branches or joins) — the current unmodified executor "
                        "can only run a linear graph; publishing will be blocked until this graph is "
                        "linear or an explicit flat 'steps' list is supplied instead."
                    )
            if self._kernel is not None:
                for node in dv.graph.nodes:
                    if node.capability_name:
                        try:
                            self._kernel.get_capability(node.capability_name)
                        except CapabilityNotFoundError:
                            warnings.append(
                                f"Node '{node.node_id}' references unknown capability '{node.capability_name}'."
                            )

            for node in dv.graph.nodes:
                is_approval = (
                    node.node_type == "approval"
                    or bool(node.metadata.get(_STEP_METADATA_KEY, {}).get("is_approval_step", False))
                )
                if is_approval and node.capability_name is not None:
                    errors.append(
                        f"Approval node '{node.node_id}' cannot specify capability_name "
                        f"'{node.capability_name}' (approval steps must be pure wait gates)."
                    )

        if not dv.steps and dv.graph is None:
            warnings.append("Definition has no steps and no graph — it is not yet runnable.")
        else:
            for step in dv.steps:
                if step.is_approval_step and step.capability_name is not None:
                    errors.append(
                        f"Approval step '{step.id}' cannot specify capability_name "
                        f"'{step.capability_name}' (approval steps must be pure wait gates)."
                    )
            if self._kernel is not None:
                for step in dv.steps:
                    if step.capability_name:
                        try:
                            self._kernel.get_capability(step.capability_name)
                        except CapabilityNotFoundError:
                            warnings.append(f"Step '{step.id}' references unknown capability '{step.capability_name}'.")

        return WorkflowDefinitionValidationReport(is_valid=not errors, errors=errors, warnings=warnings)

    async def _get_or_materialize_control_row(
        self, definition_id: str, tenant_id: str, created_by: str
    ) -> tuple[WorkflowDefinitionVersion, bool]:
        """Fetch the mutable control row, or lazily materialize one seeded from a legacy
        definition's current published content the first time it is touched by the F4 lifecycle
        (backward compatibility without data rewriting — no row is ever migrated in bulk).

        Returns `(control_row, was_materialized)`. `was_materialized=True` tells the caller the row
        is brand new: any caller-supplied `expected_lock_version` refers to a row that did not exist
        a moment ago and must be ignored in favor of the fresh row's own `lock_version` — the only
        race this admits (two concurrent first-touches) is already closed by `create_draft`'s unique
        constraint, which rejects the second one with `WorkflowDefinitionConflictError`.
        """
        control = await self._workflow_store.get_control_row(definition_id, tenant_id)
        if control is not None:
            return control, False
        legacy = await self._workflow_store.get_definition(definition_id, tenant_id=tenant_id)
        if legacy is None:
            raise WorkflowDefinitionNotFoundError(f"Workflow definition '{definition_id}' not found.")
        control = await self._workflow_store.create_draft(
            definition_id,
            tenant_id,
            created_by=created_by,
            name=legacy.name,
            description=legacy.description,
            trigger=legacy.trigger,
            priority=legacy.priority,
            timeout_seconds=legacy.timeout_seconds,
            steps=legacy.steps,
            graph=legacy.graph,
        )
        return control, True

    @staticmethod
    def _next_version_from(candidates: list[str]) -> str:
        """Simple patch-increment SemVer scheme over every known version string — every prior F4
        PUBLISHED row plus (when relevant) the legacy `workflow_definitions` row's own version, so a
        definition published for the first time via F4 never regresses behind whatever version a
        pre-F4 caller had already set (never a data-loss-shaped downgrade)."""
        if not candidates:
            return "1.0.0"

        def _key(v: str) -> tuple[int, int, int]:
            parts = v.split(".")
            try:
                return (int(parts[0]), int(parts[1]), int(parts[2]))
            except (IndexError, ValueError):
                return (0, 0, 0)

        latest = max(candidates, key=_key)
        major, minor, patch = _key(latest)
        return f"{major}.{minor}.{patch + 1}"

    # -- Capability Handlers (kortex.workflow.definition.*) -------------------

    async def create(
        self,
        name: str,
        description: str = "",
        trigger: str | WorkflowTrigger = "MANUAL",
        priority: str | WorkflowPriority = "NORMAL",
        timeout_seconds: int = 3600,
        steps: list[dict[str, Any]] | None = None,
        graph: dict[str, Any] | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        principal: SecurityPrincipal | None = None,
        tenant_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """`kortex.workflow.definition.create` — create a new definition identity with a mutable
        DRAFT. Never binds to a running instance and is not yet visible to `start_workflow` (D5) —
        only `publish()` materializes a `workflow_definitions` projection row."""
        principal, tid = self._resolve_identity(execution_context, principal, tenant_id)
        definition_id = str(uuid.uuid4())
        parsed_steps = [WorkflowStep(**s) for s in (steps or [])]
        parsed_graph = WorkflowGraph.model_validate(graph) if graph else None
        if parsed_graph is not None:
            parsed_steps = self._derive_steps_from_graph(parsed_graph, fallback=parsed_steps)

        dv = await self._workflow_store.create_draft(
            definition_id,
            tid,
            created_by=self._actor_id(principal),
            name=name,
            description=description,
            trigger=self._coerce_trigger(trigger),
            priority=self._coerce_priority(priority),
            timeout_seconds=timeout_seconds,
            steps=parsed_steps,
            graph=parsed_graph,
        )
        await self._record_audit(
            "workflow.definition.create", self._actor_id(principal), tid, resource_id=definition_id
        )
        return self._to_dict(dv)

    async def get(
        self,
        definition_id: str,
        version: str | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        principal: SecurityPrincipal | None = None,
        tenant_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """`kortex.workflow.definition.get` — fetch one exact published version (when `version` is
        given) or the full lifecycle picture: the current DRAFT/ARCHIVED control row plus every
        immutable PUBLISHED snapshot."""
        _, tid = self._resolve_identity(execution_context, principal, tenant_id)
        if version is not None:
            published = await self._workflow_store.get_published_version(definition_id, tid, version)
            if published is None:
                raise WorkflowDefinitionNotFoundError(
                    f"Published version '{version}' of definition '{definition_id}' not found."
                )
            return self._to_dict(published)

        control = await self._workflow_store.get_control_row(definition_id, tid)
        versions = await self._workflow_store.list_definition_versions(definition_id, tid)
        published_versions = [v for v in versions if v.status == WorkflowDefinitionStatus.PUBLISHED]
        legacy = await self._workflow_store.get_definition(definition_id, tenant_id=tid)
        if control is None and not published_versions and legacy is None:
            raise WorkflowDefinitionNotFoundError(f"Workflow definition '{definition_id}' not found.")
        return {
            "definition_id": definition_id,
            "tenant_id": tid,
            "draft": self._to_dict(control) if control is not None else None,
            "published_versions": [self._to_dict(v) for v in published_versions],
            "latest_published_version": legacy.version if legacy is not None else None,
        }

    async def update(
        self,
        definition_id: str,
        expected_lock_version: int,
        name: str | None = None,
        description: str | None = None,
        trigger: str | WorkflowTrigger | None = None,
        priority: str | WorkflowPriority | None = None,
        timeout_seconds: int | None = None,
        steps: list[dict[str, Any]] | None = None,
        graph: dict[str, Any] | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        principal: SecurityPrincipal | None = None,
        tenant_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """`kortex.workflow.definition.update` — apply an optimistic-lock-guarded update to the
        mutable DRAFT (D14). Lazily materializes a DRAFT for a legacy definition on first touch."""
        principal, tid = self._resolve_identity(execution_context, principal, tenant_id)
        control, materialized = await self._get_or_materialize_control_row(
            definition_id, tid, self._actor_id(principal)
        )
        lock_to_use = control.lock_version if materialized else expected_lock_version

        content: dict[str, Any] = {}
        if name is not None:
            content["name"] = name
        if description is not None:
            content["description"] = description
        if trigger is not None:
            content["trigger"] = self._coerce_trigger(trigger)
        if priority is not None:
            content["priority"] = self._coerce_priority(priority)
        if timeout_seconds is not None:
            content["timeout_seconds"] = timeout_seconds
        if graph is not None:
            parsed_graph = WorkflowGraph.model_validate(graph)
            content["graph"] = parsed_graph
            content["steps"] = self._derive_steps_from_graph(parsed_graph, fallback=control.steps)
        elif steps is not None:
            content["steps"] = [WorkflowStep(**s) for s in steps]

        if not content:
            raise WorkflowDefinitionStateError(f"No fields supplied to update definition '{definition_id}'.")

        dv = await self._workflow_store.update_draft(definition_id, tid, lock_to_use, **content)
        await self._record_audit(
            "workflow.definition.update", self._actor_id(principal), tid, resource_id=definition_id
        )
        return self._to_dict(dv)

    async def validate(
        self,
        definition_id: str,
        execution_context: CapabilityExecutionContext | None = None,
        principal: SecurityPrincipal | None = None,
        tenant_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """`kortex.workflow.definition.validate` — informational validation of the current DRAFT
        (D9): errors block a subsequent `publish()`; warnings do not."""
        _, tid = self._resolve_identity(execution_context, principal, tenant_id)
        control = await self._workflow_store.get_control_row(definition_id, tid)
        if control is None:
            raise WorkflowDefinitionNotFoundError(f"No draft exists for definition '{definition_id}'.")
        report = self._validate_content(control)
        return report.model_dump(mode="json")

    async def publish(
        self,
        definition_id: str,
        expected_lock_version: int,
        version: str | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        principal: SecurityPrincipal | None = None,
        tenant_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """`kortex.workflow.definition.publish` — publish the current DRAFT as a new immutable
        version, per the exact pipeline the F4 mandate ratifies: graph structural validation ->
        mapping validation -> referenced capability existence -> publishing principal authorization
        -> publish -> immutable version (D9/D10/D11). Every check here is BLOCKING, unlike
        `validate()`'s informational report.
        """
        principal, tid = self._resolve_identity(execution_context, principal, tenant_id)
        control = await self._workflow_store.get_control_row(definition_id, tid)
        if control is None:
            raise WorkflowDefinitionNotFoundError(f"No draft exists for definition '{definition_id}'.")
        if control.lock_version != expected_lock_version:
            raise WorkflowDefinitionConflictError(
                f"Draft for definition '{definition_id}' was modified concurrently "
                f"(expected lock_version {expected_lock_version}, found {control.lock_version})."
            )

        report = self._validate_content(control)
        if not report.is_valid:
            raise WorkflowDefinitionStateError(
                f"Definition '{definition_id}' failed validation and cannot be published: {'; '.join(report.errors)}"
            )

        effective_steps = control.steps
        if control.graph is not None:
            try:
                effective_steps = graph_to_steps(control.graph)
            except WorkflowGraphConversionError as exc:
                raise WorkflowDefinitionStateError(
                    f"Definition '{definition_id}' graph is not linear and cannot be published "
                    f"without an explicit flat 'steps' list: {exc}"
                ) from exc
            if effective_steps != control.steps:
                control = await self._workflow_store.update_draft(
                    definition_id, tid, control.lock_version, steps=effective_steps
                )
        if not effective_steps:
            raise WorkflowDefinitionStateError(f"Definition '{definition_id}' has no runnable content to publish.")

        # D10/D11 -- fresh, BLOCKING capability-existence and authorization check against the
        # CURRENT Registry/Security Engine state, at publish time -- never cached, never inferred
        # from `validate()`'s earlier informational (warning-only) check above.
        referenced = self._collect_referenced_capabilities(control)
        for capability_name in sorted(referenced):
            if self._kernel is None:
                raise WorkflowDefinitionAuthorizationError(
                    f"Cannot verify capability '{capability_name}': Workflow Engine has no Kernel reference."
                )
            try:
                descriptor = self._kernel.get_capability(capability_name)
            except CapabilityNotFoundError as exc:
                raise WorkflowDefinitionAuthorizationError(
                    f"Definition '{definition_id}' references unknown capability '{capability_name}'."
                ) from exc
            if principal is not None and self._security_engine is not None:
                requirement = PermissionRequirement(
                    capability_name=descriptor.name,
                    required_permissions=list(descriptor.required_permissions or []),
                    security_classification=_safe_classification(descriptor.security_classification),
                )
                # `resource_tenant_id` (not `tenant_id`) is the key `ABACEvaluator` reads for its
                # unconditional tenant-match rule (`abac.py`) -- every other authorize() call site
                # in the codebase (dispatch.py, projection.py, connector actions) uses this exact
                # key. Pre-existing defect discovered by the AI Workflow Builder milestone's
                # vertical-slice test: with the wrong key, ABAC's "missing resource_tenant_id
                # denies by default" rule fired unconditionally for every capability referenced by
                # a publish() call, for every tenant, regardless of actual authorization -- masked
                # until now because no prior test published a definition referencing a real,
                # authenticated-by-default capability (only a bare capability-less step, or a
                # deliberately-unauthorized case whose expected DENIED outcome coincided with this
                # bug's own unconditional denial).
                decision = await self._security_engine.authorize(principal, requirement, {"resource_tenant_id": tid})
                if not decision.is_allowed:
                    raise WorkflowDefinitionAuthorizationError(
                        f"Publishing principal is not authorized to invoke capability '{capability_name}' "
                        f"referenced by definition '{definition_id}': {decision.reason}"
                    )

        existing_versions = await self._workflow_store.list_definition_versions(definition_id, tid)
        candidates = [v.version for v in existing_versions if v.status == WorkflowDefinitionStatus.PUBLISHED]
        legacy = await self._workflow_store.get_definition(definition_id, tenant_id=tid)
        if legacy is not None:
            candidates.append(legacy.version)
        next_version = version if version is not None else self._next_version_from(candidates)

        published = await self._workflow_store.publish_draft(definition_id, tid, control.lock_version, next_version)
        await self._record_audit(
            "workflow.definition.publish",
            self._actor_id(principal),
            tid,
            resource_id=definition_id,
            context={"version": next_version},
        )
        return self._to_dict(published)

    async def archive(
        self,
        definition_id: str,
        expected_lock_version: int | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        principal: SecurityPrincipal | None = None,
        tenant_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """`kortex.workflow.definition.archive` — soft-archive a definition (D15): blocks new
        instance creation (`WorkflowEngine.start_workflow`) while retaining every published version,
        audit history, and any already-running instances untouched. Never a hard delete."""
        principal, tid = self._resolve_identity(execution_context, principal, tenant_id)
        control, materialized = await self._get_or_materialize_control_row(
            definition_id, tid, self._actor_id(principal)
        )
        lock_to_use = control.lock_version if materialized else (expected_lock_version or control.lock_version)
        dv = await self._workflow_store.archive_control_row(definition_id, tid, lock_to_use)
        await self._record_audit(
            "workflow.definition.archive", self._actor_id(principal), tid, resource_id=definition_id
        )
        return self._to_dict(dv)

    async def clone(
        self,
        source_definition_id: str,
        name: str | None = None,
        source_version: str | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        principal: SecurityPrincipal | None = None,
        tenant_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """`kortex.workflow.definition.clone` — copy one definition's content into a brand-new
        definition identity with its own fresh DRAFT. Produces a new `definition_id`, never a second
        mutable handle onto the source; never copies running instances, approval records, execution
        state, or audit history, none of which belong to the new identity."""
        principal, tid = self._resolve_identity(execution_context, principal, tenant_id)

        if source_version is not None:
            source = await self._workflow_store.get_published_version(source_definition_id, tid, source_version)
            if source is None:
                raise WorkflowDefinitionNotFoundError(
                    f"Published version '{source_version}' of definition '{source_definition_id}' not found."
                )
        else:
            source = await self._workflow_store.get_control_row(source_definition_id, tid)
            if source is None:
                legacy = await self._workflow_store.get_definition(source_definition_id, tenant_id=tid)
                if legacy is None:
                    raise WorkflowDefinitionNotFoundError(f"Workflow definition '{source_definition_id}' not found.")
                source = WorkflowDefinitionVersion(
                    definition_id=source_definition_id,
                    tenant_id=tid,
                    name=legacy.name,
                    description=legacy.description,
                    trigger=legacy.trigger,
                    priority=legacy.priority,
                    timeout_seconds=legacy.timeout_seconds,
                    steps=legacy.steps,
                    graph=legacy.graph,
                )

        new_id = str(uuid.uuid4())
        dv = await self._workflow_store.create_draft(
            new_id,
            tid,
            created_by=self._actor_id(principal),
            name=name or f"{source.name} (copy)",
            description=source.description,
            trigger=source.trigger,
            priority=source.priority,
            timeout_seconds=source.timeout_seconds,
            steps=source.steps,
            graph=source.graph,
        )
        await self._record_audit(
            "workflow.definition.clone",
            self._actor_id(principal),
            tid,
            resource_id=new_id,
            context={"source_definition_id": source_definition_id},
        )
        return self._to_dict(dv)
