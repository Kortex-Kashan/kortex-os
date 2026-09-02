import { describe, expect, it, vi } from "vitest";

import type { IpcResultEnvelope } from "@/ipc/client";

import { bootstrapFirstAdmin } from "./bootstrapCapability";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

function envelope(overrides: Partial<IpcResultEnvelope> = {}): IpcResultEnvelope {
  return {
    requestId: "req-1",
    correlationId: "corr-1",
    status: "SUCCESS",
    payload: null,
    errors: [],
    warnings: [],
    executionDurationMs: 1,
    ...overrides,
  };
}

describe("bootstrapFirstAdmin", () => {
  it("calls kortex.security.bootstrap.create_admin with snake_case parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope({ payload: { created: true } }));

    await bootstrapFirstAdmin({ tenantId: "acme", principalId: "owner", password: "a-strong-password" });

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.security.bootstrap.create_admin",
        parameters: {
          tenant_id: "acme",
          principal_id: "owner",
          password: "a-strong-password",
        },
      }),
    );
  });

  it("never includes the password in the requestId or capability name", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope({ payload: { created: true } }));
    await bootstrapFirstAdmin({ tenantId: "t", principalId: "a", password: "super-secret-value" });
    const [request] = invokeCapabilityMock.mock.calls[0];
    expect(request.requestId).not.toContain("super-secret-value");
    expect(request.capabilityName).not.toContain("super-secret-value");
  });

  it("resolves ok:true on a SUCCESS envelope", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope({ payload: { created: true } }));

    const outcome = await bootstrapFirstAdmin({ tenantId: "acme", principalId: "owner", password: "a-strong-password" });

    expect(outcome).toEqual({ ok: true });
  });

  it("classifies a PERMISSION_DENIED failure as ALREADY_BOOTSTRAPPED", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        httpStatus: 401,
        errors: [
          {
            category: "PERMISSION_DENIED",
            message: "Bootstrap is no longer available: an administrator already exists.",
            correlationId: "c-1",
          },
        ],
      }),
    );

    const outcome = await bootstrapFirstAdmin({ tenantId: "acme", principalId: "owner", password: "a-strong-password" });

    expect(outcome).toEqual({
      ok: false,
      kind: "ALREADY_BOOTSTRAPPED",
      message: "Bootstrap is no longer available: an administrator already exists.",
    });
  });

  it("classifies any other failure category as VALIDATION_FAILED, surfacing the message verbatim", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        errors: [{ category: "EXECUTION_FAILED", message: "Password must be at least 8 characters.", correlationId: "c-1" }],
      }),
    );

    const outcome = await bootstrapFirstAdmin({ tenantId: "acme", principalId: "owner", password: "short" });

    expect(outcome).toEqual({
      ok: false,
      kind: "VALIDATION_FAILED",
      message: "Password must be at least 8 characters.",
    });
  });

  it.each(["SERVICE_UNAVAILABLE", "TIMEOUT_EXCEEDED"] as const)(
    "classifies %s as BACKEND_UNAVAILABLE",
    async (category) => {
      invokeCapabilityMock.mockResolvedValueOnce(
        envelope({ status: "FAILURE", errors: [{ category, message: "x", correlationId: "c-1" }] }),
      );

      const outcome = await bootstrapFirstAdmin({ tenantId: "acme", principalId: "owner", password: "a-strong-password" });

      expect(outcome).toEqual({ ok: false, kind: "BACKEND_UNAVAILABLE", message: "The backend is unreachable." });
    },
  );

  it("resolves BACKEND_UNAVAILABLE, never throwing, when invokeCapability itself rejects", async () => {
    invokeCapabilityMock.mockRejectedValueOnce(new Error("Tauri IPC bridge failure"));

    const outcome = await bootstrapFirstAdmin({ tenantId: "acme", principalId: "owner", password: "a-strong-password" });

    expect(outcome).toEqual({ ok: false, kind: "BACKEND_UNAVAILABLE", message: "The backend is unreachable." });
  });
});
