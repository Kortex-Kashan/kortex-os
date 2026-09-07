import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  listAiProviderConfigsMock,
  configureAiProviderMock,
  testAiProviderConnectionMock,
  removeAiProviderConfigMock,
} = vi.hoisted(() => ({
  listAiProviderConfigsMock: vi.fn(),
  configureAiProviderMock: vi.fn(),
  testAiProviderConnectionMock: vi.fn(),
  removeAiProviderConfigMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    listAiProviderConfigs: listAiProviderConfigsMock,
    configureAiProvider: configureAiProviderMock,
    testAiProviderConnection: testAiProviderConnectionMock,
    removeAiProviderConfig: removeAiProviderConfigMock,
  };
});

import { AiStudioAccessDeniedError } from "../api";
import type { AiProvider, AiProviderConfig } from "../types";
import { ProviderConfigCard } from "./ProviderConfigCard";

beforeEach(() => {
  listAiProviderConfigsMock.mockReset();
  configureAiProviderMock.mockReset();
  testAiProviderConnectionMock.mockReset();
  removeAiProviderConfigMock.mockReset();
  listAiProviderConfigsMock.mockResolvedValue([]);
});

function cloudProvider(overrides: Partial<AiProvider> = {}): AiProvider {
  return {
    providerId: "openai",
    displayName: "OpenAI",
    vendor: "OpenAI",
    endpointType: "cloud",
    url: null,
    credentialRequirement: "api_key",
    supportedModels: ["gpt-4o", "gpt-4o-mini"],
    ...overrides,
  };
}

function localProvider(): AiProvider {
  return {
    providerId: "ollama-llama3",
    displayName: "Local Ollama",
    vendor: "Ollama",
    endpointType: "local_host",
    url: "http://localhost:11434",
    credentialRequirement: "none",
    supportedModels: ["llama3"],
  };
}

function config(overrides: Partial<AiProviderConfig> = {}): AiProviderConfig {
  return {
    tenantId: "acme",
    providerId: "openai",
    enabled: true,
    hasCredential: true,
    defaultModel: null,
    createdAt: null,
    updatedAt: null,
    ...overrides,
  };
}

function renderCard(provider: AiProvider, providerConfig?: AiProviderConfig) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ul>
        <ProviderConfigCard provider={provider} config={providerConfig} />
      </ul>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Presentation of configuration state
// ---------------------------------------------------------------------------

