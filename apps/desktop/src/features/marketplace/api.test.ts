import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

import { listMarketplaceListings, MarketplaceAccessDeniedError, MarketplaceRequestError } from "./api";

beforeEach(() => {
  invokeCapabilityMock.mockReset();
});

function successEnvelope(result: unknown) {
  return {
    requestId: "req-1",
    correlationId: "corr-1",
    status: "SUCCESS" as const,
    payload: { result },
    errors: [],
    warnings: [],
    executionDurationMs: 1,
  };
}

function failureEnvelope(category: string, message: string) {
  return {
    requestId: "req-1",
    correlationId: "corr-1",
    status: "FAILURE" as const,
    payload: null,
    errors: [{ category, message, correlationId: "corr-1" }],
    warnings: [],
    executionDurationMs: 1,
  };
}

describe("listMarketplaceListings", () => {
  it("calls the kortex.marketplace.listing.list capability with no parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await listMarketplaceListings();

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.marketplace.listing.list",
        parameters: {},
      }),
    );
  });

  it("maps an empty catalog to an empty array", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    const listings = await listMarketplaceListings();

    expect(listings).toEqual([]);
  });

  it("maps the raw snake_case MarketplaceListing wire shape into a typed, camelCase MarketplaceListing", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          listing_id: "listing-demo",
          name: "Sample Recipe Pack",
          description: "A sample catalog entry.",
          version: "1.0.0",
          item_type: "RECIPE",
          publisher: "KORTEX",
          status: "AVAILABLE",
          compatibility: "Kernel >= 1.0.0",
        },
      ]),
    );

    const [listing] = await listMarketplaceListings();

    expect(listing).toEqual({
      listingId: "listing-demo",
      name: "Sample Recipe Pack",
      description: "A sample catalog entry.",
      version: "1.0.0",
      itemType: "RECIPE",
      publisher: "KORTEX",
      status: "AVAILABLE",
      compatibility: "Kernel >= 1.0.0",
    });
  });

  it("never surfaces a credential/secret/token field even if one were present on the wire", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          listing_id: "listing-demo",
          name: "Sample Recipe Pack",
          description: "A sample catalog entry.",
          version: "1.0.0",
          item_type: "RECIPE",
          publisher: "KORTEX",
          status: "AVAILABLE",
          // Hypothetical extra fields a misbehaving backend might add —
          // `toMarketplaceListing` only reads the known-safe fields by
          // name, so anything else is silently dropped.
          access_token: "should_never_appear",
          api_key: "should_never_appear",
          secret_handle: "should_never_appear",
          license_key: "should_never_appear",
        },
      ]),
    );

    const [listing] = await listMarketplaceListings();

    expect(JSON.stringify(listing)).not.toMatch(/token|secret|key|password|credential/i);
  });

  it("throws MarketplaceAccessDeniedError on a PERMISSION_DENIED failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "denied"));

    await expect(listMarketplaceListings()).rejects.toBeInstanceOf(MarketplaceAccessDeniedError);
  });

  it("throws MarketplaceRequestError on any other failure category", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("SERVICE_UNAVAILABLE", "backend unreachable"));

    await expect(listMarketplaceListings()).rejects.toBeInstanceOf(MarketplaceRequestError);
  });
});
