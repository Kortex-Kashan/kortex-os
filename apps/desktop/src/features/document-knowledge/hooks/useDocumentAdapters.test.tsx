import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listDocumentAdaptersMock } = vi.hoisted(() => ({ listDocumentAdaptersMock: vi.fn() }));

vi.mock("../documentsApi", async () => {
  const actual = await vi.importActual<typeof import("../documentsApi")>("../documentsApi");
  return { ...actual, listDocumentAdapters: listDocumentAdaptersMock };
});

import { DocumentAccessDeniedError } from "../documentsApi";
import { useDocumentAdapters } from "./useDocumentAdapters";

beforeEach(() => {
  listDocumentAdaptersMock.mockReset();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useDocumentAdapters", () => {
  it("starts pending, then resolves to the adapter list on success", async () => {
    listDocumentAdaptersMock.mockResolvedValueOnce([{ adapterId: "a1" }]);

    const { result } = renderHook(() => useDocumentAdapters(), { wrapper });

    expect(result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("resolves to an empty array for an empty registry", async () => {
    listDocumentAdaptersMock.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useDocumentAdapters(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  it("surfaces a thrown DocumentAccessDeniedError as the query error without retrying", async () => {
    listDocumentAdaptersMock.mockRejectedValueOnce(new DocumentAccessDeniedError("denied"));

    const { result } = renderHook(() => useDocumentAdapters(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(DocumentAccessDeniedError);
    expect(listDocumentAdaptersMock).toHaveBeenCalledTimes(1);
  });

  it("supports refetch() as the refresh/retry action", async () => {
    listDocumentAdaptersMock.mockResolvedValueOnce([]);
    listDocumentAdaptersMock.mockResolvedValueOnce([{ adapterId: "a1" }]);

    const { result } = renderHook(() => useDocumentAdapters(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);

    await result.current.refetch();

    await waitFor(() => expect(result.current.data).toHaveLength(1));
  });
});