describe("ProviderConfigCard presentation", () => {
  it("shows an unconfigured cloud provider as not configured, offering Configure", () => {
    renderCard(cloudProvider(), undefined);

    expect(screen.getByTestId("provider-config-state")).toHaveTextContent("Not configured");
    expect(screen.getByRole("button", { name: "Configure" })).toBeInTheDocument();
  });

  it("shows a configured cloud provider as configured, offering Replace key", () => {
    renderCard(cloudProvider(), config());

    expect(screen.getByTestId("provider-config-state")).toHaveTextContent("Configured");
    expect(screen.getByRole("button", { name: "Replace key" })).toBeInTheDocument();
  });

  it("surfaces a disabled configuration as disabled", () => {
    renderCard(cloudProvider(), config({ enabled: false }));

    expect(screen.getByText("Disabled")).toBeInTheDocument();
  });

  it("shows the stored default model", () => {
    renderCard(cloudProvider(), config({ defaultModel: "gpt-4o" }));

    expect(screen.getByText("Default model: gpt-4o")).toBeInTheDocument();
  });

  it("cannot test a connection before a credential is stored", () => {
    renderCard(cloudProvider(), undefined);

    expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
  });

  it("offers removal only once a configuration exists", () => {
    renderCard(cloudProvider(), undefined);
    expect(screen.queryByRole("button", { name: "Remove configuration" })).not.toBeInTheDocument();

    renderCard(cloudProvider(), config());
    expect(screen.getByRole("button", { name: "Remove configuration" })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Ollama / local providers
// ---------------------------------------------------------------------------

describe("ProviderConfigCard for a local provider", () => {
  it("offers no credential form, and says where its settings actually live", () => {
    // The gate is `credentialRequirement === "none"`, not a hardcoded
    // "ollama" id. A tenant configuration row for Ollama would be inert:
    // its URL and default model come from operator-level SystemSettings and
    // `OllamaProvider` holds no credential resolver.
    renderCard(localProvider(), undefined);

    expect(screen.getByTestId("provider-config-state")).toHaveTextContent("Local");
    expect(screen.getByText(/needs no API key/)).toBeInTheDocument();
    expect(screen.getByText(/set by\s+your administrator in system settings/)).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Configure" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test connection" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("API key")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// The configure dialog
// ---------------------------------------------------------------------------

describe("configure dialog", () => {
  it("masks the API key input", () => {
    renderCard(cloudProvider(), undefined);
    fireEvent.click(screen.getByRole("button", { name: "Configure" }));

    expect(screen.getByLabelText("API key")).toHaveAttribute("type", "password");
  });

  it("cannot be submitted empty or whitespace-only", () => {
    renderCard(cloudProvider(), undefined);
    fireEvent.click(screen.getByRole("button", { name: "Configure" }));

    expect(screen.getByRole("button", { name: "Save key" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "Save key" })).toBeDisabled();
  });

  it("submits the trimmed key through configureAiProvider and closes on success", async () => {
    configureAiProviderMock.mockResolvedValueOnce(config());
    renderCard(cloudProvider(), undefined);

    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "  sk-live  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    await waitFor(() => {
      expect(configureAiProviderMock).toHaveBeenCalledWith({
        providerId: "openai",
        apiKey: "sk-live",
        enabled: true,
      });
    });
    await waitFor(() => {
      expect(screen.queryByLabelText("API key")).not.toBeInTheDocument();
    });
  });

  it("does not retain the entered key after the dialog closes and reopens", async () => {
    renderCard(cloudProvider(), undefined);

    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-typed" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByLabelText("API key")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    expect(screen.getByLabelText("API key")).toHaveValue("");
  });

  it("tells the user a replacement cannot display the existing key", () => {
    renderCard(cloudProvider(), config());
    fireEvent.click(screen.getByRole("button", { name: "Replace key" }));

    expect(screen.getByText(/existing key cannot be displayed/)).toBeInTheDocument();
  });

  it("reports a save failure inside the dialog and stays open", async () => {
    configureAiProviderMock.mockRejectedValueOnce(new Error("upstream rejected the key"));
    renderCard(cloudProvider(), undefined);

    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-bad" } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    expect(await screen.findByText("upstream rejected the key")).toBeInTheDocument();
    expect(screen.getByLabelText("API key")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Connection testing
// ---------------------------------------------------------------------------

describe("connection testing", () => {
  it("reports success and how many models were discovered", async () => {
    testAiProviderConnectionMock.mockResolvedValueOnce({
      providerId: "openai",
      connected: true,
      detail: null,
      models: [
        { modelId: "gpt-4o", providerId: "openai", providerDisplayName: "OpenAI" },
        { modelId: "o3", providerId: "openai", providerDisplayName: "OpenAI" },
      ],
    });
    renderCard(cloudProvider(), config());

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByTestId("provider-feedback")).toHaveTextContent(
      "Connection succeeded — 2 models available.",
    );
    expect(testAiProviderConnectionMock).toHaveBeenCalledWith("openai");
  });

  it("reports a failed connection with the backend's normalized detail", async () => {
    testAiProviderConnectionMock.mockResolvedValueOnce({
      providerId: "openai",
      connected: false,
      detail: "401 invalid_api_key",
      models: [],
    });
    renderCard(cloudProvider(), config());

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    const feedback = await screen.findByTestId("provider-feedback");
    expect(feedback).toHaveTextContent("Connection failed. 401 invalid_api_key");
    expect(feedback).toHaveAttribute("role", "alert");
  });

  it("treats a discovery failure on a valid credential as success with a note", async () => {
    // `connected: true` with a `detail` and no models is the backend's
    // documented "credential is valid, second round trip failed" shape.
    // Calling it a failure would tell the user a working key is broken.
    testAiProviderConnectionMock.mockResolvedValueOnce({
      providerId: "openai",
      connected: true,
      detail: "429 rate limited",
      models: [],
    });
    renderCard(cloudProvider(), config());

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    const feedback = await screen.findByTestId("provider-feedback");
    expect(feedback).toHaveTextContent("Connection succeeded.");
    expect(feedback).toHaveTextContent("Models could not be listed: 429 rate limited");
    expect(feedback).toHaveAttribute("role", "status");
  });

  it("disables the action and shows progress while the test is in flight", async () => {
    testAiProviderConnectionMock.mockReturnValueOnce(new Promise(() => {}));
    renderCard(cloudProvider(), config());

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByRole("button", { name: "Testing…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Replace key" })).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// Model selection
// ---------------------------------------------------------------------------

describe("default-model selection", () => {
  it("offers the provider's advertised models before any test has run", () => {
    renderCard(cloudProvider(), config());

    expect(screen.getByLabelText(/^Default model$/)).toBeInTheDocument();
  });

  it("prefers live discovered models once a test has succeeded", async () => {
    testAiProviderConnectionMock.mockResolvedValueOnce({
      providerId: "openai",
      connected: true,
      detail: null,
      models: [{ modelId: "gpt-5-preview", providerId: "openai", providerDisplayName: "OpenAI" }],
    });
    renderCard(cloudProvider(), config());

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    // The label announces that the list is live, and the discovered model
    // is selectable even though it is absent from `supportedModels`.
    const trigger = await screen.findByLabelText(/from this provider, just now/);
    fireEvent.click(trigger);
    expect(await screen.findByRole("option", { name: "gpt-5-preview" })).toBeInTheDocument();
  });

  it("persists a chosen default model through configureAiProvider without resending a key", async () => {
    configureAiProviderMock.mockResolvedValueOnce(config({ defaultModel: "gpt-4o-mini" }));
    renderCard(cloudProvider(), config());

    fireEvent.click(screen.getByLabelText(/^Default model$/));
    fireEvent.click(await screen.findByRole("option", { name: "gpt-4o-mini" }));

    await waitFor(() => {
      expect(configureAiProviderMock).toHaveBeenCalledWith({
        providerId: "openai",
        defaultModel: "gpt-4o-mini",
      });
    });
    // No credential is involved in changing a default model.
    expect(configureAiProviderMock.mock.calls[0][0]).not.toHaveProperty("apiKey");
  });

  it("offers no model selector until a credential is stored", () => {
    renderCard(cloudProvider(), undefined);

    expect(screen.queryByLabelText(/Default model/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Removal and permission denial
// ---------------------------------------------------------------------------

describe("removal and permissions", () => {
  it("removes the configuration and confirms inline", async () => {
    removeAiProviderConfigMock.mockResolvedValueOnce(true);
    renderCard(cloudProvider(), config());

    fireEvent.click(screen.getByRole("button", { name: "Remove configuration" }));

    expect(await screen.findByTestId("provider-feedback")).toHaveTextContent("Configuration removed.");
    expect(removeAiProviderConfigMock).toHaveBeenCalledWith("openai");
  });

  it("explains a permission denial in terms of the missing grant", async () => {
    testAiProviderConnectionMock.mockRejectedValueOnce(
      new AiStudioAccessDeniedError("Missing permission: ai:manage"),
    );
    renderCard(cloudProvider(), config());

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    const feedback = await screen.findByTestId("provider-feedback");
    expect(feedback).toHaveTextContent("You do not have permission to manage AI providers.");
    expect(feedback).toHaveTextContent("Missing permission: ai:manage");
  });
});
