import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { registerIntegrationConnectorProfileMock } = vi.hoisted(() => ({
  registerIntegrationConnectorProfileMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    registerIntegrationConnectorProfile: registerIntegrationConnectorProfileMock,
  };
});

const { beginGitHubOAuthMock, completeGitHubOAuthMock } = vi.hoisted(() => ({
  beginGitHubOAuthMock: vi.fn(),
  completeGitHubOAuthMock: vi.fn(),
}));

vi.mock("@/auth/githubConnectorOAuth", () => ({
  beginGitHubOAuth: beginGitHubOAuthMock,
  completeGitHubOAuth: completeGitHubOAuthMock,
}));

const { useConnectorOAuthDeepLinkMock } = vi.hoisted(() => ({
  useConnectorOAuthDeepLinkMock: vi.fn(),
}));

vi.mock("@/auth/useConnectorOAuthDeepLink", () => ({
  useConnectorOAuthDeepLink: useConnectorOAuthDeepLinkMock,
}));

const { openUrlMock } = vi.hoisted(() => ({ openUrlMock: vi.fn() }));

vi.mock("@tauri-apps/plugin-shell", () => ({
  open: openUrlMock,
}));

import { GitHubConnectionForm } from "./GitHubConnectionForm";

function renderForm(props: Partial<Parameters<typeof GitHubConnectionForm>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <GitHubConnectionForm onSuccess={props.onSuccess ?? vi.fn()} onCancel={props.onCancel ?? vi.fn()} />
    </QueryClientProvider>,
  );
}

function fillRequiredFields() {
  fireEvent.change(screen.getByLabelText(/Connection ID/i), { target: { value: "github-main" } });
  fireEvent.change(screen.getByLabelText(/Display Name/i), { target: { value: "GitHub" } });
}

describe("GitHubConnectionForm", () => {
  let capturedCallback: ((params: { code: string; state: string }) => void) | undefined;

  beforeEach(() => {
    registerIntegrationConnectorProfileMock.mockReset();
    beginGitHubOAuthMock.mockReset();
    completeGitHubOAuthMock.mockReset();
    openUrlMock.mockReset();
    capturedCallback = undefined;
    useConnectorOAuthDeepLinkMock.mockImplementation((cb: (params: { code: string; state: string }) => void) => {
      capturedCallback = cb;
    });
  });

  it("renders the required fields and no manual credential field", () => {
    renderForm();

    expect(screen.getByLabelText(/Connection ID/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Display Name/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/credential/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Connect with GitHub/i })).toBeInTheDocument();
  });

  it("begins the OAuth flow and opens the system browser on submit", async () => {
    beginGitHubOAuthMock.mockResolvedValueOnce({
      ok: true,
      authorizationUrl: "https://github.com/login/oauth/authorize?state=s1",
      state: "s1",
    });
    renderForm();
    fillRequiredFields();

    fireEvent.click(screen.getByRole("button", { name: /Connect with GitHub/i }));

    await waitFor(() => expect(beginGitHubOAuthMock).toHaveBeenCalledWith("github-main"));
    await waitFor(() =>
      expect(openUrlMock).toHaveBeenCalledWith("https://github.com/login/oauth/authorize?state=s1"),
    );
    expect(screen.getByText(/Waiting for GitHub/i)).toBeInTheDocument();
  });

  it("shows the begin failure message and does not open a browser", async () => {
    beginGitHubOAuthMock.mockResolvedValueOnce({ ok: false, message: "GitHub is not configured." });
    renderForm();
    fillRequiredFields();

    fireEvent.click(screen.getByRole("button", { name: /Connect with GitHub/i }));

    expect(await screen.findByText("GitHub is not configured.")).toBeInTheDocument();
    expect(openUrlMock).not.toHaveBeenCalled();
  });

  it("completes the flow and registers the profile when the deep link callback matches", async () => {
    beginGitHubOAuthMock.mockResolvedValueOnce({
      ok: true,
      authorizationUrl: "https://github.com/login/oauth/authorize?state=s1",
      state: "s1",
    });
    completeGitHubOAuthMock.mockResolvedValueOnce({ ok: true, secretHandle: "integration-oauth:abc123" });
    registerIntegrationConnectorProfileMock.mockResolvedValueOnce({
      profileId: "github-main",
      name: "GitHub",
      driverId: "connector-http-rest",
      isActive: true,
      rateLimitPerSec: 10,
      maxRetries: 3,
      integrationProvider: "github",
    });
    const onSuccess = vi.fn();
    renderForm({ onSuccess });
    fillRequiredFields();

    fireEvent.click(screen.getByRole("button", { name: /Connect with GitHub/i }));
    await waitFor(() => expect(openUrlMock).toHaveBeenCalled());

    capturedCallback?.({ code: "code-1", state: "s1" });

    await waitFor(() =>
      expect(completeGitHubOAuthMock).toHaveBeenCalledWith("github-main", "code-1", "s1"),
    );
    await waitFor(() =>
      expect(registerIntegrationConnectorProfileMock).toHaveBeenCalledWith(
        "github-main",
        "GitHub",
        "connector-http-rest",
        "integration-oauth:abc123",
        "github",
        { base_url: "https://api.github.com" },
      ),
    );
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
  });

  it("ignores a deep-link callback whose state does not match the pending flow", async () => {
    beginGitHubOAuthMock.mockResolvedValueOnce({
      ok: true,
      authorizationUrl: "https://github.com/login/oauth/authorize?state=s1",
      state: "s1",
    });
    renderForm();
    fillRequiredFields();

    fireEvent.click(screen.getByRole("button", { name: /Connect with GitHub/i }));
    await waitFor(() => expect(openUrlMock).toHaveBeenCalled());

    capturedCallback?.({ code: "code-1", state: "an-unrelated-state" });

    expect(completeGitHubOAuthMock).not.toHaveBeenCalled();
  });

  it("shows the complete failure message without registering a profile", async () => {
    beginGitHubOAuthMock.mockResolvedValueOnce({
      ok: true,
      authorizationUrl: "https://github.com/login/oauth/authorize?state=s1",
      state: "s1",
    });
    completeGitHubOAuthMock.mockResolvedValueOnce({ ok: false, message: "This authorization attempt has expired." });
    renderForm();
    fillRequiredFields();

    fireEvent.click(screen.getByRole("button", { name: /Connect with GitHub/i }));
    await waitFor(() => expect(openUrlMock).toHaveBeenCalled());

    capturedCallback?.({ code: "code-1", state: "s1" });

    expect(await screen.findByText("This authorization attempt has expired.")).toBeInTheDocument();
    expect(registerIntegrationConnectorProfileMock).not.toHaveBeenCalled();
  });
});
