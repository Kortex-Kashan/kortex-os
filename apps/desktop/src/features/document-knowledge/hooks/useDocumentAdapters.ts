import { useQuery } from "@tanstack/react-query";
import { DocumentAccessDeniedError, listDocumentAdapters } from "../documentsApi";

export const DOCUMENT_ADAPTERS_QUERY_KEY = ["document-knowledge", "document-adapters"] as const;

/** Server-derived state for the Document adapter registry, per ADR-0002
 * §12 (TanStack Query owns all server-derived state) — matches every
 * prior feature module's hook convention exactly. */
export function useDocumentAdapters() {
  return useQuery({
    queryKey: DOCUMENT_ADAPTERS_QUERY_KEY,
    queryFn: listDocumentAdapters,
    // An access-denied result is deterministic — see
    // `features/connectors/hooks/useConnectors.ts`'s identical rationale.
    retry: (failureCount, error) => !(error instanceof DocumentAccessDeniedError) && failureCount < 1,
  });
}
