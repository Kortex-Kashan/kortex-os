import { describe, expect, it, vi } from "vitest";

import type { IpcResultEnvelope } from "@/ipc/client";

import { checkStoredSession, classifyIpcFailure, login, requestPasswordReset, resetPassword } from "./authCapability";

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

describe("requestPasswordReset", () => {
  it("calls kortex.security.auth.request_password_reset with the email", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope({ payload: { result: { message: "generic message" } } }));

    await requestPasswordReset({ email: "alice@example.com" });

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.security.auth.request_password_reset",
        parameters: { email: "alice@example.com" },
      }),
    );
  });

  it("resolves ok:true with the identical generic message on SUCCESS", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({ payload: { result: { message: "If that email is registered, a password reset link has been sent." } } }),
    );

    const outcome = await requestPasswordReset({ email: "alice@example.com" });

    expect(outcome).toEqual({
      ok: true,
      message: "If that email is registered, a password reset link has been sent.",
    });
  });

  it("resolves ok:true with the generic message even on a non-transport FAILURE — never distinguishing match from no-match", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({ status: "FAILURE", errors: [{ category: "EXECUTION_FAILED", message: "x", correlationId: "c" }] }),
    );

    const outcome = await requestPasswordReset({ email: "nobody@example.com" });

    expect(outcome.ok).toBe(true);
  });

  it("resolves BACKEND_UNAVAILABLE when invokeCapability rejects outright", async () => {
    invokeCapabilityMock.mockRejectedValueOnce(new Error("tauri ipc failure"));

    const outcome = await requestPasswordReset({ email: "alice@example.com" });

    expect(outcome).toEqual({ ok: false, kind: "BACKEND_UNAVAILABLE", message: expect.any(String) });
  });
});

describe("resetPassword", () => {
  it("calls kortex.security.auth.reset_password with snake_case parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope({ payload: { result: { reset: true } } }));

    await resetPassword({ token: "the-token", newPassword: "brand-new-password" });

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.security.auth.reset_password",
        parameters: { token: "the-token", new_password: "brand-new-password" },
      }),
    );
  });

  it("resolves ok:true on SUCCESS", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope({ payload: { result: { reset: true } } }));
    const outcome = await resetPassword({ token: "the-token", newPassword: "brand-new-password" });
    expect(outcome).toEqual({ ok: true });
  });

  it("resolves INVALID_TOKEN surfacing the backend's message on FAILURE", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        errors: [
          { category: "PERMISSION_DENIED", message: "This password reset link is invalid or has expired.", correlationId: "c" },
        ],
      }),
    );

    const outcome = await resetPassword({ token: "bad-token", newPassword: "brand-new-password" });

    expect(outcome).toEqual({
      ok: false,
      kind: "INVALID_TOKEN",
      message: "This password reset link is invalid or has expired.",
    });
  });

  it("resolves BACKEND_UNAVAILABLE when invokeCapability rejects outright", async () => {
    invokeCapabilityMock.mockRejectedValueOnce(new Error("tauri ipc failure"));

    const outcome = await resetPassword({ token: "the-token", newPassword: "brand-new-password" });

    expect(outcome).toEqual({ ok: false, kind: "BACKEND_UNAVAILABLE", message: expect.any(String) });
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
