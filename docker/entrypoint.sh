#!/usr/bin/env bash
#
# KORTEX OS — container entrypoint.
#
# Sequence (implementation_plan.md §7, §12):
#   1. Fail-closed preflight on required runtime secrets (a container-boundary
#      safeguard only -- it does not modify kernel_bootstrap.py's own
#      Python-level ephemeral-key fallback, and it does not implement any new
#      key-management subsystem: it validates the exact two env vars
#      kernel_bootstrap.py already reads).
#   2. `alembic upgrade head` -- the repository's existing, unmodified
#      migration mechanism. Kernel.boot()'s own unconditional
#      create_all_tables() call runs afterward as a harmless no-op on an
#      already-migrated schema (proven equivalent by the accepted
#      test_create_all_and_alembic_schema_are_equivalent test) -- this script
#      does not disable or replace that call.
#   3. exec uvicorn -- replaces this shell process (PID 1) so SIGTERM/SIGINT
#      reach uvicorn directly for its existing, unmodified graceful-shutdown
#      handling. No custom signal handling is added here.
#
# This script introduces zero new KORTEX capabilities, events, migrations,
# or engine behavior. It only sequences existing, unmodified commands.

set -euo pipefail

log() {
    printf '[entrypoint] %s\n' "$1"
}

fail() {
    printf '[entrypoint] ERROR: %s\n' "$1" >&2
    exit 1
}

# --- Secret preflight -------------------------------------------------------
#
# kernel_bootstrap.py:_resolve_key (L116-128) silently falls back to an
# ephemeral os.urandom(32) key if either of these is unset, which is
# documented (kernel_bootstrap.py's own module docstring, L10-16) as
# "acceptable for M3's demonstration scope, not for a shipped product". A
# container has no equivalent of secure_keys.rs's OS-keychain persistence
# (implementation_plan.md §5), so silently accepting that fallback in a
# production container would mean every restart invalidates every session
# and every previously-encrypted secret with no error raised anywhere. This
# preflight converts that into a loud, fail-fast failure at the container
# boundary, per the operator-supplied-secret model implementation_plan.md
# §5/§20 (OD-2) specifies as the v1 posture.
#
# The check approximates (does not replace) kernel_bootstrap.py's own
# validation: a well-formed key is either a "0x"-prefixed 64-hex-character
# string (32 bytes decoded), or a plain value that is exactly 32 bytes long.
# kernel_bootstrap.py's own _resolve_key remains the authoritative validator
# at the Python layer (it raises ValueError on any other length) -- this is
# a coarser, container-level safeguard that fails in the same cases, not a
# replacement for it.
require_key() {
    local var_name="$1"
    local value="${!var_name:-}"

    if [[ -z "${value}" ]]; then
        fail "${var_name} is required and was not set. Refusing to start with a silently-generated ephemeral key in a container deployment (see implementation_plan.md §5, §20 OD-2)."
    fi

    if [[ "${value}" == 0x* ]]; then
        local hex="${value#0x}"
        if [[ ! "${hex}" =~ ^[0-9a-fA-F]{64}$ ]]; then
            fail "${var_name} is 0x-prefixed but does not decode to exactly 32 bytes (expected 64 hex characters)."
        fi
    elif (( ${#value} != 32 )); then
        fail "${var_name} must be exactly 32 bytes (got ${#value}). Use the 0x-prefixed hex form (openssl rand -hex 32) if your key contains non-UTF-8-safe bytes."
    fi
}

require_key KORTEX_MASTER_KEY
require_key KORTEX_AUTH_SIGNING_PRIVATE_KEY

: "${KORTEX_STORAGE_DIR:?KORTEX_STORAGE_DIR must be set (expected: storage_data -- see implementation_plan.md §6)}"
: "${KORTEX_DATABASE_URL:?KORTEX_DATABASE_URL must be set explicitly for container deployments (see implementation_plan.md §5)}"

# --- Canonical storage root, prepared once, up front -------------------------
#
# StorageEngine, BackupEngine, RecoveryEngine, and UpdateEngine each create
# their own subdirectories lazily (Path.mkdir(parents=True, exist_ok=True))
# once they are constructed during Kernel.boot() -- but that only happens
# once uvicorn starts, AFTER the migration step below. On a genuinely fresh
# volume, the SQLite file's parent directory must already exist before
# Alembic/aiosqlite can open it (SQLite itself never creates directories,
# only the file) -- this is that one, narrow, one-time preparation step, not
# a duplicate of any engine's own directory-creation logic.
log "Preparing canonical storage root (${KORTEX_STORAGE_DIR})..."
mkdir -p "${KORTEX_STORAGE_DIR}"

# --- Database migration ------------------------------------------------------
#
# Run from /app/backend in a subshell so the outer script's working
# directory (WORKDIR /data, the canonical storage root -- §6) is unaffected
# once uvicorn execs below.
log "Running database migrations (alembic upgrade head)..."
if ! (cd /app/backend && alembic -c alembic.ini upgrade head); then
    fail "alembic upgrade head failed -- refusing to start the application against a possibly half-migrated schema. No in-place downgrade is attempted; restore /data from an independent backup if needed (implementation_plan.md §7, §17)."
fi
log "Migrations complete."

# --- Application startup ----------------------------------------------------
log "Starting KORTEX (uvicorn kortex.api.main:app)..."
exec uvicorn kortex.api.main:app --host 0.0.0.0 --port 8000
