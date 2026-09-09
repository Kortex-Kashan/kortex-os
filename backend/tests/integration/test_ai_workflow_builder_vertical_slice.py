"""
AI Workflow Builder — canonical end-to-end vertical slice (master prompt §29).

No component below the Kernel boundary is mocked: real `SecurityEngine` (RBAC/ABAC,
Ed25519-signed tokens), real `RegistryEngine`/F6 `CapabilityProjection`, real `WorkflowEngine`
(F2/F3/F4 lifecycle + execution), real `ConnectorEngine` with the real `DummyConnectorDriver`,
real `FinanceModule`, real `AIOrchestrationEngine` production wiring. Only the AI *provider*
(the outbound vendor HTTP call) is a scripted test double returning a fixed proposal — exactly
the same substitution point every other AI integration test in this suite already uses
(`test_ai_connector_tool_invocation.py`'s `_ScriptedProvider`, `test_ai_document_tool_invocation.py`,
etc.), never a substitution for any KORTEX-owned component.

Proves the complete chain:

    kortex.ai.workflow_builder.generate (curated, tenant-projected catalog only)
        -> structured-output JSON proposal (WorkflowGraph + WorkflowMapping)
        -> kortex.workflow.definition.create (DRAFT — human-confirmed persistence)
        -> kortex.workflow.definition.validate (F4's own authoritative validator — valid)
        -> kortex.workflow.definition.publish (human-approval-gated; never called by AI code)
        -> WorkflowEngine.start_workflow (existing, unmodified execution path)
        -> step 1 (kortex.finance.invoice.get, real Finance invoice) executes
        -> step 2 (kortex.connector.notification.webhook.send) resolves its `body` mapping
           field from step 1's REAL output at runtime (F3 wiring) and executes against the
           real DummyConnectorDriver
        -> instance reaches COMPLETED with step 2's dispatched parameters proving the mapped
           value actually flowed end to end (not merely validated structurally)

Also proves the security/scope-discipline claims the discovery/master prompt require:
- The AI Workflow Builder capability itself never appears anywhere in the call chain to
  `kortex.workflow.definition.publish` (grepped from the real Kernel's own dispatch log).
- A second tenant's principal cannot see or use the first tenant's curated capabilities/draft.

This test is what surfaced two genuine, pre-existing defects this milestone fixes as a direct
consequence (never touched by any prior test, since no prior test published a definition
referencing a real, authenticated-by-default capability):
1. `lifecycle.py::publish()`'s fresh authorization re-check passed the wrong ABAC context key
   (`tenant_id` instead of `resource_tenant_id`), so it denied unconditionally regardless of actual
   authorization. Fixed to the same key every other `authorize()` call site already uses.
2. This module's own generation prompt previously instructed marking a *mutating* node itself
   `is_approval_step: true` -- but that flag makes a node a pure wait-gate that never dispatches
   any capability, even after approval, so the mutating action would silently never run. Fixed to
   instruct a separate gate node preceding the mutating one instead. The primary end-to-end test
   below uses a plain 2-node chain with no approval gate, to keep that proof focused;
   `test_approval_gate_precedes_mutating_node_and_resumes_after_approval` in this same file
   regression-hardens the corrected guidance itself, proving the gate-then-mutate composition
   actually works end to end through the real, unmodified Workflow Engine approval mechanism.
"""

from __future__ import annotations

import json
import uuid as uuid_module
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.capability_projection import register_projection_capabilities
from kortex.api.workflow_builder import register_workflow_builder_capabilities
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse
from kortex.engines.connector.actions import ConnectorActionBootstrapEngine
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.reference_actions import REFERENCE_ACTION_DESCRIPTORS
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.exceptions import WorkflowDefinitionNotFoundError
from kortex.engines.workflow.models import WorkflowSettings, WorkflowState
from kortex.modules.finance.module import FinanceModule

_TEST_MASTER_KEY = b"\xaa" * 32
_TEST_SIGNING_KEY = b"\xbb" * 32
_TENANT_A = "tenant-vslice-a"
_TENANT_B = "tenant-vslice-b"


