import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BootstrapScreen } from "./BootstrapScreen";
import type { AuthState } from "./authTypes";

const { useAuthMock, bootstrapSpy, retryConnectionSpy } = vi.hoisted(() => ({
  useAuthMock: vi.fn(),
  bootstrapSpy: vi.fn(),
  retryConnectionSpy: vi.fn(),
}));

vi.mock("./AuthProvider", () => ({
  useAuth: useAuthMock,
}));

function mockAuth(state: AuthState) {
  useAuthMock.mockReturnValue({
    state,
    login: vi.fn(),
    logout: vi.fn(),
    bootstrap: bootstrapSpy,
    retryConnection: retryConnectionSpy,
    reportIpcResult: vi.fn(),
  });
}

function fillForm(password = "a-strong-password") {
  fireEvent.change(screen.getByLabelText("Tenant ID"), { target: { value: "acme" } });
  fireEvent.change(screen.getByLabelText("Administrator username"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: password } });
}

beforeEach(() => {
  bootstrapSpy.mockReset();
  retryConnectionSpy.mockReset();
});

describe("BootstrapScreen fields", () => {
  it("renders semantically labeled Tenant ID, username, password, and confirm-password fields", () => {
    mockAuth({ status: "BOOTSTRAP_REQUIRED" });
    render(<BootstrapScreen />);

    expect(screen.getByLabelText("Tenant ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Administrator username")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm password")).toBeInTheDocument();
  });

  it("autofocuses the Tenant ID field", () => {
    mockAuth({ status: "BOOTSTRAP_REQUIRED" });
    render(<BootstrapScreen />);

    expect(screen.getByLabelText("Tenant ID")).toHaveFocus();
  });

  it("masks both password fields by default and reveals them together via the show/hide toggle", () => {
    mockAuth({ status: "BOOTSTRAP_REQUIRED" });
    render(<BootstrapScreen />);

    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Show password" }));

    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "text");
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute("type", "text");
  });
});

describe("BootstrapScreen client-side validation", () => {
  it("rejects a mismatched confirm-password without calling bootstrap()", () => {
    mockAuth({ status: "BOOTSTRAP_REQUIRED" });
    render(<BootstrapScreen />);

    fireEvent.change(screen.getByLabelText("Tenant ID"), { target: { value: "acme" } });
    fireEvent.change(screen.getByLabelText("Administrator username"), { target: { value: "owner" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-strong-password" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "a-different-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(screen.getByRole("alert")).toHaveTextContent(/passwords do not match/i);
    expect(bootstrapSpy).not.toHaveBeenCalled();
  });

  it("rejects a too-short password without calling bootstrap()", () => {
    mockAuth({ status: "BOOTSTRAP_REQUIRED" });
    render(<BootstrapScreen />);
    fillForm("short");
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(screen.getByRole("alert")).toHaveTextContent(/at least 8 characters/i);
    expect(bootstrapSpy).not.toHaveBeenCalled();
  });

  it("rejects empty tenant ID or username without calling bootstrap()", () => {
    mockAuth({ status: "BOOTSTRAP_REQUIRED" });
    render(<BootstrapScreen />);
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-strong-password" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "a-strong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(screen.getByRole("alert")).toHaveTextContent(/tenant id and username are required/i);
    expect(bootstrapSpy).not.toHaveBeenCalled();
  });
});

describe("BootstrapScreen submission", () => {
  it("submits trimmed tenant/username with the validated password", () => {
    mockAuth({ status: "BOOTSTRAP_REQUIRED" });
    render(<BootstrapScreen />);

    fireEvent.change(screen.getByLabelText("Tenant ID"), { target: { value: "  acme  " } });
    fireEvent.change(screen.getByLabelText("Administrator username"), { target: { value: "  owner  " } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-strong-password" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "a-strong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(bootstrapSpy).toHaveBeenCalledWith({ tenantId: "acme", principalId: "owner", password: "a-strong-password" });
  });

  it("shows a loading state and disables the submit button while BOOTSTRAPPING", () => {
    mockAuth({ status: "BOOTSTRAPPING" });
    render(<BootstrapScreen />);

    const button = screen.getByRole("button", { name: /Setting up/ });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("disables every field while BOOTSTRAPPING", () => {
    mockAuth({ status: "BOOTSTRAPPING" });
    render(<BootstrapScreen />);

    expect(screen.getByLabelText("Tenant ID")).toBeDisabled();
    expect(screen.getByLabelText("Administrator username")).toBeDisabled();
    expect(screen.getByLabelText("Password")).toBeDisabled();
    expect(screen.getByLabelText("Confirm password")).toBeDisabled();
  });

  it("ignores a form submission while already BOOTSTRAPPING (no duplicate submission)", () => {
    mockAuth({ status: "BOOTSTRAPPING" });
    const { container } = render(<BootstrapScreen />);

    fireEvent.submit(container.querySelector("form")!);

    expect(bootstrapSpy).not.toHaveBeenCalled();
  });
});

describe("BootstrapScreen error states", () => {
  it("accessibly announces a bootstrap error from the backend", () => {
    mockAuth({ status: "BOOTSTRAP_ERROR", message: "Bootstrap is no longer available: an administrator already exists." });
    render(<BootstrapScreen />);

    expect(screen.getByRole("alert")).toHaveTextContent(/administrator already exists/i);
  });

  it("clears both password fields, but not the tenant ID or username, after a failed attempt", () => {
    mockAuth({ status: "BOOTSTRAP_REQUIRED" });
    const { rerender } = render(<BootstrapScreen />);
    fillForm();
    expect(screen.getByLabelText("Password")).toHaveValue("a-strong-password");

    mockAuth({ status: "BOOTSTRAPPING" });
    rerender(<BootstrapScreen />);

    mockAuth({ status: "BOOTSTRAP_ERROR", message: "Setup failed." });
    rerender(<BootstrapScreen />);

    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Confirm password")).toHaveValue("");
    expect(screen.getByLabelText("Tenant ID")).toHaveValue("acme");
    expect(screen.getByLabelText("Administrator username")).toHaveValue("owner");
  });
});
