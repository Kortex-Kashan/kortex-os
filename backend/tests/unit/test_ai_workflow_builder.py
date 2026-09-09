"""
Unit tests for the AI Workflow Builder (`kortex.api.workflow_builder`).

Covers: structured-proposal parsing, curated-capability allowlist enforcement, secret-like
key rejection, structural pre-validation reuse of F2/F3 validators, the `is_approval_step`/
`mapping` graph<->step round trip, tenant-authorized curated-catalog projection, and the
architectural bypass-prevention proof (never imports Workflow/Connector engine internals,
never constructs a `kortex.workflow.definition.*` capability request).
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

import pytest

from kortex.api import workflow_builder
from kortex.core.dispatch import CapabilityExecutionContext, CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.core.projection import CapabilityProjection
from kortex.engines.registry.engine import RegistryEngine
from kortex.engines.security.authorization import AuthorizationEngine
from kortex.engines.security.models import PrincipalType, RolePermissionRecord, SecurityPrincipal
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.graph_compat import graph_to_steps


def _principal(
    tenant_id: str = "tenant-a", roles: list[str] | None = None, clearance_level: str = "INTERNAL"
) -> SecurityPrincipal:
    return SecurityPrincipal(
        principal_id=f"user-{uuid.uuid4().hex[:6]}",
        principal_type=PrincipalType.USER,
        tenant_id=tenant_id,
        roles=roles or [],
        attributes={"clearance_level": clearance_level},
    )


def _execution_context(principal: SecurityPrincipal, capability_name: str = "kortex.ai.workflow_builder.generate"):
    return CapabilityExecutionContext(
        request_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        capability_name=capability_name,
        principal=principal,
        tenant_id=principal.tenant_id,
    )


# ---------------------------------------------------------------------------
# Pure-function tests: parsing, allowlist enforcement, secret scan
# ---------------------------------------------------------------------------


def test_strip_code_fence_removes_markdown_wrapper() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert workflow_builder._strip_code_fence(raw) == '{"a": 1}'


def test_strip_code_fence_passthrough_when_no_fence() -> None:
    raw = '{"a": 1}'
    assert workflow_builder._strip_code_fence(raw) == '{"a": 1}'


def test_parse_proposal_rejects_invalid_json() -> None:
    with pytest.raises(workflow_builder.WorkflowBuilderError):
        workflow_builder._parse_proposal("not json at all")


def test_parse_proposal_rejects_non_object_json() -> None:
    with pytest.raises(workflow_builder.WorkflowBuilderError):
        workflow_builder._parse_proposal("[1, 2, 3]")


def test_scan_for_secret_like_values_flags_suspicious_key_names() -> None:
    errors = workflow_builder._scan_for_secret_like_values("n1", {"api_key": "x", "url": "https://x"})
    assert len(errors) == 1
    assert "api_key" in errors[0]


def test_scan_for_secret_like_values_clean_config_has_no_errors() -> None:
    assert workflow_builder._scan_for_secret_like_values("n1", {"invoice_id": "INV-1"}) == []


def _valid_two_node_payload() -> dict[str, Any]:
    return {
        "name": "Notify about invoice",
        "description": "test",
        "entry_node_id": "step_1",
        "nodes": [
            {
                "node_id": "step_1",
                "capability_name": "kortex.finance.invoice.get",
                "config": {"invoice_id": "INV-1"},
                "mapping": None,
                "is_approval_step": False,
            },
            {
                "node_id": "step_2",
                "capability_name": "kortex.connector.notification.webhook.send",
                "config": {"profile_id": "profile-1"},
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
                "is_approval_step": True,
            },
        ],
        "edges": [{"source_node_id": "step_1", "target_node_id": "step_2"}],
    }


def test_proposal_to_graph_and_validate_accepts_well_formed_curated_proposal() -> None:
    graph, extra_errors = workflow_builder._proposal_to_graph(_valid_two_node_payload(), "tenant-a")
    assert extra_errors == []
    is_valid, errors, _warnings = workflow_builder._validate_proposal(graph, extra_errors)
    assert is_valid is True, errors


def test_proposal_to_graph_rejects_capability_outside_curated_allowlist() -> None:
    payload = _valid_two_node_payload()
    payload["nodes"][0]["capability_name"] = "kortex.security.secret.put"
    _graph, extra_errors = workflow_builder._proposal_to_graph(payload, "tenant-a")
    assert any("not in the curated" in e for e in extra_errors)


def test_proposal_to_graph_rejects_secret_like_config_key() -> None:
    payload = _valid_two_node_payload()
    payload["nodes"][0]["config"]["api_key"] = "sk-should-not-be-here"
    _graph, extra_errors = workflow_builder._proposal_to_graph(payload, "tenant-a")
    assert any("secret-like" in e for e in extra_errors)


def test_validate_proposal_rejects_branching_graph() -> None:
    payload = _valid_two_node_payload()
    payload["nodes"].append(
        {
            "node_id": "step_3",
            "capability_name": "kortex.finance.invoice.get",
            "config": {"invoice_id": "INV-2"},
            "mapping": None,
            "is_approval_step": False,
        }
    )
    # step_1 now has two outgoing edges -> branching, must be rejected (v1 linear-only)
    payload["edges"].append({"source_node_id": "step_1", "target_node_id": "step_3"})
    graph, extra_errors = workflow_builder._proposal_to_graph(payload, "tenant-a")
    is_valid, errors, _warnings = workflow_builder._validate_proposal(graph, extra_errors)
    assert is_valid is False
    assert any("not linear" in e for e in errors)


def test_validate_proposal_rejects_invalid_mapping_reference() -> None:
    payload = _valid_two_node_payload()
    # step_1 (the entry node) illegally references step_2 (not an ancestor -- a later node).
    payload["nodes"][0]["mapping"] = {
        "some_field": {
            "kind": "reference",
            "reference": {"source_node_id": "step_2", "source_port": None, "path": ["x"]},
        }
    }
    graph, extra_errors = workflow_builder._proposal_to_graph(payload, "tenant-a")
    is_valid, errors, _warnings = workflow_builder._validate_proposal(graph, extra_errors)
    assert is_valid is False
    assert any("Mapping validation failed" in e for e in errors)


def test_is_approval_step_and_mapping_round_trip_through_graph_to_steps() -> None:
    """Proves the private-string-key convention this module shares with `graph_compat.py`
    (`kortex.workflow.step` / `mapping`) is exactly correct -- not just assumed."""
    graph, extra_errors = workflow_builder._proposal_to_graph(_valid_two_node_payload(), "tenant-a")
    assert extra_errors == []
    steps = graph_to_steps(graph)
    assert steps[0].id == "step_1"
    assert steps[0].mapping is None
    assert steps[1].id == "step_2"
    assert steps[1].is_approval_step is True
    assert steps[1].mapping == {
        "values": {
            "body": {
                "kind": "expression",
                "expression": {
                    "operator": "CONCAT",
                    "operands": [
                        {"kind": "literal", "value": "Invoice for "},
                        {
                            "kind": "reference",
                            "reference": {"source_node_id": "step_1", "source_port": None, "path": ["customer_name"]},
                        },
                    ],
                },
            }
        }
    }
    assert "profile_id" in steps[1].parameters
    assert "mapping" not in steps[1].parameters  # never leaks into literal dispatch parameters


# ---------------------------------------------------------------------------
# Curated catalog: real Kernel + RegistryEngine + real AuthorizationEngine
# ---------------------------------------------------------------------------


async def _make_test_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Kernel, StorageEngine, AuthorizationEngine]:
    db_file = tmp_path / f"test_{uuid.uuid4().hex[:8]}.db"
    storage_dir = tmp_path / f"storage_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(storage_dir))

    kernel = Kernel()
    storage = StorageEngine(base_directory=str(storage_dir))
    kernel.register_engine(storage)
    await storage.initialize(kernel)
    await storage.start()
    await kernel.db.connect()
    await kernel.db.create_all_tables()
    auth_engine = AuthorizationEngine(data_store=storage.data)
    return kernel, storage, auth_engine


async def _grant_permission(data_store: Any, role: str, permission: str) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async def _action(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))
        await session.flush()

    await data_store.execute_in_transaction(_action)


class _MockSecurityEngine:
    def __init__(self, auth_engine: AuthorizationEngine) -> None:
        self.authorization_engine = auth_engine


@pytest.mark.asyncio
async def test_curated_catalog_is_intersection_of_curated_and_tenant_authorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel, storage, auth_engine = await _make_test_env(tmp_path, monkeypatch)
    try:
        registry: RegistryEngine = kernel._registry_engine
        registry.register_capability(
            name="kortex.finance.invoice.get",
            description="Read one invoice",
            provider="finance",
            required_permissions=["finance:invoice:read"],
            requires_authentication=True,
            parameters_schema={"type": "object", "properties": {"invoice_id": {"type": "string"}}},
            returns_schema={"type": "object"},
            is_read_only=True,
        )
        # Curated but NOT authorized for this tenant (no permission granted).
        registry.register_capability(
            name="kortex.connector.notification.webhook.send",
            description="Send a webhook",
            provider="connector",
            required_permissions=["connector:execute"],
            requires_authentication=True,
            parameters_schema={"type": "object"},
            returns_schema={"type": "object"},
            is_read_only=False,
        )
        # Authorized but NOT curated -- must never appear.
        registry.register_capability(
            name="kortex.security.secret.put",
            description="Write a secret",
            provider="security",
            required_permissions=[],
            requires_authentication=True,
        )

        role = f"role-{uuid.uuid4().hex[:8]}"
        await _grant_permission(storage.data, role, "finance:invoice:read")
        principal = _principal("tenant-a", roles=[role])
        context = _execution_context(principal)

        # `CapabilityProjection._resolve_security_engine` resolves via `kernel.get_engine("security")`
        # when no explicit `security_engine=` was injected at construction time (which
        # `workflow_builder._curated_catalog` does not do) -- match that real resolution path.
        kernel.get_engine = lambda name: _MockSecurityEngine(auth_engine) if name == "security" else None  # type: ignore[method-assign]

        catalog = await workflow_builder._curated_catalog(kernel, context)
        names = {entry["capability_name"] for entry in catalog}

        assert names == {"kortex.finance.invoice.get"}
    finally:
        await kernel.db.disconnect()


# ---------------------------------------------------------------------------
# End-to-end generate() with a fake nested-dispatch Kernel: proves the exact
# nested CapabilityRequest shape, and that no F4 mutating capability is ever
# invoked by this module (bypass-prevention, direct behavioral proof).
# ---------------------------------------------------------------------------


class _FakeLLMResponse:
    def __init__(self, text_content: str) -> None:
        self.text_content = text_content


class _FakeKernelForGenerate:
    def __init__(self, llm_text: str, catalog_descriptors: list[Any]) -> None:
        self.invoked: list[CapabilityRequest] = []
        self._llm_text = llm_text
        self._catalog_descriptors = catalog_descriptors

    async def invoke_capability(self, request: CapabilityRequest) -> Any:
        self.invoked.append(request)
        if request.capability_name == "kortex.ai.response.generate":
            return _FakeLLMResponse(self._llm_text)
        raise AssertionError(f"Unexpected nested capability invocation: {request.capability_name}")

    def get_capability(self, name: str) -> Any:
        for d in self._catalog_descriptors:
            if d.name == name:
                return d
        raise LookupError(name)


@pytest.mark.asyncio
async def test_generate_never_invokes_any_workflow_definition_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct behavioral proof for D9/§14: no matter what the LLM returns, this module never
    constructs a `CapabilityRequest` targeting any `kortex.workflow.definition.*` capability."""
    from kortex.engines.registry.engine import CapabilityDescriptor

    descriptor = CapabilityDescriptor(
        name="kortex.finance.invoice.get",
        description="Read one invoice",
        provider="finance",
        parameters_schema={"type": "object", "properties": {"invoice_id": {"type": "string"}}},
        returns_schema={"type": "object"},
        is_read_only=True,
    )

    async def _fake_project_capabilities(self: Any, identity: Any, **kwargs: Any) -> list[Any]:
        return [descriptor]

    monkeypatch.setattr(CapabilityProjection, "project_capabilities", _fake_project_capabilities)

    llm_text = (
        '{"name": "n", "description": "d", "entry_node_id": "step_1", '
        '"nodes": [{"node_id": "step_1", "capability_name": "kortex.finance.invoice.get", '
        '"config": {"invoice_id": "INV-1"}, "mapping": null, "is_approval_step": false}], "edges": []}'
    )
    fake_kernel = _FakeKernelForGenerate(llm_text, [descriptor])
    principal = _principal("tenant-a")
    context = _execution_context(principal)
    context = context.model_copy(update={"session_token": None})

    result = await workflow_builder.generate_workflow_draft_proposal(
        intent="get invoice INV-1",
        execution_context=context,
        kernel=fake_kernel,  # type: ignore[arg-type]
    )

    assert result["status"] == "proposed", result["errors"]
    assert len(fake_kernel.invoked) == 1
    assert fake_kernel.invoked[0].capability_name == "kortex.ai.response.generate"
    assert not any(req.capability_name.startswith("kortex.workflow.definition.") for req in fake_kernel.invoked)


