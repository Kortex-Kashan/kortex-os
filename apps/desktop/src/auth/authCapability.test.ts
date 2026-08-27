import { describe, expect, it, vi } from "vitest";

import type { IpcResultEnvelope } from "@/ipc/client";

import { checkStoredSession, classifyIpcFailure, login } from "./authCapability";

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

describe("login", () => {
  it("calls kortex.security.auth.authenticate with snake_case credentials", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        payload: {
          result: { principal_id: "alice", principal_type: "USER", tenant_id: "acme", roles: ["reader"] },
        },
      }),
    );

    await login({ tenantId: "acme", principalId: "alice", password: "hunter2" });

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.security.auth.authenticate",
        parameters: {
          credentials: {
            principal_type: "USER",
            tenant_id: "acme",
            principal_id: "alice",
            password: "hunter2",
          },
        },
      }),
    );
  });

  it("never includes the password in the requestId or any other logged-shaped field", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({ payload: { result: { principal_id: "a", principal_type: "USER", tenant_id: "t", roles: [] } } }),
    );
    await login({ tenantId: "t", principalId: "a", password: "super-secret-value" });
    const [request] = invokeCapabilityMock.mock.calls[0];
    expect(request.requestId).not.toContain("super-secret-value");
    expect(request.capabilityName).not.toContain("super-secret-value");
  });

  it("returns the identity on a SUCCESS envelope", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        payload: {
          result: { principal_id: "bob", principal_type: "USER", tenant_id: "acme", roles: ["admin"] },
        },
      }),
    );

    const outcome = await login({ tenantId: "acme", principalId: "bob", password: "x" });

    expect(outcome).toEqual({
      ok: true,
      identity: { principalId: "bob", principalType: "USER", tenantId: "acme", roles: ["admin"] },
    });
  });

  it("reports INVALID_CREDENTIALS on a PERMISSION_DENIED failure, surfacing the backend's generic message verbatim", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        errors: [{ category: "PERMISSION_DENIED", message: "Authentication failed: invalid credentials.", correlationId: "c" }],
      }),
    );

    const outcome = await login({ tenantId: "acme", principalId: "bob", password: "wrong" });

    expect(outcome).toEqual({
      ok: false,
      kind: "INVALID_CREDENTIALS",
      message: "Authentication failed: invalid credentials.",
    });
  });

  it("reports BACKEND_UNAVAILABLE on a SERVICE_UNAVAILABLE failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        errors: [{ category: "SERVICE_UNAVAILABLE", message: "Backend unreachable", correlationId: "c" }],
      }),
    );

    const outcome = await login({ tenantId: "acme", principalId: "bob", password: "x" });

    expect(outcome).toEqual({ ok: false, kind: "BACKEND_UNAVAILABLE", message: expect.any(String) });
  });

  it("reports BACKEND_UNAVAILABLE when invokeCapability rejects outright", async () => {
    invokeCapabilityMock.mockRejectedValueOnce(new Error("tauri ipc failure"));

    const outcome = await login({ tenantId: "acme", principalId: "bob", password: "x" });

    expect(outcome).toEqual({ ok: false, kind: "BACKEND_UNAVAILABLE", message: expect.any(String) });
  });

  it("reports BACKEND_UNAVAILABLE on a SUCCESS envelope with an unparseable payload, never claiming a sign-in with no identity", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope({ status: "SUCCESS", payload: null }));

    const outcome = await login({ tenantId: "acme", principalId: "bob", password: "x" });

    expect(outcome.ok).toBe(false);
    expect(outcome).toMatchObject({ kind: "BACKEND_UNAVAILABLE" });
  });
});

describe("checkStoredSession", () => {
  it("returns VALID on a SUCCESS envelope", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope({ status: "SUCCESS", payload: { result: false } }));
    expect(await checkStoredSession()).toBe("VALID");
  });

  it("returns INVALID on a real 401", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        httpStatus: 401,
        errors: [{ category: "PERMISSION_DENIED", message: "invalid token", correlationId: "c" }],
      }),
    );
    expect(await checkStoredSession()).toBe("INVALID");
  });

  it("returns VALID on a real 403 — the token itself is genuine, just unprivileged for this check", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        httpStatus: 403,
        errors: [{ category: "PERMISSION_DENIED", message: "forbidden", correlationId: "c" }],
      }),
    );
    expect(await checkStoredSession()).toBe("VALID");
  });

  it("returns BACKEND_UNAVAILABLE when the backend is unreachable", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        httpStatus: undefined,
        errors: [{ category: "SERVICE_UNAVAILABLE", message: "unreachable", correlationId: "c" }],
      }),
    );
    expect(await checkStoredSession()).toBe("BACKEND_UNAVAILABLE");
  });

  it("returns BACKEND_UNAVAILABLE when invokeCapability rejects outright", async () => {
    invokeCapabilityMock.mockRejectedValueOnce(new Error("tauri ipc failure"));
    expect(await checkStoredSession()).toBe("BACKEND_UNAVAILABLE");
  });
});

describe("classifyIpcFailure", () => {
  it("classifies a real 401 PERMISSION_DENIED as UNAUTHORIZED", () => {
    const result = classifyIpcFailure(
      envelope({
        status: "FAILURE",
        httpStatus: 401,
        errors: [{ category: "PERMISSION_DENIED", message: "x", correlationId: "c" }],
      }),
    );
    expect(result).toBe("UNAUTHORIZED");
  });

  it("classifies a real 403 PERMISSION_DENIED as FORBIDDEN", () => {
    const result = classifyIpcFailure(
      envelope({
        status: "FAILURE",
        httpStatus: 403,
        errors: [{ category: "PERMISSION_DENIED", message: "x", correlationId: "c" }],
      }),
    );
    expect(result).toBe("FORBIDDEN");
  });

  it("never fabricates a distinction when httpStatus is unavailable", () => {
    const result = classifyIpcFailure(
      envelope({
        status: "FAILURE",
        httpStatus: undefined,
        errors: [{ category: "PERMISSION_DENIED", message: "x", correlationId: "c" }],
      }),
    );
    expect(result).toBe("OTHER");
  });

  it("classifies a SUCCESS envelope as OTHER", () => {
    expect(classifyIpcFailure(envelope({ status: "SUCCESS" }))).toBe("OTHER");
  });

  it("classifies a non-PERMISSION_DENIED failure as OTHER", () => {
    const result = classifyIpcFailure(
      envelope({
        status: "FAILURE",
        httpStatus: 404,
        errors: [{ category: "CAPABILITY_NOT_FOUND", message: "x", correlationId: "c" }],
      }),
    );
    expect(result).toBe("OTHER");
  });
});
