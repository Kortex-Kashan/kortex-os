# KORTEX OS — Document Intelligence Engine Implementation Specification

Status: Implemented (Phase 4)
Version: 1.0.0
Authority: Phase 4 Document Intelligence architecture revision (this session's
planning passes) — approved by the Chief Architect prior to implementation.
Target File: `docs/architecture/document_intelligence_engine_implementation_spec.md`

Depends On:
- Phase 1 Foundation (Kernel Engine, Event Engine, Registry Engine, Configuration Engine)
- Storage Engine (`kortex.engines.storage`)
- Security Engine (`kortex.engines.security`) — via Kernel-mediated capability dispatch only

---

## 1. Purpose & Scope

The Document Intelligence Engine (`kortex.engines.document_intelligence`) is a
standalone, capability-dispatch-driven KORTEX System Engine providing local,
deterministic-where-applicable PDF text/metadata/table extraction and local
OCR (image/scanned-page text, bounding boxes, confidence). It is named
explicitly as a "Remaining Future Engine" in `ARCHITECTURE_VERSION_1.0.md`
§20 and is now implemented for Phase 4.

It is architecturally independent of the existing Document Engine
(`kortex.engines.document`) — the ratified `document_engine_implementation_
spec.md` §3 item 9 explicitly places "OCR vision engines" out of that
engine's scope, exposing only `IDocumentIntelligenceProvider`/
`IDocumentRecommendationProvider` protocols. This engine is the separate,
concrete implementation the architecture anticipated, not an extension of
the Document Engine.

**In scope (Phase 4)**: deterministic local PDF extraction, local OCR,
deterministic structural composition, engine contracts/models, capability
registration/dispatch, Kernel integration, testing.

**Explicitly out of scope**: semantic business classification, Knowledge
Engine indexing, workflow orchestration, Document Engine integration,
persistence/migrations, Marketplace model management, AI Orchestration
Engine modifications, Process Intelligence, Production Hardening.

---

## 2. Architectural Hierarchy & Dependency Direction

```
Business Module / Recipe / AI Orchestration Engine (optional caller)
        │  capability invocation only (Article 6/7)
        ▼
Kernel — Capability Dispatcher (authentication, ABAC/RBAC, audit)
        ▼
DocumentIntelligenceEngine (BaseEngine, IEngineDiagnostics)
        │
   ┌────┼────────────────┐
   ▼    ▼                ▼
IPDFParser  IOCREngine  StructureAnalyzer
   │            │             (pure composition, no I/O)
   ▼            ▼
Storage Engine (IObjectStore / IFileStore) — platform/infrastructure dependency
```

**Terminology**: `configuration`, `registry`, `event`, and `storage`
(`DocumentIntelligenceEngine.dependencies`, §3) are **platform/
infrastructure dependencies** — Kernel-owned system engines every engine is
constitutionally permitted to depend on (Article 12: Storage Engine is the
sole gateway to storage; `registry`/`event`/`configuration` are Kernel
subsystems, not business logic). This is categorically different from a
**direct business-engine dependency**, which remains forbidden: this engine
imports nothing from `kortex.engines.document`, `kortex.engines.knowledge`,
or `kortex.engines.ai` — enforced by
`backend/tests/unit/test_document_intelligence_architecture.py`. The one
precedented exception, matching `kortex.engines.workflow.engine`'s own
documented exception, is `kortex.engines.security.models` (`TokenPayload`,
`SecurityPrincipal` — pure data models needed for functional type identity
with `SecurityEngine.authentication_manager.verify_token()`), which is not
itself a business-engine dependency either. `SecurityEngine` the class is
never imported; it is resolved dynamically via `kernel.get_engine("security")`
and duck-typed, exactly mirroring `WorkflowEngine`'s own resolution pattern.
No architecture change from the previously approved design is introduced by
this clarification — it corrects terminology only.

AI Orchestration Engine may call this engine's capabilities as a tool via its
existing `invoke_tool()` → `Kernel.invoke_capability()` path — a real,
already-working integration seam requiring zero changes to either engine.

---

## 3. Engine Lifecycle

`DocumentIntelligenceEngine(BaseEngine, IEngineDiagnostics)`:

- `name = "document_intelligence"`
- `dependencies = ["configuration", "registry", "event", "storage"]` — all
  four are platform/infrastructure dependencies (§2), not business-engine
  dependencies. `storage` is a declared, `BootEngine`-enforced hard
  dependency: the Kernel refuses to boot at all if Storage Engine is not
  also registered (verified empirically during implementation, not
  assumed).
- `initialize(kernel)`: resolves `IObjectStore` from the Kernel IoC
  container (`kernel.container.resolve("engine.storage")`, the same
  deferred-wiring pattern `DocumentEngine`/`ConnectorEngine`/`WorkflowEngine`
  already establish), then registers the three capabilities below.
