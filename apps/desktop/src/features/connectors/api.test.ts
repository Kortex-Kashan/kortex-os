import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

import { ConnectorAccessDeniedError, ConnectorRequestError, listConnectorDrivers } from "./api";

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

describe("listConnectorDrivers", () => {
  it("calls the kortex.connector.driver.list capability with no parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await listConnectorDrivers();

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.connector.driver.list",
        parameters: {},
      }),
    );
  });

  it("maps an empty registry to an empty array", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    const drivers = await listConnectorDrivers();

    expect(drivers).toEqual([]);
  });

  it("maps the raw snake_case DriverMetadata wire shape into a typed, camelCase ConnectorDriver", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          driver_id: "connector-dummy",
          display_name: "Reference Dummy Connector Driver",
          vendor: "KORTEX",
          author: "KORTEX Core Team",
          version: "1.0.0",
          description: "Reference dummy connector driver plugin.",
          supported_actions: ["SEND", "FETCH"],
          supported_capabilities: ["SEND", "FETCH", "TEST_CONNECTION"],
          is_sandboxed: true,
          homepage: null,
          license: "MIT",
        },
      ]),
    );

    const [driver] = await listConnectorDrivers();

    expect(driver).toEqual({
      driverId: "connector-dummy",
      displayName: "Reference Dummy Connector Driver",
      vendor: "KORTEX",
      author: "KORTEX Core Team",
      version: "1.0.0",
      description: "Reference dummy connector driver plugin.",
      supportedActions: ["SEND", "FETCH"],
      supportedCapabilities: ["SEND", "FETCH", "TEST_CONNECTION"],
      isSandboxed: true,
      homepage: null,
      license: "MIT",
    });
  });

  it("never surfaces a credential/secret/token field even if one were present on the wire", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          driver_id: "connector-dummy",
          display_name: "Reference Dummy Connector Driver",
          vendor: "KORTEX",
          author: "KORTEX Core Team",
          version: "1.0.0",
          description: "Reference dummy connector driver plugin.",
          // Hypothetical extra fields a misbehaving backend might add —
          // `toConnectorDriver` only reads the known-safe fields by name,
          // so anything else is silently dropped rather than passed through.
          secret_handle: "sh_should_never_appear",
          access_token: "should_never_appear",
        },
      ]),
    );

    const [driver] = await listConnectorDrivers();

    expect(JSON.stringify(driver)).not.toMatch(/secret|token|password|credential/i);
  });

  it("throws ConnectorAccessDeniedError on a PERMISSION_DENIED failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "denied"));

    await expect(listConnectorDrivers()).rejects.toBeInstanceOf(ConnectorAccessDeniedError);
  });

  it("throws ConnectorRequestError on any other failure category", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("SERVICE_UNAVAILABLE", "backend unreachable"));

    await expect(listConnectorDrivers()).rejects.toBeInstanceOf(ConnectorRequestError);
  });
});