@pytest.mark.asyncio
async def test_generate_reports_generation_failed_on_hallucinated_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    descriptor_name = "kortex.finance.invoice.get"

    async def _fake_project_capabilities(self: Any, identity: Any, **kwargs: Any) -> list[Any]:
        from kortex.engines.registry.engine import CapabilityDescriptor

        return [
            CapabilityDescriptor(
                name=descriptor_name,
                description="Read one invoice",
                provider="finance",
                parameters_schema={"type": "object"},
                returns_schema={"type": "object"},
                is_read_only=True,
            )
        ]

    monkeypatch.setattr(CapabilityProjection, "project_capabilities", _fake_project_capabilities)

    llm_text = (
        '{"name": "n", "description": "d", "entry_node_id": "step_1", '
        '"nodes": [{"node_id": "step_1", "capability_name": "kortex.made.up.capability", '
        '"config": {}, "mapping": null, "is_approval_step": false}], "edges": []}'
    )
    fake_kernel = _FakeKernelForGenerate(llm_text, [])
    context = _execution_context(_principal("tenant-a")).model_copy(update={"session_token": None})

    result = await workflow_builder.generate_workflow_draft_proposal(
        intent="do something",
        execution_context=context,
        kernel=fake_kernel,  # type: ignore[arg-type]
    )

    assert result["status"] == "generation_failed"
    assert any("not in the curated" in e for e in result["errors"])
    assert not any(req.capability_name.startswith("kortex.workflow.definition.") for req in fake_kernel.invoked)


