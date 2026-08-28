import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listWorkflowDefinitionsMock } = vi.hoisted(() => ({ listWorkflowDefinitionsMock: vi.fn() }));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, listWorkflowDefinitions: listWorkflowDefinitionsMock };
});

import { WorkflowAccessDeniedError } from "../api";
import { useWorkflows } from "./useWorkflows";

beforeEach(() => {
  listWorkflowDefinitionsMock.mockReset();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useWorkflows", () => {
  it("starts pending, then resolves to the definition list on success", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([{ id: "wf_demo", name: "Demo Workflow" }]);

    const { result } = renderHook(() => useWorkflows(), { wrapper });

    expect(result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("resolves to an empty array for an empty registry", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useWorkflows(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  it("surfaces a thrown WorkflowAccessDeniedError as the query error without retrying", async () => {
    listWorkflowDefinitionsMock.mockRejectedValueOnce(new WorkflowAccessDeniedError("denied"));

    const { result } = renderHook(() => useWorkflows(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(WorkflowAccessDeniedError);
    expect(listWorkflowDefinitionsMock).toHaveBeenCalledTimes(1);
  });

  it("supports refetch() as the refresh/retry action", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([]);
    listWorkflowDefinitionsMock.mockResolvedValueOnce([{ id: "wf_demo" }]);

    const { result } = renderHook(() => useWorkflows(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);

    await result.current.refetch();

    await waitFor(() => expect(result.current.data).toHaveLength(1));
  });
});
