"""
KORTEX Workflow Engine Pydantic v2 Models.

Defines all domain models, state enums, step descriptors, context wrappers,
retry policies, compensation actions, and approval models for the Workflow Engine.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class WorkflowState(enum.StrEnum):
    """Deterministic lifecycle state of a workflow instance."""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    APPROVED = "APPROVED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowStatus(enum.StrEnum):
    """Operational status indicator for runtime monitoring."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowPriority(enum.StrEnum):
    """Execution priority levels for workflow scheduling."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WorkflowTrigger(enum.StrEnum):
    """Trigger sources for workflow instantiation."""

    MANUAL = "MANUAL"
    EVENT = "EVENT"
    SCHEDULED = "SCHEDULED"
    API = "API"
    RECIPE = "RECIPE"


class ApprovalState(enum.StrEnum):
    """State descriptor for approval checkpoints."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class RetryPolicy(BaseModel):
    """Policy governing automatic step retries with backoff."""

    max_attempts: int = Field(default=3, ge=1, description="Maximum execution attempts")
    backoff_factor: float = Field(default=2.0, ge=1.0, description="Exponential backoff multiplier")
    initial_delay_seconds: float = Field(default=1.0, ge=0.0, description="Initial delay before first retry")
    jitter: bool = Field(default=True, description="Add random jitter to backoff delay")


class CompensationAction(BaseModel):
    """Action descriptor for LIFO rollback/compensation execution upon step failure."""

    id: str = Field(default_factory=lambda: str(uuid4()), description="Compensation action ID")
    name: str = Field(..., description="Action name or title")
    capability_name: str | None = Field(default=None, description="Kernel capability to execute for rollback")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Rollback parameter context")


class WorkflowStep(BaseModel):
    """Single step specification within a workflow definition."""

    id: str = Field(..., description="Unique step identifier within the workflow definition")
    name: str = Field(..., description="Human-readable step title")
    capability_name: str | None = Field(default=None, description="Kernel capability name to invoke")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Step invocation parameters")
    is_approval_step: bool = Field(default=False, description="True if step requires human approval")
    required_approval_role: str | None = Field(default=None, description="Role authorized to approve this step")
    retry_policy: RetryPolicy | None = Field(default=None, description="Custom retry policy for this step")
    compensation_action: CompensationAction | None = Field(default=None, description="Rollback compensation action")
    on_failure_continue: bool = Field(default=False, description="If True, step failure does not abort workflow")


class WorkflowContext(BaseModel):
    """Execution state context payload passed between workflow steps."""

    variables: dict[str, Any] = Field(default_factory=dict, description="Input and runtime context variables")
    step_outputs: dict[str, Any] = Field(default_factory=dict, description="Outputs collected from executed steps")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Execution metadata and system tags")
    session_token: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Opaque caller-supplied session token blob, stored verbatim as a plain, JSON-safe dict — "
            "never a Security Engine `TokenPayload` import here, to keep Workflow models decoupled "
            "from Security Engine types. Fields match `TokenPayload` exactly except `signature`, which "
            "MUST be a hex string here (not raw bytes) so this context remains "
            "`model_dump_json()`-safe (used by both snapshot persistence and secret-leakage "
            "inspection) — raw signature bytes are not UTF-8-representable and break JSON "
            "serialization outright. `workflow/engine.py`'s dispatch closure decodes it back to bytes "
            "and reconstructs a real `TokenPayload`, which `AuthenticationManager.verify_token()` "
            "cryptographically verifies — storing or reconstructing it here is schema validation only, "
            "never proof of authenticity."
        ),
    )


class WorkflowGraphNode(BaseModel):
    """Milestone F2 — a single structural node in a `WorkflowGraph` (Workflow Graph Model).

    Purely structural: what is connected, not how data flows through it (F3) or how it executes
    (a later execution milestone). `capability_name` is the same canonical Kernel dispatch key
    `WorkflowStep.capability_name` already uses — never a second capability-identity scheme.
    Deliberately does not carry `is_read_only`/`is_idempotent`/`owner_domain`/`resource_type`/
    `action`/`parameters_schema`/`returns_schema`: those remain authoritative exclusively on the
    Registry's own `CapabilityDescriptor` (resolved dynamically via `Kernel.get_capability()` when
    needed), never copied here — copying would recreate the exact duplicated-authoritative-metadata
    risk the Automation + Integration Fabric capability model (F1) was built to close.
    """

    node_id: str = Field(..., description="Unique node identifier within the graph")
    node_type: str = Field(..., description="Structural node kind, e.g. 'capability', 'approval'")
    capability_name: str | None = Field(
        default=None, description="Kernel capability name to invoke — a reference, never a copy of Registry metadata"
    )
    config: dict[str, Any] = Field(default_factory=dict, description="Static, author-supplied node configuration")
    input_ports: list[str] = Field(
        default_factory=list,
        description="Named input connection points — structural only, never a bound value or expression (F3)",
    )
    output_ports: list[str] = Field(
        default_factory=list, description="Named output connection points — structural only"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Opaque node metadata, e.g. future canvas position"
    )


