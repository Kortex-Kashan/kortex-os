# KORTEX OS — License Engine Implementation Specification

**Milestone**: M5.7  
**Status**: IMPLEMENTED — AWAITING REVIEW  
**Architecture Authority**: Chief Architect (KASHAN)  
**Implementation Agent**: Antigravity  
**Engine Package**: `backend/src/kortex/engines/license/`  
**Migration Revision**: `b4e89f123c5a` (`kortex_licenses`)  

---

## 1. Executive Summary & Architectural Role

The **License Engine** is an infrastructure engine within KORTEX OS responsible for offline-first cryptographic licensing, tenant entitlement resolution, quota enforcement, and tamper-resistant license state lifecycle management.

In adherence to the **KORTEX Constitution (`AGENTS.md`)**:
- **Infrastructure Only**: The License Engine contains no business rules for business modules (e.g. Finance, CRM). It governs platform entitlements and engine capabilities.
- **Strict Decoupling**: Upward and lateral dependencies are forbidden. The engine has zero imports from `kortex.modules.*`, `workflow`, `document_intelligence`, `process_intelligence`, `ai`, `knowledge`, or `marketplace`.
- **Capability-Based**: Inter-engine interaction occurs strictly through registered capabilities and standard capability execution contexts (`CapabilityExecutionContext`).
- **Offline First**: Validation is 100% local and cryptographic using compiled Ed25519 public vendor root keys (`kortex-root-2026`). No outbound network calls are ever attempted.

---

## 2. Component Architecture

```
                                  +-----------------------+
                                  | Capability Dispatcher |
                                  +-----------+-----------+
                                              | (CapabilityExecutionContext)
                                              v
+---------------------+           +-----------------------+
|  Kernel Bootstrap   | --------> |     LicenseEngine     | <----+ (Cold Start Boot)
+---------------------+           |  (ILicenseProvider)   |      |
                                  +-----------+-----------+      |
                                        |           |            |
                     +------------------+           +------+     |
                     |                                     |     |
                     v                                     v     v
       +----------------------------+       +------------------------------------+
       |    LicenseCryptoEngine     |       |   TenantScopedLicenseRepository    |
       |  (LocalCrypto / Ed25519)   |       |             (IDataStore)           |
       +----------------------------+       +-----------------+------------------+
                     |                                        |
                     v                                        v
       +----------------------------+       +------------------------------------+
       | Canonical JSON & Parser    |       |   kortex_licenses (SQLAlchemy)     |
       +----------------------------+       +------------------------------------+
```

### 2.1 Engine Lifecycle & Protocols
- **`LicenseEngine`** implements `BaseEngine` and `ILicenseProvider`.
- **Declared Dependencies**: `["configuration", "registry", "storage", "security"]`.
- **Registration**:
  - Registered in `backend/src/kortex/api/kernel_bootstrap.py` during Phase 5 boot.
  - IoC registration provides `ILicenseProvider` to the kernel container.
  - Registered capabilities:
    1. `kortex.license.activation.apply` (Permission: `license:manage`)
    2. `kortex.license.activation.revoke` (Permission: `license:manage`)
    3. `kortex.license.status.get` (Permission: `license:read`)
    4. `kortex.license.status.refresh` (Permission: `license:manage`)

### 2.2 Domain Models & Entitlements
- **`LicenseTier`**: `COMMUNITY`, `STARTER`, `PROFESSIONAL`, `ENTERPRISE`.
- **`LicenseStatusEnum`**: `ACTIVE`, `GRACE_PERIOD`, `EXPIRED`, `REVOKED`, `SUPERSEDED`, `UNLICENSED`, `SUSPENDED`.
- **`LicenseScopeEnum`**: Strictly `TENANT` in Milestone M5.7. Any token attempting `SYSTEM` or non-UUID tenant scope is rejected at parse time.
- **`EntitlementSnapshot`**: Immutable frozen snapshot containing:
  - `tenant_id`: str (UUIDv4)
  - `tier`: LicenseTier
  - `status`: LicenseStatusEnum
  - `expires_at`: datetime | None
  - `features`: frozenset[str]
  - `quotas`: Mapping[str, int]
  - `clock_tamper_detected`: bool
  - `is_degraded`: bool
