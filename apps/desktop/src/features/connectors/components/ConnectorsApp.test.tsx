import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listConnectorDriversMock } = vi.hoisted(() => ({ listConnectorDriversMock: vi.fn() }));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, listConnectorDrivers: listConnectorDriversMock };
});

import { ConnectorAccessDeniedError, ConnectorRequestError } from "../api";
import type { ConnectorDriver } from "../types";
import { ConnectorsApp } from "./ConnectorsApp";

// Each `it` queues its own `mockResolvedValueOnce`/`mockRejectedValueOnce`
// values and asserts on call counts — without a full reset, unconsumed
// queued values and call counts from a prior test leak into the next.
beforeEach(() => {
  listConnectorDriversMock.mockReset();
});

function renderConnectorsApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ConnectorsApp />
    </QueryClientProvider>,
  );
}

function makeDriver(overrides: Partial<ConnectorDriver> = {}): ConnectorDriver {
  return {
    driverId: "connector-dummy",
    displayName: "Reference Dummy Connector Driver",
    vendor: "KORTEX",
    author: "KORTEX Core Team",
    version: "1.0.0",
    description: "Reference dummy connector driver plugin.",
    supportedActions: ["SEND", "FETCH"],
    supportedCapabilities: ["SEND", "FETCH"],
    isSandboxed: true,
    homepage: null,
    license: "MIT",
    ...overrides,
  };
}

describe("ConnectorsApp", () => {
  it("shows a loading state while the request is in flight", () => {
    listConnectorDriversMock.mockReturnValueOnce(new Promise<ConnectorDriver[]>(() => {}));

    renderConnectorsApp();

    expect(screen.getByRole("status", { name: /loading connector registry/i })).toBeInTheDocument();
  });

  it("shows the empty-registry message when no drivers are registered", async () => {
    listConnectorDriversMock.mockResolvedValueOnce([]);

    renderConnectorsApp();

    expect(await screen.findByText("No connectors are currently registered.")).toBeInTheDocument();
  });

  it("renders real registry data for a populated registry, identifying each driver", async () => {
    listConnectorDriversMock.mockResolvedValueOnce([
      makeDriver(),
      makeDriver({ driverId: "connector-http", displayName: "HTTP REST Connector", version: "2.0.0" }),
    ]);

    renderConnectorsApp();

    expect(await screen.findByText("Reference Dummy Connector Driver")).toBeInTheDocument();
    expect(screen.getByText("HTTP REST Connector")).toBeInTheDocument();
    expect(screen.getAllByTestId("connector-driver-card")).toHaveLength(2);
  });

  it("never renders a credential/secret/token field, even if present on a driver object", async () => {
    listConnectorDriversMock.mockResolvedValueOnce([
      { ...makeDriver(), secretHandle: "sh_should_never_render", accessToken: "should_never_render" },
    ]);

    renderConnectorsApp();

    await screen.findByText("Reference Dummy Connector Driver");
    expect(screen.queryByText(/should_never_render/i)).not.toBeInTheDocument();
  });

  it("shows an access-denied state — not a session-expired claim — on PERMISSION_DENIED", async () => {
    listConnectorDriversMock.mockRejectedValueOnce(new ConnectorAccessDeniedError("Missing permission: connector:read"));

    renderConnectorsApp();

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(
      screen.getByText("You do not have permission to view the connector registry."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/session expired/i)).not.toBeInTheDocument();
  });

  it("shows a generic, recoverable error state with a retry action on any other failure", async () => {
    // A non-access-denied failure gets one automatic retry (`useConnectors`'s
    // own `retry` option, mirroring the app's global `retry: 1` default) —
    // persistently rejecting and extending the timeout accounts for that
    // real retry delay rather than racing it.
    listConnectorDriversMock.mockRejectedValue(new ConnectorRequestError("backend unreachable"));

    renderConnectorsApp();

    expect(
      await screen.findByText("Something went wrong loading the connector registry.", undefined, {
        timeout: 3000,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  }, 8000);

  it("retries the request when Retry is clicked", async () => {
    listConnectorDriversMock.mockRejectedValue(new ConnectorRequestError("backend unreachable"));

    renderConnectorsApp();

    const retryButton = await screen.findByRole("button", { name: "Retry" }, { timeout: 3000 });
    listConnectorDriversMock.mockReset();
    listConnectorDriversMock.mockResolvedValueOnce([makeDriver()]);
    fireEvent.click(retryButton);

    expect(await screen.findByText("Reference Dummy Connector Driver")).toBeInTheDocument();
  }, 8000);

  it("refreshes populated data when Refresh is clicked", async () => {
    listConnectorDriversMock.mockResolvedValueOnce([makeDriver()]);
    listConnectorDriversMock.mockResolvedValueOnce([
      makeDriver(),
      makeDriver({ driverId: "connector-http", displayName: "HTTP REST Connector" }),
    ]);

    renderConnectorsApp();

    await screen.findByText("Reference Dummy Connector Driver");
    const refreshButton = screen.getByRole("button", { name: "Refresh" });
    fireEvent.click(refreshButton);

    expect(await screen.findByText("HTTP REST Connector")).toBeInTheDocument();
    expect(listConnectorDriversMock).toHaveBeenCalledTimes(2);
  });
});
