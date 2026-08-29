import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelSchedule,
  createSchedule,
  listSchedules,
  pauseSchedule,
  resumeSchedule,
  triggerScheduleNow,
  WorkflowAccessDeniedError,
} from "../api";
import type { CreateSchedulePayload } from "../types";

export const SCHEDULES_QUERY_KEY = ["workflow", "schedules"] as const;

/** Fetches durable cron schedules. Polls every 30 seconds for next-run updates. */
export function useSchedules() {
  return useQuery({
    queryKey: SCHEDULES_QUERY_KEY,
    queryFn: listSchedules,
    retry: (failureCount, error) =>
      !(error instanceof WorkflowAccessDeniedError) && failureCount < 1,
    refetchInterval: 30_000,
  });
}

export function useCreateSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateSchedulePayload) => createSchedule(payload),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
    },
  });
}

export function usePauseSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (scheduleId: string) => pauseSchedule(scheduleId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
    },
  });
}

export function useResumeSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (scheduleId: string) => resumeSchedule(scheduleId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
    },
  });
}

export function useCancelSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (scheduleId: string) => cancelSchedule(scheduleId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
    },
  });
}

export function useTriggerScheduleNow() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (scheduleId: string) => triggerScheduleNow(scheduleId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
    },
  });
}
