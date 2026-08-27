import { fetchSystemHealth } from "@/ipc/client";

/**
 * One engine's raw diagnostic report, verbatim from the backend
 * (`kortex.core.base_engine.BaseEngine.health_check`). Every engine adds
 * its own extra fields on top of this minimum (see the individual engines
 * under `backend/src/kortex/engines/`) — `[key: string]: unknown` carries
 * those through untyped rather than hard-coding one shape per engine.
 */
export interface EngineHealthReport {
  status?: string;
  healthy?: boolean;
  error?: string;
  [key: string]: unknown;
}

/**
 * Exact shape of `GET /health`'s JSON body
 * (`backend/src/kortex/core/kernel.py::Kernel.health_check`). This is the
 * Kernel's own snake_case diagnostic contract, not the camelCase
 * `IpcResultEnvelope` shape used by `/capabilities/invoke` — the two are
 * unrelated wire formats and must not be confused.
 */
export interface SystemHealthReport {
  kernel_state: string;
  db_dialect: string;
  db_connected: boolean;
  system_health: {
    status: string;
    engines: Record<string, EngineHealthReport>;
  };
}

/**
 * Thrown for both a genuine transport failure (backend unreachable,
 * unparseable response) and a response that doesn't match the minimum
 * shape the Dashboard depends on. Callers treat both identically as
 * "health data is not currently available" — the honest state to show
 * either way, since rendering a status page while unsure the data means
 * what the UI assumes it means would misrepresent system state.
 */
export class SystemHealthUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SystemHealthUnavailableError";
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Validates only the fields the Dashboard actually reads
 * (`system_health.status` / `system_health.engines`), not every field
 * `health_check()` happens to return today — additive backend fields must
 * not break this.
 */
function assertSystemHealthShape(body: unknown): asserts body is SystemHealthReport {
  if (!isPlainObject(body)) {
    throw new SystemHealthUnavailableError("Health response was not a JSON object.");
  }
  const systemHealth = body.system_health;
  if (!isPlainObject(systemHealth) || typeof systemHealth.status !== "string") {
    throw new SystemHealthUnavailableError("Health response is missing system_health.status.");
  }
  if (!isPlainObject(systemHealth.engines)) {
    throw new SystemHealthUnavailableError("Health response is missing system_health.engines.");
  }
}

/**
 * Fetches and validates the current system health report.
 *
 * Deliberately routed through `fetchSystemHealth` (`GET /health` via its
 * own Tauri command), not `invokeCapability` — `/health` is an
 * intentionally unauthenticated Kernel diagnostic route, not a capability,
 * per `backend/src/kortex/api/main.py`. No session token is read or
 * required for this call.
 */
export async function getSystemHealth(): Promise<SystemHealthReport> {
  let outcome;
  try {
    outcome = await fetchSystemHealth();
  } catch (cause) {
    // `fetchSystemHealth` (a thin Tauri `invoke` wrapper) is expected to
    // resolve with `{ ok: false, error }` for a transport failure, never
    // reject — but defensively normalizing any unexpected rejection into
    // the same error type keeps every failure mode this module can
    // produce consistent for callers, rather than leaking a raw,
    // un-user-facing exception message through untouched.
    throw new SystemHealthUnavailableError(
      cause instanceof Error ? cause.message : "Unable to reach the KORTEX backend.",
    );
  }
  if (!outcome.ok) {
    throw new SystemHealthUnavailableError(outcome.error ?? "Unable to reach the KORTEX backend.");
  }
  assertSystemHealthShape(outcome.body);
  return outcome.body;
}

/**
 * Engines report health two different ways across the backend: some set
 * `status` to the literal string "healthy"/"unhealthy"; others set
 * `status` to their raw engine-state value (e.g. "RUNNING") and carry a
 * separate `healthy` boolean instead. Mirrors the backend's own
 * `kortex.engines.boot.engine._report_is_healthy` fix — kept in sync
 * deliberately, see that function's docstring for why both conventions
 * exist.
 */
export function isEngineHealthy(report: EngineHealthReport): boolean {
  if (typeof report.healthy === "boolean") {
    return report.healthy;
  }
  return typeof report.status === "string" && report.status.toLowerCase() === "healthy";
}

/**
 * The Registry Engine is the one engine that already reports a capability
 * count (`kortex.engines.registry.engine.RegistryEngine.health_check`).
 * Returns `null` — never a fabricated number — when that engine or field
 * isn't present in the response.
 */
export function engineCapabilityCount(engines: Record<string, EngineHealthReport>): number | null {
  const count = engines.registry?.capabilities_count;
  return typeof count === "number" ? count : null;
}
