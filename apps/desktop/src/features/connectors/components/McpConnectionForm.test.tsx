import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { testMcpConnectionMock, registerConnectorProfileMock } = vi.hoisted(() => ({
  testMcpConnectionMock: vi.fn(),
  registerConnectorProfileMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    testMcpConnection: testMcpConnectionMock,
    registerConnectorProfile: registerConnectorProfileMock,
  };
});

import { McpConnectionForm } from "./McpConnectionForm";

function renderForm(props: Partial<Parameters<typeof McpConnectionForm>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <McpConnectionForm onSuccess={props.onSuccess ?? vi.fn()} onCancel={props.onCancel ?? vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("McpConnectionForm", () => {
  beforeEach(() => {
    testMcpConnectionMock.mockReset();
    registerConnectorProfileMock.mockReset();
  });

  it("renders all required input fields and actions", () => {
    renderForm();

    expect(screen.getByLabelText(/Connection ID/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Display Name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Streamable HTTP Endpoint URL/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Bearer Token/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Test Connection/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Connect MCP Server/i })).toBeInTheDocument();
  });

  it("validates that non-HTTPS endpoint URLs are rejected", async () => {
    renderForm();

    fireEvent.change(screen.getByLabelText(/Connection ID/i), { target: { value: "insecure-mcp" } });
    fireEvent.change(screen.getByLabelText(/Display Name/i), { target: { value: "Insecure MCP" } });
    const urlInput = screen.getByLabelText(/Streamable HTTP Endpoint URL/i);
    fireEvent.change(urlInput, { target: { value: "http://insecure.example.com/mcp" } });

    const submitBtn = screen.getByRole("button", { name: /Connect MCP Server/i });
    fireEvent.click(submitBtn);

    expect(await screen.findByText(/MCP endpoint URL must start with 'https:\/\/'/i)).toBeInTheDocument();
  });

  it("invokes test connection API on Test Connection click", async () => {
    testMcpConnectionMock.mockResolvedValueOnce(true);
    renderForm();

    const urlInput = screen.getByLabelText(/Streamable HTTP Endpoint URL/i);
    fireEvent.change(urlInput, { target: { value: "https://secure.example.com/mcp" } });

    const testBtn = screen.getByRole("button", { name: /Test Connection/i });
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(testMcpConnectionMock).toHaveBeenCalledWith("https://secure.example.com/mcp", undefined);
      expect(screen.getByText(/Verified/i)).toBeInTheDocument();
    });
  });

  it("submits the connector profile payload with connector-mcp driver ID", async () => {
    registerConnectorProfileMock.mockResolvedValueOnce({
      profileId: "corp-mcp-1",
      name: "Corp Analytics MCP",
      driverId: "connector-mcp",
      isActive: true,
      rateLimitPerSec: 10,
      maxRetries: 3,
    });
    const onSuccess = vi.fn();
    renderForm({ onSuccess });

    fireEvent.change(screen.getByLabelText(/Connection ID/i), { target: { value: "corp-mcp-1" } });
    fireEvent.change(screen.getByLabelText(/Display Name/i), { target: { value: "Corp Analytics MCP" } });
    fireEvent.change(screen.getByLabelText(/Streamable HTTP Endpoint URL/i), {
      target: { value: "https://mcp.corp.example.com" },
    });
    fireEvent.change(screen.getByLabelText(/Bearer Token/i), {
      target: { value: "secret://tenants/alpha/key" },
    });

    fireEvent.click(screen.getByRole("button", { name: /Connect MCP Server/i }));

    await waitFor(() => {
      expect(registerConnectorProfileMock).toHaveBeenCalledWith({
        profileId: "corp-mcp-1",
        name: "Corp Analytics MCP",
        driverId: "connector-mcp",
        credential: "secret://tenants/alpha/key",
        options: {
          endpoint_url: "https://mcp.corp.example.com",
        },
      });
      expect(onSuccess).toHaveBeenCalled();
    });
  });
});
