import { useQuery } from "@tanstack/react-query";
import { AiStudioAccessDeniedError, listAiModels } from "../api";

export const AI_MODELS_QUERY_KEY = ["ai-studio", "models"] as const;

/** Server-derived state for the AI Studio model registry — see
 * `useAiProviders.ts` for the identical rationale behind every choice here. */
export function useAiModels() {
  return useQuery({
    queryKey: AI_MODELS_QUERY_KEY,
    queryFn: listAiModels,
    retry: (failureCount, error) => !(error instanceof AiStudioAccessDeniedError) && failureCount < 1,
  });
}
