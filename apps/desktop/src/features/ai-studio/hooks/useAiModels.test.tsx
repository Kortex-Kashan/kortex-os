import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listAiModelsMock } = vi.hoisted(() => ({ listAiModelsMock: vi.fn() }));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, listAiModels: listAiModelsMock };
});

import { AiStudioAccessDeniedError } from "../api";
import { useAiModels } from "./useAiModels";

beforeEach(() => {
  listAiModelsMock.mockReset();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useAiModels", () => {
  it("starts pending, then resolves to the model list on success", async () => {
    listAiModelsMock.mockResolvedValueOnce([{ modelId: "llama3" }]);

    const { result } = renderHook(() => useAiModels(), { wrapper });

    expect(result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("resolves to an empty array for an empty registry", async () => {
    listAiModelsMock.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useAiModels(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  it("surfaces a thrown AiStudioAccessDeniedError as the query error without retrying", async () => {
    listAiModelsMock.mockRejectedValueOnce(new AiStudioAccessDeniedError("denied"));

    const { result } = renderHook(() => useAiModels(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(AiStudioAccessDeniedError);
    expect(listAiModelsMock).toHaveBeenCalledTimes(1);
  });

  it("supports refetch() as the refresh/retry action", async () => {
    listAiModelsMock.mockResolvedValueOnce([]);
    listAiModelsMock.mockResolvedValueOnce([{ modelId: "llama3" }]);

    const { result } = renderHook(() => useAiModels(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);

    await result.current.refetch();

    await waitFor(() => expect(result.current.data).toHaveLength(1));
  });
});
