# KORTEX Docker

Container configuration for running the KORTEX OS backend headlessly,
without the Tauri desktop shell (`apps/server/README.md`'s documented
intent). This packages the same, unmodified FastAPI/uvicorn application the
desktop sidecar already runs (`kortex.api.main:app`) — Docker is a
deployment boundary around the existing backend, not a second application.

Full architectural evidence and reasoning: `implementation_plan.md` at the
repository root.

## Files

| File | Purpose |
|------|---------|
| `Dockerfile.backend` | Multi-stage production image (builder installs dependencies; runtime stage is minimal, non-root) |
| `entrypoint.sh` | Container entrypoint: validates required secrets, runs `alembic upgrade head`, then execs `uvicorn` |
| `docker-compose.yml` | Local development stack (builds the image locally) |
| `docker-compose.prod.yml` | Production topology (pulls a pre-built, tagged image) |
| `.env.example` | Template for required environment variables — copy to `.env` and fill in real values; `.env` is git-ignored |

## Quick start (development)

```bash
cp docker/.env.example docker/.env
# edit docker/.env: replace both key placeholders, e.g. via:
#   openssl rand -hex 32
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml logs -f
docker compose -f docker/docker-compose.yml down
```

## Production

```bash
cp docker/.env.example docker/.env   # fill in real, operator-managed secrets
KORTEX_IMAGE_TAG=0.1.0 docker compose -f docker/docker-compose.prod.yml up -d
```

No port is published by default in the production topology — front this
service with your own reverse proxy/TLS terminator if it needs to be
reachable outside a private network (the application has no TLS-termination
code of its own).

## Required environment variables

| Variable | Required | Secret? | Default | Behavior if absent |
|---|---|---|---|---|
| `KORTEX_MASTER_KEY` | Yes | Yes | none | `entrypoint.sh` refuses to start (fail-closed) |
| `KORTEX_AUTH_SIGNING_PRIVATE_KEY` | Yes | Yes | none | `entrypoint.sh` refuses to start (fail-closed) |
| `KORTEX_BACKUP_KEY` | No | Yes | falls back to `KORTEX_MASTER_KEY` | Backup Engine fails closed only if *neither* resolves |
| `KORTEX_STORAGE_DIR` | No | No | `storage_data` (baked into the image) | N/A — already set correctly |
| `KORTEX_DATABASE_URL` | No | No | `sqlite+aiosqlite:////data/storage_data/kortex_local.db` (baked into the image) | N/A — already set correctly |

`KORTEX_MASTER_KEY`/`KORTEX_AUTH_SIGNING_PRIVATE_KEY` are the same two
variables `kernel_bootstrap.py` already reads — nothing new was invented.
**Nothing in the current architecture persists these across container
replacement.** The only existing persistence mechanism for them
(`secure_keys.rs`, backed by the OS keychain) is Tauri-desktop-only and has
no container equivalent. You (the operator) must supply the same values
again on every redeploy, via your own secrets manager/vault/orchestrator —
this is the standard pattern for containerized applications generally, not
a KORTEX-Docker-specific gap. Whether achieving keychain-equivalent
persistence is required before this is considered fully production-ready is
an open, non-blocking decision (`implementation_plan.md` §20, OD-2).

## Persistent storage

A single volume is mounted at `/data` (see `implementation_plan.md` §6/§9
for the full evidence). It holds:

- `storage_data/` — documents/blobs (via `StorageEngine`)
- `storage_data/backups/` — Backup Engine artifacts
- `storage_data/.recovery/`, `storage_data/.update/` — Recovery/Update
  journals and staging (only populated once those engines are wired into
  the production kernel bootstrap — see "Recovery/Update" below)
- `kortex_local.db` (+ `-wal`/`-shm`) — the SQLite database

**If the volume is missing or misconfigured, the container does not fail
loudly.** Every directory-creation call in the codebase uses
`Path.mkdir(parents=True, exist_ok=True)`, which recreates empty
directories rather than erroring — a misconfigured mount produces a
silently-fresh, empty instance. Verify your volume configuration explicitly;
this is not something Docker configuration alone can detect for you.

## Database / migrations

SQLite is the only database path this codebase has ever tested or wired
end-to-end (`kortex/core/db.py` has exactly one PostgreSQL-specific line, a
dialect label, with zero PostgreSQL tests anywhere). This image does not
ship a PostgreSQL service.

`entrypoint.sh` runs `alembic upgrade head` once, before `uvicorn` starts,
using the same `KORTEX_DATABASE_URL` the application itself uses. A failed
migration exits the container non-zero before any traffic is served — no
in-place downgrade is ever attempted. If a migration fails destructively,
restore `/data` from an independently-taken backup and retry.

## Health

`GET /health` reflects the same, unmodified KORTEX health contract used
everywhere else in the application — it does not exist specifically for
Docker. It returns HTTP 200 for both `"healthy"` and `"degraded"` states,
503 otherwise; Docker's `HEALTHCHECK` therefore cannot distinguish healthy
from degraded (only a caller that reads the JSON body directly can — e.g.
Monitoring Engine's own dashboard). The endpoint does not respond at all
until the full engine-boot sequence completes — there is no separate
"process alive but still initializing" state visible over HTTP.

The `HEALTHCHECK --start-period=90s` in `Dockerfile.backend` is a
conservative placeholder — no measured cold-start time exists yet for the
full boot sequence. Measure it on your own hardware and adjust before
relying on it for production alerting.

## Update Engine boundary

Update Engine's live filesystem-mutation capability (`kortex.update.apply`)
defaults to swapping files under `backend/src` — a normally read-only image
layer in this topology. **This capability is out of scope / unsupported for
containers.** "Updating KORTEX in Docker" means: build a new image → deploy
it in place of the running container → the persistent volume is preserved →
`entrypoint.sh`'s `alembic upgrade head` carries the database forward. This
is container replacement via image rebuild, not an in-place mutable update.

## Recovery/Update reachability

Recovery Engine and Update Engine are implemented, tested, and accepted —
but as of this image, neither is registered in the production kernel
bootstrap (`kernel_bootstrap.py`), so their capabilities are not reachable
through the running application. This is a pre-existing platform gap that
predates and is independent of Docker (it affects every KORTEX deployment
topology equally, not just this one). Wiring them in is a documented,
narrowly-scoped, **not-yet-authorized** prerequisite correction
(`implementation_plan.md` §19 PC-1, §20 OD-1) — this image does not perform
that wiring. Backup Engine **is** reachable and works as documented above.

## What is explicitly NOT supported

- PostgreSQL, Redis, or any other external database/cache service.
- Kubernetes, Helm, Terraform, Docker Swarm, or any cloud-orchestration
  topology.
- Update Engine live-mutation inside a running container.
- Automatic secret persistence across container replacement.
- A reverse proxy or TLS terminator (bring your own).
