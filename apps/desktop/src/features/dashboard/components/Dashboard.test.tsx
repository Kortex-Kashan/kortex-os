import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

import { Dashboard } from "./Dashboard";

const HEALTHY_BODY = {
  kernel_state: "RUNNING",
  db_dialect: "sqlite",
  db_connected: true,
  system_health: {
    status: "healthy",
    engines: {
      configuration: { engine: "configuration", status: "healthy", environment: "development" },
      registry: { engine: "registry", status: "healthy", capabilities_count: 9 },
      storage: { engine: "storage", status: "RUNNING", healthy: true },
      security: { engine: "security", status: "RUNNING", healthy: true },
    },
  },
};

const DEGRADED_BODY = {
  kernel_state: "RUNNING",
  db_dialect: "sqlite",
  db_connected: true,
  system_health: {
    status: "degraded",
    engines: {
      registry: { engine: "registry", status: "healthy", capabilities_count: 4 },
      storage: { engine: "storage", status: "FAILED", healthy: false, error: "disk unavailable" },
    },
  },
};

function renderDashboard(client?: QueryClient) {
  const queryClient =
    client ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Dashboard />
    </QueryClientProvider>,
  );
}

describe("Dashboard", () => {
  it("shows a loading skeleton while the health request is unsettled", () => {
    invokeMock.mockReturnValueOnce(new Promise(() => {})); // never resolves
    renderDashboard();

    expect(screen.getByText(/loading system health/i)).toBeInTheDocument();
    expect(screen.queryByText("All systems operational")).not.toBeInTheDocument();
  });

  it("renders a real-shaped healthy response: overall status, stat tiles, and engine table", async () => {
    invokeMock.mockResolvedValueOnce({ ok: true, statusCode: 200, body: HEALTHY_BODY });
    renderDashboard();

    await waitFor(() => expect(screen.getByText("All systems operational")).toBeInTheDocument());

    expect(screen.getAllByText("RUNNING").length).toBeGreaterThan(0); // kernel tile + state-value engines
    expect(screen.getByText("sqlite")).toBeInTheDocument(); // database stat tile
    expect(screen.getByText("4")).toBeInTheDocument(); // engines count
    expect(screen.getByText("9")).toBeInTheDocument(); // capabilities count
    expect(screen.getByText(/storage/i)).toBeInTheDocument();
    expect(screen.getByText(/security/i)).toBeInTheDocument();
  });

  it("renders a degraded system honestly — never claims healthy when the backend says degraded", async () => {
    invokeMock.mockResolvedValueOnce({ ok: true, statusCode: 200, body: DEGRADED_BODY });
    renderDashboard();

    await waitFor(() => expect(screen.getByText("System degraded")).toBeInTheDocument());

    expect(screen.queryByText("All systems operational")).not.toBeInTheDocument();
    expect(screen.getByText("disk unavailable")).toBeInTheDocument();
  });

  it("renders per-engine status independently of which status convention the engine uses", async () => {
    invokeMock.mockResolvedValueOnce({ ok: true, statusCode: 200, body: HEALTHY_BODY });
    renderDashboard();

    await waitFor(() => expect(screen.getByText(/storage/i)).toBeInTheDocument());

    // storage/security report status "RUNNING" + healthy:true (state-value
    // convention); configuration/registry report status "healthy" literally.
    // Both conventions must render as healthy, proving the frontend mirrors
    // the backend's `_report_is_healthy` fix rather than only trusting the
    // literal string. Scoped to the table specifically since "RUNNING" also
    // appears in the Kernel stat tile.
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("healthy").length).toBe(2);
    expect(within(table).getAllByText("RUNNING").length).toBe(2);
    expect(within(table).queryByText("unhealthy")).not.toBeInTheDocument();
  });

  it("refresh button triggers a new health request", async () => {
    invokeMock.mockResolvedValue({ ok: true, statusCode: 200, body: HEALTHY_BODY });
    renderDashboard();
    await waitFor(() => expect(screen.getByText("All systems operational")).toBeInTheDocument());

    const callsBeforeRefresh = invokeMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => expect(invokeMock.mock.calls.length).toBeGreaterThan(callsBeforeRefresh));
  });

  it("shows a distinct, alertable error state when the backend is unreachable, with a working retry", async () => {
    invokeMock.mockResolvedValueOnce({ ok: false, error: "Backend unreachable: connection refused" });
    renderDashboard();

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/unreachable/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    invokeMock.mockResolvedValueOnce({ ok: true, statusCode: 200, body: HEALTHY_BODY });
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => expect(screen.getByText("All systems operational")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("never shows the error state mid-retry/backoff — loading persists until the query settles", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: 1, retryDelay: 0 } },
    });
    invokeMock
      .mockResolvedValueOnce({ ok: false, error: "Backend unreachable: transient blip" })
      .mockResolvedValueOnce({ ok: true, statusCode: 200, body: HEALTHY_BODY });

    renderDashboard(queryClient);

    // Immediately after the first (failing) attempt, the query is still
    // unsettled — this must render as loading, never as the error card.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await waitFor(() => expect(screen.getByText("All systems operational")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("handles an empty engine collection honestly instead of fabricating rows", async () => {
    invokeMock.mockResolvedValueOnce({
      ok: true,
      statusCode: 200,
      body: { ...HEALTHY_BODY, system_health: { status: "healthy", engines: {} } },
    });
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByText("No engines are currently registered.")).toBeInTheDocument(),
    );
  });
});
