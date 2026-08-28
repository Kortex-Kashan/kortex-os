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
