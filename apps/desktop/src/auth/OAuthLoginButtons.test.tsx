import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OAuthLoginButtons } from "./OAuthLoginButtons";

const { useAuthMock, getOAuthConfigMock, beginOAuthLoginMock, openUrlMock, useOAuthDeepLinkMock } = vi.hoisted(() => ({
  useAuthMock: vi.fn(),
  getOAuthConfigMock: vi.fn(),
  beginOAuthLoginMock: vi.fn(),
  openUrlMock: vi.fn(),
  useOAuthDeepLinkMock: vi.fn(),
}));

vi.mock("./AuthProvider", () => ({
  useAuth: useAuthMock,
}));

vi.mock("./oauthCapability", () => ({
  getOAuthConfig: getOAuthConfigMock,
  beginOAuthLogin: beginOAuthLoginMock,
}));

vi.mock("./useOAuthDeepLink", () => ({
  useOAuthDeepLink: useOAuthDeepLinkMock,
}));

vi.mock("@tauri-apps/plugin-shell", () => ({
  open: openUrlMock,
}));

describe("OAuthLoginButtons", () => {
  it("renders nothing while config is loading or when no provider is configured", async () => {
    getOAuthConfigMock.mockResolvedValue({ google: false, microsoft: false });
    useAuthMock.mockReturnValue({ loginWithOAuth: vi.fn() });
    const { container } = render(<OAuthLoginButtons />);

    await waitFor(() => expect(getOAuthConfigMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("renders an enabled Google button when configured, disabled Microsoft when not", async () => {
    getOAuthConfigMock.mockResolvedValue({ google: true, microsoft: false });
    useAuthMock.mockReturnValue({ loginWithOAuth: vi.fn() });
    render(<OAuthLoginButtons />);

    const googleButton = await screen.findByRole("button", { name: "Continue with Google" });
    expect(googleButton).not.toBeDisabled();

    const microsoftButton = screen.getByRole("button", { name: "Continue with Microsoft" });
    expect(microsoftButton).toBeDisabled();
    expect(microsoftButton).toHaveAttribute("title", "Ask your administrator to configure Microsoft sign-in.");
  });

  it("begins the flow and opens the system browser on click", async () => {
    getOAuthConfigMock.mockResolvedValue({ google: true, microsoft: false });
    beginOAuthLoginMock.mockResolvedValueOnce({
      ok: true,
      authorizationUrl: "https://accounts.google.com/authorize",
      state: "state-1",
    });
    useAuthMock.mockReturnValue({ loginWithOAuth: vi.fn() });
    render(<OAuthLoginButtons />);

    const googleButton = await screen.findByRole("button", { name: "Continue with Google" });
    fireEvent.click(googleButton);

    await waitFor(() => expect(openUrlMock).toHaveBeenCalledWith("https://accounts.google.com/authorize"));
    expect(beginOAuthLoginMock).toHaveBeenCalledWith("google");
  });

  it("calls loginWithOAuth when the deep-link callback matches the pending state", async () => {
    getOAuthConfigMock.mockResolvedValue({ google: true, microsoft: false });
    beginOAuthLoginMock.mockResolvedValueOnce({
      ok: true,
      authorizationUrl: "https://accounts.google.com/authorize",
      state: "state-1",
    });
    const loginWithOAuth = vi.fn();
    useAuthMock.mockReturnValue({ loginWithOAuth });
    let capturedCallback: ((params: { code: string; state: string }) => void) | undefined;
    useOAuthDeepLinkMock.mockImplementation((cb) => {
      capturedCallback = cb;
    });
    render(<OAuthLoginButtons />);

    const googleButton = await screen.findByRole("button", { name: "Continue with Google" });
    fireEvent.click(googleButton);
    await waitFor(() => expect(openUrlMock).toHaveBeenCalled());

    capturedCallback?.({ code: "code-1", state: "state-1" });

    expect(loginWithOAuth).toHaveBeenCalledWith("google", "code-1", "state-1");
  });

  it("ignores a deep-link callback whose state does not match the pending flow", async () => {
    getOAuthConfigMock.mockResolvedValue({ google: true, microsoft: false });
    beginOAuthLoginMock.mockResolvedValueOnce({
      ok: true,
      authorizationUrl: "https://accounts.google.com/authorize",
      state: "state-1",
    });
    const loginWithOAuth = vi.fn();
    useAuthMock.mockReturnValue({ loginWithOAuth });
    let capturedCallback: ((params: { code: string; state: string }) => void) | undefined;
    useOAuthDeepLinkMock.mockImplementation((cb) => {
      capturedCallback = cb;
    });
    render(<OAuthLoginButtons />);

    const googleButton = await screen.findByRole("button", { name: "Continue with Google" });
    fireEvent.click(googleButton);
    await waitFor(() => expect(openUrlMock).toHaveBeenCalled());

    capturedCallback?.({ code: "code-1", state: "an-unrelated-state" });

    expect(loginWithOAuth).not.toHaveBeenCalled();
  });
});
