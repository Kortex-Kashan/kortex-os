"""
KORTEX AI Workflow Builder (AI Workflow Builder milestone).

Turns a natural-language automation request into a structured `WorkflowGraph` + per-node
`WorkflowMapping` proposal, targeting the exact canonical F2/F3 models a human-authored workflow or
a future visual canvas would produce (`workflow/models.py::WorkflowGraph.__doc__`,
`workflow/lifecycle.py:17`) -- never a parallel format.

Architectural boundary (ratified, mirrors the F5/F6 seam this module reuses verbatim):
- This module is a pure PRODUCER OF PROPOSALS. It never calls `kortex.workflow.definition.create`,
  `.update`, `.validate`, `.publish`, `.archive`, or `.clone` -- those remain the caller's (desktop
  frontend's) own direct capability invocations, exactly as the discovery's D9/D10 human-approval
  boundary requires. This is enforced by absence, not by a permission check: no line of code in this
  module constructs a `CapabilityRequest` targeting any `kortex.workflow.definition.*` capability.
  Structural pre-validation here (`graph_validation`/`mapping_validation`) is purely advisory --
  F4's own `.validate()`/`.publish()` remain the sole authoritative gates.
- Lives in `kortex.api`, not `kortex.engines.ai`, for the same reason `capability_tool_bridge.py` and
  `capability_projection.py` do: `kortex.engines.ai`'s AST-enforced import allowlist
  (`test_ai_package_imports_no_forbidden_dependency`) does not, and must not, permit importing
  `kortex.engines.registry`/`kortex.engines.workflow`/`kortex.core.kernel` directly. `kortex.api`
  carries no such restriction and is already the composition-root layer that freely imports across
  engine boundaries to assemble the running Kernel.
- Capability discovery reuses F6 `CapabilityProjection` directly (in-process, no capability-dispatch
  round trip needed for a read already scoped to this handler's own `execution_context`). The LLM
  generation call reuses `kortex.ai.response.generate` via a genuine nested `Kernel.invoke_capability`
  dispatch, exactly mirroring the "trusted platform code performing a synchronous nested capability
  dispatch" precedent `CapabilityExecutionContext.session_token`'s own docstring names (Workflow's
  external-operation executor) -- never a bespoke "AI system identity," since this capability is
  itself invoked by an already-authenticated human caller acting on their own behalf, not an
  autonomous agent loop.
- The curated node catalog (`CURATED_CAPABILITY_NAMES`) is a deliberately small, hand-picked
  allowlist of already schema-complete capabilities (F1's `parameters_schema`/`returns_schema`) --
  never the full ~140+ capability catalog. A capability absent from this list is never presented to
  the LLM and never accepted in a parsed proposal, even if the LLM hallucinates it and even if it
  would otherwise be tenant-authorized.
- Vendor-native tool-calling is never used or depended upon (the platform-wide `LLMRequest.tools`
  gap the architecture discovery confirmed remains open). Generation is plain structured-output text
  generation: the LLM is instructed to emit one JSON object; this module parses and validates it
  against the real `WorkflowGraph`/`WorkflowMapping` Pydantic models. A parse or structural-validation
  failure is a first-class, non-fatal result returned to the caller for the bounded correction loop
  the frontend drives (never an unbounded retry loop inside this module).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from kortex.core.dispatch import CapabilityExecutionContext, CapabilityRequest
from kortex.core.projection import CapabilityProjection
from kortex.engines.ai.models import LLMRequest
from kortex.engines.workflow.exceptions import (
    WorkflowGraphConversionError,
    WorkflowGraphValidationError,
)
from kortex.engines.workflow.graph_compat import graph_to_steps
from kortex.engines.workflow.graph_validation import validate_graph
from kortex.engines.workflow.mapping_validation import validate_mapping
from kortex.engines.workflow.models import WorkflowGraph, WorkflowGraphEdge, WorkflowGraphNode, WorkflowMapping

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.api.workflow_builder")

GENERATE_CAPABILITY_NAME = "kortex.ai.workflow_builder.generate"

# -- Curated v1 node catalog (discovery report §11/§28; D13) -----------------------------------
#
# A small, deliberately hand-picked allowlist of already schema-complete capabilities. Extending
# this set requires authoring a real `parameters_schema`/`returns_schema` for the new capability
# first (F5 reference-action precedent) -- never just adding a name here. Chosen to demonstrate,
# with a genuinely useful two-step automation ("look up an invoice, then notify a webhook about
# it"): one read-only capability, one mutating connector action with the Workflow Engine's own
# existing per-step approval semantics, and real inter-node data flow (F3 mapping/expression).
CURATED_CAPABILITY_NAMES: tuple[str, ...] = (
    "kortex.finance.invoice.get",
    "kortex.connector.notification.webhook.send",
)

# Reserved `WorkflowGraphNode.config`/`.metadata` keys the F2 graph<->step compatibility adapter
# (`graph_compat.py`) already establishes and round-trips. Duplicated here as plain string
# literals (not imported) because they are a documented, stable *data* contract -- the same
# discipline `lifecycle.py::_validate_content` already follows for `node.config.get("mapping")` --
# never a reach into `graph_compat.py`'s own private module internals.
_MAPPING_CONFIG_KEY = "mapping"
_STEP_METADATA_KEY = "kortex.workflow.step"

MAX_PROMPT_CATALOG_ENTRIES = 20
"""Defensive ceiling on how many curated+authorized capabilities are ever embedded in one
generation prompt -- always small in practice (`len(CURATED_CAPABILITY_NAMES)`), but bounded
independently of that list's size so a future larger curated set cannot silently blow out the
prompt budget."""

_SECRET_LIKE_KEY_PATTERN = re.compile(r"(api[_-]?key|secret|password|passwd|token|credential|access[_-]?key)", re.I)
"""Heuristic, defense-in-depth key-name scan for §17/§23's 'no inline secrets' requirement --
additive to, never a replacement for, the platform's existing SecretStore/profile-reference
discipline (a connector action's `profile_id` is the sanctioned way to reference a credential;
this module never accepts a literal credential value in a node's `config` at all)."""


class WorkflowBuilderError(Exception):
    """Raised for a hard failure in generating or parsing an AI Workflow Builder proposal --
    distinct from a structural validation failure, which is returned as data (`status:
    "generation_failed"`) for the caller's bounded correction loop, never raised."""


def _actor_identity(execution_context: CapabilityExecutionContext) -> tuple[str, str]:
    """Return `(tenant_id, user_id)` derived exclusively from the dispatcher-verified
    `execution_context` -- never from any caller-supplied parameter. Fails closed."""
    if execution_context.principal is None:
        raise WorkflowBuilderError(
            "kortex.ai.workflow_builder.generate requires a verified principal; none was provided."
        )
    return execution_context.tenant_id, execution_context.principal.principal_id


async def _curated_catalog(kernel: Kernel, execution_context: CapabilityExecutionContext) -> list[dict[str, Any]]:
    """Return the intersection of `CURATED_CAPABILITY_NAMES` with the caller's own F6
    tenant-projected, authorized capability set -- never the full registry, and never a
    capability the caller's own tenant is not itself authorized to invoke."""
    projection = CapabilityProjection(kernel)
    descriptors = await projection.project_capabilities(execution_context)
    curated = {name: d for d in descriptors if d.name in CURATED_CAPABILITY_NAMES for name in (d.name,)}
    catalog = [
        {
            "capability_name": name,
            "description": curated[name].description,
            "parameters_schema": curated[name].parameters_schema,
            "returns_schema": curated[name].returns_schema,
            "is_read_only": curated[name].is_read_only,
            "is_idempotent": curated[name].is_idempotent,
        }
        for name in CURATED_CAPABILITY_NAMES
        if name in curated
    ][:MAX_PROMPT_CATALOG_ENTRIES]
    return catalog


def _build_prompt(
    intent: str,
    catalog: list[dict[str, Any]],
    *,
    previous_graph: dict[str, Any] | None,
    validation_errors: list[str] | None,
    validation_warnings: list[str] | None,
) -> str:
    catalog_json = json.dumps(catalog, indent=2, default=str)
    sections = [
        "You are generating a KORTEX Workflow Graph proposal. Output exactly one JSON object and "
        "nothing else -- no markdown code fences, no prose before or after.",
        "",
        f"User intent: {intent}",
        "",
        "Capabilities you may use as workflow nodes (this is the COMPLETE and ONLY set -- you must "
        "never invent, guess, or reference any capability_name not listed here):",
        catalog_json,
        "",
        "Hard rules:",
        "- The graph MUST be linear: exactly one entry node, a single unbranching chain, every node "
        "visited exactly once, no cycles, no node with more than one outgoing or incoming edge.",
        "- Every `capability_name` MUST be exactly one of the capabilities listed above.",
        "- A node's `config` supplies literal parameter values matching that capability's own "
        "`parameters_schema`. Never place a secret, API key, password, or token value in `config` -- "
        "connector actions reference a `profile_id` instead.",
        "- To use a PRIOR node's output as an input value, use `mapping` instead of a literal `config` "
        "entry for that field: "
        '{"kind": "reference", "reference": {"source_node_id": "<a prior node id>", '
        '"source_port": null, "path": ["<field name from that capability\'s returns_schema>"]}}. '
        "A reference may only target an earlier node in the same chain, never itself or a later node.",
        "- To compute a value deterministically, use "
        '{"kind": "expression", "expression": {"operator": "CONCAT"|"SUM"|"SUBTRACT"|"LENGTH", '
        '"operands": [<nested literal/reference/expression values>]}}. No other operators exist.',
        '- A literal value is {"kind": "literal", "value": <any JSON value>}.',
        "- `is_approval_step: true` marks a node as a PURE wait-for-human-approval gate: it never "
        "invokes any capability itself, `capability_name` MUST be null on it, and once approved the "
        "chain continues to the NEXT node. To gate a mutating (non-read-only) capability behind "
        "human approval, insert a separate `is_approval_step: true` gate node immediately before it "
        "in the chain -- never set `is_approval_step: true` on the mutating node itself, or that "
        "action will wait for approval but never actually run.",
        "",
        "Required output shape (fill in real values, do not copy this example verbatim):",
        json.dumps(
            {
                "name": "short workflow name",
                "description": "one-sentence description",
                "entry_node_id": "step_1",
                "nodes": [
                    {
                        "node_id": "step_1",
                        "capability_name": "<capability from the list above>",
                        "config": {"<param>": "<literal value>"},
                        "mapping": None,
                        "is_approval_step": False,
                    },
                    {
                        "node_id": "step_2",
                        "capability_name": "<a MUTATING capability from the list above, if one is needed>",
                        "config": {},
                        "mapping": {
                            "<param>": {
                                "kind": "reference",
                                "reference": {"source_node_id": "step_1", "source_port": None, "path": ["<field>"]},
                            }
                        },
                        "is_approval_step": False,
                    },
                ],
                "edges": [{"source_node_id": "step_1", "target_node_id": "step_2"}],
            },
            indent=2,
        ),
    ]

    if previous_graph is not None:
        sections.extend(
            [
                "",
                "Your previous proposal failed validation and must be corrected. Previous proposal:",
                json.dumps(previous_graph, indent=2, default=str),
                "Validation errors (must all be fixed):",
                json.dumps(validation_errors or [], indent=2),
                "Validation warnings (fix if reasonably possible):",
                json.dumps(validation_warnings or [], indent=2),
                "Output a complete, corrected replacement proposal in the same shape -- not a diff.",
            ]
        )

    return "\n".join(sections)


_SYSTEM_INSTRUCTION = (
    "You are the KORTEX AI Workflow Builder. You output exactly one JSON object describing a "
    "linear workflow graph, built only from the capabilities you are explicitly given. You never "
    "invent a capability name. You never place a secret, password, API key, or token value in the "
    "graph. You never output anything other than the JSON object itself."
)


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n", "", stripped)
        stripped = re.sub(r"\n```$", "", stripped)
    return stripped.strip()


async def _invoke_llm(
    kernel: Kernel,
    execution_context: CapabilityExecutionContext,
    conversation_id: str,
    prompt: str,
) -> str:
    """Call `kortex.ai.response.generate` via a genuine nested Kernel dispatch, reusing the
    *same already-verified* session token this capability itself was invoked with -- never a
    separate system identity, never a caller-suppliable credential."""
    tenant_id, user_id = _actor_identity(execution_context)
    llm_request = LLMRequest(
        request_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        prompt=prompt,
        system_instruction=_SYSTEM_INSTRUCTION,
        temperature=0.2,
    )
    nested_request = CapabilityRequest(
        capability_name="kortex.ai.response.generate",
        parameters={"request": llm_request.model_dump(mode="json")},
        context={"resource_tenant_id": tenant_id},
        session_token=execution_context.session_token,
    )
    response = await kernel.invoke_capability(nested_request)
    text_content = getattr(response, "text_content", None)
    if text_content is None and isinstance(response, dict):
        text_content = response.get("text_content")
    if not isinstance(text_content, str):
        raise WorkflowBuilderError("kortex.ai.response.generate returned no usable text_content.")
    return text_content


def _scan_for_secret_like_values(node_id: str, config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in config:
        if _SECRET_LIKE_KEY_PATTERN.search(key):
            errors.append(
                f"Node '{node_id}' config declares a secret-like key '{key}' -- reference a "
                f"connector profile_id instead of a literal credential value."
            )
    return errors


def _parse_proposal(raw_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(_strip_code_fence(raw_text))
    except json.JSONDecodeError as exc:
        raise WorkflowBuilderError(f"Model output was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowBuilderError("Model output JSON must be an object.")
    return payload


def _proposal_to_graph(payload: dict[str, Any], tenant_id: str) -> tuple[WorkflowGraph, list[str]]:
    """Convert the LLM's proposal JSON into a real `WorkflowGraph`, enforcing the curated-capability
    allowlist and the no-secret-like-config heuristic *before* structural validation runs.

    `tenant_id` (the dispatcher-verified caller's own tenant, never LLM-supplied) is injected into
    every node's `config["_authz_context"]` -- the existing, established mechanism
    `StepEvaluator.execute_step`/`WorkflowEngine`'s dispatch closure already requires for any
    step invoking an authenticated capability (ABAC's tenant-match rule denies by default without
    it; see `lifecycle.py::publish()`'s own identical `resource_tenant_id` requirement). This value
    can never grant elevated access -- ABAC only ever compares it for *equality* against the real,
    session-token-verified principal's own tenant at dispatch time, so a wrong value can only ever
    cause a denial, never an escalation; it is not an authority, only a required denial-avoidance
    echo of the same tenant the whole workflow already belongs to (D9/D17: never let AI-authored
    content stand in as an authority, but this is not one).

    Returns `(graph, extra_errors)` -- `extra_errors` holds allowlist/secret-scan failures that are
    not `graph_validation`'s own responsibility to catch, aggregated the same way F4's own
    `WorkflowDefinitionValidationReport.errors` aggregates heterogeneous error sources.
    """
    extra_errors: list[str] = []
    nodes: list[WorkflowGraphNode] = []
    for raw_node in payload.get("nodes", []):
        node_id = raw_node.get("node_id", "<missing>")
        capability_name = raw_node.get("capability_name")
        if capability_name not in CURATED_CAPABILITY_NAMES:
            extra_errors.append(
                f"Node '{node_id}' references capability '{capability_name}', which is not in the "
                f"curated, tenant-authorized capability set for this session."
            )
        config = dict(raw_node.get("config") or {})
        extra_errors.extend(_scan_for_secret_like_values(node_id, config))
        config["_authz_context"] = {"resource_tenant_id": tenant_id}
        mapping = raw_node.get("mapping")
        if mapping:
            # `WorkflowMapping`'s own (and only) field is `values: dict[str, WorkflowValue]` --
            # the LLM is prompted with the simpler bare `{field_name: value}` shape (one less
            # nesting level to get right), wrapped here into the real `WorkflowMapping`-shaped
            # dict `lifecycle.py::_validate_content`/`StepEvaluator.execute_step` both expect.
            config[_MAPPING_CONFIG_KEY] = {"values": mapping}
        metadata = {
            _STEP_METADATA_KEY: {
                "name": raw_node.get("name", node_id),
                "is_approval_step": bool(raw_node.get("is_approval_step", False)),
                "required_approval_role": raw_node.get("required_approval_role"),
                "retry_policy": None,
                "compensation_action": None,
                "on_failure_continue": False,
            }
        }
        nodes.append(
            WorkflowGraphNode(
                node_id=node_id,
                node_type="capability",
                capability_name=capability_name,
                config=config,
                input_ports=list(raw_node.get("input_ports") or []),
                output_ports=list(raw_node.get("output_ports") or []),
                metadata=metadata,
            )
        )

    edges = [
        WorkflowGraphEdge(
            edge_id=f"edge_{raw_edge['source_node_id']}_{raw_edge['target_node_id']}",
            source_node_id=raw_edge["source_node_id"],
            source_port=raw_edge.get("source_port"),
            target_node_id=raw_edge["target_node_id"],
            target_port=raw_edge.get("target_port"),
        )
        for raw_edge in payload.get("edges", [])
    ]

    entry_node_id = payload.get("entry_node_id", nodes[0].node_id if nodes else "")
    graph = WorkflowGraph(entry_node_id=entry_node_id, nodes=nodes, edges=edges)
    return graph, extra_errors


def _validate_proposal(graph: WorkflowGraph, extra_errors: list[str]) -> tuple[bool, list[str], list[str]]:
    """Structural pre-validation, advisory only -- reuses F2's `graph_validation` and F3's
    `mapping_validation` verbatim, exactly as F4's own `_validate_content` does. Never a second
    validator, never a different rule set."""
    errors = list(extra_errors)
    warnings: list[str] = []
    try:
        validate_graph(graph)
    except WorkflowGraphValidationError as exc:
        errors.append(f"Graph structural validation failed: {exc}")
        return False, errors, warnings

    # D3 (ratified): v1 supports only linear graphs. Unlike F4's own `_validate_content` (which
    # only *warns* on non-linearity, deferring the hard rejection to `publish()`), this proposal
    # layer treats it as blocking immediately -- giving the correction loop a chance to fix it
    # before an F4 draft is ever created, per the master prompt's explicit "reject it using the
    # existing workflow graph conversion/validation contract" instruction.
    try:
        graph_to_steps(graph)
    except WorkflowGraphConversionError as exc:
        errors.append(f"Graph is not linear (v1 supports linear graphs only): {exc}")
        return False, errors, warnings

    for node in graph.nodes:
        raw_mapping = node.config.get(_MAPPING_CONFIG_KEY) if isinstance(node.config, dict) else None
        if raw_mapping is None:
            continue
        try:
            mapping = WorkflowMapping.model_validate(raw_mapping)
            validate_mapping(graph, node.node_id, mapping)
        except Exception as exc:  # aggregated into `errors`, mirrors F4's own `_validate_content` behavior
            errors.append(f"Mapping validation failed for node '{node.node_id}': {exc}")

    return not errors, errors, warnings


async def generate_workflow_draft_proposal(
    intent: str,
    conversation_id: str | None = None,
    previous_graph: dict[str, Any] | None = None,
    validation_errors: list[str] | None = None,
    validation_warnings: list[str] | None = None,
    execution_context: CapabilityExecutionContext | None = None,
    kernel: Kernel | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """`kortex.ai.workflow_builder.generate` handler.

    Produces a `WorkflowGraph`/`WorkflowMapping` proposal from `intent` (or, when `previous_graph`
    is supplied together with F4's own real `validation_errors`/`validation_warnings`, a corrected
    replacement proposal). NEVER creates, updates, validates, or publishes an F4 `WorkflowDefinition`
    -- returns a proposal only. The caller (desktop frontend) is solely responsible for the
    confirmation-gated `kortex.workflow.definition.create`/`update`/`validate`/`publish` sequence.
    """
    if execution_context is None:
        raise WorkflowBuilderError("kortex.ai.workflow_builder.generate requires a verified execution context.")
    if kernel is None:
        raise WorkflowBuilderError("kortex.ai.workflow_builder.generate requires a Kernel reference.")
    if not isinstance(intent, str) or not intent.strip():
        raise WorkflowBuilderError("intent must be a non-empty string.")

    catalog = await _curated_catalog(kernel, execution_context)
    if not catalog:
        return {
            "status": "generation_failed",
            "errors": ["No curated capability is authorized for the calling tenant."],
            "warnings": [],
            "graph": None,
            "raw_text": None,
        }

    prompt = _build_prompt(
        intent,
        catalog,
        previous_graph=previous_graph,
        validation_errors=validation_errors,
        validation_warnings=validation_warnings,
    )
    effective_conversation_id = conversation_id or str(uuid.uuid4())

    try:
        raw_text = await _invoke_llm(kernel, execution_context, effective_conversation_id, prompt)
        payload = _parse_proposal(raw_text)
        graph, extra_errors = _proposal_to_graph(payload, execution_context.tenant_id)
    except WorkflowBuilderError as exc:
        return {
            "status": "generation_failed",
            "errors": [str(exc)],
            "warnings": [],
            "graph": None,
            "raw_text": None,
        }
    except Exception as exc:  # any malformed-shape error becomes a returned failure, never a raise
        return {
            "status": "generation_failed",
            "errors": [f"Proposal did not match the expected shape: {exc}"],
            "warnings": [],
            "graph": None,
            "raw_text": None,
        }

    is_valid, errors, warnings = _validate_proposal(graph, extra_errors)
    return {
        "status": "proposed" if is_valid else "generation_failed",
        "errors": errors,
        "warnings": warnings,
        "graph": graph.model_dump(mode="json"),
        "name": payload.get("name", ""),
        "description": payload.get("description", ""),
        "conversation_id": effective_conversation_id,
        "raw_text": raw_text,
    }


def register_workflow_builder_capabilities(kernel: Kernel) -> None:
    """Register the AI Workflow Builder capability with the Kernel.

    A thin `kernel`-closing wrapper is used (rather than binding `kernel` as a default parameter
    value) so the handler always receives the exact booted `Kernel` instance, mirroring
    `capability_projection.py::register_projection_capabilities`'s own closure pattern.
    """

    async def _generate_handler(
        intent: str,
        conversation_id: str | None = None,
        previous_graph: dict[str, Any] | None = None,
        validation_errors: list[str] | None = None,
        validation_warnings: list[str] | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return await generate_workflow_draft_proposal(
            intent=intent,
            conversation_id=conversation_id,
            previous_graph=previous_graph,
            validation_errors=validation_errors,
            validation_warnings=validation_warnings,
            execution_context=execution_context,
            kernel=kernel,
        )

    kernel.register_capability(
        name=GENERATE_CAPABILITY_NAME,
        description=(
            "Generate (or correct) a WorkflowGraph/WorkflowMapping proposal from a natural-language "
            "intent, using only the tenant's own curated, authorized capability catalog. Produces a "
            "proposal only -- never creates, updates, validates, or publishes a WorkflowDefinition."
        ),
        provider="ai-workflow-builder",
        handler=_generate_handler,
        parameters_schema={
            "type": "object",
            "properties": {
                "intent": {"type": "string", "minLength": 1, "description": "Natural-language automation request."},
                "conversation_id": {"type": "string", "description": "Optional existing conversation to continue."},
                "previous_graph": {
                    "type": ["object", "null"],
                    "description": "The prior proposal being corrected, if any (a WorkflowGraph JSON object).",
                },
                "validation_errors": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "F4 kortex.workflow.definition.validate errors to correct, if any.",
                },
                "validation_warnings": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "F4 kortex.workflow.definition.validate warnings, if any.",
                },
            },
            "required": ["intent"],
        },
        returns_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["proposed", "generation_failed"]},
                "errors": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "graph": {"type": ["object", "null"]},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "conversation_id": {"type": "string"},
                "raw_text": {"type": ["string", "null"]},
            },
            "required": ["status", "errors", "warnings", "graph"],
        },
        required_permissions=["ai:generate", "workflow:read"],
        requires_authentication=True,
        requires_execution_context=True,
        security_classification="INTERNAL",
        is_read_only=True,
        is_idempotent=False,
    )


__all__ = [
    "CURATED_CAPABILITY_NAMES",
    "GENERATE_CAPABILITY_NAME",
    "WorkflowBuilderError",
    "generate_workflow_draft_proposal",
    "register_workflow_builder_capabilities",
]
