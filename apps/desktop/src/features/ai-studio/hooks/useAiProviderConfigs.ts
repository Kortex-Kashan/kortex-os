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
  return useQuery({
    queryKey: AI_PROVIDER_CONFIGS_QUERY_KEY,
    queryFn: listAiProviderConfigs,
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
  return useMutation({
    mutationFn: (input: AiProviderConfigureInput) => configureAiProvider(input),
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
 * Invalidates nothing: a connection test is a read that changes no server
 * state. Its result (including discovered models) is consumed directly from
 * the mutation's own `data`, which is what keeps the discovered-model list
 * scoped to the test that produced it rather than becoming a second,
 * silently-staleable model cache.
 */
export function useTestAiProviderConnection() {
  return useMutation({
    mutationFn: (providerId: string) => testAiProviderConnection(providerId),
  });
}

/** Remove one provider configuration, then refresh the same keys as configure. */
export function useRemoveAiProviderConfig() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (providerId: string) => removeAiProviderConfig(providerId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: AI_PROVIDER_CONFIGS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: AI_PROVIDERS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: AI_MODELS_QUERY_KEY });
    },
  });
}
