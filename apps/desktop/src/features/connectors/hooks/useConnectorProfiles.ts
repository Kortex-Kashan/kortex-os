import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ConnectorAccessDeniedError,
  deleteConnectorProfile,
  listConnectorProfiles,
  registerConnectorProfile,
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