class WorkflowGraphEdge(BaseModel):
    """Milestone F2 — a directed structural relationship between two `WorkflowGraphNode`s.

    Structural only: which node follows which, through which named ports. Carries no condition,
    expression, or data-mapping content (F3) and no `edge_kind` discriminator (deliberately deferred
    — see `graph_validation.py`'s cycle-policy docstring for why introducing one now, with no
    executor and no second edge semantic to distinguish, would add surface area with no present use).
    """

    edge_id: str = Field(..., description="Unique edge identifier within the graph")
    source_node_id: str = Field(..., description="Node this edge originates from")
    source_port: str | None = Field(
        default=None, description="Named output port on the source node, or None for an unported edge"
    )
    target_node_id: str = Field(..., description="Node this edge terminates at")
    target_port: str | None = Field(
        default=None, description="Named input port on the target node, or None for an unported edge"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Opaque edge metadata")


class WorkflowGraph(BaseModel):
    """Milestone F2 — the canonical structural Workflow Graph artifact.

    `schema_version` versions this artifact's own structural shape and is deliberately independent
    of `WorkflowDefinition.version` (that field versions the *definition*, e.g. for a future
    draft/publish lifecycle — F4's concern; this one versions the *graph schema itself*, e.g. if a
    future milestone adds an `edge_kind` field). Conflating the two would make a graph-schema change
    look like a definition content change to every existing consumer of `WorkflowDefinition.version`.

    Structural only: no execution status, no runtime position, no approval/retry/compensation
    policy of its own. Those remain exactly where they already are today — `WorkflowInstance`,
    `WorkflowStepRun`, and `WorkflowStep`'s own existing fields — none of which this milestone
    touches. See `graph_compat.py` for the deterministic, lossless conversion to/from the existing
    flat `WorkflowStep` list this graph coexists with.
    """

    schema_version: str = Field(default="1.0.0", description="Version of this graph artifact's own structural schema")
    entry_node_id: str = Field(..., description="The single node execution/authoring begins from")
    nodes: list[WorkflowGraphNode] = Field(default_factory=list, description="Every node in the graph")
    edges: list[WorkflowGraphEdge] = Field(default_factory=list, description="Every directed edge in the graph")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Opaque graph-level metadata")


# ============================================================================
# Milestone F3 — Workflow Data Mapping & Expression Foundation
#
# Structural-only, exactly like WorkflowGraph itself: describes what value a
# node input should receive (a literal, a reference to another node's output,
# or a small deterministic computation over such values), never how or when
# it is actually resolved (mapping.py), never whether it is structurally
# well-formed (mapping_validation.py), and never anything about execution
# state, retries, approval, or capability risk — those remain exactly where
# they already are (WorkflowInstance, WorkflowStepRun, CapabilityDescriptor).
# ============================================================================

# Chief-Architect-ratified structural resource limits (F3 decision D4). Centralized here — the one
# module every F3 file already imports from — rather than scattered as magic numbers across
# mapping.py/expression.py/mapping_validation.py. These are defensive ceilings on the *shape* of a
# mapping, checked structurally (mapping_validation.py) and re-checked defensively at the point of
# use (mapping.py's resolver, expression.py's evaluator) — never a wall-clock timeout, which belongs
# to a future execution-boundary milestone, not this pure domain layer.
MAX_REFERENCE_PATH_DEPTH: int = 32
"""Maximum number of segments in a single `WorkflowReference.path`."""

MAX_MAPPING_VALUES_PER_NODE: int = 128
"""Maximum number of entries in a single `WorkflowMapping.values`."""

MAX_EXPRESSION_NESTING_DEPTH: int = 16
"""Maximum depth of a `WorkflowExpression` tree (an expression whose operand is itself an
expression, recursively)."""

MAX_EXPRESSION_OPERANDS: int = 64
"""Maximum number of operands in a single `WorkflowExpression.operands` list."""

MAX_TRAVERSAL_DEPTH: int = 32
"""Maximum recursion depth the resolver (`mapping.py`) will descend while walking a resolved
value's own nested structure beneath a reference's path — a distinct, independently-enforced
ceiling from `MAX_REFERENCE_PATH_DEPTH` (which bounds the *declared* path length), defending
against relying on Python's own `RecursionError` as the safety mechanism."""


class WorkflowOperator(str, enum.Enum):
    """Milestone F3 — the complete, closed set of deterministic expression operators.

    Mirrors the Document Engine's `InvariantOperator` precedent (`engines/document/ontology.py`)
    exactly: a fixed, reviewed enum of pure named operations, never a parsed grammar, never
    `eval`/`exec`, never an extensible plugin/callable-injection mechanism. Every operator here is
    directly justified by the F3 architecture discovery's own named examples ("concatenate two
    fields", "extract array length", arithmetic) — no operator is invented beyond that evidence.
    Adding a new operator later means adding one new enum member and one new pure function in
    `expression.py`, never loosening this closed-set guarantee.
    """

    CONCAT = "CONCAT"
    """String-concatenates every operand, coerced to str() in order. Requires >= 1 operand."""

    SUM = "SUM"
    """Numeric sum of every operand. Requires >= 1 numeric (int/float) operand; non-numeric
    operands are a WorkflowExpressionError, never silently coerced."""

    SUBTRACT = "SUBTRACT"
    """operands[0] - sum(operands[1:]). Requires >= 1 numeric operand, mirroring
    InvariantOperator.DIFFERENCE_EQUALS's own operand-count and typing contract exactly."""

    LENGTH = "LENGTH"
    """len() of exactly one operand, which must resolve to a str, list, or dict. Any other
    operand type or count is a WorkflowExpressionError."""


class WorkflowReference(BaseModel):
    """Milestone F3 — a structured reference to another node's output.

    Deliberately a structured object, never a bare string: a string like
    `"GetCustomer.output.body.email"` requires its own parser (and its own parser bugs) before
    anything can validate it; this shape is trivially validated by Pydantic alone and trivially
    constructed by an LLM's structured-output mode or a canvas's own UI state (F3 architecture
    discovery §8/§18/§19).

    `source_node_id` is always explicit — never inferred from graph position, never "the previous
    node." A reference to the *same* node it appears on, or to a node that is not a topological
    ancestor of the consuming node (a sibling branch, a descendant, or a disconnected node), is
    rejected by `mapping_validation.validate_reference` — never by this model itself, which stays
    pure shape, mirroring `WorkflowGraphNode`/`WorkflowGraphEdge`'s own model/validation split.

    `source_port`, when set, is resolved as the first path segment beneath the source node's raw
    output (see `mapping.py`'s resolver) — there is no separate "port-keyed" runtime data shape,
    since F2's ports carry no schema of their own (F3 discovery §9) and a capability's actual
    output today is one opaque value (`WorkflowContext.step_outputs[step_id]`), not a dict of
    named ports. `path` then continues descending from there. No wildcard traversal, no implicit
    "current node" reference, no reference to graph-level metadata.
    """

    source_node_id: str = Field(..., description="The node this reference reads from — always explicit")
    source_port: str | None = Field(
        default=None,
        description="Named output port on the source node, resolved as the first path segment beneath its raw output",
    )
    path: list[str | int] = Field(
        default_factory=list,
        description="Dotted/indexed traversal beneath source_port (or the raw output, if source_port is None) — "
        "string keys for dict/object access, integers for list/array indexing, applied in order",
    )


class WorkflowExpression(BaseModel):
    """Milestone F3 — a single deterministic computation over already-resolved operands.

    `operands` are themselves `WorkflowValue`s — each may be a literal, a reference, or another
    nested expression — so composition is possible (e.g. CONCAT of two SUM results) up to
    `mapping_validation.MAX_EXPRESSION_NESTING_DEPTH`. Every operand is resolved to a plain value
    *before* the operator ever runs (`expression.py`): the operator itself never sees a
    `WorkflowReference` or an unresolved nested expression, only the values they resolved to. This
    is what makes evaluation deterministic and side-effect-free — the operator is a pure function
    of already-resolved data, exactly matching `InvariantOperator.compute_invariant_target`'s own
    determinism guarantee.
    """

    operator: WorkflowOperator = Field(..., description="The one deterministic operation to apply")
    operands: list[WorkflowValue] = Field(
        default_factory=list, description="Already-resolvable operand values, evaluated left to right"
    )


class WorkflowLiteralValue(BaseModel):
    """Milestone F3 — a `WorkflowValue` variant: a plain, author-supplied constant."""

    kind: Literal["literal"] = "literal"
    value: Any = Field(default=None, description="The literal value verbatim — never resolved, never referenced")


class WorkflowReferenceValue(BaseModel):
    """Milestone F3 — a `WorkflowValue` variant: a reference to another node's output."""

    kind: Literal["reference"] = "reference"
    reference: WorkflowReference


class WorkflowExpressionValue(BaseModel):
    """Milestone F3 — a `WorkflowValue` variant: a deterministic computation over other values."""

    kind: Literal["expression"] = "expression"
    expression: WorkflowExpression


WorkflowValue = Annotated[
    WorkflowLiteralValue | WorkflowReferenceValue | WorkflowExpressionValue,
    Field(discriminator="kind"),
]
"""Milestone F3 — a discriminated union: a value is *exactly one* of literal/reference/expression,
never an ambiguous combination of optional fields. The `kind` tag makes this true by construction —
there is no representable state with zero or two sources active, unlike a single model with three
optional fields would allow. This is deliberately a type alias, not a class: `WorkflowMapping.values`
and `WorkflowExpression.operands` use it directly, and Pydantic resolves the correct variant purely
from the serialized `kind` key, both for `model_validate()` and for JSON Schema generation an AI
authoring surface would consume (F3 architecture discovery §18)."""

WorkflowExpression.model_rebuild()
"""Resolves the `"WorkflowValue"` forward reference now that the type alias exists in this module's
namespace — the standard Pydantic v2 fix-up for a model that refers to a name defined after it,
required here because `WorkflowExpression` and `WorkflowValue` are mutually recursive
(an expression's operands can themselves be expressions)."""


class WorkflowMapping(BaseModel):
    """Milestone F3 — the complete set of resolved-value bindings for one node's input.

    `values` maps a field name (e.g. a capability parameter name, or a declared input port) to the
    `WorkflowValue` that should supply it. This is the top-level object a future execution
    integration point, an AI workflow builder, or a visual canvas would all construct identically —
    one shared representation, never three separate per-surface formats (F3 architecture discovery
    §20). Deliberately not a field on `WorkflowGraphNode` in this milestone: F3 delivers the data
    model and its validator as reusable, standalone components — wiring a mapping onto a specific
    node field is an integration decision left to whichever future milestone actually executes one.
    """

    values: dict[str, WorkflowValue] = Field(
        default_factory=dict, description="Field name -> the value source that should supply it"
    )


class WorkflowRuntimeContext(BaseModel):
    """Milestone F3 — the explicit runtime data a pure resolver is handed; never accessed ambiently.

    `node_outputs` mirrors `WorkflowContext.step_outputs` exactly (node/step id -> that node's raw
    output value) — this is deliberately the same shape, so a future execution integration point
    can construct one directly from the other with no translation. This model is not read or
    written by the executor in this milestone; it exists solely as the documented contract
    `mapping.py`'s resolver accepts, so a future milestone knows exactly what to build and hand in
    (F3 architecture discovery §16).
    """

    node_outputs: dict[str, Any] = Field(
        default_factory=dict, description="node_id -> that node's raw resolved output, once it has executed"
    )


class WorkflowDefinition(BaseModel):
    """Declarative workflow definition specification."""

    id: str = Field(..., description="Unique workflow definition ID")
    name: str = Field(..., description="Workflow definition title")
    version: str = Field(default="1.0.0", description="Semantic version string")
    description: str = Field(default="", description="Detailed workflow description")
    tenant_id: str = Field(default="default", description="Tenant ID owning this definition")
    steps: list[WorkflowStep] = Field(default_factory=list, description="Ordered execution steps")
    trigger: WorkflowTrigger = Field(default=WorkflowTrigger.MANUAL, description="Default trigger source")
    priority: WorkflowPriority = Field(default=WorkflowPriority.NORMAL, description="Workflow execution priority")
    timeout_seconds: int = Field(default=3600, ge=1, description="Execution timeout in seconds")
    graph: WorkflowGraph | None = Field(
        default=None,
        description=(
            "Milestone F2 — the optional canonical structural Workflow Graph representation of this "
            "definition. Additive and inert: None (the default) preserves every pre-F2 definition's "
            "exact behavior — the executor (engine.py/evaluator.py/state_machine.py) reads `steps` "
            "exclusively and never this field. Milestone F4 persists this durably on "
            "`WorkflowDefinitionVersion.graph` (see `persistence.py`'s `WorkflowDefinitionVersionModel."
            "graph_json`); this in-memory field itself is unchanged and still read by nothing in the "
            "executor."
        ),
    )


# ============================================================================
# Milestone F4 — Workflow Definition Lifecycle & Authoring Foundation
#
# A WorkflowDefinition (above) is the executable *content* shape, unchanged by
# F4. What F4 adds is identity/version separation around it: a stable
# definition identity may have many WorkflowDefinitionVersion snapshots, of
# which at most one is the mutable DRAFT (or its terminal ARCHIVED state) and
# any number are immutable PUBLISHED history. See `lifecycle.py` for the
# manager that enforces these rules; this module stays pure data shape,
# mirroring F2/F3's own models/validation separation.
# ============================================================================


class WorkflowDefinitionStatus(enum.StrEnum):
    """Lifecycle status of one `WorkflowDefinitionVersion` row.

    `DRAFT` and `ARCHIVED` are mutually exclusive *control* states: at most one row per
    `(tenant_id, definition_id)` may hold either of them at a time — the single mutable authoring
    surface for that definition. `PUBLISHED` rows are immutable snapshots; any number may exist per
    definition, and a `PUBLISHED` row never transitions to any other status (F4 discovery §14/D6).
    """

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class WorkflowDefinitionVersion(BaseModel):
    """Milestone F4 — one version snapshot of a `WorkflowDefinition`'s content.

    Represents either the single mutable control row for a definition (`status=DRAFT` or, once
    archived, `status=ARCHIVED`) or one immutable published snapshot (`status=PUBLISHED`). The
    content fields (`steps`/`graph`) are a direct, additive echo of `WorkflowDefinition`'s own
    content shape — no new content model is invented; F2's graph and F3's mapping/expression
    objects nested inside `steps`/`graph` are reused completely unmodified.

    `lock_version` is the identical optimistic-locking pattern already proven by
    `WorkflowInstance.version` — meaningful only for the mutable DRAFT row (a PUBLISHED row is never
    written again after creation, so it never needs conflict detection).
    """

    id: str = Field(default_factory=lambda: str(uuid4()), description="Unique version-row identifier")
    definition_id: str = Field(..., description="The stable WorkflowDefinition identity this version belongs to")
    tenant_id: str = Field(default="default", description="Tenant owning this definition")
    version: str = Field(
        default="0.0.0-draft",
        description="SemVer string. The DRAFT/ARCHIVED control row always reads '0.0.0-draft' — it "
        "is never itself an addressable, instance-bindable version.",
    )
    status: WorkflowDefinitionStatus = Field(default=WorkflowDefinitionStatus.DRAFT)
    name: str = Field(default="", description="Definition title, editable on the draft")
    description: str = Field(default="", description="Detailed description, editable on the draft")
    trigger: WorkflowTrigger = Field(default=WorkflowTrigger.MANUAL)
    priority: WorkflowPriority = Field(default=WorkflowPriority.NORMAL)
    timeout_seconds: int = Field(default=3600, ge=1)
    steps: list[WorkflowStep] = Field(default_factory=list, description="Legacy flat execution content")
    graph: WorkflowGraph | None = Field(
        default=None, description="F2 canonical graph content — the authored source of truth for new definitions"
    )
    created_by: str = Field(default="SYSTEM", description="Principal ID that created this version row")
    lock_version: int = Field(default=1, ge=1, description="Optimistic locking counter for the DRAFT row")
    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)
    published_at: datetime | None = Field(default=None, description="Set exactly once, when status becomes PUBLISHED")


