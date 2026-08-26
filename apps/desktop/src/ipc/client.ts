// Typed IPC contract, mirroring ADR-0002 §8.2–§8.3 (CapabilityRequest /
// UniversalResult transported unchanged across the Tauri boundary).
// M3 wires this to the real Rust `invoke_capability` command
// (`apps/desktop/src-tauri/src/ipc.rs`), which forwards verbatim to the
// backend's `POST /capabilities/invoke` — this module never talks to the
// backend directly, and the session token that command captures never
// crosses back into this file's return value (see `ipc.rs`'s own
// `RawBackendResponse` / `IpcResultEnvelope` split).

import { invoke } from "@tauri-apps/api/core";

export interface IpcCapabilityRequest {
  requestId: string;
  capabilityName: string;
  parameters: Record<string, unknown>;
  correlationId?: string;
  idempotencyKey?: string;
  timeoutMs?: number;
}

export type IpcErrorCategory =
  | "CAPABILITY_NOT_FOUND"
  | "PERMISSION_DENIED"
  | "VALIDATION_FAILED"
  | "TIMEOUT_EXCEEDED"
  | "SERVICE_UNAVAILABLE"
  | "EXECUTION_FAILED";

export interface IpcError {
  category: IpcErrorCategory;
  message: string;
  details?: Record<string, unknown>;
  correlationId: string;
}

export interface IpcResultEnvelope {
  requestId: string;
  correlationId: string;
  status: "SUCCESS" | "FAILURE" | "PARTIAL_SUCCESS" | "CANCELLED";
  payload: Record<string, unknown> | null;
  errors: IpcError[];
  warnings: IpcError[];
  executionDurationMs: number;
}

export async function invokeCapability(
  request: IpcCapabilityRequest,
): Promise<IpcResultEnvelope> {
  return invoke<IpcResultEnvelope>("invoke_capability", { request });
}
