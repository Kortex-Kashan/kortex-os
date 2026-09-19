import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockIpcFn } = vi.hoisted(() => ({ mockIpcFn: vi.fn() }));

vi.mock("@tauri-apps/api/mocks", () => ({
  mockIPC: mockIpcFn,
}));

import { initDevBrowserBridge } from "./devBrowserBridge";

describe("devBrowserBridge security and isolation", () => {
  const originalEnv = { ...import.meta.env };

  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
  });

  afterEach(() => {
    Object.assign(import.meta.env, originalEnv);
  });

  it("does NOT activate mockIPC in production builds (when import.meta.env.DEV is false)", () => {
    // @ts-expect-error mutating DEV for test simulation
    import.meta.env.DEV = false;

    initDevBrowserBridge();

    expect(mockIpcFn).not.toHaveBeenCalled();
  });

  it("does NOT activate mockIPC when running inside a real Tauri Webview (__TAURI_INTERNALS__.invoke exists)", () => {
    // @ts-expect-error mutating DEV for test
    import.meta.env.DEV = true;
    (window as unknown as { __TAURI_INTERNALS__: { invoke: unknown } }).__TAURI_INTERNALS__ = {
      invoke: vi.fn(),
    };

    initDevBrowserBridge();

    expect(mockIpcFn).not.toHaveBeenCalled();
  });

  it("activates mockIPC in dev mode without Tauri internals", () => {
    // @ts-expect-error mutating DEV for test
    import.meta.env.DEV = true;

    initDevBrowserBridge();

    expect(mockIpcFn).toHaveBeenCalledTimes(1);
    expect(mockIpcFn).toHaveBeenCalledWith(expect.any(Function), { shouldMockEvents: true });
  });

  it("handlers enforce session lifecycle and strip sessionToken before resolving envelope", async () => {
    // @ts-expect-error mutating DEV for test
    import.meta.env.DEV = true;

    initDevBrowserBridge();
    const handler = mockIpcFn.mock.calls[0][0] as (cmd: string, args: unknown) => Promise<unknown>;

    // 1. Initial has_session is false
    expect(await handler("has_session", undefined)).toBe(false);

    // 2. Successful login captures sessionToken into sessionStorage and strips it from returned envelope
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        status: "SUCCESS",
        payload: { result: { principal_id: "alice", tenant_id: "acme" } },
        sessionToken: "secret-token-123",
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const loginEnvelope = (await handler("invoke_capability", {
      request: {
        requestId: "r1",
        capabilityName: "kortex.security.auth.authenticate",
      },
    })) as Record<string, unknown>;

    // Token stored in sessionStorage
    expect(sessionStorage.getItem("kortex_desktop_dev_session_token")).toBe("secret-token-123");
    // Token stripped from envelope
    expect(loginEnvelope.sessionToken).toBeUndefined();
    expect(loginEnvelope.httpStatus).toBe(200);
    expect(await handler("has_session", undefined)).toBe(true);

    // 3. Subsequent call forwards Authorization header
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        status: "SUCCESS",
        payload: { result: [] },
      }),
    });

    await handler("invoke_capability", {
      request: {
        requestId: "r2",
        capabilityName: "kortex.ai.provider.list",
      },
    });

    expect(mockFetch).toHaveBeenLastCalledWith(
      "/dev-ipc/capabilities/invoke",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer secret-token-123",
        }),
      }),
    );

    // 4. 401 response preserves real httpStatus
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({
        status: "FAILURE",
        errors: [{ category: "PERMISSION_DENIED", message: "Token expired" }],
      }),
    });

    const failureEnvelope = (await handler("invoke_capability", {
      request: {
        requestId: "r3",
        capabilityName: "kortex.ai.provider.list",
      },
    })) as Record<string, unknown>;

    expect(failureEnvelope.httpStatus).toBe(401);

    // 5. Logout clears token
    await handler("logout", undefined);
    expect(sessionStorage.getItem("kortex_desktop_dev_session_token")).toBeNull();
    expect(await handler("has_session", undefined)).toBe(false);

    vi.unstubAllGlobals();
  });
});