class WorkflowDefinitionValidationReport(BaseModel):
    """Milestone F4 — the result of validating a `WorkflowDefinitionVersion`'s content.

    Reuses F2's `graph_validation` and F3's `mapping_validation` errors verbatim as `errors`
    entries — no new validation logic is introduced here; this is purely an aggregation shape.
    """

    is_valid: bool = Field(...)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkflowInstance(BaseModel):
    """Runtime instance of an executing workflow definition."""

    id: UUID = Field(default_factory=uuid4, description="Unique workflow instance UUID")
    definition_id: str = Field(..., description="Reference definition ID")
    definition_version: str = Field(default="1.0.0", description="Definition version string")
    tenant_id: str = Field(default="default", description="Tenant ID owning this instance")
    current_step_index: int = Field(default=0, ge=0, description="Index of the currently executing step")
    current_step_id: str | None = Field(default=None, description="ID of current step")
    state: WorkflowState = Field(default=WorkflowState.CREATED, description="Current lifecycle state")
    status: WorkflowStatus = Field(default=WorkflowStatus.PENDING, description="Current status indicator")
    context: WorkflowContext = Field(default_factory=WorkflowContext, description="Execution context")
    compensation_stack: list[CompensationAction] = Field(
        default_factory=list,
        description="LIFO stack of compensation actions registered for rollback",
    )
    trace_id: str = Field(default_factory=lambda: str(uuid4()), description="Traceability ID")
    version: int = Field(default=1, ge=1, description="Optimistic locking version counter")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Creation timestamp")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last update timestamp")


