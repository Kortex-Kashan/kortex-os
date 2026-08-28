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

function mockState(state: AuthState) {
  useAuthMock.mockReturnValue({ state, login: vi.fn(), logout: vi.fn(), reportIpcResult: vi.fn() });
}

describe("AuthGate", () => {
  it("renders neither the login screen nor the shell while CHECKING", () => {
    mockState({ status: "CHECKING" });
    render(
      <AuthGate>
        <div>SHELL MARKER</div>
      </AuthGate>,
    );

    expect(screen.queryByText("LOGIN SCREEN MARKER")).not.toBeInTheDocument();
    expect(screen.queryByText("SHELL MARKER")).not.toBeInTheDocument();
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

  it("renders the login screen for AUTHENTICATION_ERROR and BACKEND_UNAVAILABLE too", async () => {
    mockState({ status: "AUTHENTICATION_ERROR", message: "nope" });
    const { rerender } = render(
      <AuthGate>
        <div>SHELL MARKER</div>
      </AuthGate>,
    );
    expect(await screen.findByText("LOGIN SCREEN MARKER")).toBeInTheDocument();

    mockState({ status: "BACKEND_UNAVAILABLE" });
    rerender(
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
