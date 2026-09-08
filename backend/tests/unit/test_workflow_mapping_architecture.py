"""
Static architectural security guard for KORTEX Workflow Data Mapping & Expression Foundation
(Milestone F3).

Mirrors this repository's existing AST-based static-guard convention exactly
(`test_capability_identity_propagation_architecture.py`, and F2's own
`test_workflow_graph_models.py::test_f2_graph_modules_do_not_import_forbidden_dependencies`) rather
than inventing a new mechanism. Proves, by direct inspection of source — never by trusting a
docstring's claim — that the F3 domain layer (`mapping.py`, `mapping_validation.py`,
`expression.py`) can structurally never become a hidden capability-execution mechanism:

- No import of Security Engine, SecretStore-adjacent modules, Connector Engine, AI Engine, the
  Kernel, or the capability-dispatch boundary itself.
- No import of a persistence, network, filesystem, or process-execution facility.
- No `eval`/`exec`/`compile`/`__import__` call anywhere, and no `importlib` import — the two
  mechanisms that would let a "pure" module execute arbitrary code despite passing every other
  check.

A module that imports any of these, or contains any of these calls, is no longer "compute/transform
already-authorized data" — it has crossed into "perform an external/system action," which is
architecturally reserved for a capability (F3 discovery §13, ratified as an invariant).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_WORKFLOW_SRC = Path(__file__).resolve().parent.parent.parent / "src" / "kortex" / "engines" / "workflow"

_F3_DOMAIN_MODULES = ("mapping.py", "mapping_validation.py", "expression.py")

_FORBIDDEN_IMPORT_PREFIXES = (
    "kortex.engines.ai",
    "kortex.engines.connector",
    "kortex.engines.security",
    "kortex.core.dispatch",  # CapabilityRequest / CapabilityDispatcher — the sanctioned execution boundary
    "kortex.core.kernel",  # Kernel.invoke_capability — F3 must never be able to reach this
    "sqlalchemy",
    "alembic",
    "subprocess",
    "socket",
    "urllib",
    "requests",
    "httpx",
    "importlib",  # dynamic import mechanism
    "os",  # filesystem/environment/process access — no legitimate use in a pure domain module
    "networkx",  # no third-party graph library, matching graph_validation.py's own precedent
)

_FORBIDDEN_CALL_NAMES = frozenset({"eval", "exec", "compile", "__import__"})


def _read_ast(py_file: Path) -> ast.Module:
    return ast.parse(py_file.read_text(encoding="utf-8"))


def _imported_module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _forbidden_call_sites(tree: ast.Module) -> list[str]:
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
            if name in _FORBIDDEN_CALL_NAMES:
                offenders.append(f"{name}() at line {node.lineno}")
    return offenders


@pytest.mark.parametrize("filename", _F3_DOMAIN_MODULES)
def test_f3_domain_modules_do_not_import_forbidden_dependencies(filename: str) -> None:
    tree = _read_ast(_WORKFLOW_SRC / filename)
    imported = _imported_module_names(tree)
    for forbidden_prefix in _FORBIDDEN_IMPORT_PREFIXES:
        offending = {name for name in imported if name == forbidden_prefix or name.startswith(forbidden_prefix + ".")}
        assert not offending, f"{filename} imports forbidden dependency: {offending}"


@pytest.mark.parametrize("filename", _F3_DOMAIN_MODULES)
def test_f3_domain_modules_contain_no_eval_exec_or_dynamic_execution(filename: str) -> None:
    tree = _read_ast(_WORKFLOW_SRC / filename)
    offenders = _forbidden_call_sites(tree)
    assert offenders == [], f"{filename} contains forbidden dynamic-execution call(s): {offenders}"


@pytest.mark.parametrize("filename", _F3_DOMAIN_MODULES)
def test_f3_domain_modules_do_not_import_executor_or_persistence(filename: str) -> None:
    """Mirrors F2's identical guard: the F3 domain layer never reaches into the execution/
    persistence layer either — it is a pure library, called by a future integration point, never
    the other way around."""
    tree = _read_ast(_WORKFLOW_SRC / filename)
    imported = _imported_module_names(tree)
    for module_name in ("engine", "evaluator", "state_machine", "scheduler", "persistence", "executor"):
        full_name = f"kortex.engines.workflow.{module_name}"
        offending = {name for name in imported if name == full_name}
        assert not offending, (
            f"{filename} imports '{full_name}' — the F3 domain layer must remain structure/compute-"
            f"only and never depend on the execution/persistence layer."
        )


def test_expression_module_never_imports_mapping_or_mapping_validation() -> None:
    """expression.py is the lowest layer (pure operator functions) — it must not depend upward on
    mapping.py (the resolver) or mapping_validation.py (the validator), keeping the dependency
    direction one-way: mapping.py -> expression.py, mapping_validation.py -> models.py only."""
    tree = _read_ast(_WORKFLOW_SRC / "expression.py")
    imported = _imported_module_names(tree)
    for forbidden in ("kortex.engines.workflow.mapping", "kortex.engines.workflow.mapping_validation"):
        assert forbidden not in imported, f"expression.py must not import '{forbidden}'."
