import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listAiProvidersMock, listAiModelsMock, getConversationHistoryMock } = vi.hoisted(() => ({
  listAiProvidersMock: vi.fn(),
  listAiModelsMock: vi.fn(),
  getConversationHistoryMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, listAiProviders: listAiProvidersMock, listAiModels: listAiModelsMock };
});

vi.mock("../chat-api", async () => {
  const actual = await vi.importActual<typeof import("../chat-api")>("../chat-api");
  return { ...actual, getConversationHistory: getConversationHistoryMock };
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
  getConversationHistoryMock.mockReset();
  window.localStorage.clear();
});

function renderAiStudioApp(initialEntries: string[] = ["/ai-studio"]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <AiStudioApp />
      </MemoryRouter>
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

  it("states where credentials live and who calls the provider", async () => {
    // Replaces a B1-era assertion that provider configuration was "not
    // available yet" -- B4 makes that copy false, so the test asserts the
    // new claim rather than being deleted. What it checks is the part that
    // must stay true no matter how the wording evolves: keys are held by
    // the Security Engine, and the backend (never this window) calls the
    // vendor.
    listAiProvidersMock.mockResolvedValueOnce([]);
    listAiModelsMock.mockResolvedValueOnce([]);

    renderAiStudioApp();

    expect(
      await screen.findByText(/API keys are held by the Security Engine and are never returned to this app/),
    ).toBeInTheDocument();
    expect(screen.getByText(/made by the KORTEX backend, never from this window/)).toBeInTheDocument();
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

  it("M7.2: switches to the Chat tab and mounts a real conversational surface", async () => {
    listAiProvidersMock.mockResolvedValueOnce([]);
    listAiModelsMock.mockResolvedValueOnce([]);
    getConversationHistoryMock.mockResolvedValueOnce([]);

    renderAiStudioApp();

    fireEvent.click(screen.getByRole("tab", { name: "Chat" }));

    expect(await screen.findByText("No messages yet. Say hello to get started.")).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeInTheDocument();
  });

  // Proves the receiving end of Mini Chat's Workflow Builder entry point
  // (`MiniChatHost.test.tsx`'s own tests prove the *call*; this proves the
  // *destination* actually honors it): a `?tab=workflowBuilder` deep link
  // lands directly on the Workflow Builder tab, with no extra click needed,
  // mirroring `WorkflowApp.tsx`'s own `?tab=approvals` precedent exactly.
  it("honors a ?tab=workflowBuilder deep link by landing directly on the Workflow Builder tab", async () => {
    listAiProvidersMock.mockResolvedValueOnce([]);
    listAiModelsMock.mockResolvedValueOnce([]);

    renderAiStudioApp(["/ai-studio?tab=workflowBuilder"]);

    expect(screen.getByRole("tab", { name: "Workflow Builder", selected: true })).toBeInTheDocument();
    expect(await screen.findByTestId("ai-workflow-builder-panel")).toBeInTheDocument();
  });
});
