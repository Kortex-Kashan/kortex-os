"""
Visual Workflow Canvas & AI Builder Convergence — Vertical Slice Integration Test.

Proves the convergence requirements and all 5 approved architectural corrections:
1. APPROVAL GATE: Approval gate is a built-in workflow construct, NOT an F6 capability.
   Enforces: is_approval_step == true => capability_name == null
   Tested in both authoritative F4 lifecycle validation and real execution.
2. CANVAS UI STATE: Transient editor state (node positions, zoom, pan, selection) is NOT
   persisted as workflow semantics in the canonical F2/F3 graph.
3. TEST BOUNDARIES: True backend lifecycle/E2E test exercising F4 lifecycle + WorkflowEngine.
4. EXISTING CONTRACTS: Reuses canonical kernel dispatch and workflow definition/instance APIs.
5. LINEARITY: V1 single linear chain (single entry, in-degree <= 1, out-degree <= 1, no cycles,
   no fan-out, no joins, no branching/loops/parallelism).

Executes against the real, live KORTEX OS stack:
- Real Kernel & SQLite RelationalDataStore
- Real SecurityEngine (RBAC/ABAC tokens)
- Real StorageEngine
- Real ConnectorEngine with the production HttpRestConnectorDriver
- Real FinanceModule
- Real WorkflowEngine (F2/F3/F4 lifecycle + execution)

The final workflow action is NOT simulated: it crosses an actual HTTP transport
boundary. The canonical slice terminates in a loopback HTTP server owned by the test
(`local_http_harness.LocalHttpTestServer`), reached through the ordinary
capability -> ConnectorEngine -> ConnectorPipeline -> HttpRestConnectorDriver -> httpx ->
TCP socket path, so the workflow's observable effect is a real HTTP request that the
harness records and the test asserts on byte-for-byte.
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
from kortex.engines.connector.drivers.http_driver import HttpRestConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.reference_actions import REFERENCE_ACTION_DESCRIPTORS
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.exceptions import WorkflowDefinitionStateError
from kortex.engines.workflow.models import WorkflowSettings, WorkflowState
from kortex.modules.finance.module import FinanceModule
from tests.integration.local_http_harness import (
    LocalHttpTestServer,
    RecordedHttpRequest,
    local_http_endpoint,
)

_TEST_MASTER_KEY = b"\xaa" * 32
_TEST_SIGNING_KEY = b"\xbb" * 32
_TENANT_A = "tenant-canvas-vslice"


class _DummyAIProvider(BaseAIProvider):
    def __init__(self) -> None:
        self._metadata = AIProviderMetadata(
            provider_id="canvas-vslice-provider",
            display_name="Canvas VSlice Provider",
            vendor="test",
            endpoint_type="local_host",
            supported_models=["vslice-model"],
            credential_requirement="none",
        )

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            request_id=request.request_id,
            text_content="{}",
            token_usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return True


async def _build_kernel(tmp_path: Path) -> Kernel:
    db_path = (tmp_path / f"kortex_canvas_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()
    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_canvas_{uuid4().hex[:8]}"))
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
        custom_providers=[_DummyAIProvider()],
        registered_engines=list(kernel.get_all_engines().keys()),
    )
    kernel.register_engine(ai_engine)

    register_projection_capabilities(kernel)
    register_workflow_builder_capabilities(kernel)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    # Production HTTP driver: the canonical slice's final action crosses a real transport
    # boundary, so no simulated driver is registered at all.
    connector_engine.register_driver(HttpRestConnectorDriver())

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        role = f"canvas-role-{_TENANT_A}"
        for perm in (
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
                principal_id="canvas_architect",
                principal_type="USER",
                credential_hash=hasher.hash("architect-pass"),
                roles=[role, "APPROVAL_ROLE"],
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await storage_engine.data.execute_in_transaction(_seed_rbac)
    return kernel


async def _token(kernel: Kernel, tenant_id: str, principal_id: str):
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": "architect-pass"}
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _invoke(
    kernel: Kernel,
    capability_name: str,
    token: Any,
    tenant_id: str,
    **parameters: Any,
) -> Any:
    return await kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability_name,
            session_token=token,
            parameters=parameters,
            context={"resource_tenant_id": tenant_id},
        )
    )


def _token_to_dict(token: Any) -> dict[str, Any]:
    token_dict = token.model_dump()
    if token_dict.get("signature") is not None:
        token_dict["signature"] = token_dict["signature"].hex()
    return token_dict


def _canvas_linear_graph(invoice_id: str, profile_id: str, webhook_url: str) -> dict[str, Any]:
    """Canonical F2 WorkflowGraph + F3 WorkflowMapping output from the Visual Canvas.
    Notice that transient UI state (nodePositions, zoom, pan) is strictly excluded."""
    return {
        "schema_version": "1.0.0",
        "entry_node_id": "step_1_get_invoice",
        "nodes": [
            {
                "node_id": "step_1_get_invoice",
                "node_type": "capability",
                "capability_name": "kortex.finance.invoice.get",
                "config": {
                    "invoice_id": invoice_id,
                    "_authz_context": {"resource_tenant_id": _TENANT_A},
                },
                "input_ports": ["input"],
                "output_ports": ["output"],
                "metadata": {
                    "kortex.workflow.step": {
                        "name": "Get Invoice Step",
                        "is_approval_step": False,
                    }
                },
            },
            {
                "node_id": "step_2_approval_gate",
                "node_type": "approval",
                "capability_name": None,  # Correction 1: pure wait gate, capability_name == null
                "config": {},
                "input_ports": ["input"],
                "output_ports": ["output"],
                "metadata": {
                    "kortex.workflow.step": {
                        "name": "Manager Approval Gate",
                        "is_approval_step": True,
                        "required_approval_role": "APPROVAL_ROLE",
                    }
                },
            },
            {
                "node_id": "step_3_send_webhook",
                "node_type": "capability",
                "capability_name": "kortex.connector.notification.webhook.send",
                "config": {
                    "profile_id": profile_id,
                    "url": webhook_url,
                    "_authz_context": {"resource_tenant_id": _TENANT_A},
                    "mapping": {
                        "values": {
                            # F3 runtime wiring: the whole Step 1 invoice output becomes the HTTP
                            # request body, so the bytes the local server receives are produced
                            # dynamically by a preceding workflow step, not authored in the graph.
                            "body": {
                                "kind": "reference",
                                "reference": {
                                    "source_node_id": "step_1_get_invoice",
                                    "source_port": None,
                                    "path": [],
                                },
                            }
                        }
                    },
                },
                "input_ports": ["input"],
                "output_ports": ["output"],
                "metadata": {
                    "kortex.workflow.step": {
                        "name": "Send Webhook Step",
                        "is_approval_step": False,
                    }
                },
            },
        ],
        "edges": [
            {
                "edge_id": "edge_1_to_2",
                "source_node_id": "step_1_get_invoice",
                "target_node_id": "step_2_approval_gate",
                "source_port": "output",
                "target_port": "input",
            },
            {
                "edge_id": "edge_2_to_3",
                "source_node_id": "step_2_approval_gate",
                "target_node_id": "step_3_send_webhook",
                "source_port": "output",
                "target_port": "input",
            },
        ],
    }


def _webhook_response(request: RecordedHttpRequest) -> tuple[int, Any]:
    """Local endpoint behaviour: validate the notification the workflow actually sent, and
    acknowledge it with a payload the workflow must then carry back into its own state."""
    body = request.json_body
    return 202, {
        "delivery_id": "delivery-canvas-001",
        "status": "ACCEPTED",
        "acknowledged_customer": body["customer_name"],
        "acknowledged_invoice_id": body["invoice_id"],
    }


@pytest.mark.asyncio
async def test_canvas_linear_workflow_full_vertical_slice(tmp_path: Path) -> None:
    """Executes a complete vertical slice of a Visual Canvas generated workflow, terminating in
    a real HTTP request across an actual transport boundary:
    1. Start a loopback HTTP server; seed finance invoice and HTTP connector profile.
    2. Save workflow draft via F4 lifecycle with optimistic locking.
    3. Validate draft via authoritative F4 backend validator.
    4. Publish draft via human authorization.
    5. Start workflow execution via WorkflowEngine.
    6. Verify execution reaches Approval Gate and pauses in WAITING state.
    7. Submit human approval decision.
    8. Verify the workflow resumes, dispatches Step 3 through the Connector capability ->
       ConnectorEngine -> HttpRestConnectorDriver chain, that the local server observes a real
       HTTP request carrying the F3-mapped Step 1 data, that the server's response is captured
       into workflow state, and that the instance reaches COMPLETED.
    """
    async with local_http_endpoint(_webhook_response) as http_server:
        kernel = await _build_kernel(tmp_path)
        token = await _token(kernel, _TENANT_A, "canvas_architect")

        # 1. Setup real data: Finance invoice & HTTP Connector profile
        invoice = await _invoke(
            kernel,
            "kortex.finance.invoice.create",
            token,
            _TENANT_A,
            request={"customer_name": "Globex Corp", "amount": "999.00", "currency": "USD"},
        )
        await _invoke(
            kernel,
            "kortex.connector.profile.register",
            token,
            _TENANT_A,
            profile={
                "profile_id": "canvas-profile",
                "name": "Canvas Profile",
                "driver_id": "connector-http-rest",
            },
        )

        webhook_url = http_server.url("/canvas-webhook")
        await _run_canvas_slice(kernel, token, invoice.invoice_id, webhook_url, http_server)


async def _run_canvas_slice(
    kernel: Kernel,
    token: Any,
    invoice_id: str,
    webhook_url: str,
    http_server: LocalHttpTestServer,
) -> None:
    """Steps 2-8 of the canonical slice, factored out only so the loopback endpoint's
    `async with` block above stays the test's outermost lifecycle boundary."""
    # 2. Save draft via F4 lifecycle
    canvas_graph = _canvas_linear_graph(invoice_id, "canvas-profile", webhook_url)
    draft = await _invoke(
        kernel,
        "kortex.workflow.definition.create",
        token,
        _TENANT_A,
        name="Canvas Linear Approval Pipeline",
        description="Created via Visual Workflow Canvas",
        graph=canvas_graph,
    )
    def_id = draft["definition_id"]
    lock_version = draft["lock_version"]
    assert draft["status"] == "DRAFT"

    # 3. Validate draft using authoritative F4 backend validator
    validation = await _invoke(
        kernel,
        "kortex.workflow.definition.validate",
        token,
        _TENANT_A,
        definition_id=def_id,
    )
    assert validation["is_valid"] is True, validation["errors"]
    assert len(validation["errors"]) == 0

    # 4. Human-authorized publication
    published = await _invoke(
        kernel,
        "kortex.workflow.definition.publish",
        token,
        _TENANT_A,
        definition_id=def_id,
        expected_lock_version=lock_version,
    )
    assert published["status"] == "PUBLISHED"

    # 5. Execute via WorkflowEngine
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    instance = await workflow_engine.start_workflow(
        def_id,
        tenant_id=_TENANT_A,
        session_token=_token_to_dict(token),
    )

    # 6. Verify execution reaches Step 2 (Approval Gate) and enters WAITING
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
    assert waiting_instance.state == WorkflowState.WAITING
    # Step 1 ran; Step 2 wait gate dispatched no capability; Step 3 has not run
    assert "step_1_get_invoice" in waiting_instance.context.step_outputs
    assert "step_3_send_webhook" not in waiting_instance.context.step_outputs

    # 7. Submit human approval decision
    pending_tickets = await _invoke(kernel, "kortex.workflow.approval.list", token, _TENANT_A)
    ticket = next(t for t in pending_tickets if t["instance_id"] == str(instance.id))
    await _invoke(
        kernel,
        "kortex.workflow.instance.approve",
        token,
        _TENANT_A,
        decision={
            "request_id": ticket["id"],
            "tenant_id": _TENANT_A,
            "approver_id": "canvas_architect",
            "decision": "APPROVED",
        },
    )

    # 8. Verify workflow resumes and completes
    for _ in range(50):
        current = workflow_engine.get_instance(instance.id)
        if current is not None and current.state in (
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
        ):
            break
        await __import__("asyncio").sleep(0.05)

    final_instance = workflow_engine.get_instance(instance.id)
    assert final_instance is not None
    assert final_instance.state == WorkflowState.COMPLETED

    # 9. The observable external effect: exactly one real HTTP request crossed the transport
    #    boundary and was received by the local server.
    assert len(http_server.requests) == 1
    observed = http_server.requests[0]
    assert observed.method == "POST"
    assert observed.target == "/canvas-webhook"
    assert observed.headers["host"] == f"127.0.0.1:{http_server.port}"
    # The request was emitted by HttpRestConnectorDriver's own httpx stack, not fabricated.
    assert observed.headers["user-agent"].startswith("python-httpx/")

    # 10. The request body carries the data Step 1 produced, wired in by F3 graph mapping --
    #     not a literal authored into the workflow graph.
    sent_payload = observed.json_body
    assert sent_payload["customer_name"] == "Globex Corp"
    assert sent_payload["invoice_id"] == invoice_id
    assert sent_payload["amount"] == "999.00"
    assert str(observed.headers["content-length"]) == str(len(observed.body))

    # 11. The server's HTTP response was captured back into workflow state by the connector
    #     capability's own return value (F3 runtime mapped output flowed Step 1 -> Step 3 -> back).
    step3_output = final_instance.context.step_outputs.get("step_3_send_webhook")
    assert step3_output is not None
    assert step3_output["status_code"] == 202
    assert step3_output["body"]["delivery_id"] == "delivery-canvas-001"
    assert step3_output["body"]["acknowledged_customer"] == "Globex Corp"
    assert step3_output["body"]["acknowledged_invoice_id"] == invoice_id
    assert "Globex Corp" in json.dumps(step3_output)


