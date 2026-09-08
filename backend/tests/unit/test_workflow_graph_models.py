"""
Unit tests for KORTEX Workflow Graph Model (Milestone F2 — Workflow Graph Model).

Covers construction, defaults, `WorkflowDefinition.graph` compatibility, and serialization/
deserialization round-tripping for `WorkflowGraph`/`WorkflowGraphNode`/`WorkflowGraphEdge`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from kortex.engines.workflow.models import (
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowGraphEdge,
    WorkflowGraphNode,
)


def _simple_graph() -> WorkflowGraph:
    return WorkflowGraph(
        entry_node_id="n1",
        nodes=[
            WorkflowGraphNode(node_id="n1", node_type="capability", capability_name="kortex.storage.data.session"),
            WorkflowGraphNode(node_id="n2", node_type="capability", capability_name="kortex.storage.file.store"),
        ],
        edges=[WorkflowGraphEdge(edge_id="e1", source_node_id="n1", target_node_id="n2")],
    )


def test_workflow_graph_node_defaults() -> None:
    node = WorkflowGraphNode(node_id="n1", node_type="capability")
    assert node.capability_name is None
    assert node.config == {}
    assert node.input_ports == []
    assert node.output_ports == []
    assert node.metadata == {}


def test_workflow_graph_node_requires_id_and_type() -> None:
    with pytest.raises(ValidationError):
        WorkflowGraphNode(node_type="capability")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        WorkflowGraphNode(node_id="n1")  # type: ignore[call-arg]


def test_workflow_graph_edge_defaults() -> None:
    edge = WorkflowGraphEdge(edge_id="e1", source_node_id="a", target_node_id="b")
    assert edge.source_port is None
    assert edge.target_port is None
    assert edge.metadata == {}


def test_workflow_graph_edge_supports_named_ports() -> None:
    edge = WorkflowGraphEdge(
        edge_id="e1",
        source_node_id="a",
        source_port="out",
        target_node_id="b",
        target_port="in",
    )
    assert edge.source_port == "out"
    assert edge.target_port == "in"


def test_workflow_graph_construction_and_defaults() -> None:
    graph = _simple_graph()
    assert graph.schema_version == "1.0.0"
    assert graph.entry_node_id == "n1"
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.metadata == {}


def test_workflow_graph_requires_entry_node_id() -> None:
    with pytest.raises(ValidationError):
        WorkflowGraph(nodes=[], edges=[])  # type: ignore[call-arg]


def test_workflow_graph_schema_version_is_independent_of_definition_version() -> None:
    """`WorkflowGraph.schema_version` and `WorkflowDefinition.version` are deliberately separate
    fields — changing one must never implicitly change the other."""
    graph = _simple_graph()
    definition = WorkflowDefinition(id="d1", name="D", version="2.3.1", graph=graph)
    assert definition.version == "2.3.1"
    assert definition.graph is not None
    assert definition.graph.schema_version == "1.0.0"


def test_workflow_definition_graph_defaults_to_none() -> None:
    """The compatibility contract: an existing WorkflowDefinition with no graph populated behaves
    exactly as it did before F2 — `graph` is optional and absent by default."""
    definition = WorkflowDefinition(id="d1", name="D")
    assert definition.graph is None


def test_workflow_definition_accepts_an_attached_graph() -> None:
    graph = _simple_graph()
    definition = WorkflowDefinition(id="d1", name="D", graph=graph)
    assert definition.graph == graph


def test_workflow_graph_node_serialization_round_trip() -> None:
    node = WorkflowGraphNode(
        node_id="n1",
        node_type="capability",
        capability_name="kortex.storage.data.session",
        config={"x": 1},
        input_ports=["in"],
        output_ports=["out"],
        metadata={"note": "example"},
    )
    restored = WorkflowGraphNode.model_validate_json(node.model_dump_json())
    assert restored == node


def test_workflow_graph_serialization_round_trip_is_deterministic() -> None:
    graph = _simple_graph()
    first = graph.model_dump_json()
    second = graph.model_dump_json()
    assert first == second

    restored = WorkflowGraph.model_validate_json(first)
    assert restored == graph


def test_workflow_definition_with_graph_serialization_round_trip() -> None:
    definition = WorkflowDefinition(id="d1", name="D", graph=_simple_graph())
    restored = WorkflowDefinition.model_validate_json(definition.model_dump_json())
    assert restored == definition


def test_workflow_graph_node_does_not_declare_capability_risk_metadata() -> None:
    """Static guard: a node must never carry a copy of Registry-authoritative capability metadata
    (F1's own capability model). Only a `capability_name` string reference is permitted."""
    declared_fields = set(WorkflowGraphNode.model_fields)
    forbidden = {
        "is_read_only",
        "is_idempotent",
        "owner_domain",
        "resource_type",
        "action",
        "parameters_schema",
        "returns_schema",
        "required_permissions",
        "security_classification",
    }
    assert declared_fields.isdisjoint(forbidden), (
        f"WorkflowGraphNode must not duplicate Registry capability metadata; found: {declared_fields & forbidden}"
    )


# -- Static architectural guard: F2's new modules stay dependency-isolated -------------------------
#
# Mirrors this repository's existing AST-based static-guard convention
# (test_capability_identity_propagation_architecture.py) rather than inventing a new mechanism.
# Proves, by direct inspection of import statements, that F2's structural graph model never reaches
# into the executor, AI, connectors, the frontend, or the database layer, and adds no third-party
# graph dependency — a graph model that could import any of those would no longer be "structure
# only," regardless of what its own code happened to do with the import.

_WORKFLOW_SRC = Path(__file__).resolve().parent.parent.parent / "src" / "kortex" / "engines" / "workflow"

_F2_GRAPH_MODULES = ("graph_validation.py", "graph_compat.py")

_FORBIDDEN_IMPORT_PREFIXES = (
    "kortex.engines.ai",
    "kortex.engines.connector",
    "kortex.engines.security",  # graph model must not authenticate/authorize directly — see F1's dispatch boundary
    "sqlalchemy",
    "alembic",
    "networkx",  # no third-party graph library — see graph_validation.py's own module docstring
    "igraph",
)

# The executor/runtime modules F2 must not import from (models.py's own new graph classes included).
_FORBIDDEN_WORKFLOW_MODULES = ("engine", "evaluator", "state_machine", "scheduler", "persistence", "executor")


def _imported_module_names(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("filename", _F2_GRAPH_MODULES)
def test_f2_graph_modules_do_not_import_forbidden_dependencies(filename: str) -> None:
    imported = _imported_module_names(_WORKFLOW_SRC / filename)
    for forbidden_prefix in _FORBIDDEN_IMPORT_PREFIXES:
        offending = {name for name in imported if name == forbidden_prefix or name.startswith(forbidden_prefix + ".")}
        assert not offending, f"{filename} imports forbidden dependency: {offending}"


@pytest.mark.parametrize("filename", (*_F2_GRAPH_MODULES, "models.py"))
def test_f2_graph_modules_do_not_import_executor_or_persistence(filename: str) -> None:
    imported = _imported_module_names(_WORKFLOW_SRC / filename)
    for module_name in _FORBIDDEN_WORKFLOW_MODULES:
        full_name = f"kortex.engines.workflow.{module_name}"
        offending = {name for name in imported if name == full_name}
        assert not offending, (
            f"{filename} imports '{full_name}' — the F2 graph model must remain structure-only and "
            f"never depend on the execution/persistence layer."
        )