class ExecutionResult(BaseModel):
    """Result payload for an individual step execution."""

    step_id: str = Field(..., description="Executed step ID")
    success: bool = Field(..., description="True if step executed successfully")
    output: Any | None = Field(default=None, description="Step output payload")
    error: str | None = Field(default=None, description="Error message if step failed")
    execution_time_ms: float = Field(default=0.0, ge=0.0, description="Step duration in milliseconds")
    attempts: int = Field(default=1, ge=1, description="Total execution attempts made")


class WorkflowResult(BaseModel):
    """Final result payload for a completed or failed workflow instance."""

    instance_id: UUID = Field(..., description="Workflow instance UUID")
    definition_name: str = Field(..., description="Name of executed definition")
    state: WorkflowState = Field(..., description="Final workflow lifecycle state")
    status: WorkflowStatus = Field(..., description="Final status indicator")
    context: WorkflowContext = Field(..., description="Final execution context")
    step_results: list[ExecutionResult] = Field(default_factory=list, description="List of step execution results")
    duration_ms: float = Field(default=0.0, ge=0.0, description="Total workflow duration in milliseconds")
    error: str | None = Field(default=None, description="Terminal error message if workflow failed")


class ApprovalRequest(BaseModel):
    """Ticket representing an approval checkpoint requirement."""

    id: UUID = Field(default_factory=uuid4, description="Approval request UUID")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    instance_id: UUID | None = Field(default=None, description="Associated workflow instance UUID")
    step_id: str | None = Field(default=None, description="Step ID requiring approval")
    required_role: str = Field(..., description="Role authorized to approve")
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Request timestamp")
    state: ApprovalState = Field(default=ApprovalState.PENDING, description="Current approval state")
    timeout_at: datetime | None = Field(default=None, description="Timeout expiration timestamp")
    context_snapshot: dict[str, Any] = Field(default_factory=dict, description="Execution context snapshot")
    signature_required: bool = Field(default=False, description="Whether Ed25519 signature is strictly mandatory")
    requester_principal_id: str | None = Field(
        default=None, description="Principal ID that created this ticket (M6.2-3, additive)"
    )
    requester_principal_type: str | None = Field(
        default=None, description="PrincipalType of the requester, e.g. 'AGENT' or 'USER' (M6.2-3, additive)"
    )
    correlation_id: str | None = Field(
        default=None,
        description="Cross-system correlation ID linking this ticket to its originating action (M6.2-3, additive)",
    )
    action_fingerprint: str | None = Field(
        default=None,
        description=(
            "Stable hash of the exact proposed action this ticket gates, re-verified before an "
            "APPROVED decision is allowed to resume execution (M6.2-3, additive)"
        ),
    )
    created_at: datetime | None = Field(default=None, description="Creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Update timestamp")