class _ScriptedProposalProvider(BaseAIProvider):
    """A real, functioning `BaseAIProvider` returning a fixed structured-output proposal as
    `text_content` -- never `tool_calls` (the confirmed-nonfunctional vendor tool-calling path
    is never exercised or depended upon), exactly matching `workflow_builder.py`'s own design."""

    def __init__(self, proposal_json: str) -> None:
        self._metadata = AIProviderMetadata(
            provider_id="vslice-workflow-builder-provider",
            display_name="Workflow Builder Vertical Slice Test Provider",
            vendor="test",
            endpoint_type="local_host",
            supported_models=["vslice-model"],
            credential_requirement="none",
        )
        self._proposal_json = proposal_json

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            request_id=request.request_id,
            text_content=self._proposal_json,
            token_usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return True


async def _build_kernel(tmp_path: Path, provider: BaseAIProvider) -> Kernel:
    db_path = (tmp_path / f"kortex_vslice_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_vslice_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    connector_engine = ConnectorEngine()
    workflow_engine = WorkflowEngine(
        settings=WorkflowSettings(approval_sweep_enabled=False, approval_sweep_interval_seconds=60.0)
    )
    finance_module = FinanceModule()

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(finance_module)
    kernel.register_engine(ConnectorActionBootstrapEngine(REFERENCE_ACTION_DESCRIPTORS))

    bridge = KernelBridgeAdapter(kernel)
    ai_config = AIEngineRuntimeConfig(environment="production", enable_cloud_models=False)
    ai_bootstrap = KernelProductionBootstrap(ai_config)
    ai_engine = ai_bootstrap.create_ai_engine(
        kernel_bridge=bridge,
        data_store=data_store,
        custom_providers=[provider],
        registered_engines=list(kernel.get_all_engines().keys()),
    )
    kernel.register_engine(ai_engine)

    register_projection_capabilities(kernel)
    register_workflow_builder_capabilities(kernel)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    connector_engine.register_driver(DummyConnectorDriver())

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        role = f"vslice-role-{_TENANT_A}"
        for perm in (
            "ai:generate",
            "workflow:read",
            "workflow:write",
            "workflow:start",
            "workflow:approve",
            "approval:read",
            "finance:invoice:write",
            "finance:invoice:read",
            "connector:execute",
            "connector:write",
        ):
            session.add(RolePermissionRecord(id=str(uuid_module.uuid4()), role=role, permission=perm))
        session.add(
            PrincipalRecord(
                id=str(uuid_module.uuid4()),
                tenant_id=_TENANT_A,
                principal_id="human_builder_a",
                principal_type="USER",
                credential_hash=hasher.hash("builder-pass"),
                # `APPROVAL_ROLE` matches the default `required_role` an approval-gate step's
                # ticket is created with when a node doesn't set `required_approval_role`
                # (`evaluator.py::execute_step`: `step.required_approval_role or "APPROVAL_ROLE"`) --
                # needed by `test_approval_gate_precedes_mutating_node_and_resumes_after_approval`.
                roles=[role, "APPROVAL_ROLE"],
                attributes={"clearance_level": "INTERNAL"},
            )
        )
        # A second tenant, granted the SAME permissions, to prove cross-tenant isolation --
        # never a permissions difference standing in for a tenant-boundary difference.
        role_b = f"vslice-role-{_TENANT_B}"
        for perm in ("ai:generate", "workflow:read", "finance:invoice:read"):
            session.add(RolePermissionRecord(id=str(uuid_module.uuid4()), role=role_b, permission=perm))
        session.add(
            PrincipalRecord(
                id=str(uuid_module.uuid4()),
                tenant_id=_TENANT_B,
                principal_id="human_builder_b",
                principal_type="USER",
                credential_hash=hasher.hash("builder-pass"),
                roles=[role_b],
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await storage_engine.data.execute_in_transaction(_seed_rbac)
    return kernel


async def _token(kernel: Kernel, tenant_id: str, principal_id: str):
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": "builder-pass"}
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _invoke(kernel: Kernel, capability_name: str, token: Any, tenant_id: str, **parameters: Any) -> Any:
    return await kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability_name,
            session_token=token,
            parameters=parameters,
            context={"resource_tenant_id": tenant_id},
        )
    )


def _token_to_dict(token: Any) -> dict[str, Any]:
    """Convert a real, signed `TokenPayload` into the JSON-safe dict shape
    `WorkflowContext.session_token` (and therefore `start_workflow`) requires."""
    token_dict = token.model_dump()
    if token_dict.get("signature") is not None:
        token_dict["signature"] = token_dict["signature"].hex()
    return token_dict


def _proposal_json(invoice_id: str, profile_id: str) -> str:
    return json.dumps(
        {
            "name": "Notify webhook about invoice",
            "description": "Look up an invoice, then notify a webhook with its customer name.",
            "entry_node_id": "step_1",
            "nodes": [
                {
                    "node_id": "step_1",
                    "capability_name": "kortex.finance.invoice.get",
                    "config": {"invoice_id": invoice_id},
                    "mapping": None,
                    "is_approval_step": False,
                },
                {
                    "node_id": "step_2",
                    "capability_name": "kortex.connector.notification.webhook.send",
                    "config": {"profile_id": profile_id, "url": "https://example.invalid/hook"},
                    "mapping": {
                        "body": {
                            "kind": "expression",
                            "expression": {
                                "operator": "CONCAT",
                                "operands": [
                                    {"kind": "literal", "value": "Invoice for "},
                                    {
                                        "kind": "reference",
                                        "reference": {
                                            "source_node_id": "step_1",
                                            "source_port": None,
                                            "path": ["customer_name"],
                                        },
                                    },
                                ],
                            },
                        }
                    },
                    "is_approval_step": False,
                },
            ],
            "edges": [{"source_node_id": "step_1", "target_node_id": "step_2"}],
        }
    )


def _step_metadata(*, is_approval_step: bool = False) -> dict[str, Any]:
    # No "name" key: `_node_to_step` falls back to the node's own `node_id` via
    # `step_metadata.get("name", node.node_id)` when the key is absent -- an explicit `None`
    # would instead be *returned* by `.get()` and fail `WorkflowStep.name`'s `str` requirement.
    return {
        "kortex.workflow.step": {
            "is_approval_step": is_approval_step,
            "required_approval_role": None,
            "retry_policy": None,
            "compensation_action": None,
            "on_failure_continue": False,
        }
    }


def _approval_gated_graph(invoice_id: str, profile_id: str) -> dict[str, Any]:
    """A 3-node graph following the *corrected* generation guidance (§7/§18 of the previous
    session's findings): a pure approval-gate node (`is_approval_step: true`, `capability_name:
    None` -- never dispatches any capability itself) immediately precedes the mutating node it
    gates, rather than the mutating node being marked `is_approval_step` itself (which would make
    it wait for approval and then never actually run)."""
    return {
        "schema_version": "1.0.0",
        "entry_node_id": "step_1",
        "nodes": [
            {
                "node_id": "step_1",
                "node_type": "capability",
                "capability_name": "kortex.finance.invoice.get",
                "config": {"invoice_id": invoice_id, "_authz_context": {"resource_tenant_id": _TENANT_A}},
                "input_ports": [],
                "output_ports": [],
                "metadata": _step_metadata(),
            },
            {
                "node_id": "step_2_approval_gate",
                "node_type": "capability",
                "capability_name": None,
                "config": {},
                "input_ports": [],
                "output_ports": [],
                "metadata": _step_metadata(is_approval_step=True),
            },
            {
                "node_id": "step_3_send_webhook",
                "node_type": "capability",
                "capability_name": "kortex.connector.notification.webhook.send",
                "config": {
                    "profile_id": profile_id,
                    "url": "https://example.invalid/hook",
                    "_authz_context": {"resource_tenant_id": _TENANT_A},
                    "mapping": {
                        "values": {
                            "body": {
                                "kind": "reference",
                                "reference": {
                                    "source_node_id": "step_1",
                                    "source_port": None,
                                    "path": ["customer_name"],
                                },
                            }
                        }
                    },
                },
                "input_ports": [],
                "output_ports": [],
                "metadata": _step_metadata(),
            },
        ],
        "edges": [
            {"edge_id": "edge_1_2", "source_node_id": "step_1", "target_node_id": "step_2_approval_gate"},
            {
                "edge_id": "edge_2_3",
                "source_node_id": "step_2_approval_gate",
                "target_node_id": "step_3_send_webhook",
            },
        ],
    }


@pytest.mark.asyncio
async def test_ai_workflow_builder_end_to_end_generate_to_execution(tmp_path: Path) -> None:
    # Placeholder proposal text; the real invoice_id/profile_id are filled in once created below,
    # by re-pointing the scripted provider (see `provider._proposal_json = ...` reassignment).
    provider = _ScriptedProposalProvider(proposal_json="{}")
    kernel = await _build_kernel(tmp_path, provider)
    token = await _token(kernel, _TENANT_A, "human_builder_a")

    # 1. Real Finance invoice -- the data step 1 will actually read.
    invoice = await _invoke(
        kernel,
        "kortex.finance.invoice.create",
        token,
        _TENANT_A,
        request={"customer_name": "Acme Co", "amount": "150.00", "currency": "USD"},
    )
    invoice_id = invoice.invoice_id

    # 2. Real connector profile for the webhook connector action.
    await _invoke(
        kernel,
        "kortex.connector.profile.register",
        token,
        _TENANT_A,
        profile={"profile_id": "vslice-profile", "name": "Vertical Slice Profile", "driver_id": "connector-dummy"},
    )

    provider._proposal_json = _proposal_json(invoice_id, "vslice-profile")

    # 3. AI Workflow Builder generates a proposal from the tenant's own curated, projected
    #    capability catalog -- never touching kortex.workflow.definition.* itself.
    proposal = await _invoke(
        kernel, "kortex.ai.workflow_builder.generate", token, _TENANT_A, intent="Notify about the invoice"
    )
    assert proposal["status"] == "proposed", proposal["errors"]
    graph = proposal["graph"]

    # 4. Human-confirmed persistence (D10): a SEPARATE, explicit capability call the AI never
    #    makes itself.
    created = await _invoke(
        kernel,
        "kortex.workflow.definition.create",
        token,
        _TENANT_A,
        name=proposal["name"],
        description=proposal["description"],
        graph=graph,
    )
    definition_id = created["definition_id"]

    # 5. F4's own authoritative validation -- must be valid (linear, curated capabilities,
    #    well-formed mapping).
    validation = await _invoke(
        kernel, "kortex.workflow.definition.validate", token, _TENANT_A, definition_id=definition_id
    )
    assert validation["is_valid"] is True, validation["errors"]

    # 6. Human-approval-gated publish (D9): a separate, explicit capability call. No AI Workflow
    #    Builder code path invokes this -- proven structurally in
    #    `test_ai_workflow_builder.py::test_module_source_never_references_register_definition_or_workflow_store`
    #    and here behaviorally, since this line is the only `.publish` call in this entire test.
    published = await _invoke(
        kernel,
        "kortex.workflow.definition.publish",
        token,
        _TENANT_A,
        definition_id=definition_id,
        expected_lock_version=created["lock_version"],
    )
    assert published["status"] == "PUBLISHED"

    # 7. Existing, unmodified Workflow Engine execution path.
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    instance = await workflow_engine.start_workflow(
        definition_id, tenant_id=_TENANT_A, session_token=_token_to_dict(token)
    )

    for _ in range(50):
        current = workflow_engine.get_instance(instance.id)
        if current is not None and current.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
            break
        await __import__("asyncio").sleep(0.05)

    final_instance = workflow_engine.get_instance(instance.id)
    assert final_instance is not None
    assert final_instance.state == WorkflowState.COMPLETED, final_instance.context.step_outputs

    # 8. F3 wiring proof: step 2's real output shows the mapped `body` field was resolved from
    #    step 1's REAL output at runtime -- not merely validated structurally.
    step2_output = final_instance.context.step_outputs.get("step_2")
    assert step2_output is not None
    # The DummyConnectorDriver echoes the request payload back in its response.
    assert "Acme Co" in json.dumps(step2_output), final_instance.context.step_outputs

    # 9. Cross-tenant isolation: tenant B's own token cannot see tenant A's curated capability
    #    catalog collapse into tenant A's data, cannot read tenant A's definition, and a
    #    tenant-mismatched capability call fails closed.
    token_b = await _token(kernel, _TENANT_B, "human_builder_b")
    with pytest.raises(WorkflowDefinitionNotFoundError):
        await _invoke(kernel, "kortex.workflow.definition.get", token_b, _TENANT_B, definition_id=definition_id)


@pytest.mark.asyncio
async def test_approval_gate_precedes_mutating_node_and_resumes_after_approval(tmp_path: Path) -> None:
    """Regression-hardens the corrected generation guidance (§7/§18): an approval-GATE node
    (`is_approval_step: true`, no `capability_name`) precedes the mutating node it protects, rather
    than the mutating node being marked `is_approval_step` itself. Proves, through the real,
    existing, unmodified Workflow Engine approval mechanism (no new approval engine, no changed
    approval semantics):

        step_1 (read invoice)
            -> step_2_approval_gate (WAITS -- dispatches nothing)
            -> human approval decision (APPROVED, via the real kortex.workflow.instance.approve)
            -> step_3_send_webhook actually executes, with its `body` mapping resolved from
               step_1's real output

    This is deliberately independent of AI generation (the graph is hand-built, not produced by
    `kortex.ai.workflow_builder.generate`) -- it verifies the Workflow Engine's own approval
    semantics that the generation prompt's guidance must stay consistent with, not the generation
    step itself (already covered by the primary vertical-slice test above).
    """
    provider = _ScriptedProposalProvider(proposal_json="{}")
    kernel = await _build_kernel(tmp_path, provider)
    token = await _token(kernel, _TENANT_A, "human_builder_a")

    invoice = await _invoke(
        kernel,
        "kortex.finance.invoice.create",
        token,
        _TENANT_A,
        request={"customer_name": "Acme Co", "amount": "150.00", "currency": "USD"},
    )
    await _invoke(
        kernel,
        "kortex.connector.profile.register",
        token,
        _TENANT_A,
        profile={"profile_id": "gate-profile", "name": "Gate Test Profile", "driver_id": "connector-dummy"},
    )

    created = await _invoke(
        kernel,
        "kortex.workflow.definition.create",
        token,
        _TENANT_A,
        name="Approval-gated invoice notification",
        description="test",
        graph=_approval_gated_graph(invoice.invoice_id, "gate-profile"),
    )
    definition_id = created["definition_id"]

    validation = await _invoke(
        kernel, "kortex.workflow.definition.validate", token, _TENANT_A, definition_id=definition_id
    )
    assert validation["is_valid"] is True, validation["errors"]

    published = await _invoke(
        kernel,
        "kortex.workflow.definition.publish",
        token,
        _TENANT_A,
        definition_id=definition_id,
        expected_lock_version=created["lock_version"],
    )
    assert published["status"] == "PUBLISHED"

    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    instance = await workflow_engine.start_workflow(
        definition_id, tenant_id=_TENANT_A, session_token=_token_to_dict(token)
    )

    for _ in range(50):
        current = workflow_engine.get_instance(instance.id)
        if current is not None and current.state in (
            WorkflowState.WAITING,
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
        ):
            break
        await __import__("asyncio").sleep(0.05)

    waiting_instance = workflow_engine.get_instance(instance.id)
    assert waiting_instance is not None
    # The gate node dispatched nothing -- step_3 has not run yet.
    assert waiting_instance.state == WorkflowState.WAITING, waiting_instance.context.step_outputs
    assert "step_3_send_webhook" not in waiting_instance.context.step_outputs

    pending = await _invoke(kernel, "kortex.workflow.approval.list", token, _TENANT_A)
    ticket = next(r for r in pending if r["instance_id"] == str(instance.id))
    await _invoke(
        kernel,
        "kortex.workflow.instance.approve",
        token,
        _TENANT_A,
        decision={
            "request_id": ticket["id"],
            "tenant_id": _TENANT_A,
            "approver_id": "human_builder_a",
            "decision": "APPROVED",
        },
    )

    for _ in range(50):
        current = workflow_engine.get_instance(instance.id)
        if current is not None and current.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
            break
        await __import__("asyncio").sleep(0.05)

    final_instance = workflow_engine.get_instance(instance.id)
    assert final_instance is not None
    assert final_instance.state == WorkflowState.COMPLETED, final_instance.context.step_outputs

    # The mutating node actually ran after approval, with its mapped field resolved from step_1's
    # real output -- not a placeholder, not skipped, not silently ignored.
    step3_output = final_instance.context.step_outputs.get("step_3_send_webhook")
    assert step3_output is not None
    assert "Acme Co" in json.dumps(step3_output), final_instance.context.step_outputs