- **Canonical Community Fallback**:
  - Features: `core_workflows`, `local_storage`, `basic_search`, `standard_documents`.
  - Quotas: `max_users: 5`, `max_tenants: 1`, `storage_gb: 10`, `api_calls_per_minute: 60`.

---

## 3. Cryptographic Subsystem & Canonicalization

### 3.1 Token Format
A detached, compact, URL-safe three-part token formatted as:
```
header_b64url . payload_b64url . signature_b64url
```
- **Header**: `{"alg": "Ed25519", "typ": "kortex-license", "kid": "kortex-root-2026", "ver": 1}`
- **Signed Bytes**: `ASCII(header_b64url + "." + payload_b64url)`
- **Signature**: 64-byte raw Ed25519 signature encoded as Base64URL without padding.

### 3.2 KORTEX Constrained Canonicalization Profile
The implementation adheres to a precisely defined KORTEX canonicalization profile:
1. Keys sorted strictly by Unicode code points.
2. Compact formatting with `,` and `:` separators (no whitespace).
3. UTF-8 byte encoding.
4. Integers only; floating point numbers, `NaN`, and `Infinity` are rejected.
5. ISO 8601 UTC datetimes formatted strictly as `YYYY-MM-DDTHH:MM:SSZ`.
6. JSON parser (`parse_json_safe`) enforces duplicate key rejection (`object_pairs_hook`).

### 3.3 Cryptographic Verification
- Backed by `LocalCrypto.verify_ed25519(data, signature, public_key)`.
- Root keys: Compiled vendor dictionary containing official vendor keys (`_OFFICIAL_ROOT_KID = "kortex-root-2026"`). Custom keys are rejected in production mode.

---

## 4. Persistence & Database Migration

### 4.1 Schema Definition (`kortex_licenses`)
Managed via Alembic migration `b4e89f123c5a_create_kortex_licenses_table.py`:

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | String(36) | No | Primary Key (UUIDv4) |
| `license_id` | String(36) | No | Unique license identifier from token claims |
| `tenant_id` | String(64) | No | Tenant subject ID (UUIDv4) |
| `active_tenant_id` | String(64) | Yes | Unique constraint column for concurrency control |
| `scope` | String(16) | No | Scope (`TENANT`) |
| `tier` | String(32) | No | License Tier |
| `status` | String(32) | No | Lifecycle status |
| `raw_token` | Text | No | Complete serialized token |
| `kid` | String(64) | No | Key ID |
| `signature_hex` | String(128) | No | Hex signature |
| `issued_at` | DateTime(UTC) | No | Token issuance timestamp |
| `not_before` | DateTime(UTC) | No | Activation validity start |
| `expires_at` | DateTime(UTC) | Yes | Expiration (NULL for perpetual) |
| `grace_period_days`| Integer | No | Days of grace allowed |
| `features_json` | Text | No | Canonical JSON array of enabled features |
| `quotas_json` | Text | No | Canonical JSON object of quotas |
| `activated_at` | DateTime(UTC) | No | Local activation timestamp |
| `activated_by` | String(64) | No | Principal ID that activated license |
| `revoked_at` | DateTime(UTC) | Yes | Revocation timestamp |
| `revocation_reason`| Text | Yes | Reason for revocation |
| `highest_observed_at`| DateTime(UTC) | No | High-water mark for clock tamper detection |
| `grace_event_emitted`| Boolean | No | Atomic flag for one-time grace event publication |
| `created_at` | DateTime(UTC) | No | Creation timestamp |
| `updated_at` | DateTime(UTC) | No | Update timestamp |