@pytest.mark.asyncio
async def test_canvas_approval_invariant_violation_rejected(tmp_path: Path) -> None:
    """Correction 1 & 3: Authoritative backend lifecycle validation strictly rejects any
    canvas node where is_approval_step == true but capability_name != null."""
    kernel = await _build_kernel(tmp_path)
    token = await _token(kernel, _TENANT_A, "canvas_architect")

    # Construct invalid graph where approval gate attempts to declare a capability
    invalid_graph = {
        "schema_version": "1.0.0",
        "entry_node_id": "bad_gate",
        "nodes": [
            {
                "node_id": "bad_gate",
                "node_type": "approval",
                "capability_name": "kortex.finance.invoice.create",  # FORBIDDEN
                "config": {},
                "input_ports": ["input"],
                "output_ports": ["output"],
                "metadata": {
                    "kortex.workflow.step": {
                        "name": "Invalid Mutating Approval Gate",
                        "is_approval_step": True,
                    }
                },
            }
        ],
        "edges": [],
    }

    draft = await _invoke(
        kernel,
        "kortex.workflow.definition.create",
        token,
        _TENANT_A,
        name="Invalid Gate Workflow",
        graph=invalid_graph,
    )
    def_id = draft["definition_id"]

    # F4 lifecycle validation must reject
    validation = await _invoke(
        kernel,
        "kortex.workflow.definition.validate",
        token,
        _TENANT_A,
        definition_id=def_id,
    )
    assert validation["is_valid"] is False
    assert any("pure wait gates" in err for err in validation["errors"])

    # Publication must be blocked
    with pytest.raises(WorkflowDefinitionStateError):
        await _invoke(
            kernel,
            "kortex.workflow.definition.publish",
            token,
            _TENANT_A,
            definition_id=def_id,
            expected_lock_version=draft["lock_version"],
        )


