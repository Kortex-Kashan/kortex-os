/**
 * Schedule Manager — durable cron schedule registry with create/pause/resume/cancel/trigger actions.
 */

import { useState } from "react";
import type { FormEvent } from "react";
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Skeleton,
} from "@kortex/design-system";
import {
  useCancelSchedule,
  useCreateSchedule,
  usePauseSchedule,
  useResumeSchedule,
  useSchedules,
  useTriggerScheduleNow,
} from "../hooks/useSchedules";
import { WorkflowAccessDeniedError } from "../api";
import type { WorkflowSchedule } from "../types";
import { ScheduleStatusBadge, formatDateTime } from "./StatusBadge";

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function ScheduleManager() {
  const { data, isPending, isError, error, refetch, isFetching } = useSchedules();
  const [createOpen, setCreateOpen] = useState(false);

  if (isPending) {
    return (
      <section aria-label="Schedule Manager">
        <h3 className="text-heading mb-4 leading-none tracking-tight">Cron Schedules</h3>
        <div className="space-y-3" role="status" aria-label="Loading schedules">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      </section>
    );
  }

  if (isError) {
    const msg = error instanceof WorkflowAccessDeniedError
      ? `Access denied: ${error.message}`
      : error.message;
    return (
      <section aria-label="Schedule Manager">
        <h3 className="text-heading mb-4 leading-none tracking-tight">Cron Schedules</h3>
        <p className="text-sm text-destructive" role="alert">{msg}</p>
      </section>
    );
  }

  const schedules = data ?? [];

  return (
    <section aria-label="Schedule Manager">
      <div className="mb-4 flex items-center justify-between gap-4">
        <h3 className="text-heading leading-none tracking-tight">Cron Schedules</h3>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refetch()}
            disabled={isFetching}
            aria-label="Refresh schedules"
          >
            Refresh
          </Button>
          <Button size="sm" onClick={() => setCreateOpen(true)} aria-label="Create new schedule">
            New Schedule
          </Button>
        </div>
      </div>

      {schedules.length === 0 ? (
        <p className="text-body text-muted-foreground" role="status">
          No schedules configured.
        </p>
      ) : (
        <div className="space-y-3" role="list" aria-label="Cron schedule list">
          {schedules.map((s) => (
            <ScheduleCard key={s.scheduleId} schedule={s} onRefresh={() => void refetch()} />
          ))}
        </div>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Schedule</DialogTitle>
            <DialogDescription>Configure a new durable cron schedule for a workflow.</DialogDescription>
          </DialogHeader>
          <CreateScheduleForm
            onSuccess={() => { setCreateOpen(false); void refetch(); }}
            onCancel={() => setCreateOpen(false)}
          />
        </DialogContent>
      </Dialog>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Schedule Card
// ---------------------------------------------------------------------------

function ScheduleCard({ schedule, onRefresh }: { schedule: WorkflowSchedule; onRefresh: () => void }) {
  const pause = usePauseSchedule();
  const resume = useResumeSchedule();
  const cancel = useCancelSchedule();
  const trigger = useTriggerScheduleNow();

  const isBusy = pause.isPending || resume.isPending || cancel.isPending || trigger.isPending;

  return (
    <Card role="listitem" aria-label={`Schedule ${schedule.scheduleId}`}>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <CardTitle className="text-base">{schedule.workflowName || schedule.workflowId}</CardTitle>
            <CardDescription className="font-mono text-xs">
              {schedule.cronExpression}
            </CardDescription>
          </div>
          <ScheduleStatusBadge status={schedule.status} />
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>Next Run</span>
          <span>{formatDateTime(schedule.nextRunAt)}</span>
          <span>Last Run</span>
          <span>{formatDateTime(schedule.lastRunAt)}</span>
          <span>Run Count</span>
          <span>{schedule.runCount}{schedule.maxRuns ? ` / ${schedule.maxRuns}` : ""}</span>
          {schedule.description && (
            <>
              <span>Description</span>
              <span>{schedule.description}</span>
            </>
          )}
        </div>

        {schedule.status !== "CANCELLED" && (
          <div className="flex gap-2 flex-wrap pt-1">
            {schedule.status === "ACTIVE" && (
              <Button
                variant="outline"
                size="sm"
                disabled={isBusy}
                onClick={() => {
                  void pause.mutateAsync(schedule.scheduleId).then(onRefresh);
                }}
                aria-label={`Pause schedule ${schedule.scheduleId}`}
              >
                Pause
              </Button>
            )}
            {schedule.status === "PAUSED" && (
              <Button
                variant="outline"
                size="sm"
                disabled={isBusy}
                onClick={() => {
                  void resume.mutateAsync(schedule.scheduleId).then(onRefresh);
                }}
                aria-label={`Resume schedule ${schedule.scheduleId}`}
              >
                Resume
              </Button>
            )}
            <Button
              variant="secondary"
              size="sm"
              disabled={isBusy}
              onClick={() => {
                void trigger.mutateAsync(schedule.scheduleId).then(onRefresh);
              }}
              aria-label={`Trigger schedule ${schedule.scheduleId} now`}
            >
              Trigger Now
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={isBusy}
              onClick={() => {
                void cancel.mutateAsync(schedule.scheduleId).then(onRefresh);
              }}
              aria-label={`Cancel schedule ${schedule.scheduleId}`}
            >
              Cancel
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Create Schedule Form
// ---------------------------------------------------------------------------

function CreateScheduleForm({
  onSuccess,
  onCancel,
}: {
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const create = useCreateSchedule();
  const [workflowId, setWorkflowId] = useState("");
  const [cron, setCron] = useState("");
  const [description, setDescription] = useState("");
  const [maxRuns, setMaxRuns] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!workflowId.trim() || !cron.trim()) {
      setError("Workflow ID and cron expression are required.");
      return;
    }
    setError(null);
    try {
      await create.mutateAsync({
        workflowId: workflowId.trim(),
        cronExpression: cron.trim(),
        description: description.trim(),
        maxRuns: maxRuns ? parseInt(maxRuns, 10) : null,
      });
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create schedule.");
    }
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
      <div>
        <Label htmlFor="sched-workflow-id">Workflow ID *</Label>
        <Input
          id="sched-workflow-id"
          value={workflowId}
          onChange={(e) => setWorkflowId(e.target.value)}
          placeholder="workflow-uuid-here"
          disabled={create.isPending}
          aria-required="true"
        />
      </div>
      <div>
        <Label htmlFor="sched-cron">Cron Expression *</Label>
        <Input
          id="sched-cron"
          value={cron}
          onChange={(e) => setCron(e.target.value)}
          placeholder="0 9 * * 1-5"
          disabled={create.isPending}
          aria-required="true"
        />
      </div>
      <div>
        <Label htmlFor="sched-description">Description</Label>
        <Input
          id="sched-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Optional description"
          disabled={create.isPending}
        />
      </div>
      <div>
        <Label htmlFor="sched-max-runs">Max Runs (leave blank for unlimited)</Label>
        <Input
          id="sched-max-runs"
          type="number"
          min="1"
          value={maxRuns}
          onChange={(e) => setMaxRuns(e.target.value)}
          placeholder="Unlimited"
          disabled={create.isPending}
        />
      </div>
      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-2 justify-end">
        <Button variant="outline" size="sm" type="button" onClick={onCancel} disabled={create.isPending}>
          Cancel
        </Button>
        <Button size="sm" type="submit" disabled={create.isPending}>
          Create
        </Button>
      </div>
    </form>
  );
}