class ApprovalDecision(BaseModel):
    """Decision payload submitted by an authorized approver."""

    id: UUID = Field(default_factory=uuid4, description="Decision UUID")
    request_id: UUID = Field(..., description="ID of approval request")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    approver_id: str = Field(..., description="ID or username of approver")
    decision: ApprovalState = Field(..., description="APPROVED or REJECTED decision")
    reason: str | None = Field(default=None, description="Optional decision notes or reason")
    signature_hex: str | None = Field(default=None, description="Cryptographic Ed25519 signature in hex format")
    public_key_hex: str | None = Field(default=None, description="Optional public key in hex format for verification")
    decision_data: dict[str, Any] = Field(default_factory=dict, description="Additional context data")
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Timestamp of decision")


class ApprovalDelegation(BaseModel):
    """Delegation granting an approver role to a deputy user for a bounded time window."""

    id: UUID = Field(default_factory=uuid4, description="Delegation UUID")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    delegator_id: str = Field(..., description="Principal ID delegating the role")
    delegatee_id: str = Field(..., description="Principal ID receiving the delegated role")
    role: str = Field(..., description="Role name being delegated")
    valid_from: datetime = Field(..., description="UTC start time of delegation validity")
    valid_until: datetime = Field(..., description="UTC end time of delegation validity")
    is_active: bool = Field(default=True, description="Whether the delegation is active")
    created_at: datetime | None = Field(default=None, description="Creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Update timestamp")


