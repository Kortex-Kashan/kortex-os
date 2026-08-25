// Typed IPC contract, mirroring ADR-0002 §8.2–§8.3 (CapabilityRequest /
// UniversalResult transported unchanged across the Tauri boundary). The
// actual Tauri `invoke("invoke_capability", ...)` transport is wired in
// Milestones M1/M3 — this module only establishes the contract shape so
// feature code can be written against a stable type today.

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
  _request: IpcCapabilityRequest,
): Promise<IpcResultEnvelope> {
  throw new Error("IPC transport not yet wired — see ADR-0002 Milestones M1/M3.");
}