### 4.2 Concurrency & Supersession Invariant
The partial uniqueness invariant is enforced via `active_tenant_id`:
- For `ACTIVE` and `GRACE_PERIOD` licenses: `active_tenant_id = tenant_id`.
- For terminal states (`EXPIRED`, `REVOKED`, `SUPERSEDED`): `active_tenant_id = NULL`.
- Under SQLite and PostgreSQL, `UNIQUE(active_tenant_id)` permits multiple `NULL` values while guaranteeing that at most **one** license is active per tenant at any given moment.
- License replacement executes atomically within a single transaction: existing active license transitions to `SUPERSEDED` (`active_tenant_id = NULL`) before the new license is inserted with `active_tenant_id = tenant_id`.

---

## 5. Security & Tenant Isolation

### 5.1 Identity Propagation
- Handlers derive tenant identity strictly from `execution_context.tenant_id`.
- Caller-supplied `tenant_id` parameters are forbidden from handler signatures.
- Reserved parameters (`execution_context`, `principal`) are rejected fail-closed by `CapabilityDispatcher`.
- Calling a capability without an authenticated session token raises `AuthenticationError`.
- Calling without the required permission (`license:manage` or `license:read`) raises `AuthorizationDeniedError`.
- Activating a token whose `subject_tenant_id` does not match the authenticated caller's tenant raises `TenantMismatchError`.

### 5.2 Tamper Resistance & Clock Rollback
- The engine maintains an in-memory and database monotonic high-water mark `highest_observed_at`.
- If the current wall clock `now` is observed to be more than 300 seconds behind `highest_observed_at`, clock tampering is flagged:
  - `clock_tamper_detected = True`
  - `is_degraded = True`
  - Immediate fail-closed fallback to Canonical Community entitlements.
- Recovery occurs automatically once wall clock advances past the recorded watermark.

---

## 6. Verification & Test Coverage Matrix

A comprehensive suite of 63 automated tests covers the License Engine across unit, security, architecture, migration, and kernel integration layers:

| Test Module | Tests | Focus Area | Status |
|---|---|---|---|
| `test_license_crypto.py` | 14 | Canonicalization profile, duplicate keys, Base64URL, signature verification, tampering | Passed |
| `test_license_models.py` | 13 | Pydantic v2 schemas, non-TENANT scope rejection, chronological invariants, quotas | Passed |
| `test_license_repository.py` | 7 | Activation, supersession, idempotency, watermark updates, grace flags, concurrency | Passed |
| `test_license_engine.py` | 9 | Lifecycle, cold start, provider protocol, perpetual, clock rollback, production key locks | Passed |
| `test_license_capabilities.py`| 10 | Handler execution, activation, status retrieval, revocation, refresh, mismatch checks | Passed |
| `test_license_security.py` | 5 | Dispatcher integration, authentication, RBAC, reserved parameter rejection, cross-tenant isolation | Passed |
| `test_license_architecture.py`| 3 | AST boundary checks, forbidden engine/module imports, handler signature cleanliness | Passed |
| `test_license_migration.py` | 1 | Alembic upgrade to head, table verification, schema inspection, downgrade to baseline | Passed |
| `test_license_kernel_integration.py` | 1 | Full kernel bootstrap, provider resolution from IoC, end-to-end capability lifecycle | Passed |
| **Total License Engine Tests** | **63** | **100% Passing** | **Passed** |

In addition:
- Static Type Checking: `mypy` passed with **0 errors** across all 9 engine source files and bootstrap bindings.
- Linting & Formatting: `ruff check` and `ruff format` passed with **0 errors**.
- Database Migration Suite: `test_alembic_migrations.py` passed all **6 tests**, proving `Base.metadata.create_all()` and Alembic schemas are 100% equivalent.
- Regression Verification: Full unit test suite verified (2,590 passing tests; zero new failures introduced).
