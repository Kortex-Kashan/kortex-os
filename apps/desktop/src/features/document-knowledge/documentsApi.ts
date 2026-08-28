import { invokeCapability } from "@/ipc/client";
import type { DocumentAdapter, DocumentTemplate } from "./types";

const ADAPTER_LIST_CAPABILITY = "kortex.document.adapter.list";
const TEMPLATE_LIST_CAPABILITY = "kortex.document.template.list";

/**
 * Thrown when the backend denies a call with `PERMISSION_DENIED` — see
 * `apps/desktop/src/features/connectors/api.ts`'s `ConnectorAccessDeniedError`
 * for why this stays a single, unified category rather than splitting
 * 401 vs. 403, matching every prior feature module's own convention.
 */
export class DocumentAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DocumentAccessDeniedError";
  }
}

/** Any other `FAILURE` envelope — a generic, recoverable failure. */
export class DocumentRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DocumentRequestError";
  }
}

/** Raw wire shape of one `kortex.document.adapter.list` entry —
 * snake_case, since `AdapterMetadata` has no camelCase alias generator on
 * the Python side. */
interface RawDocumentAdapter {
  adapter_id: string;
  display_name: string;
  vendor: string;
  author: string;
  version: string;
  license: string;
  description: string;
  homepage?: string | null;
  supported_capabilities?: string[];
  supported_operations?: string[];
  supports_preview?: boolean;
  supports_streaming?: boolean;
  supports_macros?: boolean;
  supports_security?: boolean;
  supports_versioning?: boolean;
}

interface RawDocumentTemplate {
  template_id: string;
  name: string;
  namespace: string;
  version: string;
  description: string;
  placeholders?: string[];
  required_fields?: string[];
}

function toDocumentAdapter(raw: RawDocumentAdapter): DocumentAdapter {
  return {
    adapterId: raw.adapter_id,
    displayName: raw.display_name,
    vendor: raw.vendor,
    author: raw.author,
    version: raw.version,
    license: raw.license,
    description: raw.description,
    homepage: raw.homepage ?? null,
    supportedCapabilities: raw.supported_capabilities ?? [],
    supportedOperations: raw.supported_operations ?? [],
    supportsPreview: raw.supports_preview ?? false,
    supportsStreaming: raw.supports_streaming ?? false,
    supportsMacros: raw.supports_macros ?? false,
    supportsSecurity: raw.supports_security ?? false,
    supportsVersioning: raw.supports_versioning ?? false,
  };
}

function toDocumentTemplate(raw: RawDocumentTemplate): DocumentTemplate {
  return {
    templateId: raw.template_id,
    name: raw.name,
    namespace: raw.namespace,
    version: raw.version,
    description: raw.description,
    placeholders: raw.placeholders ?? [],
    requiredFields: raw.required_fields ?? [],
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
    throw new DocumentAccessDeniedError(message);
  }
  throw new DocumentRequestError(message);
}

/**
 * Calls the existing `kortex.document.adapter.list` capability through the
 * existing generic IPC path (React -> `ipc/client.ts` -> Tauri
 * `invoke_capability` -> Rust -> backend `CapabilityDispatcher`). No
 * dedicated Tauri command is introduced.
 */
export async function listDocumentAdapters(): Promise<DocumentAdapter[]> {
  const raw = await invokeListCapability(ADAPTER_LIST_CAPABILITY);
  return (raw as RawDocumentAdapter[]).map(toDocumentAdapter);
}

/** Calls the existing `kortex.document.template.list` capability the same way. */
export async function listDocumentTemplates(): Promise<DocumentTemplate[]> {
  const raw = await invokeListCapability(TEMPLATE_LIST_CAPABILITY);
  return (raw as RawDocumentTemplate[]).map(toDocumentTemplate);
}
