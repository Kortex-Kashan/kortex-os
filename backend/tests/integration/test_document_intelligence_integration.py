"""Integration tests for Document Intelligence (M5/M6).

Boots a full Kernel with all production engines registered (mirroring
`kortex.api.kernel_bootstrap.build_and_boot_kernel`), then exercises all
three capabilities end-to-end through the real Kernel capability dispatch
path.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.document.engine import DocumentEngine
from kortex.engines.document_intelligence.engine import DocumentIntelligenceEngine
from kortex.engines.document_intelligence.models import DocumentParseRequest, StructureAnalysisRequest
from kortex.engines.knowledge.engine import KnowledgeEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.engine import WorkflowEngine

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "document_intelligence"

_MASTER_KEY = b"\xcc" * 32
_SIGNING_KEY = b"\xdd" * 32


async def _boot_full_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine]:
    from tests.unit.test_capability_dispatch_adversarial import _grant_role_permission, _seed_principal

    kernel = Kernel()
    kernel._db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{tmp_path / 'integration.db'}")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    security_engine = SecurityEngine(master_key=_MASTER_KEY, signing_private_key=_SIGNING_KEY)

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(ConnectorEngine())
    kernel.register_engine(WorkflowEngine())
    kernel.register_engine(DocumentEngine())
    kernel.register_engine(KnowledgeEngine())
    kernel.register_engine(DocumentIntelligenceEngine())

    await kernel.boot()

    tenant_id = "tenant-integration"
    role = "role-integration"
    await _grant_role_permission(storage_engine.data, role, "document_intelligence:parse")
    await _grant_role_permission(storage_engine.data, role, "document_intelligence:analyze")
    await _seed_principal(
        storage_engine.data,
        tenant_id,
        "principal-integration",
        roles=[role],
        clearance_level="INTERNAL",
    )
    return kernel, storage_engine


async def _issue_test_token(kernel: Kernel, tenant_id: str, principal_id: str) -> TokenPayload:
    from tests.unit.test_capability_dispatch_adversarial import _issue_token

    security_engine = cast(SecurityEngine, kernel.get_engine("security"))
    return await _issue_token(security_engine, tenant_id, principal_id)


@pytest.mark.asyncio
async def test_full_kernel_boots_with_document_intelligence_registered(tmp_path: Path) -> None:
    kernel, _ = await _boot_full_kernel(tmp_path)
    engine = kernel.get_engine("document_intelligence")
    assert engine.state.value == "RUNNING"
    for other in ("document", "knowledge", "storage", "security", "connector", "workflow"):
        assert kernel.get_engine(other).state.value == "RUNNING"


@pytest.mark.asyncio
async def test_pdf_parse_end_to_end(tmp_path: Path) -> None:
    kernel, _ = await _boot_full_kernel(tmp_path)
    token = await _issue_test_token(kernel, "tenant-integration", "principal-integration")

    request = DocumentParseRequest(
        content=(FIXTURES / "normal_text.pdf").read_bytes(),
        mime_type="application/pdf",
    )
    cap_request = CapabilityRequest(
        capability_name="kortex.document_intelligence.pdf.parse",
        session_token=token,
        parameters={"request": request},
        context={"resource_tenant_id": "tenant-integration"},
    )
    result = await kernel.invoke_capability(cap_request)
    assert "KORTEX Document Intelligence Fixture" in result.raw_text


@pytest.mark.asyncio
async def test_ocr_extract_end_to_end(tmp_path: Path) -> None:
    kernel, _ = await _boot_full_kernel(tmp_path)
    token = await _issue_test_token(kernel, "tenant-integration", "principal-integration")

    request = DocumentParseRequest(
        content=(FIXTURES / "clear_text.png").read_bytes(),
        mime_type="image/png",
    )
    cap_request = CapabilityRequest(
        capability_name="kortex.document_intelligence.ocr.extract",
        session_token=token,
        parameters={"request": request},
        context={"resource_tenant_id": "tenant-integration"},
    )
    result = await kernel.invoke_capability(cap_request)
    assert "KORTEX" in result.text


@pytest.mark.asyncio
async def test_structure_analyze_end_to_end(tmp_path: Path) -> None:
    kernel, _ = await _boot_full_kernel(tmp_path)
    token = await _issue_test_token(kernel, "tenant-integration", "principal-integration")

    pdf_request = DocumentParseRequest(
        content=(FIXTURES / "table.pdf").read_bytes(),
        mime_type="application/pdf",
    )
    parsed = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.document_intelligence.pdf.parse",
            session_token=token,
            parameters={"request": pdf_request},
            context={"resource_tenant_id": "tenant-integration"},
        )
    )

    structure_request = StructureAnalysisRequest(parsed_result=parsed)
    blocks = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.document_intelligence.structure.analyze",
            session_token=token,
            parameters={"request": structure_request},
            context={"resource_tenant_id": "tenant-integration"},
        )
    )
    assert any(b.block_type == "table" for b in blocks)


@pytest.mark.asyncio
async def test_shutdown_succeeds_cleanly(tmp_path: Path) -> None:
    kernel, _ = await _boot_full_kernel(tmp_path)
    await kernel.shutdown()
    assert kernel.get_engine("document_intelligence").state.value == "STOPPED"
