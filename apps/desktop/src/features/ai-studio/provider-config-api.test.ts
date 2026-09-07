/**
 * The four tenant provider-configuration capabilities (B4.2).
 *
 * These assert the exact capability names and the exact parameter names on
 * the wire. That is the whole point of the file: `api_key` in particular is
 * load-bearing security, because the Kernel dispatcher's audit sanitizer
 * (`core.idempotency.sanitize_for_persistence`) redacts by exact key name.
 * A rename would not fail a type check, would not fail a render test, and
 * would silently begin writing live provider credentials into the audit
 * log — so it fails here.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

import {
  AiStudioAccessDeniedError,
  AiStudioRequestError,
  configureAiProvider,
  listAiProviderConfigs,
  removeAiProviderConfig,
  testAiProviderConnection,
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

const RAW_CONFIG = {
  tenant_id: "acme",
  provider_id: "openai",
  enabled: true,
  has_credential: true,
  default_model: "gpt-4o",
  created_at: "2026-01-01T00:00:00+00:00",
  updated_at: "2026-01-02T00:00:00+00:00",
};

function lastParameters(): Record<string, unknown> {
  const call = invokeCapabilityMock.mock.calls.at(-1);
  if (!call) throw new Error("invokeCapability was never called");
  return (call[0] as { parameters: Record<string, unknown> }).parameters;
}

// ---------------------------------------------------------------------------
// listAiProviderConfigs
// ---------------------------------------------------------------------------

describe("listAiProviderConfigs", () => {
  it("calls kortex.ai.provider.config.list with no parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await listAiProviderConfigs();

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({ capabilityName: "kortex.ai.provider.config.list", parameters: {} }),
    );
  });

  it("sends no tenant_id: the backend derives the tenant from the verified context", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await listAiProviderConfigs();

    expect(lastParameters()).not.toHaveProperty("tenant_id");
  });

  it("maps the snake_case wire shape into a typed camelCase config", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([RAW_CONFIG]));

    expect(await listAiProviderConfigs()).toEqual([
      {
        tenantId: "acme",
        providerId: "openai",
        enabled: true,
        hasCredential: true,
        defaultModel: "gpt-4o",
        createdAt: "2026-01-01T00:00:00+00:00",
        updatedAt: "2026-01-02T00:00:00+00:00",
      },
    ]);
  });

  it("normalizes absent optional fields to null rather than undefined", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([{ tenant_id: "acme", provider_id: "gemini", enabled: false, has_credential: false }]),
    );

    const [config] = await listAiProviderConfigs();

    expect(config.defaultModel).toBeNull();
    expect(config.createdAt).toBeNull();
    expect(config.updatedAt).toBeNull();
  });

  it("never surfaces a secret handle, even if the backend response regained one", async () => {
    // The B4.1 wire view omits `secret_handle` entirely. This asserts the
    // mapping is a second, independent barrier: if a future backend change
    // re-added the field, it still would not reach a frontend object.
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([{ ...RAW_CONFIG, secret_handle: "kortex/ai/providers/openai" }]),
    );

    const [config] = await listAiProviderConfigs();

    expect(config).not.toHaveProperty("secretHandle");
    expect(config).not.toHaveProperty("secret_handle");
    expect(JSON.stringify(config)).not.toContain("kortex/ai/providers");
  });

  it("maps a non-array result to an empty list rather than throwing", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(null));

    expect(await listAiProviderConfigs()).toEqual([]);
  });

  it("raises AiStudioAccessDeniedError on PERMISSION_DENIED", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "Missing permission: ai:read"));

    await expect(listAiProviderConfigs()).rejects.toBeInstanceOf(AiStudioAccessDeniedError);
  });

  it("raises AiStudioRequestError on any other failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("INTERNAL", "boom"));

    await expect(listAiProviderConfigs()).rejects.toBeInstanceOf(AiStudioRequestError);
  });
});

// ---------------------------------------------------------------------------
// configureAiProvider
// ---------------------------------------------------------------------------

describe("configureAiProvider", () => {
  it("calls kortex.ai.provider.configure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(RAW_CONFIG));

    await configureAiProvider({ providerId: "openai", apiKey: "sk-live" });

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({ capabilityName: "kortex.ai.provider.configure" }),
    );
  });

  it("sends the credential under the parameter name `api_key`, exactly", async () => {
    // Guards `sanitize_for_persistence`'s exact-key redaction. See this
    // file's header.
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(RAW_CONFIG));

    await configureAiProvider({ providerId: "openai", apiKey: "sk-live" });

    expect(lastParameters().api_key).toBe("sk-live");
    for (const rejected of ["key", "apiKey", "token", "credential", "secret", "plaintext"]) {
      expect(lastParameters()).not.toHaveProperty(rejected);
    }
  });

  it("sends provider_id, default_model and enabled in snake_case", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(RAW_CONFIG));

    await configureAiProvider({
      providerId: "openai",
      apiKey: "sk-live",
      defaultModel: "gpt-4o",
      enabled: true,
    });

    expect(lastParameters()).toEqual({
      provider_id: "openai",
      api_key: "sk-live",
      default_model: "gpt-4o",
      enabled: true,
    });
  });

  it("sends only provider_id and default_model when changing a default model", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(RAW_CONFIG));

    await configureAiProvider({ providerId: "openai", defaultModel: "gpt-4o" });

    expect(lastParameters()).toEqual({ provider_id: "openai", default_model: "gpt-4o" });
  });

  it("omits an unsupplied field entirely rather than sending an explicit null", async () => {
    // The backend reads `None` as "leave the stored value alone", so today
    // omitting and sending null behave identically -- which is exactly why
    // this needs asserting: nothing else would notice the difference until
    // a later backend change read an explicit null as "clear this", quietly
    // wiping a tenant's stored default model or credential handle.
    //
    // Asserted with `toEqual` on the whole payload, not `not.toHaveProperty`
    // per field: an exact-shape assertion also catches a field being added
    // that no caller asked for.
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(RAW_CONFIG));

    await configureAiProvider({ providerId: "openai", apiKey: "sk-live" });

    expect(lastParameters()).toEqual({ provider_id: "openai", api_key: "sk-live" });
  });

  it("sends only provider_id when toggling nothing but enabled", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(RAW_CONFIG));

    await configureAiProvider({ providerId: "openai", enabled: false });

    expect(lastParameters()).toEqual({ provider_id: "openai", enabled: false });
  });

  it("is a single call: it never invokes a Security Engine secret capability", async () => {
    // The Connectors feature registers a profile and then separately calls
    // `kortex.security.secret.put` with plaintext from the frontend. That
    // pattern is deliberately not copied here.
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(RAW_CONFIG));

    await configureAiProvider({ providerId: "openai", apiKey: "sk-live" });

    expect(invokeCapabilityMock).toHaveBeenCalledTimes(1);
    const names = invokeCapabilityMock.mock.calls.map(
      (call) => (call[0] as { capabilityName: string }).capabilityName,
    );
    expect(names.some((name) => name.startsWith("kortex.security."))).toBe(false);
  });

  it("sends no tenant_id", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(RAW_CONFIG));

    await configureAiProvider({ providerId: "openai", apiKey: "sk-live" });

    expect(lastParameters()).not.toHaveProperty("tenant_id");
  });

  it("maps the returned configuration and drops any handle", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({ ...RAW_CONFIG, secret_handle: "kortex/ai/providers/openai" }),
    );

    const config = await configureAiProvider({ providerId: "openai", apiKey: "sk-live" });

    expect(config.hasCredential).toBe(true);
    expect(config).not.toHaveProperty("secretHandle");
  });

  it("raises AiStudioAccessDeniedError when the caller lacks ai:manage", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "Missing permission: ai:manage"));

    await expect(configureAiProvider({ providerId: "openai", apiKey: "sk-live" })).rejects.toBeInstanceOf(
      AiStudioAccessDeniedError,
    );
  });
});

// ---------------------------------------------------------------------------
// testAiProviderConnection
// ---------------------------------------------------------------------------

describe("testAiProviderConnection", () => {
  it("calls kortex.ai.provider.test with only provider_id", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({ provider_id: "openai", connected: true, detail: null, models: [] }),
    );

    await testAiProviderConnection("openai");

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.ai.provider.test",
        parameters: { provider_id: "openai" },
      }),
    );
  });

  it("sends no credential: the backend resolves the tenant's stored key", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({ provider_id: "openai", connected: true, models: [] }),
    );

    await testAiProviderConnection("openai");

    for (const rejected of ["api_key", "apiKey", "credential", "plaintext", "tenant_id"]) {
      expect(lastParameters()).not.toHaveProperty(rejected);
    }
  });

  it("maps discovered models into typed AiModel objects", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({
        provider_id: "openai",
        connected: true,
        detail: null,
        models: [{ model_id: "gpt-4o", provider_id: "openai", provider_display_name: "OpenAI" }],
      }),
    );

    const result = await testAiProviderConnection("openai");

    expect(result).toEqual({
      providerId: "openai",
      connected: true,
      detail: null,
      models: [{ modelId: "gpt-4o", providerId: "openai", providerDisplayName: "OpenAI" }],
    });
  });

  it("preserves connected=true alongside a discovery failure detail", async () => {
    // The backend returns this shape when the credential validated but the
    // second, independent model-discovery round trip failed. Collapsing it
    // to a failure would tell the user a working key is broken.
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({ provider_id: "openai", connected: true, detail: "rate limited", models: [] }),
    );

    const result = await testAiProviderConnection("openai");

    expect(result.connected).toBe(true);
    expect(result.detail).toBe("rate limited");
    expect(result.models).toEqual([]);
  });

  it("surfaces a failed connection as a value, not an exception", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({ provider_id: "openai", connected: false, detail: "invalid key", models: [] }),
    );

    const result = await testAiProviderConnection("openai");

    expect(result.connected).toBe(false);
    expect(result.detail).toBe("invalid key");
  });

  it("defaults a missing models array to empty", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope({ provider_id: "openai", connected: true }));

    expect((await testAiProviderConnection("openai")).models).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// removeAiProviderConfig
// ---------------------------------------------------------------------------

describe("removeAiProviderConfig", () => {
  it("calls kortex.ai.provider.config.remove -- not kortex.ai.provider.remove", async () => {
    // The capability removes the tenant's *configuration*. There is no
    // capability that removes a provider: the registry is process-global.
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({ provider_id: "openai", tenant_id: "acme", removed: true }),
    );

    await removeAiProviderConfig("openai");

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.ai.provider.config.remove",
        parameters: { provider_id: "openai" },
      }),
    );
  });

  it("returns the backend's removed flag", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({ provider_id: "openai", tenant_id: "acme", removed: false }),
    );

    expect(await removeAiProviderConfig("openai")).toBe(false);
  });

  it("treats a null result as not removed", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(null));

    expect(await removeAiProviderConfig("openai")).toBe(false);
  });

  it("raises AiStudioAccessDeniedError when the caller lacks ai:manage", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "Missing permission: ai:manage"));

    await expect(removeAiProviderConfig("openai")).rejects.toBeInstanceOf(AiStudioAccessDeniedError);
  });
});
