import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listDocumentTemplatesMock } = vi.hoisted(() => ({ listDocumentTemplatesMock: vi.fn() }));

vi.mock("../documentsApi", async () => {
  const actual = await vi.importActual<typeof import("../documentsApi")>("../documentsApi");
  return { ...actual, listDocumentTemplates: listDocumentTemplatesMock };
});

import { DocumentAccessDeniedError } from "../documentsApi";
import { useDocumentTemplates } from "./useDocumentTemplates";

beforeEach(() => {
  listDocumentTemplatesMock.mockReset();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useDocumentTemplates", () => {
  it("starts pending, then resolves to the template list on success", async () => {
    listDocumentTemplatesMock.mockResolvedValueOnce([{ templateId: "t1" }]);

    const { result } = renderHook(() => useDocumentTemplates(), { wrapper });

    expect(result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("resolves to an empty array for an empty registry", async () => {
    listDocumentTemplatesMock.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useDocumentTemplates(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  it("surfaces a thrown DocumentAccessDeniedError as the query error without retrying", async () => {
    listDocumentTemplatesMock.mockRejectedValueOnce(new DocumentAccessDeniedError("denied"));

    const { result } = renderHook(() => useDocumentTemplates(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(DocumentAccessDeniedError);
    expect(listDocumentTemplatesMock).toHaveBeenCalledTimes(1);
  });

  it("supports refetch() as the refresh/retry action", async () => {
    listDocumentTemplatesMock.mockResolvedValueOnce([]);
    listDocumentTemplatesMock.mockResolvedValueOnce([{ templateId: "t1" }]);

    const { result } = renderHook(() => useDocumentTemplates(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);

    await result.current.refetch();

    await waitFor(() => expect(result.current.data).toHaveLength(1));
  });
});
