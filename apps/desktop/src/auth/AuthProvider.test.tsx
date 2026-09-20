import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { IpcResultEnvelope } from "@/ipc/client";

import { AuthProvider, useAuth } from "./AuthProvider";
import type { AuthIdentity } from "./authTypes";
import type { UseInactivityLogoutOptions } from "./useInactivityLogout";

const {
  hasStoredSessionMock,
  clearStoredSessionMock,
  loginMock,
  checkStoredSessionMock,
  renewSessionMock,
  classifyIpcFailureMock,
  loadCachedIdentityMock,
  saveCachedIdentityMock,
  clearCachedIdentityMock,
  waitForBackendReadyMock,
  bootstrapFirstAdminMock,
} = vi.hoisted(() => ({
  hasStoredSessionMock: vi.fn(),
  clearStoredSessionMock: vi.fn(),
  loginMock: vi.fn(),
  checkStoredSessionMock: vi.fn(),
  renewSessionMock: vi.fn(),
  classifyIpcFailureMock: vi.fn(),
  loadCachedIdentityMock: vi.fn(),
  saveCachedIdentityMock: vi.fn(),
  clearCachedIdentityMock: vi.fn(),
  waitForBackendReadyMock: vi.fn(),
  bootstrapFirstAdminMock: vi.fn(),
}));

vi.mock("@/ipc/session", () => ({
  hasStoredSession: hasStoredSessionMock,
  clearStoredSession: clearStoredSessionMock,
}));

vi.mock("./authCapability", () => ({
  login: loginMock,
  checkStoredSession: checkStoredSessionMock,
  renewSession: renewSessionMock,
  classifyIpcFailure: classifyIpcFailureMock,
}));

vi.mock("./identityCache", () => ({
  loadCachedIdentity: loadCachedIdentityMock,
  saveCachedIdentity: saveCachedIdentityMock,
  clearCachedIdentity: clearCachedIdentityMock,
}));

vi.mock("./backendReadiness", () => ({
  waitForBackendReady: waitForBackendReadyMock,
}));

vi.mock("./bootstrapCapability", () => ({
  bootstrapFirstAdmin: bootstrapFirstAdminMock,
}));

const IDENTITY: AuthIdentity = { principalId: "alice", principalType: "USER", tenantId: "acme", roles: ["reader"] };

function envelope(overrides: Partial<IpcResultEnvelope> = {}): IpcResultEnvelope {
  return {
    requestId: "req-1",
    correlationId: "corr-1",
    status: "FAILURE",
    payload: null,
    errors: [],
    warnings: [],
    executionDurationMs: 1,
    ...overrides,
  };
}

function Probe() {
  const auth = useAuth();
  return (
    <div>
      <p data-testid="status">{auth.state.status}</p>
      <p data-testid="identity">
        {auth.state.status === "AUTHENTICATED" ? auth.state.identity?.principalId ?? "none" : ""}
      </p>
      <p data-testid="error">{auth.state.status === "AUTHENTICATION_ERROR" ? auth.state.message : ""}</p>
      <p data-testid="bootstrap-error">{auth.state.status === "BOOTSTRAP_ERROR" ? auth.state.message : ""}</p>
      <p data-testid="attempt">
        {auth.state.status === "STARTING" ? `${auth.state.attempt}/${auth.state.maxAttempts}` : ""}
      </p>
      <button onClick={() => void auth.login({ tenantId: "acme", principalId: "alice", password: "x" })}>
        Login
      </button>
      <button onClick={() => void auth.logout()}>Logout</button>
      <button
        onClick={() => void auth.bootstrap({ tenantId: "acme", principalId: "alice", password: "a-strong-password" })}
      >
        Bootstrap
      </button>
      <button onClick={() => auth.retryConnection()}>RetryConnection</button>
      <button onClick={() => auth.reportIpcResult(envelope({ httpStatus: 401 }))}>Report401</button>
      <button onClick={() => auth.reportIpcResult(envelope({ httpStatus: 403 }))}>Report403</button>
    </div>
  );
}

