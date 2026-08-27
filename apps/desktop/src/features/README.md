# Features

One folder per business feature: `<feature>/components`, `<feature>/hooks`,
and `<feature>/api.ts` (typed capability-call wrappers), per ADR-0002 §7.4.

- `dashboard/` — system health overview (`GET /health`, via its own
  `get_system_health` Tauri command — not a capability call, since
  `/health` is intentionally unauthenticated). The first real business
  feature; registered in `workspace/defaultApps.ts`.
