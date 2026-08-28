/**
 * Mirrors `kortex.engines.document.models.AdapterMetadata`
 * (backend/src/kortex/engines/document/models.py) — the shape
 * `kortex.document.adapter.list` returns. Fully safe metadata by design —
 * no credential/secret/token field exists on `AdapterMetadata` at all.
 *
 * `supportedCapabilities`/`supportedOperations` are typed as `string[]`
 * rather than an exhaustive literal union: the backend enums
 * (`AdapterCapability`, `DocumentOperationType`) each carry ~11-19 values
 * that exist purely for backend routing/validation, not frontend branching
 * — this workspace only ever displays them as read-only tags, so mirroring
 * every value here would be sync-maintenance overhead with no type-safety
 * benefit for how they're actually used.
 */
export interface DocumentAdapter {
  adapterId: string;
  displayName: string;
  vendor: string;
  author: string;
  version: string;
  license: string;
  description: string;
  homepage: string | null;
  supportedCapabilities: string[];
  supportedOperations: string[];
  supportsPreview: boolean;
  supportsStreaming: boolean;
  supportsMacros: boolean;
  supportsSecurity: boolean;
  supportsVersioning: boolean;
}

/**
 * Mirrors `kortex.engines.document.models.TemplateSchema` — the shape
 * `kortex.document.template.list` returns. `schema_definition` (an open
 * `dict[str, Any]`) is deliberately not mirrored here: `placeholders` and
 * `requiredFields` already describe a template's shape for a browse
 * experience, and this workspace never needs to interpret/validate the
 * raw schema itself.
 */
export interface DocumentTemplate {
  templateId: string;
  name: string;
  namespace: string;
  version: string;
  description: string;
  placeholders: string[];
  requiredFields: string[];
}

/**
 * Mirrors `kortex.engines.knowledge.models.KnowledgeNode` — the shape both
 * `kortex.knowledge.graph.list` and `kortex.knowledge.graph.traverse`
 * return. `vectorEmbedding` is intentionally omitted: a raw float array
 * has no honest rendering in a browse UI and is never read here.
 *
 * `properties: Record<string, unknown>` is a deliberate, understood
 * mirror of the backend's own genuinely dynamic `Dict[str, Any]` field —
 * an entity's properties have no fixed schema by design (this is the
 * actual knowledge payload, not a placeholder for a field this module
 * failed to model) — not a substitute for understanding the contract.
 */
export interface KnowledgeNode {
  nodeId: string;
  tenantId: string;
  entityType: string;
  label: string;
  properties: Record<string, unknown>;
}
