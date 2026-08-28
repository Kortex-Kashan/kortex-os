import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listMarketplaceListingsMock } = vi.hoisted(() => ({ listMarketplaceListingsMock: vi.fn() }));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, listMarketplaceListings: listMarketplaceListingsMock };
});

import { MarketplaceAccessDeniedError, MarketplaceRequestError } from "../api";
import type { MarketplaceListing } from "../types";
import { MarketplaceApp } from "./MarketplaceApp";

beforeEach(() => {
  listMarketplaceListingsMock.mockReset();
});

function renderMarketplaceApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MarketplaceApp />
    </QueryClientProvider>,
  );
}

function makeListing(overrides: Partial<MarketplaceListing> = {}): MarketplaceListing {
  return {
    listingId: "listing-demo",
    name: "Sample Recipe Pack",
    description: "A sample catalog entry.",
    version: "1.0.0",
    itemType: "RECIPE",
    publisher: "KORTEX",
    status: "AVAILABLE",
    compatibility: "Kernel >= 1.0.0",
    ...overrides,
  };
}

describe("MarketplaceApp", () => {
  it("shows a loading state while the request is in flight", () => {
    listMarketplaceListingsMock.mockReturnValueOnce(new Promise<MarketplaceListing[]>(() => {}));

    renderMarketplaceApp();

    expect(screen.getByRole("status", { name: /loading marketplace catalog/i })).toBeInTheDocument();
  });

  it("shows the empty-catalog message when no items are available", async () => {
    listMarketplaceListingsMock.mockResolvedValueOnce([]);

    renderMarketplaceApp();

    expect(await screen.findByText("No items are currently available in the catalog.")).toBeInTheDocument();
  });

  it("communicates that installation/purchasing/publishing are not available", async () => {
    listMarketplaceListingsMock.mockResolvedValueOnce([]);

    renderMarketplaceApp();

    expect(
      await screen.findByText(/Installing, purchasing, and publishing are not available yet\./),
    ).toBeInTheDocument();
  });

  it("renders real catalog data for a populated catalog, identifying each listing", async () => {
    listMarketplaceListingsMock.mockResolvedValueOnce([
      makeListing(),
      makeListing({ listingId: "listing-other", name: "Other Template", itemType: "TEMPLATE" }),
    ]);

    renderMarketplaceApp();

    expect(await screen.findByText("Sample Recipe Pack")).toBeInTheDocument();
    expect(screen.getByText("Other Template")).toBeInTheDocument();
    expect(screen.getAllByTestId("marketplace-listing-card")).toHaveLength(2);
  });

  it("shows a Deprecated badge for a deprecated listing", async () => {
    listMarketplaceListingsMock.mockResolvedValueOnce([makeListing({ status: "DEPRECATED" })]);

    renderMarketplaceApp();

    expect(await screen.findByText("Deprecated")).toBeInTheDocument();
  });

  it("never renders a credential/secret/token field, even if present on a listing object", async () => {
    listMarketplaceListingsMock.mockResolvedValueOnce([
      { ...makeListing(), apiKey: "should_never_render", licenseKey: "should_never_render" },
    ]);

    renderMarketplaceApp();

    await screen.findByText("Sample Recipe Pack");
    expect(screen.queryByText(/should_never_render/i)).not.toBeInTheDocument();
  });

  it("shows an access-denied state — not a session-expired claim — on PERMISSION_DENIED", async () => {
    listMarketplaceListingsMock.mockRejectedValueOnce(
      new MarketplaceAccessDeniedError("Missing permission: marketplace:read"),
    );

    renderMarketplaceApp();

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(
      screen.getByText("You do not have permission to view the marketplace catalog."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/session expired/i)).not.toBeInTheDocument();
  });

  it("shows a generic, recoverable error state with a retry action on any other failure", async () => {
    // A non-access-denied failure gets one automatic retry (`useMarketplace`'s
    // own `retry` option, mirroring the app's global `retry: 1` default) —
    // persistently rejecting and extending the timeout accounts for that
    // real retry delay rather than racing it (see the M5/M6 component
    // tests for the same pattern and its rationale).
    listMarketplaceListingsMock.mockRejectedValue(new MarketplaceRequestError("backend unreachable"));

    renderMarketplaceApp();

    expect(
      await screen.findByText("Something went wrong loading the marketplace catalog.", undefined, {
        timeout: 3000,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  }, 8000);

  it("retries the request when Retry is clicked", async () => {
    listMarketplaceListingsMock.mockRejectedValue(new MarketplaceRequestError("backend unreachable"));

    renderMarketplaceApp();

    const retryButton = await screen.findByRole("button", { name: "Retry" }, { timeout: 3000 });
    listMarketplaceListingsMock.mockReset();
    listMarketplaceListingsMock.mockResolvedValueOnce([makeListing()]);
    fireEvent.click(retryButton);

    expect(await screen.findByText("Sample Recipe Pack")).toBeInTheDocument();
  }, 8000);

  it("refreshes populated data when Refresh is clicked", async () => {
    listMarketplaceListingsMock.mockResolvedValueOnce([makeListing()]);
    listMarketplaceListingsMock.mockResolvedValueOnce([
      makeListing(),
      makeListing({ listingId: "listing-other", name: "Other Template" }),
    ]);

    renderMarketplaceApp();

    await screen.findByText("Sample Recipe Pack");
    const refreshButton = screen.getByRole("button", { name: "Refresh" });
    fireEvent.click(refreshButton);

    expect(await screen.findByText("Other Template")).toBeInTheDocument();
    expect(listMarketplaceListingsMock).toHaveBeenCalledTimes(2);
  });
});
