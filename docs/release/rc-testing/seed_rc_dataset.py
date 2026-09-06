"""KORTEX RC test-dataset seeder — TEST TOOLING, NOT PRODUCT CODE.

WHY THIS EXISTS
---------------
KORTEX deliberately ships no provisioning capability for principals, tenants,
or RBAC grants. This is an explicit, documented architectural state, not an
oversight — see `security/models.py::RolePermissionRecord`'s own docstring:

    "There is no provisioning capability in M4 (matching M3's PrincipalRecord
     precedent): no role has any row here unless a caller inserts it directly
     via IDataStore, so RBAC fails closed (denies) for every role until
     explicitly granted."

`kortex.security.bootstrap.create_admin` creates exactly ONE administrator on a
fresh install and then closes permanently. There is therefore no supported
product path to create a SECOND tenant, additional users, or permission grants
— which the RC security tests (tenant isolation, RBAC denial) require.

This script is the same mechanism the repository's own integration tests use
(direct `PrincipalRecord`/`RolePermissionRecord` inserts). It exists solely to
make RC testing reproducible. It is not part of the product, is not shipped in
any installer or image, and must never be run against a real deployment.

CREDENTIALS IN THIS FILE ARE DELIBERATELY ARTIFICIAL AND PUBLIC
---------------------------------------------------------------
The passwords below are fixed, published test values. They are not secrets and
must never be used in any real environment. Their whole purpose is determinism:
every tester gets an identical dataset.

USAGE
-----
Point it at the SAME database the backend uses, then run it with the backend
STOPPED (SQLite tolerates concurrent readers, but seeding while the app runs
can interleave with its own writes):

    # Docker RC environment (host-side, against the mounted volume)
    KORTEX_DATABASE_URL="sqlite+aiosqlite:///./data/storage_data/kortex_local.db" \
        python docs/release/rc-testing/seed_rc_dataset.py

    # Windows desktop RC environment (PowerShell)
    $env:KORTEX_DATABASE_URL =
      "sqlite+aiosqlite:///$env:APPDATA/com.kortex.desktop/storage_data/kortex_local.db"
    python docs/release/rc-testing/seed_rc_dataset.py

Re-running is safe: existing rows are left untouched (idempotent upsert-by-key).

WHAT IT CREATES
---------------
Two tenants, four principals, and the RBAC grants the RC test cases need. See
TEST_DATA.md for the full table and the intent behind each row.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path
from uuid import uuid4

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Allow running straight from a source checkout without installing the package.
_BACKEND_SRC = Path(__file__).resolve().parents[3] / "backend" / "src"
if _BACKEND_SRC.is_dir() and str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

# `kortex.core` MUST be imported before `kortex.engines.security.models`.
# Importing the security models first hits a genuine circular import
# (security.models -> core.db -> core.__init__ -> dispatch -> security.engine
# -> ... -> security.models, still partially initialized). The repository's own
# tests never trip this because they import the app/kernel first, which
# resolves the package cycle in a working order. This is a pre-existing
# import-graph characteristic of the platform (KNOWN_FINDINGS.md
# OBSERVATION-002), not something this script should "fix" in product source.
#
# Done via `importlib` deliberately: a plain `import kortex.core` sits in the
# same contiguous import block as the line below and would be re-sorted after
# it by any import formatter, silently reintroducing the circular-import
# failure. A statement cannot be reordered that way.
importlib.import_module("kortex.core")

from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord

TENANT_ALPHA = "tenant-alpha"
TENANT_BETA = "tenant-beta"

# Roles are arbitrary strings in this platform; permissions are the real,
# repository-declared permission strings each capability requires.
ROLE_ADMIN = "rc-admin"
ROLE_MANAGER = "rc-manager"
ROLE_EMPLOYEE = "rc-employee"
ROLE_RESTRICTED = "rc-restricted"

# Clearance ranks, from `security/abac.py::_CLASSIFICATION_RANK`:
#   PUBLIC(0) < INTERNAL(1) < CONFIDENTIAL(2) < RESTRICTED(3)
# Note the counter-intuitive naming: "RESTRICTED" is the HIGHEST clearance,
# not the lowest. Most Phase 7 engine capabilities require INTERNAL.
CLEARANCE_HIGHEST = "RESTRICTED"
CLEARANCE_INTERNAL = "INTERNAL"
CLEARANCE_LOWEST = "PUBLIC"

# Artificial, published, test-only credentials. Not secrets.
PRINCIPALS: list[dict[str, object]] = [
    {
        "tenant_id": TENANT_ALPHA,
        "principal_id": "alpha-admin",
        "password": "rc-test-alpha-admin",
        "roles": [ROLE_ADMIN],
        "clearance": CLEARANCE_HIGHEST,
        "intent": "Full-access administrator in Tenant Alpha.",
    },
    {
        "tenant_id": TENANT_ALPHA,
        "principal_id": "alpha-manager",
        "password": "rc-test-alpha-manager",
        "roles": [ROLE_MANAGER],
        "clearance": CLEARANCE_INTERNAL,
        "intent": "Business operator: finance/HR/operations read+write, no system:* admin.",
    },
    {
        "tenant_id": TENANT_ALPHA,
        "principal_id": "alpha-employee",
        "password": "rc-test-alpha-employee",
        "roles": [ROLE_EMPLOYEE],
        "clearance": CLEARANCE_INTERNAL,
        "intent": "Read-mostly employee. Must be DENIED write and system capabilities.",
    },
    {
        "tenant_id": TENANT_BETA,
        "principal_id": "beta-admin",
        "password": "rc-test-beta-admin",
        "roles": [ROLE_ADMIN],
        "clearance": CLEARANCE_HIGHEST,
        "intent": "Second tenant. Used to prove Alpha cannot see Beta's data and vice versa.",
    },
    {
        "tenant_id": TENANT_ALPHA,
        "principal_id": "alpha-restricted",
        "password": "rc-test-alpha-restricted",
        "roles": [ROLE_RESTRICTED],
        "clearance": CLEARANCE_LOWEST,
        "intent": "Lowest clearance, no grants at all. Every privileged call must fail closed.",
    },
]

# Real permission strings, verified present in the repository.
ROLE_GRANTS: dict[str, list[str]] = {
    ROLE_ADMIN: [
        "finance:invoice:read",
        "finance:invoice:write",
        "hr:employee:read",
        "hr:employee:write",
        "hr:attendance:read",
        "hr:attendance:write",
        "hr:leave:read",
        "hr:leave:write",
        "hr:leave:approve",
        "payroll:run:read",
        "payroll:run:write",
        "payroll:payslip:read",
        "operations:vehicle:read",
        "operations:vehicle:write",
        "operations:incident:read",
        "operations:incident:write",
        "operations:incident:manage",
        "security:secret:write",
        "system:backup:read",
        "system:backup:manage",
        "system:monitoring:read",
        "system:sentinel:read",
        "system:recovery:read",
        "system:recovery:manage",
        "system:update:read",
        "system:update:manage",
    ],
    ROLE_MANAGER: [
        "finance:invoice:read",
        "finance:invoice:write",
        "hr:employee:read",
        "hr:employee:write",
        "hr:attendance:read",
        "hr:attendance:write",
        "hr:leave:read",
        "hr:leave:approve",
        "payroll:run:read",
        "payroll:payslip:read",
        "operations:vehicle:read",
        "operations:vehicle:write",
        "operations:incident:read",
        "operations:incident:write",
        "system:monitoring:read",
    ],
    ROLE_EMPLOYEE: [
        "finance:invoice:read",
        "hr:employee:read",
        "hr:attendance:read",
        "hr:attendance:write",
        "hr:leave:read",
        "hr:leave:write",
        "payroll:payslip:read",
        "operations:vehicle:read",
        "operations:incident:read",
    ],
    # Deliberately empty: RBAC fails closed, so this principal is denied
    # everything. That denial is itself the test.
    ROLE_RESTRICTED: [],
}


def _database_url() -> str:
    url = os.environ.get("KORTEX_DATABASE_URL")
    if not url:
        raise SystemExit(
            "KORTEX_DATABASE_URL is not set.\n"
            "Point it at the SAME database the KORTEX backend uses; see this file's "
            "docstring for the Docker and Windows-desktop forms."
        )
    if not url.startswith("sqlite+aiosqlite:///"):
        raise SystemExit(
            f"Refusing to seed a non-SQLite URL: {url!r}. The RC topologies are SQLite-only."
        )
    return url


async def _seed() -> None:
    url = _database_url()
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    hasher = PasswordHasher()

    created_principals = 0
    created_grants = 0
    skipped_principals = 0
    skipped_grants = 0

    try:
        async with session_factory() as session:
            for spec in PRINCIPALS:
                tenant_id = str(spec["tenant_id"])
                principal_id = str(spec["principal_id"])
                existing = await session.execute(
                    select(PrincipalRecord).where(
                        PrincipalRecord.tenant_id == tenant_id,
                        PrincipalRecord.principal_id == principal_id,
                        PrincipalRecord.principal_type == "USER",
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    skipped_principals += 1
                    continue
                session.add(
                    PrincipalRecord(
                        id=str(uuid4()),
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        principal_type="USER",
                        enabled=True,
                        credential_hash=hasher.hash(str(spec["password"])),
                        roles=list(spec["roles"]),  # type: ignore[arg-type]
                        attributes={"clearance_level": spec["clearance"]},
                    )
                )
                created_principals += 1

            for role, permissions in ROLE_GRANTS.items():
                for permission in permissions:
                    existing_grant = await session.execute(
                        select(RolePermissionRecord).where(
                            RolePermissionRecord.role == role,
                            RolePermissionRecord.permission == permission,
                        )
                    )
                    if existing_grant.scalar_one_or_none() is not None:
                        skipped_grants += 1
                        continue
                    session.add(
                        RolePermissionRecord(
                            id=str(uuid4()), role=role, permission=permission
                        )
                    )
                    created_grants += 1

            await session.commit()
    finally:
        await engine.dispose()

    print(f"Database: {url}")
    print(
        f"Principals created: {created_principals} (already present, left untouched: {skipped_principals})"
    )
    print(
        f"RBAC grants created: {created_grants} (already present, left untouched: {skipped_grants})"
    )
    print("\nSeeded principals (test-only credentials, documented in TEST_DATA.md):")
    for spec in PRINCIPALS:
        print(
            f"  {spec['tenant_id']:<14} {spec['principal_id']:<18} clearance={spec['clearance']:<12} roles={spec['roles']}"
        )
    print(
        "\nRestricted principal has ZERO grants by design — its denials are a test case, not a defect."
    )


if __name__ == "__main__":
    asyncio.run(_seed())
