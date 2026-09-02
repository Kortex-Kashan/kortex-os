import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthGate } from "./AuthGate";
import type { AuthState } from "./authTypes";

const { useAuthMock } = vi.hoisted(() => ({ useAuthMock: vi.fn() }));

vi.mock("./AuthProvider", () => ({
  useAuth: useAuthMock,
}));

vi.mock("./LoginScreen", () => ({
  LoginScreen: () => <div>LOGIN SCREEN MARKER</div>,
}));

vi.mock("./BootstrapScreen", () => ({
  BootstrapScreen: () => <div>BOOTSTRAP SCREEN MARKER</div>,
}));

vi.mock("./BackendUnavailableScreen", () => ({
  BackendUnavailableScreen: () => <div>BACKEND UNAVAILABLE SCREEN MARKER</div>,
}));

function mockState(state: AuthState) {
  useAuthMock.mockReturnValue({
    state,
    login: vi.fn(),
    logout: vi.fn(),
    bootstrap: vi.fn(),
    retryConnection: vi.fn(),
    reportIpcResult: vi.fn(),
  });
}

describe("AuthGate", () => {
  it("renders neither any screen nor the shell while CHECKING", () => {
    mockState({ status: "CHECKING" });
    render(
      <AuthGate>
        <div>SHELL MARKER</div>
      </AuthGate>,
    );

    expect(screen.queryByText("LOGIN SCREEN MARKER")).not.toBeInTheDocument();
    expect(screen.queryByText("BOOTSTRAP SCREEN MARKER")).not.toBeInTheDocument();
    expect(screen.queryByText("BACKEND UNAVAILABLE SCREEN MARKER")).not.toBeInTheDocument();
    expect(screen.queryByText("SHELL MARKER")).not.toBeInTheDocument();
  });

  it("renders the starting screen with live attempt progress while STARTING", async () => {
    mockState({ status: "STARTING", attempt: 2, maxAttempts: 8 });
    render(
      <AuthGate>
        <div>SHELL MARKER</div>
      </AuthGate>,
    );

    expect(await screen.findByText(/starting kortex backend/i)).toHaveTextContent("(2/8)");
    expect(screen.queryByText("SHELL MARKER")).not.toBeInTheDocument();
  });

  it("renders the backend-unavailable screen, not the login screen, when BACKEND_UNAVAILABLE", async () => {
    mockState({ status: "BACKEND_UNAVAILABLE" });
    render(
      <AuthGate>
        <div>SHELL MARKER</div>
      </AuthGate>,
    );

    expect(await screen.findByText("BACKEND UNAVAILABLE SCREEN MARKER")).toBeInTheDocument();
    expect(screen.queryByText("LOGIN SCREEN MARKER")).not.toBeInTheDocument();
  });

  it.each(["BOOTSTRAP_REQUIRED", "BOOTSTRAPPING"] as const)(
    "renders the bootstrap screen, not the login screen, for %s",
    async (status) => {
      mockState({ status });
      render(
        <AuthGate>
          <div>SHELL MARKER</div>
        </AuthGate>,
      );

      expect(await screen.findByText("BOOTSTRAP SCREEN MARKER")).toBeInTheDocument();
      expect(screen.queryByText("LOGIN SCREEN MARKER")).not.toBeInTheDocument();
    },
  );

  it("renders the bootstrap screen for BOOTSTRAP_ERROR", async () => {
    mockState({ status: "BOOTSTRAP_ERROR", message: "Password must be at least 8 characters." });
    render(
      <AuthGate>
        <div>SHELL MARKER</div>
      </AuthGate>,
    );

    expect(await screen.findByText("BOOTSTRAP SCREEN MARKER")).toBeInTheDocument();
  });

  it("renders the login screen when UNAUTHENTICATED", async () => {
    mockState({ status: "UNAUTHENTICATED" });
    render(
      <AuthGate>
        <div>SHELL MARKER</div>
      </AuthGate>,
    );

    expect(await screen.findByText("LOGIN SCREEN MARKER")).toBeInTheDocument();
    expect(screen.queryByText("SHELL MARKER")).not.toBeInTheDocument();
  });

  it("renders the login screen for AUTHENTICATION_ERROR too", async () => {
    mockState({ status: "AUTHENTICATION_ERROR", message: "nope" });
    render(
      <AuthGate>
        <div>SHELL MARKER</div>
      </AuthGate>,
    );
    expect(await screen.findByText("LOGIN SCREEN MARKER")).toBeInTheDocument();
  });

  it("renders the authenticated shell, never the login screen, once AUTHENTICATED", async () => {
    mockState({ status: "AUTHENTICATED", identity: null });
    render(
      <AuthGate>
        <div>SHELL MARKER</div>
      </AuthGate>,
    );

    expect(await screen.findByText("SHELL MARKER")).toBeInTheDocument();
    expect(screen.queryByText("LOGIN SCREEN MARKER")).not.toBeInTheDocument();
  });

  it("never shows the shell and the login screen at the same time when transitioning from CHECKING to AUTHENTICATED", () => {
    mockState({ status: "CHECKING" });
    const { rerender } = render(
      <AuthGate>
        <div>SHELL MARKER</div>
      </AuthGate>,
    );
    expect(screen.queryByText("SHELL MARKER")).not.toBeInTheDocument();
    expect(screen.queryByText("LOGIN SCREEN MARKER")).not.toBeInTheDocument();

    mockState({ status: "AUTHENTICATED", identity: null });
    rerender(
      <AuthGate>
        <div>SHELL MARKER</div>
      </AuthGate>,
    );

    // A valid stored session must transition CHECKING -> AUTHENTICATED
    // directly, never rendering the login screen in between.
    expect(screen.queryByText("LOGIN SCREEN MARKER")).not.toBeInTheDocument();
  });
});
