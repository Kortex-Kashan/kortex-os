import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

import {
  KnowledgeAccessDeniedError,
  KnowledgeRequestError,
  listKnowledgeNodes,
  traverseKnowledgeGraph,
} from "./knowledgeApi";

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

describe("listKnowledgeNodes", () => {
  it("calls the kortex.knowledge.graph.list capability with the given tenant_id", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await listKnowledgeNodes("tenant-1");

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.knowledge.graph.list",
        parameters: { tenant_id: "tenant-1" },
      }),
    );
  });

  it("maps an empty registry to an empty array", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    expect(await listKnowledgeNodes("tenant-1")).toEqual([]);
  });

  it("maps the raw snake_case KnowledgeNode wire shape into a typed, camelCase KnowledgeNode", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          node_id: "node-1",
          tenant_id: "tenant-1",
          entity_type: "Concept",
          label: "Distributed Systems",
          properties: { difficulty: "advanced" },
          vector_embedding: [0.1, 0.2, 0.3],
        },
      ]),
    );

    const [node] = await listKnowledgeNodes("tenant-1");

    expect(node).toEqual({
      nodeId: "node-1",
      tenantId: "tenant-1",
      entityType: "Concept",
      label: "Distributed Systems",
      properties: { difficulty: "advanced" },
    });
  });

  it("never surfaces vector_embedding, even though the raw wire object carries it", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          node_id: "node-1",
          tenant_id: "tenant-1",
          entity_type: "Concept",
          label: "Distributed Systems",
          vector_embedding: [0.123456, 0.654321],
        },
      ]),
    );

    const [node] = await listKnowledgeNodes("tenant-1");

    expect(JSON.stringify(node)).not.toMatch(/0\.123456/);
  });

  it("throws KnowledgeAccessDeniedError on a PERMISSION_DENIED failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "denied"));

    await expect(listKnowledgeNodes("tenant-1")).rejects.toBeInstanceOf(KnowledgeAccessDeniedError);
  });

  it("throws KnowledgeRequestError on any other failure category", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("EXECUTION_FAILED", "boom"));

    await expect(listKnowledgeNodes("tenant-1")).rejects.toBeInstanceOf(KnowledgeRequestError);
  });
});

describe("traverseKnowledgeGraph", () => {
  it("calls the kortex.knowledge.graph.traverse capability with node_id/tenant_id/max_hops", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await traverseKnowledgeGraph("node-1", "tenant-1", 2);

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.knowledge.graph.traverse",
        parameters: { node_id: "node-1", tenant_id: "tenant-1", max_hops: 2 },
      }),
    );
  });

  it("maps returned nodes the same way as listKnowledgeNodes", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([{ node_id: "node-2", tenant_id: "tenant-1", entity_type: "Concept", label: "Consensus" }]),
    );

    const [node] = await traverseKnowledgeGraph("node-1", "tenant-1", 2);

    expect(node).toEqual({
      nodeId: "node-2",
      tenantId: "tenant-1",
      entityType: "Concept",
      label: "Consensus",
      properties: {},
    });
  });

  it("throws KnowledgeAccessDeniedError on a PERMISSION_DENIED failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "denied"));

    await expect(traverseKnowledgeGraph("node-1", "tenant-1", 2)).rejects.toBeInstanceOf(
      KnowledgeAccessDeniedError,
    );
  });
});
