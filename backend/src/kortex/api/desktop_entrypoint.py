"""Production desktop/frozen entrypoint for the KORTEX backend.

Distinct from `main.py` (the ASGI app object, imported unchanged by both
dev-mode `uvicorn` invocations and this entrypoint) and from
`kortex.engines.update.migrator` (Update Engine's own, currently-unreachable
Alembic driver -- not reused here; Update Engine is not modified by this
module, see implementation_plan.md Part 2 SS D10/D11).

This module exists to solve exactly one problem Docker did not have and
desktop distribution does: a frozen/installed backend has no reliable
Python-source-tree-relative path to its own Alembic migration assets, and
no reliable, safe current-working-directory. Every path this module needs
is therefore computed as an ABSOLUTE path before use, resolved differently
depending on whether the process is a PyInstaller-frozen build
(`sys.frozen`) or a normal source/editable install -- never left as a bare
relative string, and never fixed by changing cwd (implementation_plan.md
Part 2 SS D10, Control 3).

Startup sequence (mirrors Docker's own entrypoint.sh sequencing, Part 1
SS 12): resolve migration assets -> alembic upgrade head -> start uvicorn.
Kernel.boot()'s own unconditional create_all_tables() call (unchanged,
inside kortex.api.main's lifespan) still runs afterward as a harmless
no-op on an already-migrated schema.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger("kortex.api.desktop_entrypoint")

# The *complete* set of tables each migration revision introduces (verified
# by direct extraction from each revision file's own `upgrade()` body -- not
# hand-transcribed, and not just one representative table per revision).
# Order is the actual migration chain, oldest first. Every one of these
# migrations only ever does `op.create_table` (+ `op.create_index` on a
# table it just created in the same migration) -- confirmed by grepping each
# revision's `upgrade()` for every `op.*` call -- so table-existence is a
# sufficient (not merely convenient) proxy for "this revision's schema
# changes are fully present"; there is no `add_column`/`alter_column` this
# check would need to additionally account for.
_REVISION_CHAIN: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "81d6d64c51ba",
        frozenset(
            {
                "ai_agent_tasks",
                "ai_conversation_turns",
                "ai_decision_records",
                "ai_governance_policies",
                "ai_tenant_quotas",
                "approval_delegations",
                "approval_requests",
                "connector_action_history",
                "connector_profiles",
                "document_operation_history",
                "document_operation_profiles",
                "document_template_schemas",
                "documents",
                "event_outbox",
                "external_executions",
                "finance_invoices",
                "idempotency_records",
                "knowledge_annotations",
                "knowledge_packs",
                "knowledge_records",
                "security_audit_records",
                "security_principals",
                "security_role_permissions",
                "security_secrets",
                "workflow_definitions",
                "approval_decisions",
                "document_versions",
                "workflow_instances",
                "workflow_schedules",
                "workflow_step_runs",
            }
        ),
    ),
    ("b4e89f123c5a", frozenset({"kortex_licenses"})),
    (
        "c7d8e9f1a2b3",
        frozenset(
            {
                "hr_employees",
                "hr_attendance_records",
                "hr_leave_balances",
                "hr_leave_requests",
                "hr_payroll_runs",
                "hr_payroll_entries",
            }
        ),
    ),
    ("4c99c2ff7376", frozenset({"ops_vehicles", "ops_vehicle_tracking_records", "ops_incidents"})),
)


def is_frozen() -> bool:
    """True inside a PyInstaller-frozen build."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Absolute directory containing the bundled `alembic.ini`/`alembic/`.

    Frozen (PyInstaller): `sys._MEIPASS`, PyInstaller's own version-stable
    pointer to the actual bundled-data root -- NOT `Path(sys.executable)
    .resolve().parent`. A real build during this milestone's own
    proof-of-concept proved these differ: PyInstaller 6.x's default
    `--onedir` layout places data files (and `alembic.ini`/`alembic/`
    alongside them) under a `_internal/` subdirectory next to the `.exe`,
    not directly beside it. `sys._MEIPASS` already points at the correct
    directory in both `--onedir` (`_internal/`, or the app directory under
    older layouts) and `--onefile` (the per-run temp extraction directory)
    builds, so resolving through it -- rather than re-deriving the
    directory layout ourselves -- survives future PyInstaller layout
    changes.

    Dev/editable install: `backend/`, computed relative to this file's own
    known depth under `backend/src/kortex/api/` -- deliberately a fresh
    computation, not reused from `kortex.engines.update.migrator`'s
    equivalent (and, at present, frozen-unsafe) logic.
    """
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[3]


def resolve_alembic_config() -> Config:
    """Build an Alembic `Config` using only absolute paths.

    Both `script_location` and the ini file itself are set as absolute
    paths. This is the actual fix: Alembic's own bare-relative-string
    `script_location` resolution walks the *process's current working
    directory*, not the ini file's own directory (proven by direct reading
    of the installed `alembic` package's own source -- see
    implementation_plan.md Part 2 SS D10). Passing an absolute path bypasses
    that resolution entirely, regardless of what the process cwd happens
    to be -- this is a CWD-independent fix, not a "cd first" workaround.
    """
    root = resource_root()
    ini_path = root / "alembic.ini"
    script_location = root / "alembic"

    if not ini_path.is_file():
        raise FileNotFoundError(
            f"alembic.ini not found at {ini_path} (frozen={is_frozen()}); "
            "the packaged/installed build is missing its migration assets."
        )
    if not script_location.is_dir():
        raise FileNotFoundError(f"alembic/ migration directory not found at {script_location} (frozen={is_frozen()}).")

    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(script_location))
    return cfg


def resolve_database_path() -> Path | None:
    """Best-effort path to the actual SQLite file this run targets, mirroring
    `alembic/env.py`'s own resolution (`KORTEX_DATABASE_URL`, else the same
    shared default `kortex.core.db._default_sqlite_url()` computes) -- used
    only for the legacy-pre-Alembic-database detection below. Returns `None`
    for a non-SQLite URL: nothing to detect for a database topology this
    entrypoint doesn't target anyway (desktop is SQLite-only, Part 1 SS 7/SS 8
    of this document).

    This module is a *reader* of `KORTEX_DATABASE_URL`/`KORTEX_STORAGE_DIR`,
    never their source of truth. The one authoritative persistent-data root
    for production desktop mode is computed exactly once, in Rust
    (`backend_process.rs::resolve_app_data_dir`, via Tauri's own
    `app.path().app_data_dir()`), and both env vars this function/module
    reads are derived from that SAME resolved directory before this process
    is ever spawned (`resolve_backend_sidecar_config_production_path_with_keys`
    builds `KORTEX_DATABASE_URL` as `<that directory>/storage_data/
    kortex_local.db` directly -- not a second, independently-hardcoded
    path). This function's own fallback to `_default_sqlite_url()` only
    matters for a dev-mode/direct invocation where no such env var was set
    at all; it does not compete with, override, or re-derive the production
    root.
    """
    from kortex.core.db import _default_sqlite_url

    url = os.environ.get("KORTEX_DATABASE_URL") or _default_sqlite_url()
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return None
    return Path(url[len(prefix) :])


def stamp_revision_for_preexisting_database(db_path: Path) -> None:
    """If `db_path` already contains KORTEX's tables but has never recorded
    an Alembic revision, stamp it at the most advanced revision whose
    *complete* table set is verified present -- the "upgrading a
    pre-Desktop-Installers install" scenario (implementation_plan.md Part 2,
    Control 3/SS D10): before this entrypoint existed, `Kernel.boot()`'s own
    unconditional `create_all_tables()` was the only thing that ever created
    these tables, and it does not record migration history. Running
    `alembic upgrade head` unstamped against such a database would try to
    re-create tables that already exist.

    Safety property (verified by dedicated tests,
    `test_desktop_entrypoint_migration.py`): this never stamps past a
    revision whose full table set isn't verified present -- a single
    representative table is never treated as proof an entire revision was
    applied. A genuinely fresh (nonexistent or empty) database, and a
    partial/corrupted schema too incomplete to safely characterize, are both
    left untouched here -- `run_migrations` proceeds with the normal,
    already-proven `upgrade head` path, which either builds a fresh schema
    from scratch or surfaces a loud, diagnosable failure for a genuinely
    inconsistent one.
    """
    if not db_path.is_file():
        return

    conn = sqlite3.connect(str(db_path))
    try:
        existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # The table existing is not sufficient -- proven by a real, encountered
        # case during this milestone's own testing: an `alembic_version` table
        # present but containing zero rows (no revision actually recorded,
        # e.g. from an interrupted prior migration attempt) gives Alembic no
        # usable revision either, identically to the table not existing at
        # all. Only a table that actually has a recorded revision means
        # "already stamped, nothing to do."
        has_recorded_revision = "alembic_version" in existing and bool(
            conn.execute("SELECT 1 FROM alembic_version LIMIT 1").fetchone()
        )
    finally:
        conn.close()

    if has_recorded_revision:
        return  # Already stamped -- nothing to do here.

    # Walk the chain oldest-to-newest, accumulating the *complete* table set
    # each revision requires. `safe_revision` only ever advances past a
    # revision when every single table it introduces is actually present --
    # never on the strength of one representative table alone. This is the
    # deliberate safety property: a database missing even one table from an
    # otherwise-later-looking revision (a genuinely partial/corrupted legacy
    # schema) is stamped no further than the last revision that is fully,
    # completely verified -- never guessed past that point. Whatever gap
    # remains is then left for the normal `upgrade head` call below to
    # surface as a loud, diagnosable failure (e.g. "table already exists" for
    # the subset that *is* present, or a clean create for what's genuinely
    # missing) -- never silently skipped and never silently mis-stamped.
    safe_revision: str | None = None
    for revision, new_tables in _REVISION_CHAIN:
        if not new_tables.issubset(existing):
            break
        safe_revision = revision

    if safe_revision is None:
        # Not even the baseline's complete table set is present -- either a
        # genuinely fresh/empty database (handled normally below) or an
        # unrecognized/partial schema too incomplete to safely characterize
        # at all. Do not guess; let `upgrade head` proceed and surface
        # whatever the real state actually is.
        return

    logger.warning(
        "Database at %s already contains KORTEX's tables (created before migration "
        "tracking existed) but has no Alembic revision stamped. The complete table set "
        "through revision %s is verified present; stamping there instead of attempting "
        "to re-create existing tables. Any gap beyond this revision is left for the "
        "normal migration step to apply or report.",
        db_path,
        safe_revision,
    )
    command.stamp(resolve_alembic_config(), safe_revision)


def run_migrations() -> None:
    """Run `alembic upgrade head` once, before serving traffic.

    Fails loud (propagates the exception) rather than silently falling
    through to `Kernel.boot()`'s weaker `create_all_tables()` guarantee --
    matching Docker's own entrypoint design (Part 1 SS 12/SS 16). No
    in-place downgrade is ever attempted; existing migration history is
    unmodified.
    """
    db_path = resolve_database_path()
    if db_path is not None:
        # SQLite creates the database *file* but never its parent directories,
        # so that directory must already exist before Alembic/aiosqlite can
        # open it. On a genuinely fresh install nothing has created it yet:
        # StorageEngine/BackupEngine/RecoveryEngine/UpdateEngine each create
        # their own subdirectories lazily, but only once `Kernel.boot()`
        # constructs them -- which happens when uvicorn starts, AFTER this
        # migration step. Docker's entrypoint already performs exactly this
        # one-time preparation, for exactly this reason (docker/entrypoint.sh
        # "Canonical storage root, prepared once, up front"); this is the
        # desktop path's missing equivalent, not a duplicate of any engine's
        # own directory-creation logic.
        #
        # Without it the frozen backend dies at startup with
        # `sqlite3.OperationalError: unable to open database file` on every
        # fresh install, Tauri's supervisor exhausts its restart attempts, and
        # /health never becomes reachable -- observed directly against a real
        # installed build, and the cause of the Windows installer smoke test
        # failing in Desktop CI #41 and #42.
        db_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_revision_for_preexisting_database(db_path)

    cfg = resolve_alembic_config()
    logger.info("Running database migrations (alembic upgrade head)...")
    command.upgrade(cfg, "head")
    logger.info("Migrations complete.")


def selftest_ocr() -> None:
    """Diagnostic: exercise the bundled RapidOCR/ONNXRuntime models end to
    end (not just import them) against a synthetic in-memory image. Proves
    the native OCR dependency chain actually runs inference inside a
    frozen build, not merely that it imports cleanly -- `RapidOCR`'s own
    construction is lazy (`ocr_provider.py:_get_engine`), so a successful
    engine boot alone does not exercise this path.
    """
    import cv2
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    logger.info("Running OCR self-test against a synthetic image...")
    img = np.full((120, 400, 3), 255, dtype=np.uint8)
    cv2.putText(img, "KORTEX OCR TEST", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)

    engine = RapidOCR()
    result, _elapse = engine(img)
    logger.info("OCR self-test result: %r", result)
    if not result:
        raise RuntimeError("OCR self-test produced no text detections.")
    recognized = " ".join(line[1] for line in result)
    logger.info("OCR self-test recognized text: %r", recognized)
    print(f"OCR_SELFTEST_OK: {recognized!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="KORTEX production/desktop backend entrypoint.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--selftest-ocr",
        action="store_true",
        help="Run a bundled-model OCR self-test and exit (diagnostic only, no server started).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.selftest_ocr:
        selftest_ocr()
        return

    run_migrations()

    # Imported here (not at module scope, and as the app object directly
    # rather than an import string) so the explicit import is visible to
    # PyInstaller's static analysis and so Kernel.boot() only begins once
    # migrations have already succeeded.
    import uvicorn

    from kortex.api.main import app

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
