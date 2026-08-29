import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listAiProvidersMock, listAiModelsMock } = vi.hoisted(() => ({
  listAiProvidersMock: vi.fn(),
  listAiModelsMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, listAiProviders: listAiProvidersMock, listAiModels: listAiModelsMock };
});

// Stub useAuth so AiStudioApp (which reads tenantId for the Governance tab) works
// without a real AuthProvider in these registry-focused tests.
vi.mock("@/auth/AuthProvider", () => ({
  useAuth: () => ({
    state: { status: "AUTHENTICATED", identity: { tenantId: "acme", principalId: "alice", principalType: "USER", roles: [] } },
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

import { AiStudioAccessDeniedError, AiStudioRequestError } from "../api";
import type { AiModel, AiProvider } from "../types";
import { AiStudioApp } from "./AiStudioApp";

beforeEach(() => {
  listAiProvidersMock.mockReset();
  listAiModelsMock.mockReset();
});

function renderAiStudioApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AiStudioApp />
    </QueryClientProvider>,
  );
}

function makeProvider(overrides: Partial<AiProvider> = {}): AiProvider {
  return {
    providerId: "ollama-local",
    displayName: "Local Ollama",
    vendor: "Ollama",
    endpointType: "local_host",
    url: "http://localhost:11434",
    credentialRequirement: "none",
    supportedModels: ["llama3"],
    ...overrides,
  };
}

function makeModel(overrides: Partial<AiModel> = {}): AiModel {
  return {
    modelId: "llama3",
    providerId: "ollama-local",
    providerDisplayName: "Local Ollama",
    ...overrides,
  };
}

describe("AiStudioApp", () => {
  it("shows loading state for both sections while requests are in flight", () => {
    listAiProvidersMock.mockReturnValueOnce(new Promise<AiProvider[]>(() => {}));
    listAiModelsMock.mockReturnValueOnce(new Promise<AiModel[]>(() => {}));

    renderAiStudioApp();

    expect(screen.getByRole("status", { name: /loading ai providers/i })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: /loading ai models/i })).toBeInTheDocument();
  });

  it("shows empty-registry messages when neither providers nor models exist", async () => {
    listAiProvidersMock.mockResolvedValueOnce([]);
    listAiModelsMock.mockResolvedValueOnce([]);

    renderAiStudioApp();

    expect(await screen.findByText("No AI providers are currently registered.")).toBeInTheDocument();
    expect(await screen.findByText("No AI models are currently available.")).toBeInTheDocument();
  });

  it("communicates that generation/orchestration/configuration are not available", async () => {
    listAiProvidersMock.mockResolvedValueOnce([]);
    listAiModelsMock.mockResolvedValueOnce([]);

    renderAiStudioApp();

    expect(
      await screen.findByText(/Generation, agent orchestration, and provider configuration are not available yet\./),
    ).toBeInTheDocument();
  });

  it("renders real provider and model data, identifying each entry", async () => {
    listAiProvidersMock.mockResolvedValueOnce([makeProvider()]);
    listAiModelsMock.mockResolvedValueOnce([makeModel(), makeModel({ modelId: "mistral" })]);

    renderAiStudioApp();

    expect(await screen.findByText("Local Ollama")).toBeInTheDocument();
    expect(screen.getAllByTestId("ai-provider-card")).toHaveLength(1);
    expect(screen.getAllByTestId("ai-model-card")).toHaveLength(2);
  });

  it("never renders a secret_handle field, even if present on a provider object", async () => {
    listAiProvidersMock.mockResolvedValueOnce([
      { ...makeProvider(), secretHandle: "sh_should_never_render" },
    ]);
    listAiModelsMock.mockResolvedValueOnce([]);

    renderAiStudioApp();

    await screen.findByText("Local Ollama");
    expect(screen.queryByText(/should_never_render/i)).not.toBeInTheDocument();
  });

  it("shows an access-denied state for the providers section on PERMISSION_DENIED", async () => {
    listAiProvidersMock.mockRejectedValueOnce(new AiStudioAccessDeniedError("Missing permission: ai:read"));
    listAiModelsMock.mockResolvedValueOnce([]);

    renderAiStudioApp();

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(screen.getByText("You do not have permission to view this registry.")).toBeInTheDocument();
    expect(screen.queryByText(/session expired/i)).not.toBeInTheDocument();
    // Models section is unaffected by the providers section's failure.
    expect(await screen.findByText("No AI models are currently available.")).toBeInTheDocument();
  });

  it("shows a generic, recoverable error with retry for the models section on any other failure", async () => {
    listAiProvidersMock.mockResolvedValueOnce([]);
    listAiModelsMock.mockRejectedValue(new AiStudioRequestError("backend unreachable"));

    renderAiStudioApp();

    expect(
      await screen.findByText("Something went wrong loading this registry.", undefined, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  }, 8000);

  it("retries the models request when Retry is clicked", async () => {
    listAiProvidersMock.mockResolvedValueOnce([]);
    listAiModelsMock.mockRejectedValue(new AiStudioRequestError("backend unreachable"));

    renderAiStudioApp();

    const retryButton = await screen.findByRole("button", { name: "Retry" }, { timeout: 3000 });
    listAiModelsMock.mockReset();
    listAiModelsMock.mockResolvedValueOnce([makeModel()]);
    fireEvent.click(retryButton);

    expect(await screen.findByText("llama3")).toBeInTheDocument();
  }, 8000);

  it("refreshes the providers section independently when its Refresh is clicked", async () => {
    listAiProvidersMock.mockResolvedValueOnce([makeProvider()]);
    listAiProvidersMock.mockResolvedValueOnce([makeProvider({ displayName: "Updated Ollama" })]);
    listAiModelsMock.mockResolvedValue([]);

    renderAiStudioApp();

    await screen.findByText("Local Ollama");
    const providersHeading = screen.getByText("Providers");
    const providersSection = providersHeading.closest("section");
    if (!providersSection) {
      throw new Error("Providers section not found");
    }
    fireEvent.click(within(providersSection).getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText("Updated Ollama")).toBeInTheDocument();
    expect(listAiProvidersMock).toHaveBeenCalledTimes(2);
  });
});
