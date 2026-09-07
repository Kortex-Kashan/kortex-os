import { invokeCapability } from "@/ipc/client";
import type {
  AiConnectionTestResult,
  AiModel,
  AiProvider,
  AiProviderConfig,
  AiProviderConfigureInput,
  AiProviderCredentialRequirement,
  AiProviderEndpointType,
} from "./types";

const PROVIDER_LIST_CAPABILITY = "kortex.ai.provider.list";
const MODEL_LIST_CAPABILITY = "kortex.ai.model.list";
const PROVIDER_CONFIG_LIST_CAPABILITY = "kortex.ai.provider.config.list";
const PROVIDER_CONFIGURE_CAPABILITY = "kortex.ai.provider.configure";
const PROVIDER_TEST_CAPABILITY = "kortex.ai.provider.test";
const PROVIDER_CONFIG_REMOVE_CAPABILITY = "kortex.ai.provider.config.remove";

/**
 * Thrown when the backend denies a call with `PERMISSION_DENIED` — see
 * `apps/desktop/src/features/connectors/api.ts`'s `ConnectorAccessDeniedError`
 * for why this stays a single, unified category rather than splitting
 * 401 vs. 403 (the IPC transport carries `httpStatus` for that distinction,
 * but this feature does not yet consume it, matching Connectors/Workflow/
 * Marketplace).
 */
export class AiStudioAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AiStudioAccessDeniedError";
  }
}

/** Any other `FAILURE` envelope — a generic, recoverable failure. */
export class AiStudioRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AiStudioRequestError";
  }
}

/** Raw wire shape of one `kortex.ai.provider.list` entry — snake_case,
 * since `AIProviderMetadata` has no camelCase alias generator on the
 * Python side. Declares `secret_handle` only so it is named here, once,
 * as the field this mapping deliberately never reads — see `types.ts`. */
interface RawAiProvider {
  provider_id: string;
  display_name: string;
  vendor: string;
  endpoint_type: string;
  url?: string | null;
  credential_requirement?: string;
  secret_handle?: string | null;
  supported_models?: string[];
}

interface RawAiModel {
  model_id: string;
  provider_id: string;
  provider_display_name: string;
}

function toAiProvider(raw: RawAiProvider): AiProvider {
  return {
    providerId: raw.provider_id,
    displayName: raw.display_name,
    vendor: raw.vendor,
    endpointType: raw.endpoint_type as AiProviderEndpointType,
    url: raw.url ?? null,
    credentialRequirement: (raw.credential_requirement ?? "none") as AiProviderCredentialRequirement,
    supportedModels: raw.supported_models ?? [],
  };
}

function toAiModel(raw: RawAiModel): AiModel {
  return {
    modelId: raw.model_id,
    providerId: raw.provider_id,
    providerDisplayName: raw.provider_display_name,
  };
}

async function invokeListCapability(capabilityName: string): Promise<unknown[]> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName,
    parameters: {},
  });

  if (envelope.status === "SUCCESS") {
    const result = envelope.payload?.result;
    return Array.isArray(result) ? result : [];
  }

  const failure = envelope.errors[0];
  const message = failure?.message ?? `Failed to load ${capabilityName}.`;
  if (failure?.category === "PERMISSION_DENIED") {
    throw new AiStudioAccessDeniedError(message);
  }
  throw new AiStudioRequestError(message);
}

/**
 * Calls the existing `kortex.ai.provider.list` capability through the
 * existing generic IPC path (React -> `ipc/client.ts` -> Tauri
 * `invoke_capability` -> Rust -> backend `CapabilityDispatcher`). No
 * dedicated Tauri command is introduced.
 */
export async function listAiProviders(): Promise<AiProvider[]> {
  const raw = await invokeListCapability(PROVIDER_LIST_CAPABILITY);
  return (raw as RawAiProvider[]).map(toAiProvider);
}

/** Calls the existing `kortex.ai.model.list` capability the same way. */
export async function listAiModels(): Promise<AiModel[]> {
  const raw = await invokeListCapability(MODEL_LIST_CAPABILITY);
  return (raw as RawAiModel[]).map(toAiModel);
}

// ---------------------------------------------------------------------------
// Tenant provider configuration (B4.2)
//
// Four capabilities that existed since B1/B2 but had no frontend consumer.
// All four go through the same single generic `invokeCapability` path as the
// two above — no dedicated Tauri command, no second IPC mechanism.
//
// None of them takes a `tenant_id`: the backend handlers derive the tenant
// from the verified execution context and have no parameter a caller could
// use to name a different one. Sending one would be meaningless, so none is
// sent.
// ---------------------------------------------------------------------------

/** Raw wire shape of one `kortex.ai.provider.config.list` entry.
 *
 * `secret_handle` is intentionally NOT declared. Unlike `RawAiProvider`
 * above — which names the field so the deliberate omission is visible — the
 * provider-configuration response no longer carries it at all (B4.1
 * narrowed `_provider_config_view`), so declaring it here would document a
 * field that does not exist. `has_credential` is the whole signal. */
