// Typed IPC contract, mirroring ADR-0002 §8.2–§8.3 (CapabilityRequest /
// UniversalResult transported unchanged across the Tauri boundary).
// M3 wires this to the real Rust `invoke_capability` command
// (`apps/desktop/src-tauri/src/ipc.rs`), which forwards verbatim to the
// backend's `POST /capabilities/invoke` — this module never talks to the
// backend directly, and the session token that command captures never
// crosses back into this file's return value (see `ipc.rs`'s own
// `RawBackendResponse` / `IpcResultEnvelope` split).
//
// M4.1 adds `IpcResultEnvelope.httpStatus`: the real HTTP status the
// backend's response line carried, threaded through by Rust's
// `forward_capability_request`. It exists so callers can distinguish 401
// (invalid/expired session — force re-authentication) from 403
// (authenticated but forbidden — stay signed in), a distinction the
// `errors[].category` field alone cannot carry since both collapse to the
// identical `PERMISSION_DENIED` value (see `backend/src/kortex/api/
// errors.py`'s documented taxonomy). See `@/auth/authCapability.ts`'s
// `classifyIpcFailure`.

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
  /** Undefined only when no real HTTP response was ever received (backend
   * unreachable) — see this file's module doc. */
  httpStatus?: number;
}

export async function invokeCapability(
  request: IpcCapabilityRequest,
): Promise<IpcResultEnvelope> {
  return invoke<IpcResultEnvelope>("invoke_capability", { request });
}
