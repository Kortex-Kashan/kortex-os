import { invokeCapability } from "@/ipc/client";
import type {
  ConnectorActionType,
  ConnectorCapabilityTag,
  ConnectorDriver,
  ConnectorProfile,
  CreateConnectionPayload,
} from "./types";

const DRIVER_LIST_CAPABILITY = "kortex.connector.driver.list";
const PROFILE_LIST_CAPABILITY = "kortex.connector.profile.list";
const PROFILE_REGISTER_CAPABILITY = "kortex.connector.profile.register";
const PROFILE_DELETE_CAPABILITY = "kortex.connector.profile.delete";
const SECRET_PUT_CAPABILITY = "kortex.security.secret.put";

/**
 * Thrown when the backend denies the call with `PERMISSION_DENIED` — the
 * one category that covers both "no/invalid session" and "authenticated but
 * not authorized" (`backend/src/kortex/api/errors.py`'s documented, already
 * ratified collapse of the two). The Tauri/Rust IPC boundary
 * (`apps/desktop/src-tauri/src/ipc.rs`) does not forward the underlying
 * HTTP status code today, so this error is deliberately not split into a
 * 401 vs. 403 variant here — doing so would require guessing. The M4.1
 * session boundary (not yet wired into this branch) owns reacting to an
 * actual session expiry; this error only ever means "access denied."
 */
export class ConnectorAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConnectorAccessDeniedError";
  }
}

/** Any other `FAILURE` envelope (timeout, capability not found, backend
 * unreachable, execution failure, ...) — a generic, recoverable failure. */
export class ConnectorRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConnectorRequestError";
  }
}

/** Raw wire shape of one entry in the capability's `result` array —
 * snake_case, since `DriverMetadata` (unlike the IPC envelope itself) has
 * no camelCase alias generator on the Python side. */
interface RawConnectorDriver {
  driver_id: string;
  display_name: string;
  vendor: string;
  author: string;
  version: string;
  description: string;
  supported_actions?: string[];
  supported_capabilities?: string[];
  is_sandboxed?: boolean;
  homepage?: string | null;
  license?: string;
}

function toConnectorDriver(raw: RawConnectorDriver): ConnectorDriver {
  return {
    driverId: raw.driver_id,
    displayName: raw.display_name,
    vendor: raw.vendor,
    author: raw.author,
    version: raw.version,
    description: raw.description,
    supportedActions: (raw.supported_actions ?? []) as ConnectorActionType[],
    supportedCapabilities: (raw.supported_capabilities ?? []) as ConnectorCapabilityTag[],
    isSandboxed: raw.is_sandboxed ?? true,
    homepage: raw.homepage ?? null,
    license: raw.license ?? "MIT",
  };
}

/**
 * Calls the existing `kortex.connector.driver.list` capability through the
 * existing generic IPC path (React -> `ipc/client.ts` -> Tauri
 * `invoke_capability` -> Rust -> backend `CapabilityDispatcher`). No
 * dedicated Tauri command is introduced — the generic bridge already
 * carries this request unmodified.
 *
 * `invokeCapability` never throws for a business failure (see
 * `ipc/client.test.ts`'s "business failures are data, not exceptions"); this
 * function is the boundary that turns a `FAILURE` envelope into a thrown,
 * typed error so `useConnectors` can rely on TanStack Query's normal
 * success/error split.
 */
export async function listConnectorDrivers(): Promise<ConnectorDriver[]> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: DRIVER_LIST_CAPABILITY,
    parameters: {},
  });

  if (envelope.status === "SUCCESS") {
    const raw = (envelope.payload?.result as RawConnectorDriver[] | undefined) ?? [];
    return raw.map(toConnectorDriver);
  }
  throwForFailure(envelope, "Failed to load the connector registry.");
}

/** Raw wire shape of one entry in `kortex.connector.profile.list`'s `result`
 * array — mirrors `ConnectorProfile` (backend/src/kortex/engines/connector/
 * models.py) field-for-field. `secret_handle` is present on the backend
 * model but deliberately never read here — see `types.ts`'s docstring on
 * `ConnectorProfile`. */