function renderAuth(inactivityOptions?: UseInactivityLogoutOptions) {
  return render(
    <AuthProvider inactivityOptions={inactivityOptions}>
      <Probe />
    </AuthProvider>,
  );
}

beforeEach(() => {
  // Default: backend immediately ready, already bootstrapped — preserves
  // every pre-M7.1 test's expectations about the OLD single-step
  // hasStoredSession/checkStoredSession flow. Individual tests override
  // this to exercise the STARTING/BOOTSTRAP_REQUIRED/BACKEND_UNAVAILABLE
  // paths this mock now sits in front of.
  waitForBackendReadyMock.mockResolvedValue({ ready: true, bootstrapRequired: false });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("AuthProvider startup — backend readiness (M7.1)", () => {
  it("starts in CHECKING before backend readiness resolves", () => {
    waitForBackendReadyMock.mockReturnValue(new Promise(() => {})); // never resolves
    renderAuth();
    expect(screen.getByTestId("status")).toHaveTextContent("CHECKING");
  });

  it("moves to STARTING with live attempt progress while readiness is still being polled", async () => {
    waitForBackendReadyMock.mockImplementation(
      (options: { onAttempt?: (attempt: number, maxAttempts: number) => void }) =>
        new Promise(() => {
          options.onAttempt?.(3, 8);
        }),
    );
    renderAuth();

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("STARTING"));
    expect(screen.getByTestId("attempt")).toHaveTextContent("3/8");
  });

  it("resolves to BACKEND_UNAVAILABLE, never calling hasStoredSession, when readiness never succeeds", async () => {
    waitForBackendReadyMock.mockResolvedValue({ ready: false });
    renderAuth();

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("BACKEND_UNAVAILABLE"));
    expect(hasStoredSessionMock).not.toHaveBeenCalled();
  });

  it("resolves to BOOTSTRAP_REQUIRED when the backend reports no principal exists yet", async () => {
    waitForBackendReadyMock.mockResolvedValue({ ready: true, bootstrapRequired: true });
    renderAuth();

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("BOOTSTRAP_REQUIRED"));
    expect(hasStoredSessionMock).not.toHaveBeenCalled();
  });

  it("retryConnection() re-runs the readiness poll from a BACKEND_UNAVAILABLE state", async () => {
    waitForBackendReadyMock.mockResolvedValueOnce({ ready: false });
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("BACKEND_UNAVAILABLE"));
    expect(waitForBackendReadyMock).toHaveBeenCalledTimes(1);

    waitForBackendReadyMock.mockResolvedValueOnce({ ready: true, bootstrapRequired: false });
    hasStoredSessionMock.mockResolvedValue(false);
    await act(async () => screen.getByText("RetryConnection").click());

    expect(waitForBackendReadyMock).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED"));
  });
});

describe("AuthProvider startup — session resolution (pre-M7.1 behavior, now gated behind readiness)", () => {
  it("resolves to UNAUTHENTICATED when no session is stored, without ever calling the backend", async () => {
    hasStoredSessionMock.mockResolvedValue(false);
    renderAuth();

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED"));
    expect(checkStoredSessionMock).not.toHaveBeenCalled();
  });

  it("resolves directly to AUTHENTICATED for a valid stored session, restoring cached identity for display", async () => {
    hasStoredSessionMock.mockResolvedValue(true);
    checkStoredSessionMock.mockResolvedValue("VALID");
    loadCachedIdentityMock.mockReturnValue(IDENTITY);
    renderAuth();

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED"));
    expect(screen.getByTestId("identity")).toHaveTextContent("alice");
  });

  it("clears the invalid session and resolves to UNAUTHENTICATED for an invalid/expired stored token", async () => {
    hasStoredSessionMock.mockResolvedValue(true);
    checkStoredSessionMock.mockResolvedValue("INVALID");
    renderAuth();

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED"));
    expect(clearStoredSessionMock).toHaveBeenCalledTimes(1);
    expect(clearCachedIdentityMock).toHaveBeenCalledTimes(1);
  });

  it("fails closed to UNAUTHENTICATED, never stuck in a startup state, if the Tauri IPC bridge itself rejects", async () => {
    hasStoredSessionMock.mockRejectedValue(new Error("window.__TAURI_INTERNALS__ is undefined"));
    renderAuth();

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED"));
    expect(checkStoredSessionMock).not.toHaveBeenCalled();
  });

  it("resolves to BACKEND_UNAVAILABLE without clearing the session when the backend can't be reached mid-session-check", async () => {
    hasStoredSessionMock.mockResolvedValue(true);
    checkStoredSessionMock.mockResolvedValue("BACKEND_UNAVAILABLE");
    renderAuth();

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("BACKEND_UNAVAILABLE"));
    expect(clearStoredSessionMock).not.toHaveBeenCalled();
  });
});

