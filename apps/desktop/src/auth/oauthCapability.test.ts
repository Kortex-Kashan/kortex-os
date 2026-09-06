import { describe, expect, it, vi } from "vitest";

import type { IpcResultEnvelope } from "@/ipc/client";

import { beginOAuthLogin, completeOAuthLogin, getOAuthConfig } from "./oauthCapability";

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

describe("getOAuthConfig", () => {
  it("returns the configured/unconfigured flags from a SUCCESS envelope", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(envelope({ payload: { result: { google: true, microsoft: false } } }));

    const config = await getOAuthConfig();

    expect(config).toEqual({ google: true, microsoft: false });
  });

  it("fails safe (both false) when the backend is unreachable", async () => {
    invokeCapabilityMock.mockRejectedValueOnce(new Error("tauri ipc failure"));

    const config = await getOAuthConfig();

    expect(config).toEqual({ google: false, microsoft: false });
  });
});

describe("beginOAuthLogin", () => {
  it("calls kortex.security.oauth.login_begin with the provider", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({ payload: { result: { authorization_url: "https://accounts.google.com/authorize", state: "s1" } } }),
    );

    const outcome = await beginOAuthLogin("google");

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.security.oauth.login_begin",
        parameters: { provider: "google" },
      }),
    );
    expect(outcome).toEqual({ ok: true, authorizationUrl: "https://accounts.google.com/authorize", state: "s1" });
  });

  it("resolves ok:false when the provider is not configured", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        errors: [{ category: "EXECUTION_FAILED", message: "google sign-in is not configured.", correlationId: "c" }],
      }),
    );

    const outcome = await beginOAuthLogin("google");

    expect(outcome).toEqual({ ok: false, message: "google sign-in is not configured." });
  });
});

describe("completeOAuthLogin", () => {
  it("returns the identity on a SUCCESS envelope", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        payload: { result: { principal_id: "alice", principal_type: "USER", tenant_id: "acme", roles: ["member"] } },
      }),
    );

    const outcome = await completeOAuthLogin("google", "code-1", "state-1");

    expect(outcome).toEqual({
      ok: true,
      identity: { principalId: "alice", principalType: "USER", tenantId: "acme", roles: ["member"] },
    });
  });

  it("classifies a 'no linked account' failure distinctly", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        errors: [
          {
            category: "PERMISSION_DENIED",
            message:
              "No KORTEX account is linked to this google identity. Sign in with your username and password, then link google from Account settings.",
            correlationId: "c",
          },
        ],
      }),
    );

    const outcome = await completeOAuthLogin("google", "code-1", "state-1");

    expect(outcome.ok).toBe(false);
    expect(outcome).toMatchObject({ kind: "NO_LINKED_ACCOUNT" });
  });

  it("classifies any other failure as INVALID_STATE", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      envelope({
        status: "FAILURE",
        errors: [
          { category: "PERMISSION_DENIED", message: "This sign-in attempt is invalid or has expired.", correlationId: "c" },
        ],
      }),
    );

    const outcome = await completeOAuthLogin("google", "code-1", "state-1");

    expect(outcome).toMatchObject({ ok: false, kind: "INVALID_STATE" });
  });

  it("resolves BACKEND_UNAVAILABLE when invokeCapability rejects outright", async () => {
    invokeCapabilityMock.mockRejectedValueOnce(new Error("tauri ipc failure"));

    const outcome = await completeOAuthLogin("google", "code-1", "state-1");

    expect(outcome).toMatchObject({ ok: false, kind: "BACKEND_UNAVAILABLE" });
  });
});
