import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

import {
  AiStudioAccessDeniedError,
  AiStudioRequestError,
  listAiModels,
  listAiProviders,
  normalizePayload,
} from "./api";

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

describe("listAiProviders", () => {
  it("calls the kortex.ai.provider.list capability with no parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await listAiProviders();

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({ capabilityName: "kortex.ai.provider.list", parameters: {} }),
    );
  });

  it("maps an empty registry to an empty array", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    expect(await listAiProviders()).toEqual([]);
  });

  it("maps the raw snake_case AIProviderMetadata wire shape into a typed, camelCase AiProvider", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          provider_id: "ollama-local",
          display_name: "Local Ollama",
          vendor: "Ollama",
          endpoint_type: "local_host",
          url: "http://localhost:11434",
          credential_requirement: "none",
          secret_handle: null,
          supported_models: ["llama3", "mistral"],
        },
      ]),
    );

    const [provider] = await listAiProviders();

    expect(provider).toEqual({
      providerId: "ollama-local",
      displayName: "Local Ollama",
      vendor: "Ollama",
      endpointType: "local_host",
      url: "http://localhost:11434",
      credentialRequirement: "none",
      supportedModels: ["llama3", "mistral"],
    });
  });

  it("never surfaces secret_handle even though the raw wire object carries it", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          provider_id: "cloud-provider",
          display_name: "Cloud Provider",
          vendor: "Acme",
          endpoint_type: "cloud",
          credential_requirement: "api_key",
          secret_handle: "sh_should_never_appear",
          supported_models: [],
        },
      ]),
    );

    const [provider] = await listAiProviders();

    expect(JSON.stringify(provider)).not.toMatch(/secret|should_never_appear/i);
  });

  it("throws AiStudioAccessDeniedError on a PERMISSION_DENIED failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "denied"));

    await expect(listAiProviders()).rejects.toBeInstanceOf(AiStudioAccessDeniedError);
  });

  it("throws AiStudioRequestError on any other failure category", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("SERVICE_UNAVAILABLE", "backend unreachable"));

    await expect(listAiProviders()).rejects.toBeInstanceOf(AiStudioRequestError);
  });
});

describe("listAiModels", () => {
  it("calls the kortex.ai.model.list capability with no parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await listAiModels();

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({ capabilityName: "kortex.ai.model.list", parameters: {} }),
    );
  });

  it("maps an empty registry to an empty array", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    expect(await listAiModels()).toEqual([]);
  });

  it("maps the raw snake_case AIModelSummary wire shape into a typed, camelCase AiModel", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([{ model_id: "llama3", provider_id: "ollama-local", provider_display_name: "Local Ollama" }]),
    );

    const [model] = await listAiModels();

    expect(model).toEqual({ modelId: "llama3", providerId: "ollama-local", providerDisplayName: "Local Ollama" });
  });

  it("throws AiStudioAccessDeniedError on a PERMISSION_DENIED failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "denied"));

    await expect(listAiModels()).rejects.toBeInstanceOf(AiStudioAccessDeniedError);
  });

  it("throws AiStudioRequestError on any other failure category", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("EXECUTION_FAILED", "boom"));

    await expect(listAiModels()).rejects.toBeInstanceOf(AiStudioRequestError);
  });
});

describe("normalizePayload", () => {
  it("unwraps single-key { result: [...] } wrapper", () => {
    expect(normalizePayload({ result: ["item-1", "item-2"] })).toEqual(["item-1", "item-2"]);
  });

  it("unwraps single-key { result: primitive } wrapper", () => {
    expect(normalizePayload({ result: true })).toBe(true);
    expect(normalizePayload({ result: 42 })).toBe(42);
    expect(normalizePayload({ result: "done" })).toBe("done");
  });

  it("unwraps single-key { result: null } to null", () => {
    expect(normalizePayload({ result: null })).toBeNull();
  });

  it("returns direct dictionary untouched", () => {
    const dict = {
      tenant_id: "acme",
      provider_id: "google-gemini",
      enabled: true,
      has_credential: true,
    };
    expect(normalizePayload(dict)).toEqual(dict);
  });

  it("returns objects with multiple keys including 'result' untouched", () => {
    const multiKey = { result: "value", extra: 123 };
    expect(normalizePayload(multiKey)).toEqual(multiKey);
  });

  it("returns arrays untouched", () => {
    expect(normalizePayload([{ id: "1" }])).toEqual([{ id: "1" }]);
  });

  it("normalizes null and undefined to null", () => {
    expect(normalizePayload(null)).toBeNull();
    expect(normalizePayload(undefined)).toBeNull();
  });
});

describe("error envelope propagation", () => {
  it("attaches the original IpcResultEnvelope to AiStudioAccessDeniedError", async () => {
    const envelope = failureEnvelope("PERMISSION_DENIED", "no access");
    invokeCapabilityMock.mockResolvedValueOnce(envelope);

    let caughtError: unknown;
    try {
      await listAiProviders();
    } catch (e) {
      caughtError = e;
    }

    expect(caughtError).toBeInstanceOf(AiStudioAccessDeniedError);
    expect((caughtError as AiStudioAccessDeniedError).envelope).toBe(envelope);
  });

  it("attaches the original IpcResultEnvelope to AiStudioRequestError", async () => {
    const envelope = failureEnvelope("EXECUTION_FAILED", "internal error");
    invokeCapabilityMock.mockResolvedValueOnce(envelope);

    let caughtError: unknown;
    try {
      await listAiModels();
    } catch (e) {
      caughtError = e;
    }

    expect(caughtError).toBeInstanceOf(AiStudioRequestError);
    expect((caughtError as AiStudioRequestError).envelope).toBe(envelope);
  });
});