- `start()` / `stop()`: no background tasks — the engine is fully stateless
  in Phase 4, so lifecycle transitions are immediate.
- `health_check()` / `IEngineDiagnostics` (`health`, `metrics`, `diagnostics`,
  `status`, `version`, `capabilities`): implemented via
  `DocumentIntelligenceDiagnostics`, mirroring `SentinelDiagnostics`'s
  established pattern exactly.

Configuration (`DocumentIntelligenceConfig`) follows the `SentinelConfig`/
`AIEngineRuntimeConfig` precedent: a standalone frozen Pydantic model passed
as an optional constructor argument, not registered with
`ConfigurationEngine` and not read from `KORTEX_*` env vars.

---

## 4. Provider Interfaces

### `IPDFParser`
```python
async def parse(self, content: bytes, options: dict[str, Any]) -> ParsedDocumentResult
```
Deterministic extraction of text, metadata, and tables from PDF bytes. The
provider does not own timeout policy — the engine wraps every call in
`asyncio.wait_for(..., timeout=config.operation_timeout_seconds)`.

**Concrete implementation**: `PdfPlumberParser`, backed by `pdfplumber`
(pure Python, built on `pdfminer.six`). No subprocess, no system package,
no native-binary dependency. **Dependency tree verified via `pip show`/
`pipdeptree` against the actually-installed distribution metadata, not
inferred from what happened to already be present in the environment**:
`pdfplumber`'s own declared `Requires:` is exactly `pdfminer.six`,
`Pillow`, `pypdfium2` — `pdfminer.six` in turn pulls `charset-normalizer`
and `cryptography` (the latter already a core KORTEX dependency, so not a
new install), and `cryptography` pulls `cffi`→`pycparser`. Every package in
this full tree installs from a prebuilt wheel on Windows/Python 3.12 — zero
build-from-source steps observed.

### `IOCREngine`
```python
async def extract_text(self, image: bytes, options: dict[str, Any]) -> OCRResult
```
Local OCR: text, bounding boxes, confidence from image bytes. Same
timeout-envelope ownership rule as `IPDFParser`.

**Concrete implementation**: `RapidOcrProvider`, backed by
`rapidocr-onnxruntime` (ONNXRuntime CPU execution provider).
**Dependency tree verified the same way**: `rapidocr-onnxruntime`'s own
declared `Requires:` is exactly `numpy`, `onnxruntime`, `opencv-python`,
`Pillow`, `pyclipper`, `PyYAML`, `Shapely`, `six`, `tqdm` — a prior pass
documented only `onnxruntime`, `opencv-python`, `numpy`, `Pillow`,
`shapely`, `pyclipper`, omitting `PyYAML`, `six`, and `tqdm`; corrected
here. `onnxruntime` itself further pulls `flatbuffers`, `packaging`, and
`protobuf`; `tqdm` pulls `colorama` on Windows. Every package in this full
tree installs from a prebuilt wheel on Windows/Python 3.12 — zero
build-from-source steps observed — and detection/classification/
recognition ONNX models are bundled inside the `rapidocr_onnxruntime` wheel
itself, so engine construction performs no network access (measured: under
one second, no download attempt). Accepts raw image bytes directly.

Both providers defer their real third-party imports to first actual use
(not module load time), so a missing/broken install of either dependency
cannot prevent the Kernel from booting every other engine.

There is no separate `IDocumentIntelligenceEngine` provider protocol — that
name refers to the engine facade itself.

---

## 5. Data Models

All Pydantic v2, frozen (`ConfigDict(frozen=True)`), matching platform
convention. Defined in `document_intelligence/models.py`.

- **`DocumentParseRequest`**: `document_id`/`version_id` (optional,
  correlation-only), `bucket_name`+`object_key` XOR `content` (exactly one
  required), `mime_type` (required), `session_token: TokenPayload`
  (required — the sole source of tenant authority, see §6), `options`.
- **`StructureAnalysisRequest`**: `parsed_result`/`ocr_result` (at least one
  required). No `session_token` — this capability performs no tenant-scoped
  Storage I/O.
- **`ParsedDocumentResult`**: `raw_text`, `structured_tables:
  list[ExtractedTable]`, `metadata_fields`, `page_count`, `language`.
- **`ExtractedTable`**: `table_id`, `page_number`, `rows: list[list[str]]`.
- **`OCRResult`**: `text`, `layout_blocks: list[DocumentLayoutBlock]`,
  `average_confidence`, `engine_used`.
- **`DocumentLayoutBlock`**: `block_type` (structural only — `"text"`/
  `"table"`, never a business classification), `page_number`, `text`,
  `bounding_box`, `confidence`, `source` (`"pdf"`/`"ocr"`).

