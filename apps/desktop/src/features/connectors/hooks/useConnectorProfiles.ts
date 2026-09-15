import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ConnectorAccessDeniedError,
  deleteConnectorProfile,
  disconnectIntegration,
  listConnectorProfiles,
  registerConnectorProfile,
  registerIntegrationConnectorProfile,
} from "../api";
import type { CreateConnectionPayload } from "../types";

/** M7.3: server-derived state for the tenant's own connector profiles
 * ("Connections"). Not keyed by an event topic (same reasoning as
 * `useConnectors.ts` — no Connector Engine event is wired to trigger an
 * automatic refresh in this milestone); mutations below invalidate this
 * query explicitly instead. */
export const CONNECTOR_PROFILES_QUERY_KEY = ["connectors", "profiles"] as const;

export function useConnectorProfiles() {
  return useQuery({
    queryKey: CONNECTOR_PROFILES_QUERY_KEY,
    queryFn: listConnectorProfiles,
    retry: (failureCount, error) => !(error instanceof ConnectorAccessDeniedError) && failureCount < 1,
  });
}

export function useRegisterConnectorProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateConnectionPayload) => registerConnectorProfile(payload),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: CONNECTOR_PROFILES_QUERY_KEY });
    },
  });
}

export function useDeleteConnectorProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) => deleteConnectorProfile(profileId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: CONNECTOR_PROFILES_QUERY_KEY });
    },
  });
}

export interface RegisterIntegrationConnectionInput {
  profileId: string;
  name: string;
  driverId: string;
  secretHandle: string;
  integrationProvider: string;
  options?: Record<string, unknown>;
}

/** Integration Hub M2: registers a connector profile whose credential an
 * OAuth flow (e.g. `completeGitHubOAuth`) already wrote — see
 * `registerIntegrationConnectorProfile` in `api.ts` for why this is a
 * distinct call from `useRegisterConnectorProfile` above. */
export function useRegisterIntegrationConnectorProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: RegisterIntegrationConnectionInput) =>
      registerIntegrationConnectorProfile(
        input.profileId,
        input.name,
        input.driverId,
        input.secretHandle,
        input.integrationProvider,
        input.options,
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: CONNECTOR_PROFILES_QUERY_KEY });
    },
  });
}

/** Integration Hub M2: disconnects a connected integration in one
 * backend-authoritative call (`kortex.connector.integration.disconnect`) —
 * never followed by a separate `useDeleteConnectorProfile` call for this
 * purpose (see `disconnectIntegration` in `api.ts`). */
export function useDisconnectIntegration() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) => disconnectIntegration(profileId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: CONNECTOR_PROFILES_QUERY_KEY });
    },
  });
}