class ApprovalEvent(BaseModel):
    """Event payload published during approval lifecycle transitions."""

    event_type: str = Field(..., description="Type of approval event (requested, decision, expired)")
    request_id: UUID = Field(..., description="ID of approval request")
    instance_id: UUID = Field(..., description="Associated workflow instance UUID")
    step_id: str = Field(..., description="Step ID")
    approver_id: str | None = Field(default=None, description="ID of decision submitter if applicable")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Event timestamp")


class WorkflowSettings(BaseModel):
    """Configuration settings for the Workflow Engine."""

    execution_timeout_seconds: int = Field(default=3600, ge=1, description="Global execution timeout")
    retry_default_attempts: int = Field(default=3, ge=1, description="Default max retry attempts")
    retry_backoff_factor: float = Field(default=2.0, ge=1.0, description="Default retry backoff factor")
    approval_timeout_seconds: int = Field(default=86400, ge=1, description="Default approval timeout")
    worker_count: int = Field(default=4, ge=1, description="Worker thread pool count")
    concurrency_limit: int = Field(default=100, ge=1, description="Maximum concurrent running workflows")
    logging_level: str = Field(default="INFO", description="Logging level string")
    metrics_enabled: bool = Field(default=True, description="Enable metrics collection")
    scheduler_enabled: bool = Field(default=False, description="Enable background scheduler daemon")
    scheduler_poll_interval_seconds: float = Field(default=1.0, ge=0.1, description="Scheduler polling interval")
    # M6.4-2: deliberately independent of `scheduler_enabled` (which
    # defaults to False and gates the unrelated CRON/interval workflow-
    # schedule feature) -- coupling approval-expiry propagation to that
    # flag would silently disable it in every deployment that hasn't
    # opted into CRON scheduling for other reasons. Defaults enabled so a
    # timed-out approval ticket always eventually propagates without
    # requiring any additional configuration.
    approval_sweep_enabled: bool = Field(default=True, description="Enable the background approval-expiry sweep loop")
    approval_sweep_interval_seconds: float = Field(
        default=30.0, ge=0.1, description="Approval-expiry sweep polling interval"
    )


