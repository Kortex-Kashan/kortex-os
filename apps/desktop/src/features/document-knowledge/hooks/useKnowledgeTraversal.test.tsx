import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { traverseKnowledgeGraphMock, useAuthMock } = vi.hoisted(() => ({
  traverseKnowledgeGraphMock: vi.fn(),
  useAuthMock: vi.fn(),
}));

vi.mock("../knowledgeApi", async () => {
  const actual = await vi.importActual<typeof import("../knowledgeApi")>("../knowledgeApi");
  return { ...actual, traverseKnowledgeGraph: traverseKnowledgeGraphMock };
});

vi.mock("@/auth/AuthProvider", () => ({
  useAuth: useAuthMock,
}));

import { useKnowledgeTraversal } from "./useKnowledgeTraversal";

const AUTHENTICATED_STATE = {
  state: {
    status: "AUTHENTICATED" as const,
    identity: { principalId: "alice", principalType: "USER", tenantId: "tenant-1", roles: [] },
  },
};

beforeEach(() => {
  traverseKnowledgeGraphMock.mockReset();
  useAuthMock.mockReset();
  useAuthMock.mockReturnValue(AUTHENTICATED_STATE);
});

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useKnowledgeTraversal", () => {
  it("stays disabled and never calls traverseKnowledgeGraph while no node is selected", () => {
    const { result } = renderHook(() => useKnowledgeTraversal(null), { wrapper });

    expect(result.current.fetchStatus).toBe("idle");
    expect(traverseKnowledgeGraphMock).not.toHaveBeenCalled();
  });

  it("calls traverseKnowledgeGraph with the selected node once one is provided", async () => {
    traverseKnowledgeGraphMock.mockResolvedValueOnce([]);

    renderHook(() => useKnowledgeTraversal("node-1"), { wrapper });

    await waitFor(() => expect(traverseKnowledgeGraphMock).toHaveBeenCalledWith("node-1", "tenant-1", 2));
  });

  it("resolves to related nodes on success", async () => {
    traverseKnowledgeGraphMock.mockResolvedValueOnce([{ nodeId: "node-2" }]);

    const { result } = renderHook(() => useKnowledgeTraversal("node-1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("resolves to an empty array when no relationships exist", async () => {
    traverseKnowledgeGraphMock.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useKnowledgeTraversal("node-1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });
});