describe("bootstrap (M7.1)", () => {
  it("moves through BOOTSTRAPPING then reuses login() to reach AUTHENTICATED on success", async () => {
    waitForBackendReadyMock.mockResolvedValue({ ready: true, bootstrapRequired: true });
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("BOOTSTRAP_REQUIRED"));

    let resolveBootstrap!: (value: unknown) => void;
    bootstrapFirstAdminMock.mockReturnValueOnce(new Promise((resolve) => (resolveBootstrap = resolve)));

    act(() => screen.getByText("Bootstrap").click());
    expect(screen.getByTestId("status")).toHaveTextContent("BOOTSTRAPPING");

    loginMock.mockResolvedValueOnce({ ok: true, identity: IDENTITY });
    await act(async () => resolveBootstrap({ ok: true }));

    expect(loginMock).toHaveBeenCalledWith({ tenantId: "acme", principalId: "alice", password: "a-strong-password" });
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED"));
  });

  it("moves to BOOTSTRAP_ERROR on a validation failure, surfacing the message", async () => {
    waitForBackendReadyMock.mockResolvedValue({ ready: true, bootstrapRequired: true });
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("BOOTSTRAP_REQUIRED"));

    bootstrapFirstAdminMock.mockResolvedValueOnce({
      ok: false,
      kind: "VALIDATION_FAILED",
      message: "Password must be at least 8 characters.",
    });
    await act(async () => screen.getByText("Bootstrap").click());

    expect(screen.getByTestId("status")).toHaveTextContent("BOOTSTRAP_ERROR");
    expect(screen.getByTestId("bootstrap-error")).toHaveTextContent("Password must be at least 8 characters.");
    expect(loginMock).not.toHaveBeenCalled();
  });

  it("moves to BOOTSTRAP_ERROR when the system was already bootstrapped concurrently", async () => {
    waitForBackendReadyMock.mockResolvedValue({ ready: true, bootstrapRequired: true });
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("BOOTSTRAP_REQUIRED"));

    bootstrapFirstAdminMock.mockResolvedValueOnce({
      ok: false,
      kind: "ALREADY_BOOTSTRAPPED",
      message: "Bootstrap is no longer available: an administrator already exists.",
    });
    await act(async () => screen.getByText("Bootstrap").click());

    expect(screen.getByTestId("status")).toHaveTextContent("BOOTSTRAP_ERROR");
  });

  it("moves to BACKEND_UNAVAILABLE if bootstrap reports the backend is unreachable", async () => {
    waitForBackendReadyMock.mockResolvedValue({ ready: true, bootstrapRequired: true });
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("BOOTSTRAP_REQUIRED"));

    bootstrapFirstAdminMock.mockResolvedValueOnce({ ok: false, kind: "BACKEND_UNAVAILABLE", message: "unreachable" });
    await act(async () => screen.getByText("Bootstrap").click());

    expect(screen.getByTestId("status")).toHaveTextContent("BACKEND_UNAVAILABLE");
  });

  it("prevents a duplicate submission while a bootstrap is already in flight", async () => {
    waitForBackendReadyMock.mockResolvedValue({ ready: true, bootstrapRequired: true });
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("BOOTSTRAP_REQUIRED"));

    let resolveBootstrap!: (value: unknown) => void;
    bootstrapFirstAdminMock.mockReturnValueOnce(new Promise((resolve) => (resolveBootstrap = resolve)));

    act(() => {
      screen.getByText("Bootstrap").click();
      screen.getByText("Bootstrap").click();
      screen.getByText("Bootstrap").click();
    });

    expect(bootstrapFirstAdminMock).toHaveBeenCalledTimes(1);
    await act(async () => resolveBootstrap({ ok: false, kind: "VALIDATION_FAILED", message: "x" }));
  });
});

