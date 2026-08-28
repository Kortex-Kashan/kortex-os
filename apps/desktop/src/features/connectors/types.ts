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
