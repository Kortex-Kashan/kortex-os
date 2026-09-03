"""
Static architectural boundary tests for KORTEX License Engine (M5.7).

Enforces Clean Architecture rules, engine decoupling, and zero unauthorized imports.
"""

from __future__ import annotations

import ast
from pathlib import Path

from kortex.engines.license.engine import LicenseEngine

_LICENSE_PKG_PATH = Path(__file__).resolve().parent.parent.parent / "src" / "kortex" / "engines" / "license"

_FORBIDDEN_IMPORT_PREFIXES = (
    "kortex.modules",
    "kortex.engines.workflow",
    "kortex.engines.document_intelligence",
    "kortex.engines.process_intelligence",
    "kortex.engines.ai",
    "kortex.engines.knowledge",
    "kortex.engines.marketplace",
    "zipfile",
    "tarfile",
)


def test_engine_declared_dependencies() -> None:
    engine = LicenseEngine()
    assert engine.name == "license"
    assert engine.dependencies == ["configuration", "registry", "storage", "security"]


def test_forbidden_imports_static_guard() -> None:
    violations: list[str] = []

    for py_file in _LICENSE_PKG_PATH.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                        if alias.name == prefix or alias.name.startswith(prefix + "."):
                            violations.append(f"{py_file.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                    if node.module == prefix or node.module.startswith(prefix + "."):
                        violations.append(f"{py_file.name}: from {node.module} import ...")

    assert not violations, "Architectural boundary violations in license engine:\n" + "\n".join(violations)


def test_no_caller_supplied_tenant_id_in_handler_signatures() -> None:
    tree = ast.parse((_LICENSE_PKG_PATH / "engine.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
            "verify_token",
            "apply_activation",
            "revoke_activation",
            "get_status",
        ):
            arg_names = [arg.arg for arg in node.args.args]
            assert "tenant_id" not in arg_names, (
                f"Handler {node.name} must not accept tenant_id as a parameter; "
                "tenant identity must be extracted strictly from execution_context."
            )
