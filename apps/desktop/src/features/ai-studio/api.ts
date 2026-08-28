import { invokeCapability } from "@/ipc/client";
import type { AiModel, AiProvider, AiProviderCredentialRequirement, AiProviderEndpointType } from "./types";

const PROVIDER_LIST_CAPABILITY = "kortex.ai.provider.list";
const MODEL_LIST_CAPABILITY = "kortex.ai.model.list";

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