describe("login", () => {
  it("moves through AUTHENTICATING to AUTHENTICATED on success, caching the returned identity", async () => {
    hasStoredSessionMock.mockResolvedValue(false);
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED"));

    let resolveLogin!: (value: unknown) => void;
    loginMock.mockReturnValueOnce(new Promise((resolve) => (resolveLogin = resolve)));

    act(() => screen.getByText("Login").click());
    expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATING");

    await act(async () => resolveLogin({ ok: true, identity: IDENTITY }));

    expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED");
    expect(screen.getByTestId("identity")).toHaveTextContent("alice");
    expect(saveCachedIdentityMock).toHaveBeenCalledWith(IDENTITY);
  });

  it("moves to AUTHENTICATION_ERROR on invalid credentials, surfacing the message", async () => {
    hasStoredSessionMock.mockResolvedValue(false);
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED"));

    loginMock.mockResolvedValueOnce({ ok: false, kind: "INVALID_CREDENTIALS", message: "Authentication failed." });
    await act(async () => screen.getByText("Login").click());

    expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATION_ERROR");
    expect(screen.getByTestId("error")).toHaveTextContent("Authentication failed.");
  });

  it("moves to BACKEND_UNAVAILABLE when login reports the backend is unreachable", async () => {
    hasStoredSessionMock.mockResolvedValue(false);
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED"));

    loginMock.mockResolvedValueOnce({ ok: false, kind: "BACKEND_UNAVAILABLE", message: "unreachable" });
    await act(async () => screen.getByText("Login").click());

    expect(screen.getByTestId("status")).toHaveTextContent("BACKEND_UNAVAILABLE");
  });

  it("moves to BACKEND_UNAVAILABLE, never a stuck AUTHENTICATING, if the login call unexpectedly rejects", async () => {
    hasStoredSessionMock.mockResolvedValue(false);
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED"));

    loginMock.mockRejectedValueOnce(new Error("unexpected"));
    await act(async () => screen.getByText("Login").click());

    expect(screen.getByTestId("status")).toHaveTextContent("BACKEND_UNAVAILABLE");
  });

  it("prevents a duplicate submission while a login is already in flight", async () => {
    hasStoredSessionMock.mockResolvedValue(false);
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED"));

    let resolveLogin!: (value: unknown) => void;
    loginMock.mockReturnValueOnce(new Promise((resolve) => (resolveLogin = resolve)));

    act(() => {
      screen.getByText("Login").click();
      screen.getByText("Login").click();
      screen.getByText("Login").click();
    });

    expect(loginMock).toHaveBeenCalledTimes(1);
    await act(async () => resolveLogin({ ok: true, identity: IDENTITY }));
  });
});

describe("logout", () => {
  it("clears the session and cached identity, returning to UNAUTHENTICATED", async () => {
    hasStoredSessionMock.mockResolvedValue(true);
    checkStoredSessionMock.mockResolvedValue("VALID");
    loadCachedIdentityMock.mockReturnValue(IDENTITY);
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED"));

    await act(async () => screen.getByText("Logout").click());

    expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED");
    expect(clearStoredSessionMock).toHaveBeenCalled();
    expect(clearCachedIdentityMock).toHaveBeenCalled();
  });
});

