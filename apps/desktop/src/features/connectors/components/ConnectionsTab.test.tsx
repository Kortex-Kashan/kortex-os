import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listConnectorProfilesMock, listConnectorDriversMock, registerConnectorProfileMock, deleteConnectorProfileMock } =
  vi.hoisted(() => ({
    listConnectorProfilesMock: vi.fn(),
    listConnectorDriversMock: vi.fn(),
    registerConnectorProfileMock: vi.fn(),
    deleteConnectorProfileMock: vi.fn(),
  }));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    listConnectorProfiles: listConnectorProfilesMock,
    listConnectorDrivers: listConnectorDriversMock,
    registerConnectorProfile: registerConnectorProfileMock,
    deleteConnectorProfile: deleteConnectorProfileMock,
  };
});

import { ConnectorAccessDeniedError } from "../api";
import type { ConnectorDriver, ConnectorProfile } from "../types";
import { ConnectionsTab } from "./ConnectionsTab";

beforeEach(() => {
  listConnectorProfilesMock.mockReset();
  listConnectorDriversMock.mockReset();
  registerConnectorProfileMock.mockReset();
  deleteConnectorProfileMock.mockReset();
  listConnectorDriversMock.mockResolvedValue([makeDriver()]);
});

function renderConnectionsTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ConnectionsTab />
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
    supportedActions: ["FETCH"],
    supportedCapabilities: ["FETCH"],
    isSandboxed: true,
    homepage: null,
    license: "MIT",
    ...overrides,
  };
}

function makeProfile(overrides: Partial<ConnectorProfile> = {}): ConnectorProfile {
  return {
    profileId: "billing-api",
    name: "Billing API",
    driverId: "connector-dummy",
    isActive: true,
    rateLimitPerSec: 10,
    maxRetries: 3,
    ...overrides,
  };
}

describe("ConnectionsTab", () => {
  it("shows a loading state while the request is in flight", () => {
    listConnectorProfilesMock.mockReturnValueOnce(new Promise<ConnectorProfile[]>(() => {}));

    renderConnectionsTab();

    expect(screen.getByRole("status", { name: /loading connections/i })).toBeInTheDocument();
  });

  it("shows the empty-connections message when none exist", async () => {
    listConnectorProfilesMock.mockResolvedValueOnce([]);

    renderConnectionsTab();

    expect(await screen.findByText("No connections configured.")).toBeInTheDocument();
  });

  it("renders each connection without ever showing a credential value", async () => {
    listConnectorProfilesMock.mockResolvedValueOnce([
      { ...makeProfile(), secretHandle: "sh_should_never_render", credential: "should_never_render" },
    ]);

    renderConnectionsTab();

    expect(await screen.findByText("Billing API")).toBeInTheDocument();
    expect(screen.getByText(/connector-dummy · billing-api/)).toBeInTheDocument();
    expect(screen.queryByText(/should_never_render/i)).not.toBeInTheDocument();
  });

  it("shows an access-denied state on PERMISSION_DENIED", async () => {
    listConnectorProfilesMock.mockRejectedValueOnce(new ConnectorAccessDeniedError("Missing permission: connector:read"));

    renderConnectionsTab();

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(screen.getByText("You do not have permission to view connections.")).toBeInTheDocument();
  });

  it("creates a connection and never re-displays the entered credential", async () => {
    listConnectorProfilesMock.mockResolvedValueOnce([]);
    listConnectorProfilesMock.mockResolvedValueOnce([makeProfile()]);
    registerConnectorProfileMock.mockResolvedValueOnce(makeProfile());

    renderConnectionsTab();

    await screen.findByText("No connections configured.");
    fireEvent.click(screen.getByRole("button", { name: "New Connection" }));

    fireEvent.change(screen.getByLabelText("Connection ID"), { target: { value: "billing-api" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Billing API" } });
    fireEvent.change(screen.getByLabelText("Credential (optional)"), { target: { value: "super-secret-key" } });
    // With exactly one driver installed, the form defaults to it
    // automatically — no picker interaction needed to exercise submission.
    await waitFor(
      () => expect(screen.getByRole("combobox")).toHaveTextContent("Reference Dummy Connector Driver"),
      { timeout: 3000 },
    );

    fireEvent.click(screen.getByRole("button", { name: "Create Connection" }));

    await waitFor(() => expect(registerConnectorProfileMock).toHaveBeenCalledTimes(1));
    expect(registerConnectorProfileMock).toHaveBeenCalledWith(
      expect.objectContaining({
        profileId: "billing-api",
        name: "Billing API",
        driverId: "connector-dummy",
        credential: "super-secret-key",
      }),
    );

    expect(await screen.findByText("Billing API")).toBeInTheDocument();
    // The password field itself is the only place the value was ever typed;
    // it must never reappear as static, readable text anywhere else.
    expect(screen.queryByDisplayValue("super-secret-key")).not.toBeInTheDocument();
  });

  it("deletes a connection after confirming, and not before", async () => {
    listConnectorProfilesMock.mockResolvedValueOnce([makeProfile()]);
    listConnectorProfilesMock.mockResolvedValueOnce([]);
    deleteConnectorProfileMock.mockResolvedValueOnce(undefined);

    renderConnectionsTab();

    await screen.findByText("Billing API");
    fireEvent.click(screen.getByRole("button", { name: "Delete connection Billing API" }));

    expect(deleteConnectorProfileMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Confirm Delete" }));

    await waitFor(() => expect(deleteConnectorProfileMock).toHaveBeenCalledWith("billing-api"));
    await waitFor(() => expect(screen.queryByText("Billing API")).not.toBeInTheDocument());
  });
});
