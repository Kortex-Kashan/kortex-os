import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginScreen } from "./LoginScreen";
import type { AuthState } from "./authTypes";

const { useAuthMock, loginSpy } = vi.hoisted(() => ({
  useAuthMock: vi.fn(),
  loginSpy: vi.fn(),
}));

vi.mock("./AuthProvider", () => ({
  useAuth: useAuthMock,
}));

function mockAuth(state: AuthState) {
  useAuthMock.mockReturnValue({
    state,
    login: loginSpy,
    logout: vi.fn(),
    bootstrap: vi.fn(),
    retryConnection: vi.fn(),
    reportIpcResult: vi.fn(),
  });
}

function fillForm() {
  fireEvent.change(screen.getByLabelText("Tenant ID"), { target: { value: "acme" } });
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: "alice" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter2" } });
}

beforeEach(() => {
  loginSpy.mockReset();
});

describe("LoginScreen fields", () => {
  it("renders semantically labeled Tenant ID, Username, and Password fields", () => {
    mockAuth({ status: "UNAUTHENTICATED" });
    render(<LoginScreen />);

    expect(screen.getByLabelText("Tenant ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });

  it("autofocuses the Tenant ID field", () => {
    mockAuth({ status: "UNAUTHENTICATED" });
    render(<LoginScreen />);

    expect(screen.getByLabelText("Tenant ID")).toHaveFocus();
  });

  it("masks the password by default and reveals it via the show/hide toggle", () => {
    mockAuth({ status: "UNAUTHENTICATED" });
    render(<LoginScreen />);

    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Hide password" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Hide password" }));
    expect(password).toHaveAttribute("type", "password");
  });
});

describe("LoginScreen submission", () => {
  it("submits the entered, trimmed credentials", () => {
    mockAuth({ status: "UNAUTHENTICATED" });
    render(<LoginScreen />);

    fireEvent.change(screen.getByLabelText("Tenant ID"), { target: { value: "  acme  " } });
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "  alice  " } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter2" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));

    expect(loginSpy).toHaveBeenCalledWith({ tenantId: "acme", principalId: "alice", password: "hunter2" });
  });

  it("shows a loading state and disables the submit button while AUTHENTICATING", () => {
    mockAuth({ status: "AUTHENTICATING" });
    render(<LoginScreen />);

    const button = screen.getByRole("button", { name: /Signing in/ });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("disables every field while AUTHENTICATING", () => {
    mockAuth({ status: "AUTHENTICATING" });
    render(<LoginScreen />);

    expect(screen.getByLabelText("Tenant ID")).toBeDisabled();
    expect(screen.getByLabelText("Username")).toBeDisabled();
    expect(screen.getByLabelText("Password")).toBeDisabled();
  });

  it("never renders more than one loading indicator at a time", () => {
    mockAuth({ status: "AUTHENTICATING" });
    render(<LoginScreen />);

    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("ignores a form submission while already AUTHENTICATING (no duplicate submission)", () => {
    mockAuth({ status: "AUTHENTICATING" });
    const { container } = render(<LoginScreen />);

    fireEvent.submit(container.querySelector("form")!);

    expect(loginSpy).not.toHaveBeenCalled();
  });
});

describe("LoginScreen error states", () => {
  it("accessibly announces an authentication error", () => {
    mockAuth({ status: "AUTHENTICATION_ERROR", message: "Authentication failed: invalid credentials." });
    render(<LoginScreen />);

    expect(screen.getByRole("alert")).toHaveTextContent("Authentication failed: invalid credentials.");
  });

  it("clears the password, but not the tenant ID or username, after a failed attempt", () => {
    mockAuth({ status: "UNAUTHENTICATED" });
    const { rerender } = render(<LoginScreen />);
    fillForm();
    expect(screen.getByLabelText("Password")).toHaveValue("hunter2");

    mockAuth({ status: "AUTHENTICATING" });
    rerender(<LoginScreen />);

    mockAuth({ status: "AUTHENTICATION_ERROR", message: "Authentication failed." });
    rerender(<LoginScreen />);

    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Tenant ID")).toHaveValue("acme");
    expect(screen.getByLabelText("Username")).toHaveValue("alice");
  });

  it("clears the password after the backend turns out to be unreachable mid-attempt too", () => {
    mockAuth({ status: "UNAUTHENTICATED" });
    const { rerender } = render(<LoginScreen />);
    fillForm();

    mockAuth({ status: "AUTHENTICATING" });
    rerender(<LoginScreen />);

    mockAuth({ status: "BACKEND_UNAVAILABLE" });
    rerender(<LoginScreen />);

    expect(screen.getByLabelText("Password")).toHaveValue("");
  });
});