interface RawAiProviderConfig {
  tenant_id: string;
  provider_id: string;
  enabled: boolean;
  has_credential: boolean;
  default_model?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface RawConnectionTestResult {
  provider_id: string;
  connected: boolean;
  detail?: string | null;
  models?: RawAiModel[];
}

function toAiProviderConfig(raw: RawAiProviderConfig): AiProviderConfig {
  return {
    tenantId: raw.tenant_id,
    providerId: raw.provider_id,
    enabled: raw.enabled,
    hasCredential: raw.has_credential,
    defaultModel: raw.default_model ?? null,
    createdAt: raw.created_at ?? null,
    updatedAt: raw.updated_at ?? null,
  };
}

function toConnectionTestResult(raw: RawConnectionTestResult): AiConnectionTestResult {
  return {
    providerId: raw.provider_id,
    connected: raw.connected,
    detail: raw.detail ?? null,
    models: (raw.models ?? []).map(toAiModel),
  };
}

/** Invoke one capability with parameters, sharing the error taxonomy above. */
async function invokeWithParameters(
  capabilityName: string,
  parameters: Record<string, unknown>,
): Promise<unknown> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName,
    parameters,
  });

  if (envelope.status === "SUCCESS") {
    return envelope.payload?.result ?? null;
  }

  const failure = envelope.errors[0];
  const message = failure?.message ?? `Capability ${capabilityName} failed.`;
  if (failure?.category === "PERMISSION_DENIED") {
    throw new AiStudioAccessDeniedError(message);
  }
  throw new AiStudioRequestError(message);
}

/** The calling tenant's own provider configurations (`ai:read`). */
export async function listAiProviderConfigs(): Promise<AiProviderConfig[]> {
  const raw = await invokeWithParameters(PROVIDER_CONFIG_LIST_CAPABILITY, {});
  return (Array.isArray(raw) ? (raw as RawAiProviderConfig[]) : []).map(toAiProviderConfig);
}

/**
 * Configure one provider for the calling tenant (`ai:manage`).
 *
 * **The key parameter must be named exactly `api_key`.** The Kernel
 * dispatcher runs `core.idempotency.sanitize_for_persistence` over
 * `request.parameters` before they reach the audit log, and that sanitizer
 * redacts by *exact key name* from its `SENSITIVE_KEY_NAMES` set, which
 * contains `api_key`. Renaming it here — to `key`, `token`, `credential` —
 * would silently start writing live provider credentials into the audit
 * trail. The backend handler's own parameter carries the same constraint
 * and the same comment.
 *
 * This is a **single** call. The Connectors feature registers a profile and
 * then makes a second, separate `kortex.security.secret.put` call carrying
 * plaintext from the frontend; that pattern is deliberately NOT copied
 * here. One call means the plaintext crosses exactly one boundary, this
 * workspace needs no `security:secret:write` grant, and no window exists in
 * which a configuration claims a credential that was never stored.
 *
 * Omitted fields are omitted from the payload rather than sent as `null`:
 * the backend treats a `None` `api_key`/`default_model` as "leave the
 * stored value alone", so sending an explicit `null` for an untouched field
 * and sending nothing are equivalent — but omitting makes it impossible for
 * a future backend change to read an explicit `null` as "clear this".
 */
export async function configureAiProvider(input: AiProviderConfigureInput): Promise<AiProviderConfig> {
  const parameters: Record<string, unknown> = { provider_id: input.providerId };
  if (input.apiKey !== undefined) {
    parameters.api_key = input.apiKey;
  }
  if (input.defaultModel !== undefined) {
    parameters.default_model = input.defaultModel;
  }
  if (input.enabled !== undefined) {
    parameters.enabled = input.enabled;
  }

  const raw = await invokeWithParameters(PROVIDER_CONFIGURE_CAPABILITY, parameters);
  return toAiProviderConfig(raw as RawAiProviderConfig);
}

/**
 * Test one provider's stored credential and discover its live models
 * (`ai:manage`).
 *
 * No credential is sent: the backend resolves the calling tenant's own
 * stored key through `TenantCredentialResolver`. The vendor API is called
 * by the backend provider adapter — never by this workspace.
 */
export async function testAiProviderConnection(providerId: string): Promise<AiConnectionTestResult> {
  const raw = await invokeWithParameters(PROVIDER_TEST_CAPABILITY, { provider_id: providerId });
  return toConnectionTestResult(raw as RawConnectionTestResult);
}

/**
 * Remove one of the calling tenant's provider configurations (`ai:manage`).
 *
 * The capability is `kortex.ai.provider.config.remove` — it removes the
 * *configuration*, not the provider, which is a process-global registry
 * entry no tenant can unregister. The `SecretStore` entry is deliberately
 * left in place by the backend (the AI engine has no authority to delete
 * Security Engine records), and is unreachable without a configuration row
 * pointing at it.
 */
export async function removeAiProviderConfig(providerId: string): Promise<boolean> {
  const raw = await invokeWithParameters(PROVIDER_CONFIG_REMOVE_CAPABILITY, { provider_id: providerId });
  return Boolean((raw as { removed?: boolean } | null)?.removed);
}
