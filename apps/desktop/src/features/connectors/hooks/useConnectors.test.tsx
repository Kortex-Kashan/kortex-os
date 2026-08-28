import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listConnectorDriversMock } = vi.hoisted(() => ({ listConnectorDriversMock: vi.fn() }));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, listConnectorDrivers: listConnectorDriversMock };
});

import { ConnectorAccessDeniedError } from "../api";
import { useConnectors } from "./useConnectors";

// See ConnectorsApp.test.tsx's identical beforeEach for why a full reset
// (not just clearing call history) is required between tests here.
beforeEach(() => {
  listConnectorDriversMock.mockReset();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useConnectors", () => {
  it("starts pending, then resolves to the driver list on success", async () => {
    listConnectorDriversMock.mockResolvedValueOnce([
      { driverId: "connector-dummy", displayName: "Dummy", vendor: "KORTEX" },
    ]);

    const { result } = renderHook(() => useConnectors(), { wrapper });

    expect(result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("resolves to an empty array for an empty registry", async () => {
    listConnectorDriversMock.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useConnectors(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  it("surfaces a thrown ConnectorAccessDeniedError as the query error without retrying", async () => {
    listConnectorDriversMock.mockRejectedValueOnce(new ConnectorAccessDeniedError("denied"));

    const { result } = renderHook(() => useConnectors(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ConnectorAccessDeniedError);
    expect(listConnectorDriversMock).toHaveBeenCalledTimes(1);
  });

  it("supports refetch() as the refresh/retry action", async () => {
    listConnectorDriversMock.mockResolvedValueOnce([]);
    listConnectorDriversMock.mockResolvedValueOnce([{ driverId: "connector-dummy" }]);

    const { result } = renderHook(() => useConnectors(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);

    await result.current.refetch();

    await waitFor(() => expect(result.current.data).toHaveLength(1));
  });
});
