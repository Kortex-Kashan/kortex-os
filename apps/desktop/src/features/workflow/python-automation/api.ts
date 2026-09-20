/**
 * IPC wrappers for the Python Automation surface (AI Studio functional
 * stabilization, Phase E).
 *
 * Both calls go through the existing generic IPC path (React ->
 * `invokeCapability` -> Tauri `invoke_capability` -> Rust ->
 * `CapabilityDispatcher`), the same transport every other feature in this
 * app already uses — no new IPC mechanism, no bypass of
 * `CapabilityDispatcher`, and no second Python execution path. This module
 * calls exactly two EXISTING backend capabilities, already governed and
 * already permission-gated: `kortex.python.action.list` (`python:read`)
 * and `kortex.python.execute` (`python:execute`). `execute_action`'s own
 * backend contract requires an explicit, already-published, pinned
 * `version` — there is no "run this source code" path here or on the
 * backend, so this can never become an arbitrary-code-execution UI.
 */

import { invokeCapability } from "@/ipc/client";
import type { IpcResultEnvelope } from "@/ipc/client";
import type { PythonAction, PythonExecutionResult } from "./types";

const LIST_CAPABILITY = "kortex.python.action.list";
const EXECUTE_CAPABILITY = "kortex.python.execute";

export class PythonAutomationAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PythonAutomationAccessDeniedError";
  }
}

export class PythonAutomationRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PythonAutomationRequestError";
  }
}

function extractResult(envelope: IpcResultEnvelope, capabilityName: string): unknown {
  if (envelope.status === "SUCCESS") {
    return envelope.payload?.result ?? null;
  }
  const failure = envelope.errors[0];
  const message = failure?.message ?? `Capability ${capabilityName} failed.`;
  if (failure?.category === "PERMISSION_DENIED") {
    throw new PythonAutomationAccessDeniedError(message);
  }
  throw new PythonAutomationRequestError(message);
}

async function invoke(capabilityName: string, parameters: Record<string, unknown>): Promise<unknown> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName,
    parameters,
  });
  return extractResult(envelope, capabilityName);
}

interface RawPythonAction {
  action_id: string;
  name: string;
  description?: string;
  latest_version: number;
  is_active: boolean;
}

function toPythonAction(raw: RawPythonAction): PythonAction {
  return {
    actionId: raw.action_id,
    name: raw.name,
    description: raw.description ?? "",
    latestVersion: raw.latest_version,
    isActive: raw.is_active,
  };
}

/** Lists the calling tenant's already-published Python Actions
 * (`kortex.python.action.list`) — read-only, never creates or modifies
 * anything. */
export async function listPythonActions(): Promise<PythonAction[]> {
  const raw = await invoke(LIST_CAPABILITY, {});
  const arr = Array.isArray(raw) ? raw : [];
  return (arr as RawPythonAction[]).map(toPythonAction);
}

interface RawPythonExecutionResult {
  status: string;
  output: unknown;
  error?: string | null;
  exit_code?: number | null;
  duration_ms?: number;
}

function toExecutionResult(raw: RawPythonExecutionResult): PythonExecutionResult {
  return {
    status: raw.status,
    output: raw.output ?? null,
    error: raw.error ?? null,
    exitCode: raw.exit_code ?? null,
    durationMs: raw.duration_ms ?? 0,
  };
}

/** Triggers one already-published, pinned Python Action version through
 * the existing governed execution path (`kortex.python.execute`). Always
 * runs with an empty input payload — there is deliberately no UI here for
 * constructing arbitrary input, matching the minimum scope this surface is
 * meant to cover (trigger an existing action, not author or configure
 * one). */
export async function executePythonAction(actionId: string, version: number): Promise<PythonExecutionResult> {
  const raw = await invoke(EXECUTE_CAPABILITY, { action_id: actionId, version, input_payload: {} });
  return toExecutionResult(raw as RawPythonExecutionResult);
}