**No model carries a `tenant_id` field** — request models use
`session_token` exclusively (§6); result models are transient values
returned to an already tenant-authorized caller. Verified by a dedicated
test (`test_request_carries_no_plain_tenant_id_field`,
`test_result_models_carry_no_tenant_field`).

These are deliberately new, engine-owned types — not a reuse of the
existing, dormant `kortex.engines.document.interfaces.IDocumentParser`/
`models.DocumentExtractionResult` (Document-Engine-local, never promoted to
a shared layer; reusing them as-is would require an unprecedented
cross-engine import of another engine's private module tree).

---

## 6. Security & Tenant Authority (HARD invariant)

The authoritative tenant is **never** a caller-supplied claim. Mechanism,
identical in shape to `WorkflowEngine`'s own established pattern:

1. The caller places the same `TokenPayload` used for
   `CapabilityRequest.session_token` a second time inside
   `DocumentParseRequest.session_token` (the only way it survives into the
   handler — `CapabilityDispatcher._invoke_handler` forwards only
   `request.parameters`, never `context` or the top-level `session_token`).
2. Every tenant-scoped handler independently re-verifies it via
   `SecurityEngine.authentication_manager.verify_token()`, obtained through
   `kernel.get_engine("security")` and duck-typed
   (`hasattr(..., "authentication_manager")`) — never a direct
   `SecurityEngine` import.
3. Only the resulting `SecurityPrincipal.tenant_id` is used to construct
   Storage Engine keys: `self._tenant_bucket(tenant_id, request.bucket_name)`
   always prefixes the **verified** tenant onto the caller-supplied bucket
   name, so a forged `bucket_name`/`object_key` can at most resolve to a
   nonexistent path inside the caller's own tenant namespace — never into
   another tenant's real object.

Separately, `context["resource_tenant_id"]` (checked by `ABACEvaluator`
against `principal.tenant_id`, unconditionally, fail-closed) gates whether
the capability dispatches at all — before the handler ever runs. Both
mechanisms are pre-existing platform infrastructure; neither the dispatcher
nor Security Engine was modified.

**Proven by** `backend/tests/unit/test_document_intelligence_security.py`,
notably `test_forged_bucket_cannot_redirect_to_tenant_b_storage`: an
authenticated tenant-A principal, presenting a request whose
`bucket_name`/`object_key` are deliberately set to match tenant B's real,
existing object, receives `StorageAccessError` (object not found in
tenant A's own namespace) — never tenant B's content.

### 6.1 Platform-level gap — RESOLVED (KORTEX Platform Security: Capability Identity Propagation)

**Historical note, kept for record**: this section previously documented a
confirmed, unresolved platform-level identity-confusion gap, proven by an
`xfail(strict=True)` test — if `CapabilityRequest.session_token` (tenant A)
and `DocumentParseRequest.session_token` (a separately valid tenant-B
token) disagreed, this engine's handler independently re-verified the
nested token and acted under tenant B's identity, reading tenant B's real
storage under an authorization decision only ever made for tenant A.

**This is now closed, not merely worked around.** `DocumentParseRequest`
no longer has a `session_token` field (or any credential field) at all —
the vector the exploit required is structurally gone. Identity is now
delivered exclusively via the Kernel's dispatcher-injected, immutable
`CapabilityExecutionContext` (`kortex.core.dispatch`): `handle_pdf_parse`/
`handle_ocr_extract` receive `execution_context.tenant_id` directly, and
`_verify_principal()` — the method that used to perform the independent
re-verification — has been deleted entirely.

The regression test that used to be `xfail` is now
`test_identity_confusion_attack_is_structurally_impossible` in
`test_document_intelligence_security.py` — a normal, passing test proving
the original exploit scenario fails at every layer. A repo-wide static
guard (`backend/tests/unit/test_capability_identity_propagation_
architecture.py`) additionally proves no capability handler anywhere in
the codebase independently authenticates a credential outside the Kernel's
own sanctioned dispatch path. `WorkflowEngine`'s four analogous sites, and
an adjacent Workflow approval-impersonation defect, were fixed in the same
milestone — see the platform-level identity-propagation architecture
report and implementation for full detail.

---

## 7. Storage Boundary

`content = await object_store.get_object(bucket_name=f"docint/{tenant_id}/{bucket_name}", object_key=object_key)`

`IObjectStore`/`IFileStore` are bytes-in/bytes-out only (verified directly
against `storage/interfaces.py` — no stream or filesystem-path abstraction
exists at the Storage Engine boundary, so none was invented here). Neither
provider requires a filesystem path: `pdfplumber.open(io.BytesIO(content))`
and `RapidOCR()(image_bytes)` both operate fully in-memory. No raw
`open()`/`tempfile.*` is used anywhere in this engine.

---

## 8. Capabilities

| Capability | Handler | Permission | Classification |
|---|---|---|---|
| `kortex.document_intelligence.pdf.parse` | `handle_pdf_parse` | `document_intelligence:parse` | `INTERNAL` |
| `kortex.document_intelligence.ocr.extract` | `handle_ocr_extract` | `document_intelligence:parse` | `INTERNAL` |
| `kortex.document_intelligence.structure.analyze` | `handle_structure_analyze` | `document_intelligence:analyze` | `INTERNAL` |

All `requires_authentication=True`. `structure.analyze` is a pure
composition capability over already-computed `ParsedDocumentResult`/
`OCRResult` — it does not invoke `pdf.parse`/`ocr.extract` automatically,
performs no I/O, and introduces no semantic interpretation. Chaining is the
caller's responsibility (a recipe/workflow step), keeping each step
separately authorized and audited at the Kernel dispatch boundary rather
than hidden inside one opaque handler.

---

## 9. Resource Limits & Error Semantics

`DocumentIntelligenceConfig`: `operation_timeout_seconds` (default 30s),
`max_input_size_bytes` (default 25MB), `max_pdf_pages` (default 200),
`max_ocr_image_bytes` (default 10MB). Every provider call is wrapped in
`asyncio.wait_for`.

Typed exception hierarchy (`document_intelligence/exceptions.py`, rooted at
`DocumentIntelligenceError`, deliberately independent of
`kortex.engines.document.exceptions`): `TenantAuthorityError`,
`StorageAccessError`, `CorruptedDocumentError`, `EncryptedDocumentError`
(distinguished from corruption via `pdfminer.pdfdocument.PDFEncryptionError`
checked against the implicit `__context__` chain — not a message-string
match), `UnsupportedImageError`, `ExtractionTimeoutError`,
`ResourceLimitExceededError`. Extracted document content is never logged.

---

## 10. Persistence

**Stateless.** No new relational schema, no Alembic migration, no
background/in-memory state carried between requests. Results are computed
on demand and returned directly in the capability response; nothing this
engine produces is written back to durable storage.

"Stateless" describes this engine's own **output** side only — it does not
mean Storage is unreachable. Storage remains an allowed **input** boundary:
`pdf.parse`/`ocr.extract` read caller-referenced bytes from `IObjectStore`
(§7) as part of resolving what to process. Reading an input through the one
constitutionally sanctioned storage gateway is not persistence; persistence
would mean this engine itself writing new, durably retrievable state — which
it does not do.

If a future need for durable, searchable extraction results emerges, the
correct owner is the Knowledge Engine (`IKnowledgeSourceProvider`/
`index_source()`) — not a table owned by this engine; that wiring remains
explicitly deferred.

---

## 11. Testing Strategy

- **Contracts** (`test_document_intelligence_models.py`): input-union
  invariants, tenant-field absence, immutability.
- **PDF** (`test_document_intelligence_pdf_parser.py`): fixture-driven —
  normal text, metadata, multi-page, tables, empty, malformed, encrypted,
  deterministic repeated parsing.
- **OCR** (`test_document_intelligence_ocr_provider.py`): fixture-driven —
  clear text, bounding boxes, confidence, non-text image (empty result, not
  an error), malformed image, timeout enforcement, deterministic behavior.
- **Structure** (`test_document_intelligence_structure_analyzer.py`): PDF
  input, OCR input, both, neither, determinism, no semantic content.
- **Security** (`test_document_intelligence_security.py`): the tenant
  adversarial suite (§6), ABAC mismatch/missing-context rejection, missing
  token rejection, RBAC permission denial.
- **Architecture** (`test_document_intelligence_architecture.py`):
  AST-based, enforced (not merely documented) checks that no forbidden
  engine import exists anywhere in this package.
- **Lifecycle** (`test_document_intelligence_engine.py`): boot/ready/stop,
  capability registration, double-initialize rejection, fail-fast boot when
  the declared Storage dependency is missing.
- **Integration** (`tests/integration/test_document_intelligence_
  integration.py`): full Kernel boot alongside every other production
  engine (Storage, Security, Connector, Workflow, Document, Knowledge), all
  three capabilities exercised end-to-end through real capability dispatch,
  clean shutdown.

---

## 12. Non-Goals (Explicit)

No semantic business document classification. No Knowledge Engine
ingestion wiring. No AI Orchestration Engine dependency or modification. No
Document Engine integration or modification. No persistence/migrations. No
Marketplace model management, model download service, or model registry —
Phase 4 uses the upstream package-distributed model set as-is. No Process
Intelligence, Sentinel, Monitoring, Backup, Recovery, Update Engine,
Docker, or Desktop installer work.