describe("401 vs 403 (Phase 7)", () => {
  it("a 401 on any authenticated call ends the session and forces re-authentication", async () => {
    hasStoredSessionMock.mockResolvedValue(true);
    checkStoredSessionMock.mockResolvedValue("VALID");
    loadCachedIdentityMock.mockReturnValue(IDENTITY);
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED"));

    classifyIpcFailureMock.mockReturnValueOnce("UNAUTHORIZED");
    await act(async () => screen.getByText("Report401").click());

    expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED");
    expect(clearStoredSessionMock).toHaveBeenCalled();
  });

  it("a 403 on any authenticated call never logs the user out", async () => {
    hasStoredSessionMock.mockResolvedValue(true);
    checkStoredSessionMock.mockResolvedValue("VALID");
    loadCachedIdentityMock.mockReturnValue(IDENTITY);
    renderAuth();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED"));

    classifyIpcFailureMock.mockReturnValueOnce("FORBIDDEN");
    await act(async () => screen.getByText("Report403").click());

    expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED");
    expect(clearStoredSessionMock).not.toHaveBeenCalled();
  });
});

describe("Phase F — true one-hour inactivity logout", () => {
  // Fake timers are installed BEFORE mount for every test in this block:
  // `useInactivityLogout`'s `setTimeout`/`setInterval` calls must be made
  // against the fake clock from the moment the effect first runs, or
  // `vi.advanceTimersByTime` later has no effect on them (a timer created
  // against the real clock is not retroactively adopted by switching to
  // fake timers afterward). Fake timers do not intercept microtask-based
  // Promise resolution, so the async startup chain
  // (`waitForBackendReady`/`hasStoredSession`/`checkStoredSession`, all
  // pre-resolved mocks with no real delay) still resolves; `flushAsync`
  // below drains exactly that microtask queue in place of
  // testing-library's `waitFor` (which polls via a real `setTimeout` and
  // would hang against a fake clock that nothing is advancing).
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function flushAsync() {
    await act(async () => {
      for (let i = 0; i < 10; i += 1) {
        await Promise.resolve();
      }
    });
  }

  async function renderAuthenticated(inactivityOptions: UseInactivityLogoutOptions) {
    hasStoredSessionMock.mockResolvedValue(true);
    checkStoredSessionMock.mockResolvedValue("VALID");
    renewSessionMock.mockResolvedValue("VALID");
    loadCachedIdentityMock.mockReturnValue(IDENTITY);
    renderAuth(inactivityOptions);
    await flushAsync();
    expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED");
  }

  it("logs the user out after the configured inactivity timeout with no activity", async () => {
    await renderAuthenticated({ timeoutMs: 1000, heartbeatIntervalMs: 100_000 });

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await flushAsync();

    expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED");
    expect(clearStoredSessionMock).toHaveBeenCalled();
    expect(clearCachedIdentityMock).toHaveBeenCalled();
  });

  it("does not log out an active user -- genuine activity resets the countdown", async () => {
    await renderAuthenticated({ timeoutMs: 1000, heartbeatIntervalMs: 100_000 });

    act(() => {
      vi.advanceTimersByTime(900);
      window.dispatchEvent(new Event("mousemove"));
      vi.advanceTimersByTime(900);
    });
    await flushAsync();

    // 1800ms of elapsed wall-clock time against a 1000ms timeout would
    // have logged a genuinely-idle user out -- the reset at 900ms is what
    // must have prevented it.
    expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED");

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await flushAsync();
    expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED");
  });

  it("background heartbeat polling does not reset the inactivity countdown", async () => {
    await renderAuthenticated({ timeoutMs: 1000, heartbeatIntervalMs: 100 });

    act(() => {
      // Several heartbeats fire (every 100ms) well before the 1000ms
      // inactivity deadline -- none of them may push it out.
      vi.advanceTimersByTime(999);
    });
    await flushAsync();
    expect(renewSessionMock.mock.calls.length).toBeGreaterThan(0); // heartbeats renewing the session
    expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED");

    act(() => {
      vi.advanceTimersByTime(1);
    });
    await flushAsync();
    expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED");
  });

  it("the heartbeat renews the session via the dedicated refresh capability, not an ordinary authenticated call", async () => {
    // Phase F security correction: after the security review found that
    // minting a fresh access token on ANY successful authenticated call
    // made a stolen access token renewable indefinitely, ordinary
    // capability calls (including checkStoredSession's own signature.verify
    // ping) no longer mint or renew anything. The heartbeat now calls
    // `renewSession` (`kortex.security.auth.refresh`) specifically.
    await renderAuthenticated({ timeoutMs: 100_000, heartbeatIntervalMs: 500 });
    const renewCallsAtStart = renewSessionMock.mock.calls.length;
    const checkCallsAtStart = checkStoredSessionMock.mock.calls.length;

    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await flushAsync();

    expect(renewSessionMock.mock.calls.length).toBeGreaterThanOrEqual(renewCallsAtStart + 3);
    // checkStoredSession is only ever the one-time startup validation ping
    // -- heartbeats must never call it.
    expect(checkStoredSessionMock.mock.calls.length).toBe(checkCallsAtStart);
    expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED");
  });

  it("logs out immediately if the heartbeat discovers the refresh token is already invalid", async () => {
    hasStoredSessionMock.mockResolvedValue(true);
    checkStoredSessionMock.mockResolvedValue("VALID"); // startup check
    loadCachedIdentityMock.mockReturnValue(IDENTITY);
    // No heartbeat has fired yet at this point (interval is 500ms, nothing
    // has advanced the clock) -- checkStoredSession alone (the startup
    // check) is what gets this to AUTHENTICATED, so renewSession need not
    // be primed until just before the first heartbeat below.
    renderAuth({ timeoutMs: 100_000, heartbeatIntervalMs: 500 });
    await flushAsync();
    expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED");

    renewSessionMock.mockResolvedValue("INVALID"); // every heartbeat from here on

    act(() => {
      vi.advanceTimersByTime(500);
    });
    await flushAsync();

    expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED");
    expect(clearStoredSessionMock).toHaveBeenCalled();
  });

  it("cleans up timers/listeners on logout -- no further logout/heartbeat calls after the session ends", async () => {
    await renderAuthenticated({ timeoutMs: 1000, heartbeatIntervalMs: 200 });

    await act(async () => screen.getByText("Logout").click());
    expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED");
    const renewCallsAtLogout = renewSessionMock.mock.calls.length;
    const clearCallsAtLogout = clearStoredSessionMock.mock.calls.length;

    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    await flushAsync();

    // Already signed out -- the hook must be disabled (isAuthenticated is
    // false), so neither the heartbeat nor another timeout-triggered
    // logout can fire again.
    expect(renewSessionMock.mock.calls.length).toBe(renewCallsAtLogout);
    expect(clearStoredSessionMock.mock.calls.length).toBe(clearCallsAtLogout);
  });

  it("production default (no inactivityOptions override) is exactly 60 minutes and does not fire early", async () => {
    hasStoredSessionMock.mockResolvedValue(true);
    checkStoredSessionMock.mockResolvedValue("VALID");
    renewSessionMock.mockResolvedValue("VALID");
    loadCachedIdentityMock.mockReturnValue(IDENTITY);
    renderAuth(); // no override -- exercises the real INACTIVITY_TIMEOUT_MS/HEARTBEAT_INTERVAL_MS constants
    await flushAsync();
    expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED");

    act(() => {
      vi.advanceTimersByTime(59 * 60 * 1000); // 59 minutes -- must not have logged out yet
    });
    await flushAsync();
    expect(screen.getByTestId("status")).toHaveTextContent("AUTHENTICATED");

    act(() => {
      vi.advanceTimersByTime(2 * 60 * 1000); // past the full 60-minute mark
    });
    await flushAsync();
    expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED");
  });
});

describe("useAuth", () => {
  it("throws when used outside an AuthProvider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    function Orphan() {
      useAuth();
      return null;
    }
    expect(() => render(<Orphan />)).toThrow("useAuth must be used within an AuthProvider");
    spy.mockRestore();
  });
});
