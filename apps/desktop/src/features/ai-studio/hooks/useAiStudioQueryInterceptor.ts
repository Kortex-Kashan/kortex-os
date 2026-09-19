import { useCallback } from "react";
import { useOptionalAuth } from "@/auth/AuthProvider";
import { AiStudioAccessDeniedError, AiStudioRequestError } from "../api";

/**
 * Shared interceptor for AI Studio queries and mutations (TanStack Query v5).
 *
 * Catches typed AI Studio errors (`AiStudioAccessDeniedError` / `AiStudioRequestError`),
 * extracts their attached `IpcResultEnvelope`, and forwards it to `reportIpcResult`.
 *
 * If `reportIpcResult` classifies the failure as `UNAUTHORIZED` (HTTP 401),
 * the session is invalidated and the app returns to `LoginScreen`.
 * If 403 (FORBIDDEN), the user remains authenticated and the error is surfaced inline.
 * Other errors (4xx, 5xx) propagate as ordinary query/mutation errors.
 *
 * TanStack Query v5 compliant: wraps queryFn/mutationFn rather than introducing
 * deprecated query-level `onError` handlers.
 */
export function useAiStudioQueryInterceptor() {
  const auth = useOptionalAuth();
  const reportIpcResult = auth?.reportIpcResult;

  const interceptQuery = useCallback(
    <T>(queryFn: () => Promise<T>) =>
      async (): Promise<T> => {
        try {
          return await queryFn();
        } catch (error) {
          if (
            (error instanceof AiStudioAccessDeniedError ||
              error instanceof AiStudioRequestError) &&
            error.envelope
          ) {
            reportIpcResult?.(error.envelope);
          }
          throw error;
        }
      },
    [reportIpcResult],
  );

  const interceptMutation = useCallback(
    <TArgs, TResult>(mutationFn: (args: TArgs) => Promise<TResult>) =>
      async (args: TArgs): Promise<TResult> => {
        try {
          return await mutationFn(args);
        } catch (error) {
          if (
            (error instanceof AiStudioAccessDeniedError ||
              error instanceof AiStudioRequestError) &&
            error.envelope
          ) {
            reportIpcResult?.(error.envelope);
          }
          throw error;
        }
      },
    [reportIpcResult],
  );

  return { interceptQuery, interceptMutation };
}
