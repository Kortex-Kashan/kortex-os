import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IpcResultEnvelope } from "@/ipc/client";

import { AuthProvider, useAuth } from "./AuthProvider";
import type { AuthIdentity } from "./authTypes";

const {
  hasStoredSessionMock,
  clearStoredSessionMock,
  loginMock,
  checkStoredSessionMock,
  classifyIpcFailureMock,
  loadCachedIdentityMock,
  saveCachedIdentityMock,
  clearCachedIdentityMock,
} = vi.hoisted(() => ({
  hasStoredSessionMock: vi.fn(),
  clearStoredSessionMock: vi.fn(),
  loginMock: vi.fn(),
  checkStoredSessionMock: vi.fn(),
  classifyIpcFailureMock: vi.fn(),
  loadCachedIdentityMock: vi.fn(),
  saveCachedIdentityMock: vi.fn(),
  clearCachedIdentityMock: vi.fn(),
}));

vi.mock("@/ipc/session", () => ({
  hasStoredSession: hasStoredSessionMock,
  clearStoredSession: clearStoredSessionMock,
}));

vi.mock("./authCapability", () => ({
  login: loginMock,
  checkStoredSession: checkStoredSessionMock,
  classifyIpcFailure: classifyIpcFailureMock,
}));

vi.mock("./identityCache", () => ({
  loadCachedIdentity: loadCachedIdentityMock,
  saveCachedIdentity: saveCachedIdentityMock,
  clearCachedIdentity: clearCachedIdentityMock,
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
      <button onClick={() => void auth.login({ tenantId: "acme", principalId: "alice", password: "x" })}>
        Login
      </button>
      <button onClick={() => void auth.logout()}>Logout</button>
      <button onClick={() => auth.reportIpcResult(envelope({ httpStatus: 401 }))}>Report401</button>
      <button onClick={() => auth.reportIpcResult(envelope({ httpStatus: 403 }))}>Report403</button>
    </div>
  );
}

function renderAuth() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("AuthProvider startup", () => {
  it("starts in CHECKING before the session check resolves", () => {
    hasStoredSessionMock.mockReturnValue(new Promise(() => {})); // never resolves
    renderAuth();
    expect(screen.getByTestId("status")).toHaveTextContent("CHECKING");
  });

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

  it("fails closed to UNAUTHENTICATED, never stuck in CHECKING, if the Tauri IPC bridge itself rejects", async () => {
    hasStoredSessionMock.mockRejectedValue(new Error("window.__TAURI_INTERNALS__ is undefined"));
    renderAuth();

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("UNAUTHENTICATED"));
    expect(checkStoredSessionMock).not.toHaveBeenCalled();
  });

  it("resolves to BACKEND_UNAVAILABLE without clearing the session when the backend can't be reached", async () => {
    hasStoredSessionMock.mockResolvedValue(true);
    checkStoredSessionMock.mockResolvedValue("BACKEND_UNAVAILABLE");
    renderAuth();

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("BACKEND_UNAVAILABLE"));
    expect(clearStoredSessionMock).not.toHaveBeenCalled();
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
