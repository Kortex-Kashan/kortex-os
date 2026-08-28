import { useQuery } from "@tanstack/react-query";
import { AiStudioAccessDeniedError, listAiProviders } from "../api";

export const AI_PROVIDERS_QUERY_KEY = ["ai-studio", "providers"] as const;

/**
 * Server-derived state for the AI Studio provider registry, per ADR-0002
 * §12 (TanStack Query owns all server-derived state). Exposes exactly
 * TanStack Query's own loading/success/error/refetch surface —
 * `AiStudioApp` derives "empty" itself from `data.length === 0`.
 */
export function useAiProviders() {
  return useQuery({
    queryKey: AI_PROVIDERS_QUERY_KEY,
    queryFn: listAiProviders,
    // An access-denied result is deterministic — see
    // `features/connectors/hooks/useConnectors.ts`'s identical rationale.
    retry: (failureCount, error) => !(error instanceof AiStudioAccessDeniedError) && failureCount < 1,
  });
}
