"""Architecture-protection tests for Document Intelligence (M1/M20).

Mirrors the AI Engine's own enforced dependency-direction test (per its
closeout doc) — verifies, not merely documents, that Document Intelligence
never imports Document Engine, Knowledge Engine, AI Engine, or Security
Engine internals.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "kortex" / "engines" / "document_intelligence"

_FORBIDDEN_IMPORT_PREFIXES = (
    "kortex.engines.document.",
    "kortex.engines.document ",  # defensive: exact "kortex.engines.document" module itself
    "kortex.engines.knowledge",
    "kortex.engines.ai",
)

# The one precedented exception (matches `kortex.engines.workflow.engine`'s
# own documented exception for the identical reason): a pure data model
# needed for functional type identity with `verify_token()`. Importing the
# `SecurityEngine` class itself, or anything else from
# `kortex.engines.security.engine`/`.interfaces`, remains forbidden.
_ALLOWED_SECURITY_IMPORT = "kortex.engines.security.models"


def _python_files() -> list[Path]:
    return sorted(_PACKAGE_ROOT.rglob("*.py"))


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(_PACKAGE_ROOT)))
def test_no_forbidden_engine_imports(path: Path) -> None:
    for module in _imported_modules(path):
        for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
            assert not module.startswith(forbidden), (
                f"{path.relative_to(_PACKAGE_ROOT)} imports forbidden module '{module}' "
                f"(matches forbidden prefix '{forbidden}') — Document Intelligence must not "
                f"directly import Document/Knowledge/AI Engine internals (Article 6)."
            )


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(_PACKAGE_ROOT)))
def test_security_engine_import_limited_to_models(path: Path) -> None:
    for module in _imported_modules(path):
        if module.startswith("kortex.engines.security") and module != _ALLOWED_SECURITY_IMPORT:
            pytest.fail(
                f"{path.relative_to(_PACKAGE_ROOT)} imports '{module}' — only "
                f"'{_ALLOWED_SECURITY_IMPORT}' (TokenPayload, a pure data model) is permitted, "
                f"mirroring kortex.engines.workflow.engine's own precedented exception. "
                f"SecurityEngine itself must be resolved dynamically via "
                f"kernel.get_engine('security') and duck-typed, never imported."
            )


def test_package_has_no_direct_storage_engine_class_import() -> None:
    """Storage is an allowed dependency (Article 12), but only via
    Kernel-mediated IoC resolution — never a direct `StorageEngine()`
    construction, which would bypass the Kernel entirely."""
    for path in _python_files():
        for module in _imported_modules(path):
            assert module != "kortex.engines.storage.engine", (
                f"{path.relative_to(_PACKAGE_ROOT)} imports StorageEngine directly — "
                f"Storage must be resolved via kernel.container.resolve('engine.storage')."
            )


def test_capability_names_match_locked_contract() -> None:
    from kortex.engines.document_intelligence.engine import DocumentIntelligenceEngine

    engine = DocumentIntelligenceEngine()
    assert set(engine.registered_capabilities) == {
        "kortex.document_intelligence.pdf.parse",
        "kortex.document_intelligence.ocr.extract",
        "kortex.document_intelligence.structure.analyze",
    }


def test_no_idocumentintelligenceengine_provider_protocol_exists() -> None:
    """The engine facade is `DocumentIntelligenceEngine`; there is
    deliberately no separate provider protocol by that name (locked
    contract) — only `IPDFParser`/`IOCREngine`."""
    import kortex.engines.document_intelligence.interfaces as interfaces_module

    assert not hasattr(interfaces_module, "IDocumentIntelligenceEngine")
    assert hasattr(interfaces_module, "IPDFParser")
    assert hasattr(interfaces_module, "IOCREngine")
