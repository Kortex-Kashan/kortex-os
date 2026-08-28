import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listKnowledgeNodesMock, useAuthMock } = vi.hoisted(() => ({
  listKnowledgeNodesMock: vi.fn(),
  useAuthMock: vi.fn(),
}));

vi.mock("../knowledgeApi", async () => {
  const actual = await vi.importActual<typeof import("../knowledgeApi")>("../knowledgeApi");
  return { ...actual, listKnowledgeNodes: listKnowledgeNodesMock };
});

vi.mock("@/auth/AuthProvider", () => ({
  useAuth: useAuthMock,
}));

import { KnowledgeAccessDeniedError } from "../knowledgeApi";
import { useKnowledgeNodes } from "./useKnowledgeNodes";

const AUTHENTICATED_STATE = {
  state: {
    status: "AUTHENTICATED" as const,
    identity: { principalId: "alice", principalType: "USER", tenantId: "tenant-1", roles: [] },
  },
};

beforeEach(() => {
  listKnowledgeNodesMock.mockReset();
  useAuthMock.mockReset();
  useAuthMock.mockReturnValue(AUTHENTICATED_STATE);
});

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useKnowledgeNodes", () => {
  it("calls listKnowledgeNodes with the current session's tenantId", async () => {
    listKnowledgeNodesMock.mockResolvedValueOnce([]);

    renderHook(() => useKnowledgeNodes(), { wrapper });

    await waitFor(() => expect(listKnowledgeNodesMock).toHaveBeenCalledWith("tenant-1"));
  });

  it("starts pending, then resolves to the node list on success", async () => {
    listKnowledgeNodesMock.mockResolvedValueOnce([{ nodeId: "n1" }]);

    const { result } = renderHook(() => useKnowledgeNodes(), { wrapper });

    expect(result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("resolves to an empty array for an empty graph", async () => {
    listKnowledgeNodesMock.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useKnowledgeNodes(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  it("surfaces a thrown KnowledgeAccessDeniedError as the query error without retrying", async () => {
    listKnowledgeNodesMock.mockRejectedValueOnce(new KnowledgeAccessDeniedError("denied"));

    const { result } = renderHook(() => useKnowledgeNodes(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(KnowledgeAccessDeniedError);
    expect(listKnowledgeNodesMock).toHaveBeenCalledTimes(1);
  });

  it("stays disabled and never calls listKnowledgeNodes when not authenticated", () => {
    useAuthMock.mockReturnValue({ state: { status: "UNAUTHENTICATED" } });

    const { result } = renderHook(() => useKnowledgeNodes(), { wrapper });

    expect(result.current.isPending).toBe(true);
    expect(result.current.fetchStatus).toBe("idle");
    expect(listKnowledgeNodesMock).not.toHaveBeenCalled();
  });

  it("supports refetch() as the refresh/retry action", async () => {
    listKnowledgeNodesMock.mockResolvedValueOnce([]);
    listKnowledgeNodesMock.mockResolvedValueOnce([{ nodeId: "n1" }]);

    const { result } = renderHook(() => useKnowledgeNodes(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);

    await result.current.refetch();

    await waitFor(() => expect(result.current.data).toHaveLength(1));
  });
});