# ---------------------------------------------------------------------------
# Architectural bypass-prevention proof (master prompt §14): static source scan
# ---------------------------------------------------------------------------


def test_module_never_imports_workflow_engine_persistence_or_connector_engine() -> None:
    """The AI Workflow Builder MAY reuse F2/F3's pure, side-effect-free data/validation modules
    (`workflow.models`/`.exceptions`/`.graph_validation`/`.mapping_validation`/`.graph_compat` --
    none of which hold a Kernel reference, dispatch a capability, or touch persistence, per those
    modules' own architecture docstrings). It must NEVER import the dangerous surface: the
    execution engine, its persistence layer, or the Connector Engine object itself."""
    source_path = Path(workflow_builder.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden_prefixes = (
        "kortex.engines.workflow.engine",
        "kortex.engines.workflow.persistence",
        "kortex.engines.connector.engine",
        "kortex.engines.connector.registry",
    )
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)

    offenders = [m for m in imported if any(m.startswith(p) for p in forbidden_prefixes)]
    assert offenders == [], f"Forbidden runtime import(s) found: {offenders}"

    # `kortex.core.kernel` is imported only as a `TYPE_CHECKING`-guarded annotation (never gives
    # runtime access to a live Kernel beyond what the handler's own `kernel` parameter provides).
    type_checking_block = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
    )
    kernel_import_in_type_checking = any(
        isinstance(n, ast.ImportFrom) and n.module == "kortex.core.kernel" for n in ast.walk(type_checking_block)
    )
    assert kernel_import_in_type_checking
    kernel_imports_outside_type_checking = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "kortex.core.kernel"
        and node not in ast.walk(type_checking_block)
    ]
    assert kernel_imports_outside_type_checking == []


def test_module_source_never_references_register_definition_or_workflow_store() -> None:
    source_path = Path(workflow_builder.__file__)
    text = source_path.read_text(encoding="utf-8")
    forbidden_tokens = ("register_definition_async", "register_definition(", "WorkflowStore", "WorkflowEngine(")
    for token in forbidden_tokens:
        assert token not in text, f"Forbidden token '{token}' found in workflow_builder.py"


def test_curated_capability_names_are_a_small_fixed_allowlist() -> None:
    # A change to this list is a deliberate, reviewed act (schema authoring first) -- this test
    # exists so growing it silently and unboundedly shows up as a diff reviewers must justify.
    assert len(workflow_builder.CURATED_CAPABILITY_NAMES) <= 10
    assert len(set(workflow_builder.CURATED_CAPABILITY_NAMES)) == len(workflow_builder.CURATED_CAPABILITY_NAMES)
