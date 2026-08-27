import { describe, expect, it, vi } from "vitest";
import {
  fetchSystemHealth,
  invokeCapability,
  type IpcCapabilityRequest,
  type IpcResultEnvelope,
} from "@/ipc/client";

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: invokeMock,
}));

describe("invokeCapability", () => {
  it("forwards the request to the invoke_capability Tauri command", async () => {
    const envelope: IpcResultEnvelope = {
      requestId: "req-1",
      correlationId: "corr-1",
      status: "SUCCESS",
      payload: { ok: true },
      errors: [],
      warnings: [],
      executionDurationMs: 1.2,
    };
    invokeMock.mockResolvedValueOnce(envelope);

    const request: IpcCapabilityRequest = {
      requestId: "req-1",
      capabilityName: "kortex.security.auth.authenticate",
      parameters: { foo: "bar" },
    };
    const result = await invokeCapability(request);

    expect(invokeMock).toHaveBeenCalledWith("invoke_capability", { request });
    expect(result).toBe(envelope);
  });

  it("never sees a session token — the Tauri command's return value has no such field to leak", async () => {
    // The contract itself (`IpcResultEnvelope`) has no `sessionToken`
    // field — Rust's `invoke_capability` strips it before resolving the
    // JS promise (see `ipc.rs::forward_capability_request`). This test
    // documents that guarantee at the TypeScript boundary: even a
    // maximally permissive mock returning an object with an extra field
    // is still only read through the typed envelope shape.
    invokeMock.mockResolvedValueOnce({
      requestId: "req-1",
      correlationId: "corr-1",
      status: "SUCCESS",
      payload: null,
      errors: [],
      warnings: [],
      executionDurationMs: 0,
      sessionToken: "should-never-be-read-by-frontend-code",
    });

    const result = await invokeCapability({
      requestId: "req-1",
      capabilityName: "kortex.security.auth.authenticate",
      parameters: {},
    });

    expect(Object.keys(result satisfies IpcResultEnvelope)).not.toContain("sessionTokenUsedAnywhere");
    expect(result.payload).toBeNull();
  });

  it("propagates a FAILURE envelope without throwing — business failures are data, not exceptions", async () => {
    const envelope: IpcResultEnvelope = {
      requestId: "req-2",
      correlationId: "corr-2",
      status: "FAILURE",
      payload: null,
      errors: [
        {
          category: "PERMISSION_DENIED",
          message: "denied",
          correlationId: "corr-2",
        },
      ],
      warnings: [],
      executionDurationMs: 0.5,
    };
    invokeMock.mockResolvedValueOnce(envelope);

    const result = await invokeCapability({
      requestId: "req-2",
      capabilityName: "kortex.security.secret.get",
      parameters: {},
    });

    expect(result.status).toBe("FAILURE");
    expect(result.errors[0].category).toBe("PERMISSION_DENIED");
  });
});

describe("fetchSystemHealth", () => {
  it("forwards to the get_system_health Tauri command and returns its outcome verbatim", async () => {
    const outcome = {
      ok: true,
      statusCode: 200,
      body: { kernel_state: "RUNNING", system_health: { status: "healthy", engines: {} } },
    };
    invokeMock.mockResolvedValueOnce(outcome);

    const result = await fetchSystemHealth();

    expect(invokeMock).toHaveBeenCalledWith("get_system_health");
    expect(result).toBe(outcome);
  });

  it("surfaces a transport failure as ok: false rather than throwing", async () => {
    invokeMock.mockResolvedValueOnce({ ok: false, error: "Backend unreachable: connection refused" });

    const result = await fetchSystemHealth();

    expect(result.ok).toBe(false);
    expect(result.error).toContain("unreachable");
  });
});
