import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AiStudioAccessDeniedError,
  configureAiProvider,
  listAiProviderConfigs,
  removeAiProviderConfig,
  testAiProviderConnection,
} from "../api";
import type { AiProviderConfigureInput } from "../types";
import { AI_MODELS_QUERY_KEY } from "./useAiModels";
import { AI_PROVIDERS_QUERY_KEY } from "./useAiProviders";
import { useAiStudioQueryInterceptor } from "./useAiStudioQueryInterceptor";

/**
 * Server-derived state for the calling tenant's provider configurations,
 * per ADR-0002 §12 (TanStack Query owns all server-derived state).
 *
 * Configuration state is deliberately NOT mirrored into component state or
 * `localStorage`: it is tenant-scoped server state that another session can
 * change, and the only durable record of it is `ai_provider_configs`. The
 * mutations below invalidate this key rather than patching a local copy, so
 * what the UI shows is always what the backend last returned.
 */
export const AI_PROVIDER_CONFIGS_QUERY_KEY = ["ai-studio", "provider-configs"] as const;

export function useAiProviderConfigs() {
  const { interceptQuery } = useAiStudioQueryInterceptor();

  return useQuery({
    queryKey: AI_PROVIDER_CONFIGS_QUERY_KEY,
    queryFn: interceptQuery(listAiProviderConfigs),
    // An access-denied result is deterministic -- retrying cannot change
    // it. Matches `useAiProviders`/`useConnectors`.
    retry: (failureCount, error) => !(error instanceof AiStudioAccessDeniedError) && failureCount < 1,
  });
}

/**
 * Configure (or reconfigure) one provider.
 *
 * Invalidates the provider registry alongside the configuration list: a
 * newly credentialed provider changes what `kortex.ai.provider.list` and
 * `kortex.ai.model.list` can report about it, so leaving those caches in
 * place would show a provider as configured while its registry entry still
 * described the unconfigured state.
 */
export function useConfigureAiProvider() {
  const client = useQueryClient();
  const { interceptMutation } = useAiStudioQueryInterceptor();

  return useMutation({
    mutationFn: interceptMutation((input: AiProviderConfigureInput) => configureAiProvider(input)),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: AI_PROVIDER_CONFIGS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: AI_PROVIDERS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: AI_MODELS_QUERY_KEY });
    },
  });
}

/**
 * Test one provider's stored credential.
 *
 * A successful test with discovered models is no longer a pure read: the
 * backend now persists that discovery into the tenant's durable model
 * catalog (`AIProviderModelCatalogStore`), so `kortex.ai.model.list` can
 * report it after this component unmounts, after navigation, and after a
 * reload — fixing the defect where a live 41-model Gemini catalog collapsed
 * back to the small static fallback list the moment the user left this tab.
 * Invalidating `AI_MODELS_QUERY_KEY` here is what makes that persisted
 * catalog visible without a manual refresh. The mutation's own `data` is
 * still consumed directly for the *immediate* "N models available" feedback
 * in this same session — invalidation is what makes that catalog survive
 * beyond it.
 */
export function useTestAiProviderConnection() {
  const client = useQueryClient();
  const { interceptMutation } = useAiStudioQueryInterceptor();

  return useMutation({
    mutationFn: interceptMutation(({ providerId, apiKey }: { providerId: string; apiKey?: string }) =>
      testAiProviderConnection(providerId, apiKey),
    ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: AI_MODELS_QUERY_KEY });
    },
  });
}

/** Remove one provider configuration, then refresh the same keys as configure. */
export function useRemoveAiProviderConfig() {
  const client = useQueryClient();
  const { interceptMutation } = useAiStudioQueryInterceptor();

  return useMutation({
    mutationFn: interceptMutation((providerId: string) => removeAiProviderConfig(providerId)),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: AI_PROVIDER_CONFIGS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: AI_PROVIDERS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: AI_MODELS_QUERY_KEY });
    },
  });
}

