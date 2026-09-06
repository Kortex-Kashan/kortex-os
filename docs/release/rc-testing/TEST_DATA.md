# KORTEX RC Test Dataset

Deterministic, artificial, and identical for every tester. Created by `seed_rc_dataset.py`.

> **These credentials are published test values, not secrets.** They exist so results are comparable across testers and machines. **Never use them in any real environment.** They must never appear in a production database.

## 1. Why a seeder is required

KORTEX ships **no capability** to create tenants, users, or RBAC grants. `kortex.security.bootstrap.create_admin` creates exactly one administrator on a fresh install and then closes permanently.

This is explicit, documented architecture — `security/models.py::RolePermissionRecord`:

> *"There is no provisioning capability in M4 (matching M3's PrincipalRecord precedent): no role has any row here unless a caller inserts it directly via IDataStore, so RBAC fails closed (denies) for every role until explicitly granted."*

The RC security tests require a second tenant and differentiated users, so there is no product path to set them up. The seeder uses the same direct-insert mechanism the repository's own integration tests use. It is test tooling, is never shipped, and must never run against a real deployment.

**This is worth the Chief Architect's attention on its own**: a product that cannot create its second user without direct database access is a real product-completeness observation, independent of whether it blocks the RC.

## 2. Tenants

| Tenant ID | Purpose |
|---|---|
| `tenant-alpha` | Primary tenant. Almost all functional testing happens here |
| `tenant-beta` | Isolation counterpart. Exists to prove Alpha cannot see it and vice versa |

## 3. Principals

All are `principal_type: USER`, enabled.

| Tenant | Principal ID | Password (test-only) | Role | Clearance | Purpose |
|---|---|---|---|---|---|
| `tenant-alpha` | `alpha-admin` | `rc-test-alpha-admin` | `rc-admin` | `RESTRICTED` | Full access, including `system:*` |
| `tenant-alpha` | `alpha-manager` | `rc-test-alpha-manager` | `rc-manager` | `INTERNAL` | Business operator; no system administration |
| `tenant-alpha` | `alpha-employee` | `rc-test-alpha-employee` | `rc-employee` | `INTERNAL` | Read-mostly; must be denied writes/system access |
| `tenant-alpha` | `alpha-restricted` | `rc-test-alpha-restricted` | `rc-restricted` | `PUBLIC` | **Zero grants.** Every privileged call must fail closed |
| `tenant-beta` | `beta-admin` | `rc-test-beta-admin` | `rc-admin` | `RESTRICTED` | Second-tenant administrator for isolation tests |

> **Clearance ranks are counter-intuitive:** `PUBLIC(0) < INTERNAL(1) < CONFIDENTIAL(2) < RESTRICTED(3)`. `RESTRICTED` is the **highest** clearance, not the lowest. Most Phase 7 engine capabilities require `INTERNAL` or above. `alpha-restricted` is deliberately `PUBLIC` — the lowest.

## 4. RBAC grants

Permission strings are the real ones declared by capabilities in the repository — none are invented.

**`rc-admin`** (26 permissions) — finance read/write, HR employee/attendance/leave incl. approve, payroll run/payslip, operations vehicle/incident incl. manage, `security:secret:write`, and all `system:*` (backup, monitoring, sentinel, recovery, update — read and manage).

**`rc-manager`** (15) — finance read/write; HR employee/attendance read+write, leave read + approve; payroll run/payslip **read only**; operations vehicle/incident read+write; `system:monitoring:read`. **No** `system:*:manage`, **no** `security:secret:write`.

**`rc-employee`** (9) — read-only across finance/HR/payroll/operations, plus `hr:attendance:write` and `hr:leave:write` (a user may clock in and request leave). **No** approve, **no** system permissions.

**`rc-restricted`** (0) — intentionally empty. RBAC fails closed, so every privileged capability must be denied. **These denials are the test, not a defect.**

## 5. Business data

The seeder creates **identity and permissions only** — no business records. Business data is created *through the product* during the Golden Path (`TEST_CASES.md` GP-23…GP-26), because exercising those capabilities is itself the test. Seeding invoices directly would bypass the very code paths under test.

Suggested deterministic identifiers so results are comparable:

| Domain | Identifier | Created in |
|---|---|---|
| Finance | invoice ref `RC-INV-001` | GP-23 |
| HR | employee `RC-EMP-001` | GP-24 |
| Payroll | payroll run over `RC-EMP-001` | GP-25 |
| Operations | vehicle `RC-VEH-001`, incident `RC-INC-001` | GP-26 |
| Documents | one small artificial PDF, one small artificial image | GP-09/GP-10 |
| Knowledge | one artificial source with a distinctive term | GP-12 |
| Backup | invoice `RC-BACKUP-001` before backup | BR-01 |

For tenant-isolation tests, create an equivalent record under `tenant-beta` as `beta-admin` (e.g. `RC-INV-BETA-001`) so both directions can be checked.

## 6. Running the seeder

Run with the application **stopped**, against the same database it uses:

```powershell
# Windows desktop
$env:KORTEX_DATABASE_URL = "sqlite+aiosqlite:///$env:APPDATA/com.kortex.desktop/storage_data/kortex_local.db"
python docs/release/rc-testing/seed_rc_dataset.py
```
```bash
# Docker (container stopped, against the mounted volume)
KORTEX_DATABASE_URL="sqlite+aiosqlite:///./data/storage_data/kortex_local.db" \
  python docs/release/rc-testing/seed_rc_dataset.py
```

Expected first run:
```
Principals created: 5 (already present, left untouched: 0)
RBAC grants created: 50 (already present, left untouched: 0)
```
Expected re-run (idempotent):
```
Principals created: 0 (already present, left untouched: 5)
RBAC grants created: 0 (already present, left untouched: 50)
```

The database must already have its schema — the desktop app and the Docker entrypoint both run migrations automatically on first start, so **launch once before seeding**.

## 7. Validation status of this dataset

Validated against a real backend while preparing this environment:

| Check | Result |
|---|---|
| Seeder runs against a real Alembic-migrated database | 5 principals + 50 grants created |
| Idempotency | Re-run created 0, modified nothing |
| `alpha-admin` authenticates | 200, session token issued |
| `alpha-admin` → `kortex.monitoring.metrics.get` (granted) | **200 — allowed** |
| `alpha-restricted` authenticates | 200 |
| `alpha-restricted` → `kortex.monitoring.metrics.get` (no grant) | **403 PERMISSION_DENIED — correctly denied** |
| `alpha-admin` with wrong password | **401** |

This validates the dataset and the RBAC path. It is **not** a validation of the RC as a whole.
