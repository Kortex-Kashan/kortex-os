import { useQuery } from "@tanstack/react-query";
import { DocumentAccessDeniedError, listDocumentTemplates } from "../documentsApi";

export const DOCUMENT_TEMPLATES_QUERY_KEY = ["document-knowledge", "document-templates"] as const;

/** See `useDocumentAdapters.ts` for the identical rationale behind every
 * choice here. */
export function useDocumentTemplates() {
  return useQuery({
    queryKey: DOCUMENT_TEMPLATES_QUERY_KEY,
    queryFn: listDocumentTemplates,
    retry: (failureCount, error) => !(error instanceof DocumentAccessDeniedError) && failureCount < 1,
  });
}
