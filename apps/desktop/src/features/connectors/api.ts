import { invokeCapability } from "@/ipc/client";
import type { ConnectorActionType, ConnectorCapabilityTag, ConnectorDriver } from "./types";

const DRIVER_LIST_CAPABILITY = "kortex.connector.driver.list";

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

  const failure = envelope.errors[0];
  const message = failure?.message ?? "Failed to load the connector registry.";
  if (failure?.category === "PERMISSION_DENIED") {
    throw new ConnectorAccessDeniedError(message);
  }
  throw new ConnectorRequestError(message);
}
