// M7.1 first-run bootstrap capability call. Calls the real, already-audited
// `kortex.security.bootstrap.create_admin` capability
// (`backend/src/kortex/engines/security/engine.py`) — this module
// introduces no new backend capability or endpoint of its own, mirroring
// `authCapability.ts`'s own "integrate, don't invent" precedent exactly.

import { invokeCapability, type IpcResultEnvelope } from "@/ipc/client";
import type { BootstrapCredentials } from "./authTypes";

const BOOTSTRAP_CAPABILITY = "kortex.security.bootstrap.create_admin";

function newRequestId(): string {
  return crypto.randomUUID();
}

export type BootstrapOutcome =
  | { ok: true }
  | { ok: false; kind: "VALIDATION_FAILED"; message: string }
  | { ok: false; kind: "ALREADY_BOOTSTRAPPED"; message: string }
  | { ok: false; kind: "BACKEND_UNAVAILABLE"; message: string };

/**
 * Calls `kortex.security.bootstrap.create_admin`. Never throws — a
 * rejected `invokeCapability` promise is caught and reported as
 * BACKEND_UNAVAILABLE, mirroring `authCapability.ts::login`'s identical
 * handling of the same failure mode.
 *
 * Error classification: `BootstrapClosedError` (the system already has an
 * administrator — including the rare genuine-concurrency-race outcome,
 * which surfaces as a generic storage failure rather than this specific
 * error — see `AuthenticationManager.bootstrap_first_admin`'s own
 * docstring) maps through `AuthenticationError` to the `PERMISSION_DENIED`
 * category/401 status, same as every other identity-layer fail-closed
 * rejection. `BootstrapValidationError` (empty fields, weak password) has
 * no dedicated category in the existing six-value taxonomy and falls to
 * the generic `EXECUTION_FAILED` category — surfaced here as
 * `VALIDATION_FAILED` using the message text, not a category branch, since
 * `EXECUTION_FAILED` is also what a genuine backend-side failure produces;
 * this is the best distinction available without inventing a new wire
 * category (out of scope for M7.1 per the master prompt's Rule 6).
 */
export async function bootstrapFirstAdmin(credentials: BootstrapCredentials): Promise<BootstrapOutcome> {
  let envelope: IpcResultEnvelope;
  try {
    envelope = await invokeCapability({
      requestId: newRequestId(),
      capabilityName: BOOTSTRAP_CAPABILITY,
      parameters: {
        tenant_id: credentials.tenantId,
        principal_id: credentials.principalId,
        password: credentials.password,
      },
    });
  } catch {
    return { ok: false, kind: "BACKEND_UNAVAILABLE", message: "The backend is unreachable." };
  }

  if (envelope.status === "SUCCESS") {
    return { ok: true };
  }

  const category = envelope.errors[0]?.category;
  const message = envelope.errors[0]?.message ?? "Setup failed.";

  if (category === "SERVICE_UNAVAILABLE" || category === "TIMEOUT_EXCEEDED") {
    return { ok: false, kind: "BACKEND_UNAVAILABLE", message: "The backend is unreachable." };
  }
  if (category === "PERMISSION_DENIED") {
    return { ok: false, kind: "ALREADY_BOOTSTRAPPED", message };
  }
  return { ok: false, kind: "VALIDATION_FAILED", message };
}