# ============================================================================
# Scheduling Models (Milestone M5.4)
# ============================================================================


class ScheduleType(enum.StrEnum):
    """Type of scheduling mechanism."""

    CRON = "CRON"
    INTERVAL = "INTERVAL"
    ONCE = "ONCE"


class ScheduleStatus(enum.StrEnum):
    """Operational status of a scheduled workflow job."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"
    COMPLETED = "COMPLETED"
    # M5-A5: transient claimed-but-not-yet-ticked state. Set atomically by
    # `SchedulerStore.claim_due_schedules` (ACTIVE -> TRIGGERING, one winner
    # per row) so two concurrent scheduler ticks/workers can never both
    # start a workflow instance for the same due fire. Cleared back to
    # ACTIVE/COMPLETED by `record_schedule_tick` once the instance has
    # actually started; a row stuck here past its claim lease (the
    # scheduler process died mid-tick) is reclaimed back to ACTIVE by the
    # same `claim_due_schedules` call on a later tick.
    TRIGGERING = "TRIGGERING"


class WorkflowSchedule(BaseModel):
    """Declarative specification and runtime state for a scheduled workflow job."""

    id: UUID = Field(default_factory=uuid4, description="Unique schedule UUID")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    name: str = Field(..., description="Unique human-readable schedule name within tenant")
    definition_id: str = Field(..., description="Target workflow definition ID")
    schedule_type: ScheduleType = Field(default=ScheduleType.INTERVAL, description="Scheduling strategy")
    cron_expression: str | None = Field(default=None, description="5-field cron expression if type is CRON")
    interval_seconds: int | None = Field(default=None, ge=1, description="Execution interval in seconds if INTERVAL")
    run_at: datetime | None = Field(default=None, description="Target execution timestamp if type is ONCE")
    next_run_at: datetime | None = Field(default=None, description="Next calculated UTC execution timestamp")
    last_run_at: datetime | None = Field(default=None, description="Timestamp of the most recent execution trigger")
    last_instance_id: UUID | None = Field(default=None, description="UUID of the instance created on last run")
    status: ScheduleStatus = Field(default=ScheduleStatus.ACTIVE, description="Current schedule lifecycle status")
    initial_context: dict[str, Any] = Field(default_factory=dict, description="Initial context passed to workflow")
    max_runs: int | None = Field(default=None, ge=1, description="Maximum executions before auto-completing")
    run_count: int = Field(default=0, ge=0, description="Total execution triggers performed")
    timezone: str = Field(default="UTC", description="Timezone name for cron calculations")
    created_by: str = Field(default="SYSTEM", description="Principal ID that created the schedule")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Creation timestamp")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last update timestamp")


class ScheduleEvent(BaseModel):
    """Domain event payload for schedule lifecycle transitions."""

    event_type: str = Field(..., description="Event type (created, triggered, paused, resumed, cancelled)")
    schedule_id: UUID = Field(..., description="ID of the workflow schedule")
    tenant_id: str = Field(default="default", description="Tenant ID")
    name: str = Field(..., description="Schedule name")
    definition_id: str = Field(..., description="Target workflow definition ID")
    instance_id: UUID | None = Field(default=None, description="Spawned instance ID if triggered")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Event timestamp")


# ============================================================================
# Governed External Execution Models (Milestone M5.4)
# ============================================================================


class ExternalExecutionStatus(enum.StrEnum):
    """Lifecycle status of a governed external execution."""

    PENDING = "PENDING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class ExternalExecutionRequest(BaseModel):
    """Request payload to execute a governed external operation."""

    id: UUID = Field(default_factory=uuid4, description="Unique execution request UUID")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    operation_type: str = Field(default="CAPABILITY", description="Operation category (e.g. CAPABILITY, HTTP)")
    target: str = Field(..., description="Target capability name, endpoint, or driver action")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Invocation parameter payload")
    timeout_seconds: float = Field(default=30.0, ge=0.1, description="Execution timeout in seconds")
    retry_policy: RetryPolicy | None = Field(default=None, description="Custom retry policy")
    requires_approval: bool = Field(default=False, description="Whether human approval is required prior to execution")
    required_approval_role: str | None = Field(default=None, description="Role authorized to approve if required")
    approval_timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        description=(
            "M6.4-2: optional expiry timeout (seconds) for the approval ticket created when "
            "requires_approval=True. None (the default) preserves the pre-M6.4 behavior of an "
            "approval ticket that never expires -- opting in is required to make a request's "
            "approval gate subject to the expiry sweep at all."
        ),
    )
    idempotency_key: str | None = Field(default=None, description="Caller-supplied idempotency key")
    correlation_id: str | None = Field(default=None, description="Trace correlation ID")
    created_by: str = Field(default="SYSTEM", description="Principal ID initiating the request")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Request timestamp")


class ExternalExecutionRecord(BaseModel):
    """Durable record of a governed external execution outcome."""

    id: UUID = Field(default_factory=uuid4, description="Execution record UUID")
    request_id: UUID = Field(..., description="Associated execution request UUID")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    operation_type: str = Field(..., description="Operation category")
    target: str = Field(..., description="Target capability name or endpoint")
    status: ExternalExecutionStatus = Field(default=ExternalExecutionStatus.PENDING, description="Execution status")
    status_code: int | None = Field(default=None, description="HTTP status code or exit code if applicable")
    output: Any | None = Field(default=None, description="Sanitized execution output payload")
    error: str | None = Field(default=None, description="Error message if execution failed")
    attempts: int = Field(default=1, ge=1, description="Number of execution attempts made")
    execution_time_ms: float = Field(default=0.0, ge=0.0, description="Duration in milliseconds")
    idempotency_key: str | None = Field(default=None, description="Idempotency key")
    correlation_id: str | None = Field(default=None, description="Correlation ID")
    approval_request_id: UUID | None = Field(default=None, description="Linked approval ticket UUID if approval gated")
    created_by: str = Field(default="SYSTEM", description="Principal ID")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Creation timestamp")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last update timestamp")
    completed_at: datetime | None = Field(default=None, description="Completion timestamp")
