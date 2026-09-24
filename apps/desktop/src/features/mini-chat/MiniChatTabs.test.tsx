import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  getConversationHistoryMock,
  listAgentTasksMock,
  projectCapabilitiesMock,
  listConnectorProfilesMock,
  useAuthMock,
} = vi.hoisted(() => ({
  getConversationHistoryMock: vi.fn(),
  listAgentTasksMock: vi.fn(),
  projectCapabilitiesMock: vi.fn(),
  listConnectorProfilesMock: vi.fn(),
  useAuthMock: vi.fn(),
}));

vi.mock("@/auth/AuthProvider", async () => {
  const actual = await vi.importActual<typeof import("@/auth/AuthProvider")>("@/auth/AuthProvider");
  return { ...actual, useAuth: useAuthMock };
});

vi.mock("@/features/ai-studio/chat-api", async () => {
  const actual = await vi.importActual<typeof import("@/features/ai-studio/chat-api")>(
    "@/features/ai-studio/chat-api",
  );
  return {
    ...actual,
    getConversationHistory: getConversationHistoryMock,
    listAgentTasks: listAgentTasksMock,
  };
});

vi.mock("@/features/workflow/api", async () => {
  const actual = await vi.importActual<typeof import("@/features/workflow/api")>(
    "@/features/workflow/api",
  );
  return {
    ...actual,
    projectCapabilities: projectCapabilitiesMock,
  };
});

vi.mock("@/features/connectors/api", async () => {
  const actual = await vi.importActual<typeof import("@/features/connectors/api")>(
    "@/features/connectors/api",
  );
  return {
    ...actual,
    listConnectorProfiles: listConnectorProfilesMock,
  };
});

vi.mock("@/navigation/navigationBridge", () => ({
  useApplicationNavigation: () => ({
    state: { applicationId: "dashboard", route: "/" },
    navigateToApplication: vi.fn(),
  }),
}));

import { MiniChatHost } from "./MiniChatHost";

describe("MiniChatHost Copilot Tabs", () => {
  beforeEach(() => {
    useAuthMock.mockReturnValue({
      state: {
        status: "AUTHENTICATED",
        identity: { tenantId: "tenant-test", principalId: "user-test", principalType: "USER", roles: [] },
      },
      login: vi.fn(),
      logout: vi.fn(),
      bootstrap: vi.fn(),
      retryConnection: vi.fn(),
      reportIpcResult: vi.fn(),
    });

    getConversationHistoryMock.mockResolvedValue([]);
    listAgentTasksMock.mockResolvedValue([
      {
        taskId: "task-1",
        tenantId: "tenant-test",
        userId: "user-test",
        conversationId: "conv-1",
        goal: "Analyze database health",
        agentRole: "diagnostics",
        status: "RUNNING",
        currentStep: 2,
        maxSteps: 10,
        totalTokens: 420,
        createdAt: "2026-03-01T10:00:00Z",
        updatedAt: "2026-03-01T10:01:00Z",
      },
    ]);
    projectCapabilitiesMock.mockResolvedValue([
      {
        name: "kortex.finance.invoice.get",
        description: "Retrieve invoice details",
        provider: "finance",
        parametersSchema: { type: "object", properties: {} },
        isReadOnly: true,
        isIdempotent: true,
        requiredPermissions: ["finance:read"],
      },
    ]);
    listConnectorProfilesMock.mockResolvedValue([
      {
        profileId: "mcp-server-1",
        name: "Postgres MCP Service",
        driverId: "connector-mcp",
        isActive: true,
        rateLimitPerSec: 10,
        maxRetries: 3,
        integrationProvider: null,
      },
    ]);
  });

  function renderCopilot() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <MiniChatHost />
      </QueryClientProvider>,
    );
  }

  it("switches to Agents tab and displays running agent tasks", async () => {
    renderCopilot();

    // Open Copilot
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));

    // Switch to Agents tab
    const agentsTabBtn = screen.getByRole("tab", { name: "agents" });
    fireEvent.click(agentsTabBtn);

    expect(await screen.findByText("Analyze database health")).toBeInTheDocument();
    expect(screen.getByText("Role: diagnostics")).toBeInTheDocument();
    expect(screen.getByText("Step 2 of 10")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("switches to Tools tab and displays projected capabilities", async () => {
    renderCopilot();

    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));

    const toolsTabBtn = screen.getByRole("tab", { name: "tools" });
    fireEvent.click(toolsTabBtn);

    expect(await screen.findByText("kortex.finance.invoice.get")).toBeInTheDocument();
    expect(screen.getByText("Retrieve invoice details")).toBeInTheDocument();
    expect(screen.getByText("Read Only")).toBeInTheDocument();
  });

  it("switches to MCP tab and displays connected MCP servers", async () => {
    renderCopilot();

    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));

    const mcpTabBtn = screen.getByRole("tab", { name: "mcp" });
    fireEvent.click(mcpTabBtn);

    expect(await screen.findByText("Postgres MCP Service")).toBeInTheDocument();
    expect(screen.getByText("Online")).toBeInTheDocument();
    expect(screen.getByText("Rate limit: 10/s")).toBeInTheDocument();
  });
});
