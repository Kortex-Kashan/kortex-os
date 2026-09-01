/**
 * Mirrors `kortex.engines.connector.models.DriverMetadata` field-for-field
 * (backend/src/kortex/engines/connector/models.py) — the exact shape
 * `kortex.connector.driver.list` returns. No field here is invented, and no
 * credential/secret field exists on `DriverMetadata` to begin with: driver
 * *profiles* (which do carry a `secret_handle`) are a separate model this
 * capability never returns.
 */
export type ConnectorActionType = "SEND" | "RECEIVE" | "FETCH" | "PUSH" | "VERIFY";

export type ConnectorCapabilityTag =
  | "SEND"
  | "RECEIVE"
  | "FETCH"
  | "PUSH"
  | "VERIFY"
  | "TEST_CONNECTION"
  | "AUTHENTICATE"
  | "WEBHOOK"
  | "STREAMING";

export interface ConnectorDriver {
  driverId: string;
  displayName: string;
  vendor: string;
  author: string;
  version: string;
  description: string;
  supportedActions: ConnectorActionType[];
  supportedCapabilities: ConnectorCapabilityTag[];
  isSandboxed: boolean;
  homepage: string | null;
  license: string;
}

/**
 * Mirrors `kortex.engines.connector.models.ConnectorProfile` field-for-field
 * (M7.3) — deliberately omits `secretHandle`: a tenant's connections view
 * has no legitimate need to render even the opaque handle string, and the
 * resolved secret value is never returned by any capability this feature
 * calls (`kortex.connector.profile.*`/`kortex.security.secret.put` — see
 * `api.ts`'s own docstrings).
 */
export interface ConnectorProfile {
  profileId: string;
  name: string;
  driverId: string;
  isActive: boolean;
  rateLimitPerSec: number;
  maxRetries: number;
}

/** Payload for `registerConnectorProfile` — creates or updates a profile.
 * `credential`, when provided, is written via a separate
 * `kortex.security.secret.put` call under a handle derived from
 * `profileId`, never included in the profile payload itself (the two are
 * genuinely separate capabilities/engines, matching the backend design). */
export interface CreateConnectionPayload {
  profileId: string;
  name: string;
  driverId: string;
  credential?: string;
}
