import { describe, expect, it, vi } from "vitest";

import type { IpcResultEnvelope } from "@/ipc/client";

import { AccountAccessDeniedError, AccountRequestError, changePassword, registerPrincipal, setEmail } from "./api";

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

describe("changePassword", () => {
  it("calls kortex.security.auth.change_password with snake_case parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope());
    await changePassword({ currentPassword: "old", newPassword: "new-password" });
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.security.auth.change_password",
        parameters: { current_password: "old", new_password: "new-password" },
      }),
    );
  });

  it("throws AccountAccessDeniedError on PERMISSION_DENIED", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        errors: [{ category: "PERMISSION_DENIED", message: "Authentication failed: invalid credentials.", correlationId: "c" }],
      }),
    );
    await expect(changePassword({ currentPassword: "wrong", newPassword: "new-password" })).rejects.toThrow(
      AccountAccessDeniedError,
    );
  });

  it("throws AccountRequestError on any other failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({ status: "FAILURE", errors: [{ category: "EXECUTION_FAILED", message: "boom", correlationId: "c" }] }),
    );
    await expect(changePassword({ currentPassword: "old", newPassword: "new-password" })).rejects.toThrow(
      AccountRequestError,
    );
  });
});

describe("setEmail", () => {
  it("calls kortex.security.principal.set_email with the email", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope());
    await setEmail({ email: "alice@example.com" });
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.security.principal.set_email",
        parameters: { email: "alice@example.com" },
      }),
    );
  });
});

describe("registerPrincipal", () => {
  it("calls kortex.security.principal.register with snake_case parameters, no tenant field", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope());
    await registerPrincipal({ principalId: "bob", password: "temp-password", roles: ["member"] });
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.security.principal.register",
        parameters: { principal_id: "bob", password: "temp-password", roles: ["member"], email: null },
      }),
    );
  });

  it("throws AccountAccessDeniedError when a non-admin is denied", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        errors: [{ category: "PERMISSION_DENIED", message: "denied", correlationId: "c" }],
      }),
    );
    await expect(registerPrincipal({ principalId: "bob", password: "x", roles: ["member"] })).rejects.toThrow(
      AccountAccessDeniedError,
    );
  });
});
