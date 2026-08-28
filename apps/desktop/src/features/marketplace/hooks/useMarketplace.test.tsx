import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listMarketplaceListingsMock } = vi.hoisted(() => ({ listMarketplaceListingsMock: vi.fn() }));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, listMarketplaceListings: listMarketplaceListingsMock };
});

import { MarketplaceAccessDeniedError } from "../api";
import { useMarketplace } from "./useMarketplace";

beforeEach(() => {
  listMarketplaceListingsMock.mockReset();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useMarketplace", () => {
  it("starts pending, then resolves to the listing list on success", async () => {
    listMarketplaceListingsMock.mockResolvedValueOnce([{ listingId: "listing-1", name: "Sample" }]);

    const { result } = renderHook(() => useMarketplace(), { wrapper });

    expect(result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("resolves to an empty array for an empty catalog", async () => {
    listMarketplaceListingsMock.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useMarketplace(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  it("surfaces a thrown MarketplaceAccessDeniedError as the query error without retrying", async () => {
    listMarketplaceListingsMock.mockRejectedValueOnce(new MarketplaceAccessDeniedError("denied"));

    const { result } = renderHook(() => useMarketplace(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(MarketplaceAccessDeniedError);
    expect(listMarketplaceListingsMock).toHaveBeenCalledTimes(1);
  });

  it("supports refetch() as the refresh/retry action", async () => {
    listMarketplaceListingsMock.mockResolvedValueOnce([]);
    listMarketplaceListingsMock.mockResolvedValueOnce([{ listingId: "listing-1" }]);

    const { result } = renderHook(() => useMarketplace(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);

    await result.current.refetch();

    await waitFor(() => expect(result.current.data).toHaveLength(1));
  });
});
