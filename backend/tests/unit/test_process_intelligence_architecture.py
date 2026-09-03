"""
Architecture and dependency quarantine tests for Process Intelligence Engine.

Verifies:
1. Zero forbidden imports from other business engines or workflow persistence.
2. Zero external data-science dependencies (pm4py, pandas, numpy, scipy).
3. Schema compatibility: Local projection columns match authoritative Workflow schema.
"""

from __future__ import annotations

import ast
import pathlib


def _get_process_intelligence_source_files() -> list[pathlib.Path]:
    root = pathlib.Path(__file__).parent.parent.parent / "src" / "kortex" / "engines" / "process_intelligence"
    return list(root.glob("*.py"))


def test_no_forbidden_engine_imports() -> None:
    """Verify production Process Intelligence code has zero imports from forbidden engines."""
    forbidden_prefixes = [
        "kortex.engines.workflow",
        "kortex.engines.ai",
        "kortex.engines.knowledge",
        "kortex.engines.document",
        "kortex.engines.security.engine",
        "kortex.engines.event.engine",
    ]

    for file_path in _get_process_intelligence_source_files():
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_prefixes:
                        assert not alias.name.startswith(forbidden), (
                            f"Forbidden import '{alias.name}' in {file_path.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_prefixes:
                    assert not node.module.startswith(forbidden), (
                        f"Forbidden import from '{node.module}' in {file_path.name}"
                    )


def test_no_heavy_external_dependencies() -> None:
    """Verify zero third-party data science / heavy process mining libraries are imported."""
    forbidden_libs = ["pm4py", "pandas", "numpy", "scipy", "sklearn"]

    for file_path in _get_process_intelligence_source_files():
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    assert root_pkg not in forbidden_libs, (
                        f"Forbidden heavy external dependency '{root_pkg}' in {file_path.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_pkg = node.module.split(".")[0]
                assert root_pkg not in forbidden_libs, (
                    f"Forbidden heavy external dependency '{root_pkg}' in {file_path.name}"
                )


def test_schema_projection_matches_authoritative_workflow_models() -> None:
    """Test-only reflection verifying Process Intelligence descriptors match actual tables."""
    from kortex.engines.process_intelligence.tables import (
        t_approval_requests,
        t_workflow_instances,
        t_workflow_step_runs,
    )
    from kortex.engines.workflow.persistence import (
        ApprovalRequestModel,
        WorkflowInstanceModel,
        WorkflowStepRunModel,
    )

    pairings = [
        (t_workflow_instances, WorkflowInstanceModel),
        (t_workflow_step_runs, WorkflowStepRunModel),
        (t_approval_requests, ApprovalRequestModel),
    ]

    for table_desc, model_cls in pairings:
        model_columns = model_cls.__table__.columns
        for col in table_desc.columns:
            assert col.name in model_columns, (
                f"Column '{col.name}' in descriptor '{table_desc.name}' does not exist in authoritative schema"
            )
            model_col = model_columns[col.name]
            # Verify compatible types
            assert type(col.type) is type(model_col.type), (
                f"Column '{col.name}' type mismatch: {type(col.type)} vs {type(model_col.type)}"
            )
            # If Process Intelligence relies on non-null, model must be non-nullable
            if not col.nullable:
                assert not model_col.nullable, (
                    f"Column '{col.name}' is declared non-null in descriptor but nullable in authoritative model"
                )