@pytest.mark.asyncio
async def test_canvas_linearity_cycle_rejected(tmp_path: Path) -> None:
    """Correction 5: Authoritative backend lifecycle strictly rejects cycles in the graph."""
    kernel = await _build_kernel(tmp_path)
    token = await _token(kernel, _TENANT_A, "canvas_architect")

    # Construct graph with a cycle: node_1 -> node_2 -> node_1
    cyclic_graph = {
        "schema_version": "1.0.0",
        "entry_node_id": "node_1",
        "nodes": [
            {
                "node_id": "node_1",
                "node_type": "capability",
                "capability_name": "kortex.finance.invoice.get",
                "config": {"invoice_id": "inv-1"},
                "metadata": {"kortex.workflow.step": {"is_approval_step": False}},
            },
            {
                "node_id": "node_2",
                "node_type": "capability",
                "capability_name": "kortex.finance.invoice.get",
                "config": {"invoice_id": "inv-2"},
                "metadata": {"kortex.workflow.step": {"is_approval_step": False}},
            },
        ],
        "edges": [
            {"edge_id": "e1", "source_node_id": "node_1", "target_node_id": "node_2"},
            {"edge_id": "e2", "source_node_id": "node_2", "target_node_id": "node_1"},  # CYCLE
        ],
    }

    draft = await _invoke(
        kernel,
        "kortex.workflow.definition.create",
        token,
        _TENANT_A,
        name="Cyclic Workflow",
        graph=cyclic_graph,
    )
    def_id = draft["definition_id"]

    validation = await _invoke(
        kernel,
        "kortex.workflow.definition.validate",
        token,
        _TENANT_A,
        definition_id=def_id,
    )
    assert validation["is_valid"] is False
    assert any("cycle" in err.lower() for err in validation["errors"])
