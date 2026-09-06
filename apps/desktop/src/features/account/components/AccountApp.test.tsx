import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountApp } from "./AccountApp";

const {
  useAuthMock,
  changePasswordMock,
  setEmailMock,
  registerPrincipalMock,
  listOAuthLinksMock,
  getOAuthConfigMock,
} = vi.hoisted(() => ({
  useAuthMock: vi.fn(),
  changePasswordMock: vi.fn(),
  setEmailMock: vi.fn(),
  registerPrincipalMock: vi.fn(),
  listOAuthLinksMock: vi.fn(),
  getOAuthConfigMock: vi.fn(),
}));

vi.mock("@/auth/AuthProvider", () => ({
  useAuth: useAuthMock,
}));

vi.mock("@/auth/oauthCapability", () => ({
  getOAuthConfig: getOAuthConfigMock,
}));

vi.mock("@/auth/useOAuthDeepLink", () => ({
  useOAuthDeepLink: vi.fn(),
}));

vi.mock("@tauri-apps/plugin-shell", () => ({
  open: vi.fn(),
}));

vi.mock("../api", () => ({
  changePassword: changePasswordMock,
  setEmail: setEmailMock,
  registerPrincipal: registerPrincipalMock,
  listOAuthLinks: listOAuthLinksMock,
  beginOAuthLink: vi.fn(),
  completeOAuthLink: vi.fn(),
  unlinkOAuth: vi.fn(),
}));

function mockIdentity(roles: string[]) {
  getOAuthConfigMock.mockResolvedValue({ google: false, microsoft: false });
  listOAuthLinksMock.mockResolvedValue([]);
  const logout = vi.fn();
  useAuthMock.mockReturnValue({
    state: {
      status: "AUTHENTICATED",
      identity: { principalId: "alice", principalType: "USER", tenantId: "acme", roles },
    },
    logout,
  });
  return logout;
}

describe("AccountApp identity", () => {
  it("renders the current principal, tenant, and roles", () => {
    mockIdentity(["member"]);
    render(<AccountApp />);

    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("acme")).toBeInTheDocument();
    expect(screen.getByText("member")).toBeInTheDocument();
  });

  it("calls logout when Sign out is clicked", () => {
    const logout = mockIdentity(["member"]);
    render(<AccountApp />);

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(logout).toHaveBeenCalled();
  });
});

describe("AccountApp change password", () => {
  it("submits current and new password, then shows success", async () => {
    mockIdentity(["member"]);
    changePasswordMock.mockResolvedValueOnce(undefined);
    render(<AccountApp />);

    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "old-pw" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "brand-new-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() => expect(screen.getByText("Your password has been changed.")).toBeInTheDocument());
    expect(changePasswordMock).toHaveBeenCalledWith({ currentPassword: "old-pw", newPassword: "brand-new-password" });
  });

  it("shows the backend's error message on failure", async () => {
    mockIdentity(["member"]);
    changePasswordMock.mockRejectedValueOnce(new Error("Authentication failed: invalid credentials."));
    render(<AccountApp />);

    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "wrong" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "brand-new-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Authentication failed: invalid credentials.");
    });
  });
});

describe("AccountApp email", () => {
  it("submits the trimmed email and shows success", async () => {
    mockIdentity(["member"]);
    setEmailMock.mockResolvedValueOnce(undefined);
    render(<AccountApp />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "  alice@example.com  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save email" }));

    await waitFor(() => expect(screen.getByText("Your email has been updated.")).toBeInTheDocument());
    expect(setEmailMock).toHaveBeenCalledWith({ email: "alice@example.com" });
  });
});

describe("AccountApp linked sign-in providers", () => {
  it("hides the card entirely when no provider is configured", async () => {
    mockIdentity(["member"]);
    render(<AccountApp />);

    await waitFor(() => expect(getOAuthConfigMock).toHaveBeenCalled());
    expect(screen.queryByText("Linked sign-in providers")).not.toBeInTheDocument();
  });

  it("shows Connect for a configured, not-yet-linked provider", async () => {
    getOAuthConfigMock.mockResolvedValue({ google: true, microsoft: false });
    listOAuthLinksMock.mockResolvedValue([]);
    mockIdentity(["member"]);
    // mockIdentity resets the two mocks above — restore this test's values.
    getOAuthConfigMock.mockResolvedValue({ google: true, microsoft: false });
    listOAuthLinksMock.mockResolvedValue([]);
    render(<AccountApp />);

    expect(await screen.findByText("Google")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
  });

  it("shows Unlink for an already-linked provider and removes it on click", async () => {
    mockIdentity(["member"]);
    getOAuthConfigMock.mockResolvedValue({ google: true, microsoft: false });
    listOAuthLinksMock.mockResolvedValue(["google"]);
    const { unlinkOAuth } = await import("../api");
    vi.mocked(unlinkOAuth).mockResolvedValueOnce(undefined);
    render(<AccountApp />);

    const unlinkButton = await screen.findByRole("button", { name: "Unlink" });
    fireEvent.click(unlinkButton);

    await waitFor(() => expect(screen.getByText("Google was removed.")).toBeInTheDocument());
    expect(unlinkOAuth).toHaveBeenCalledWith("google");
  });
});

describe("AccountApp admin-only team members panel", () => {
  it("hides the Team members panel for a non-admin", () => {
    mockIdentity(["member"]);
    render(<AccountApp />);

    expect(screen.queryByText("Team members")).not.toBeInTheDocument();
  });

  it("shows the Team members panel for an admin and creates a user", async () => {
    mockIdentity(["admin"]);
    registerPrincipalMock.mockResolvedValueOnce(undefined);
    render(<AccountApp />);

    expect(screen.getByText("Team members")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText("Temporary password"), { target: { value: "temp-password-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Create user" }));

    await waitFor(() => expect(screen.getByText('User "bob" was created.')).toBeInTheDocument());
    expect(registerPrincipalMock).toHaveBeenCalledWith({
      principalId: "bob",
      password: "temp-password-1",
      roles: ["member"],
    });
  });
});
