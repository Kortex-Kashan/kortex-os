"""Unit tests for Document Security Integration and Storage Engine Bindings (Milestone 7).

Target: 100% pass rate, 100% line coverage for security.py.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Callable, List, Optional
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.document.exceptions import DocumentSecurityError
from kortex.engines.document.models import (
    AdapterCapability,
    SecurityClassification,
)
from kortex.engines.document.security import (
    DefaultVerificationService,
    DocumentSecurityVerifier,
    DocumentStorageBinder,
    IVerificationService,
)
from kortex.engines.storage.interfaces import (
    ICacheStore,
    IDataStore,
    IFileStore,
    IObjectStore,
)
from kortex.engines.storage.models import FileMetadata, ObjectMetadata


# Mock Storage Stores for testing DocumentStorageBinder
class MockDataStore(IDataStore):
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        yield None  # type: ignore[misc]

    async def execute_in_transaction(self, action: Callable[[AsyncSession], Any]) -> Any:
        return action(None)  # type: ignore[arg-type]


class MockFileStore(IFileStore):
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def read_file(self, relative_path: str) -> bytes:
        if relative_path not in self.files:
            raise FileNotFoundError(f"File '{relative_path}' not found.")
        return self.files[relative_path]

    async def write_file(self, relative_path: str, content: bytes) -> FileMetadata:
        self.files[relative_path] = content
        return FileMetadata(
            relative_path=relative_path,
            file_name=relative_path.split("/")[-1],
            file_size_bytes=len(content),
            sha256_hash="dummy_hash",
            created_at="2026-08-11T12:00:00Z",
            modified_at="2026-08-11T12:00:00Z",
        )

    async def delete_file(self, relative_path: str) -> bool:
        if relative_path in self.files:
            del self.files[relative_path]
            return True
        return False

    async def file_exists(self, relative_path: str) -> bool:
        return relative_path in self.files

    async def list_files(self, relative_path: str = "") -> List[FileMetadata]:
        return []

    async def get_metadata(self, relative_path: str) -> FileMetadata:
        return FileMetadata(
            relative_path=relative_path,
            file_name=relative_path.split("/")[-1],
            file_size_bytes=len(self.files.get(relative_path, b"")),
            sha256_hash="dummy_hash",
            created_at="2026-08-11T12:00:00Z",
            modified_at="2026-08-11T12:00:00Z",
        )


class MockObjectStore(IObjectStore):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_object(
        self,
        bucket_name: str,
        object_key: str,
        data: bytes,
        mime_type: str = "application/octet-stream",
    ) -> ObjectMetadata:
        full_key = f"{bucket_name}/{object_key}"
        self.objects[full_key] = data
        return ObjectMetadata(
            bucket_name=bucket_name,
            object_key=object_key,
            storage_key=full_key,
            file_name=object_key.split("/")[-1],
            file_size_bytes=len(data),
            sha256_hash="dummy_hash",
            size_bytes=len(data),
            mime_type=mime_type,
            created_at="2026-08-11T12:00:00Z",
        )

    async def get_object(self, bucket_name: str, object_key: str) -> bytes:
        full_key = f"{bucket_name}/{object_key}"
        if full_key not in self.objects:
            raise KeyError(f"Object '{full_key}' not found.")
        return self.objects[full_key]

    async def delete_object(self, bucket_name: str, object_key: str) -> bool:
        full_key = f"{bucket_name}/{object_key}"
        if full_key in self.objects:
            del self.objects[full_key]
            return True
        return False

    async def object_exists(self, bucket_name: str, object_key: str) -> bool:
        return f"{bucket_name}/{object_key}" in self.objects

    async def list_objects(self, bucket_name: str, prefix: Optional[str] = None) -> List[ObjectMetadata]:
        return []


class MockCacheStore(ICacheStore):
    def __init__(self) -> None:
        self.cache: dict[str, Any] = {}

    async def get(self, key: str) -> Optional[Any]:
        return self.cache.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> bool:
        self.cache[key] = value
        return True

    async def delete(self, key: str) -> bool:
        if key in self.cache:
            del self.cache[key]
            return True
        return False

    async def clear(self) -> bool:
        self.cache.clear()
        return True


@pytest.mark.asyncio
async def test_verification_service_hash_and_signature() -> None:
    """Test DefaultVerificationService SHA256 and HMAC signing implementation."""
    service = DefaultVerificationService()
    assert isinstance(service, IVerificationService)

    payload = b"Sample Document Payload Binary Data"

    # Compute hash
    h = await service.compute_hash(payload)
    assert isinstance(h, str)
    assert len(h) == 64  # SHA256 hex length

    # Verify matching and mismatching hash
    assert await service.verify_hash(payload, h) is True
    assert await service.verify_hash(payload, "invalid_hash_string") is False
    assert await service.verify_hash(None, h) is False  # type: ignore[arg-type]
    assert await service.verify_hash(payload, "") is False

    # Sign document and verify signature
    sig = await service.sign_document(payload, secret_key="my_secret")
    assert isinstance(sig, str)
    assert len(sig) == 64

    assert await service.verify_signature(payload, sig, secret_key="my_secret") is True
    assert await service.verify_signature(payload, sig, secret_key="wrong_secret") is False
    assert await service.verify_signature(None, sig) is False  # type: ignore[arg-type]
    assert await service.verify_signature(payload, "") is False

    # Error handling for None payloads
    with pytest.raises(DocumentSecurityError, match="Cannot compute hash"):
        await service.compute_hash(None)  # type: ignore[arg-type]

    with pytest.raises(DocumentSecurityError, match="Cannot sign None"):
        await service.sign_document(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_document_security_verifier() -> None:
    """Test DocumentSecurityVerifier classification, integrity, and capability authorization."""
    verifier = DocumentSecurityVerifier()
    assert isinstance(verifier.verification_service, IVerificationService)

    # Classification validation
    c1 = verifier.validate_security_classification(SecurityClassification.CONFIDENTIAL)
    assert c1 == SecurityClassification.CONFIDENTIAL

    c2 = verifier.validate_security_classification("restricted")
    assert c2 == SecurityClassification.RESTRICTED

    with pytest.raises(DocumentSecurityError, match="Invalid security classification"):
        verifier.validate_security_classification("invalid_class_name")

    # Document integrity verification
    payload = b"Secret Document Payload"
    h = await verifier.verification_service.compute_hash(payload)
    assert await verifier.verify_document_integrity(payload, h) is True
    assert await verifier.verify_document_integrity(payload, "wrong_hash") is False

    # Capability permission enforcement
    assert verifier.enforce_capability_permission(AdapterCapability.GENERATE, ["generate"]) is True
    assert verifier.enforce_capability_permission(AdapterCapability.GENERATE, ["document:generate"]) is True
    assert verifier.enforce_capability_permission(AdapterCapability.GENERATE, ["*"]) is True
    assert verifier.enforce_capability_permission(AdapterCapability.GENERATE, ["document:admin"]) is True
    assert verifier.enforce_capability_permission(AdapterCapability.GENERATE, ["convert"]) is False
    assert verifier.enforce_capability_permission(AdapterCapability.GENERATE, []) is False


@pytest.mark.asyncio
async def test_document_storage_binder_with_stores() -> None:
    """Test DocumentStorageBinder integration bindings across IDataStore, IFileStore, IObjectStore, ICacheStore."""
    data_store = MockDataStore()
    file_store = MockFileStore()
    object_store = MockObjectStore()
    cache_store = MockCacheStore()

    binder = DocumentStorageBinder(
        data_store=data_store,
        file_store=file_store,
        object_store=object_store,
        cache_store=cache_store,
    )

    assert binder.data_store is data_store
    assert binder.file_store is file_store
    assert binder.object_store is object_store
    assert binder.cache_store is cache_store

    # Template schema file store bindings
    meta = await binder.save_template_schema("templates/payslip.json", b'{"template_id": "p1"}')
    assert meta is not None
    assert meta.relative_path == "templates/payslip.json"

    content = await binder.load_template_schema("templates/payslip.json")
    assert content == b'{"template_id": "p1"}'

    assert await binder.load_template_schema("templates/missing.json") is None

    # Object store output bindings
    obj_meta = await binder.store_document_output("documents", "out_100.pdf", b"[PDF_BYTES]")
    assert obj_meta is not None
    assert obj_meta.bucket_name == "documents"

    retrieved = await binder.retrieve_document_output("documents", "out_100.pdf")
    assert retrieved == b"[PDF_BYTES]"

    assert await binder.retrieve_document_output("documents", "missing_out.pdf") is None

    # Cache store bindings
    set_ok = await binder.cache_set("schemas", "payslip", {"v": "1.0"}, ttl_seconds=600)
    assert set_ok is True

    cached_val = await binder.cache_get("schemas", "payslip")
    assert cached_val == {"v": "1.0"}

    # Data store transactional persistence
    result = await binder.persist_transactional(lambda session: "PERSISTED")
    assert result == "PERSISTED"


@pytest.mark.asyncio
async def test_document_storage_binder_cache_tenant_isolation() -> None:
    """Test that DocumentStorageBinder cache keys are tenant-scoped (Milestone 7 fix).

    Two tenants writing under the identical (cache_category, key) pair must not collide;
    each tenant must only ever observe its own cached value.
    """
    cache_store = MockCacheStore()
    binder = DocumentStorageBinder(cache_store=cache_store)

    await binder.cache_set("schemas", "payslip", {"tenant": "alpha"}, tenant_id="tenant-alpha")
    await binder.cache_set("schemas", "payslip", {"tenant": "beta"}, tenant_id="tenant-beta")

    alpha_val = await binder.cache_get("schemas", "payslip", tenant_id="tenant-alpha")
    beta_val = await binder.cache_get("schemas", "payslip", tenant_id="tenant-beta")
    assert alpha_val == {"tenant": "alpha"}
    assert beta_val == {"tenant": "beta"}
    assert alpha_val != beta_val

    # Default tenant_id ("default") must not observe either tenant's value.
    assert await binder.cache_get("schemas", "payslip") is None

    # Composite key must actually embed the tenant_id segment, not just coincidentally
    # produce distinct values (guards against a no-op "fix" that still collides).
    assert "doc_engine:tenant-alpha:schemas:payslip" in cache_store.cache
    assert "doc_engine:tenant-beta:schemas:payslip" in cache_store.cache
    assert "doc_engine:default:schemas:payslip" not in cache_store.cache


@pytest.mark.asyncio
async def test_document_storage_binder_without_stores() -> None:
    """Test DocumentStorageBinder graceful behavior when stores are None."""
    binder = DocumentStorageBinder()

    assert binder.data_store is None
    assert binder.file_store is None
    assert binder.object_store is None
    assert binder.cache_store is None

    assert await binder.save_template_schema("path", b"data") is None
    assert await binder.load_template_schema("path") is None
    assert await binder.store_document_output("b", "k", b"data") is None
    assert await binder.retrieve_document_output("b", "k") is None
    assert await binder.cache_get("c", "k") is None
    assert await binder.cache_set("c", "k", "v") is False
    assert await binder.persist_transactional(lambda s: "ok") is None
