import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listAiProvidersMock } = vi.hoisted(() => ({ listAiProvidersMock: vi.fn() }));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, listAiProviders: listAiProvidersMock };
});

import { AiStudioAccessDeniedError } from "../api";
import { useAiProviders } from "./useAiProviders";

beforeEach(() => {
  listAiProvidersMock.mockReset();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useAiProviders", () => {
  it("starts pending, then resolves to the provider list on success", async () => {
    listAiProvidersMock.mockResolvedValueOnce([{ providerId: "ollama-local" }]);

    const { result } = renderHook(() => useAiProviders(), { wrapper });

    expect(result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("resolves to an empty array for an empty registry", async () => {
    listAiProvidersMock.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useAiProviders(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  it("surfaces a thrown AiStudioAccessDeniedError as the query error without retrying", async () => {
    listAiProvidersMock.mockRejectedValueOnce(new AiStudioAccessDeniedError("denied"));

    const { result } = renderHook(() => useAiProviders(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(AiStudioAccessDeniedError);
    expect(listAiProvidersMock).toHaveBeenCalledTimes(1);
  });

  it("supports refetch() as the refresh/retry action", async () => {
    listAiProvidersMock.mockResolvedValueOnce([]);
    listAiProvidersMock.mockResolvedValueOnce([{ providerId: "ollama-local" }]);

    const { result } = renderHook(() => useAiProviders(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);

    await result.current.refetch();

    await waitFor(() => expect(result.current.data).toHaveLength(1));
  });
});