interface RawConnectorProfile {
  profile_id: string;
  name: string;
  driver_id: string;
  is_active?: boolean;
  rate_limit_per_sec?: number;
  max_retries?: number;
}

function toConnectorProfile(raw: RawConnectorProfile): ConnectorProfile {
  return {
    profileId: raw.profile_id,
    name: raw.name,
    driverId: raw.driver_id,
    isActive: raw.is_active ?? true,
    rateLimitPerSec: raw.rate_limit_per_sec ?? 10.0,
    maxRetries: raw.max_retries ?? 3,
  };
}

function throwForFailure(envelope: Awaited<ReturnType<typeof invokeCapability>>, fallbackMessage: string): never {
  const failure = envelope.errors[0];
  const message = failure?.message ?? fallbackMessage;
  if (failure?.category === "PERMISSION_DENIED") {
    throw new ConnectorAccessDeniedError(message);
  }
  throw new ConnectorRequestError(message);
}

/** Calls the real `kortex.connector.profile.list` capability (M7.3) —
 * tenant-scoped server-side from the caller's authenticated session, never
 * from a client-supplied tenant id (see `ConnectorEngine.list_profiles`). */
export async function listConnectorProfiles(): Promise<ConnectorProfile[]> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: PROFILE_LIST_CAPABILITY,
    parameters: {},
  });

  if (envelope.status === "SUCCESS") {
    const raw = (envelope.payload?.result as RawConnectorProfile[] | undefined) ?? [];
    return raw.map(toConnectorProfile);
  }
  throwForFailure(envelope, "Failed to load your connections.");
}

/**
 * Creates a connection: registers the profile via `kortex.connector.
 * profile.register`, then — only if a credential was actually entered —
 * writes it via a separate `kortex.security.secret.put` call. The two are
 * genuinely different capabilities on different engines (Connector Engine
 * owns the profile; Security Engine owns the encrypted secret vault); this
 * function is only a convenience that sequences both from one form
 * submission, it does not merge them into a new combined capability.
 *
 * The secret handle is derived deterministically from `profileId`
 * (`connector/<profileId>`) so `ConnectorProfile.secret_handle` set on
 * registration and the handle written here always agree — no separate
 * "link a handle to a profile" step is needed for this single-credential-
 * per-profile shape.
 */
export async function registerConnectorProfile(payload: CreateConnectionPayload): Promise<ConnectorProfile> {
  const secretHandle = `connector/${payload.profileId}`;

  const profileEnvelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: PROFILE_REGISTER_CAPABILITY,
    parameters: {
      profile: {
        profile_id: payload.profileId,
        name: payload.name,
        driver_id: payload.driverId,
        secret_handle: payload.credential ? secretHandle : null,
      },
    },
  });

  if (profileEnvelope.status !== "SUCCESS") {
    throwForFailure(profileEnvelope, "Failed to create the connection.");
  }

  if (payload.credential) {
    const secretEnvelope = await invokeCapability({
      requestId: crypto.randomUUID(),
      capabilityName: SECRET_PUT_CAPABILITY,
      parameters: { secret_handle: secretHandle, plaintext: payload.credential },
    });
    if (secretEnvelope.status !== "SUCCESS") {
      throwForFailure(secretEnvelope, "The connection was created, but storing its credential failed.");
    }
  }

  return toConnectorProfile(profileEnvelope.payload?.result as RawConnectorProfile);
}

/** Calls `kortex.connector.profile.delete` (M7.3). Does not attempt to also
 * delete the associated secret — `SecretStore` has no cascade-delete
 * concept, and a dangling, never-again-resolvable handle left behind is
 * harmless (it is only ever read back through a profile that must itself
 * be re-created first). Deleting it too is a reasonable follow-up, not
 * required for this milestone's correctness. */
export async function deleteConnectorProfile(profileId: string): Promise<void> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: PROFILE_DELETE_CAPABILITY,
    parameters: { profile_id: profileId },
  });

  if (envelope.status !== "SUCCESS") {
    throwForFailure(envelope, "Failed to delete the connection.");
  }
}
