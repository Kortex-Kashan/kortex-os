import { invokeCapability } from "@/ipc/client";
import type { KnowledgeNode } from "./types";

const GRAPH_LIST_CAPABILITY = "kortex.knowledge.graph.list";
const GRAPH_TRAVERSE_CAPABILITY = "kortex.knowledge.graph.traverse";

/** See `documentsApi.ts`'s `DocumentAccessDeniedError` for the identical
 * rationale (single, unified PERMISSION_DENIED category). */
export class KnowledgeAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "KnowledgeAccessDeniedError";
  }
}

/** Any other `FAILURE` envelope — a generic, recoverable failure. */
export class KnowledgeRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "KnowledgeRequestError";
  }
}

/** Raw wire shape of one `KnowledgeNode` — snake_case, since the backend
 * model has no camelCase alias generator. `vector_embedding` is declared
 * only so it is named here, once, as the field this mapping deliberately
 * never reads — see `types.ts`. */
interface RawKnowledgeNode {
  node_id: string;
  tenant_id: string;
  entity_type: string;
  label: string;
  properties?: Record<string, unknown>;
  vector_embedding?: number[] | null;
}

function toKnowledgeNode(raw: RawKnowledgeNode): KnowledgeNode {
  return {
    nodeId: raw.node_id,
    tenantId: raw.tenant_id,
    entityType: raw.entity_type,
    label: raw.label,
    properties: raw.properties ?? {},
  };
}

async function invokeAndMapNodes(capabilityName: string, parameters: Record<string, unknown>): Promise<KnowledgeNode[]> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName,
    parameters,
  });

  if (envelope.status === "SUCCESS") {
    const result = envelope.payload?.result;
    const raw = Array.isArray(result) ? (result as RawKnowledgeNode[]) : [];
    return raw.map(toKnowledgeNode);
  }

  const failure = envelope.errors[0];
  const message = failure?.message ?? `Failed to load ${capabilityName}.`;
  if (failure?.category === "PERMISSION_DENIED") {
    throw new KnowledgeAccessDeniedError(message);
  }
  throw new KnowledgeRequestError(message);
}

/**
 * Calls the existing `kortex.knowledge.graph.list` capability (Slice 4.7,
 * new — see backend `KnowledgeEngine.list_nodes` docstring) through the
 * existing generic IPC path. `kortex.knowledge.query.search` is
 * deliberately never called from this feature: it is broken over the real
 * dict-based IPC path (see backend `test_knowledge_capability_dispatch.py`),
 * a pre-existing defect this slice reports rather than works around.
 */
export async function listKnowledgeNodes(tenantId: string): Promise<KnowledgeNode[]> {
  return invokeAndMapNodes(GRAPH_LIST_CAPABILITY, { tenant_id: tenantId });
}

/** Calls the existing `kortex.knowledge.graph.traverse` capability —
 * explores relationships outward from `nodeId` up to `maxHops` hops. */
export async function traverseKnowledgeGraph(
  nodeId: string,
  tenantId: string,
  maxHops: number,
): Promise<KnowledgeNode[]> {
  return invokeAndMapNodes(GRAPH_TRAVERSE_CAPABILITY, {
    node_id: nodeId,
    tenant_id: tenantId,
    max_hops: maxHops,
  });
}
