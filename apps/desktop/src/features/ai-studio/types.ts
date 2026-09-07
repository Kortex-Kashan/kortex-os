/**
 * Mirrors `kortex.engines.ai.models.AIProviderMetadata`
 * (backend/src/kortex/engines/ai/models.py) — the shape
 * `kortex.ai.provider.list` returns — MINUS `secret_handle`. That field
 * exists on the backend model and is NOT filtered out at the capability
 * layer (it isn't a secret's plaintext, just an opaque handle Security
 * Engine resolves later), but this workspace has no legitimate reason to
 * receive it at all, so it is deliberately absent from this type and from
 * `api.ts`'s mapping — never merely hidden in the UI.
 */
export type AiProviderEndpointType = "local_host" | "network" | "cloud";

export type AiProviderCredentialRequirement = "none" | "api_key" | "bearer_token" | "oauth" | "custom";

export interface AiProvider {
  providerId: string;
  displayName: string;
  vendor: string;
  endpointType: AiProviderEndpointType;
  url: string | null;
  credentialRequirement: AiProviderCredentialRequirement;
  supportedModels: string[];
}

/**
 * Mirrors `kortex.engines.ai.models.AIModelSummary` — the shape
 * `kortex.ai.model.list` returns. A derived, flattened view over each
 * provider's `supported_models`, not a first-class Model entity (see that
 * model's own backend docstring) — carries no field beyond these three.
 */
export interface AiModel {
  modelId: string;
  providerId: string;
  providerDisplayName: string;
}

/**
 * Mirrors what `kortex.ai.provider.config.list` and
 * `kortex.ai.provider.configure` return — one tenant's configuration of one
 * provider.
 *
 * `secret_handle` is absent here for the same reason it is absent from
 * `AiProvider`, and now also absent from the backend response itself
 * (B4.1): the frontend must never *receive* a secret handle, not merely
 * decline to render one. `hasCredential` carries the entire signal this
 * workspace needs — "is a credential stored for this provider?" — and
 * carries nothing that could be used to request one.
 *
 * There is deliberately no field an API key could be assigned to. A key
 * travels in exactly one direction (this workspace -> `provider.configure`)
 * and never comes back.
 */
export interface AiProviderConfig {
  tenantId: string;
  providerId: string;
  enabled: boolean;
  hasCredential: boolean;
  defaultModel: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

/**
 * Mirrors what `kortex.ai.provider.test` returns.
 *
 * `connected` and `models` are independent results of two separate provider
 * round trips: the backend returns `connected: true` with an empty `models`
 * and an explanatory `detail` when the credential is valid but discovery
 * failed on its own. The UI must therefore treat `detail` as informational
 * whenever `connected` is true, not as an error.
 */
export interface AiConnectionTestResult {
  providerId: string;
  connected: boolean;
  detail: string | null;
  models: AiModel[];
}

/** Input to `kortex.ai.provider.configure`. `tenantId` is deliberately
 * absent: the backend derives the tenant from the verified execution
 * context and ignores any caller-supplied value. */
export interface AiProviderConfigureInput {
  providerId: string;
  /** Plaintext key, sent once. Omit to change other fields without
   * replacing a stored credential. */
  apiKey?: string;
  defaultModel?: string;
  enabled?: boolean;
}
