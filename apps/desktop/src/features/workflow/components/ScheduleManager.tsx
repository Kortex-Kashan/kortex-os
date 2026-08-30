/**
 * Schedule Manager — durable schedule registry with create/pause/resume/cancel/trigger actions.
 *
 * M5-A6: `WorkflowSchedule` no longer carries `workflowName`, `description`,
 * `maxRuns`, or `createdAt` — none of those exist on the real
 * `kortex.workflow.schedule.*` responses, and the create form's previous
 * `{workflowId, cronExpression, description, maxRuns}` payload omitted the
 * two fields `create_schedule` actually requires (`name`, `definitionId`),
 * so every real submission threw a backend `TypeError`. The corrected form
 * also supports all three real schedule types (CRON/INTERVAL/ONCE), not
 * only cron.
 *
 * M5-A7: every mutation now confirms (for destructive actions) and surfaces
 * failures inline instead of a bare `.then(onRefresh)` with no `.catch`.
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
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
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
import { usePagedList } from "../hooks/usePagedList";
import { WorkflowAccessDeniedError } from "../api";
import type { CreateSchedulePayload, ScheduleType, WorkflowSchedule } from "../types";
import { ScheduleStatusBadge, formatDateTime } from "./StatusBadge";
import { PaginationControls } from "./PaginationControls";

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function ScheduleManager() {
  const { data, isPending, isError, error, refetch, isFetching } = useSchedules();
  const [createOpen, setCreateOpen] = useState(false);
  const { pageItems, page, pageCount, hasPrev, hasNext, goPrev, goNext } = usePagedList(
    data ?? [],
    10,
  );

  if (isPending) {
    return (
      <section aria-label="Schedule Manager">
        <h3 className="text-heading mb-4 leading-none tracking-tight">Schedules</h3>
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
        <h3 className="text-heading mb-4 leading-none tracking-tight">Schedules</h3>
        <p className="text-sm text-destructive" role="alert">{msg}</p>
      </section>
    );
  }

  const schedules = data ?? [];

  return (
    <section aria-label="Schedule Manager">
      <div className="mb-4 flex items-center justify-between gap-4">
        <h3 className="text-heading leading-none tracking-tight">Schedules</h3>
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
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            New Schedule
          </Button>
        </div>
      </div>

      {schedules.length === 0 ? (
        <p className="text-body text-muted-foreground" role="status">
          No schedules configured.
        </p>
      ) : (
        <>
          <div className="space-y-3" role="list" aria-label="Schedule list">
            {pageItems.map((s) => (
              <ScheduleCard key={s.id} schedule={s} />
            ))}
          </div>
          <PaginationControls
            page={page}
            pageCount={pageCount}
            hasPrev={hasPrev}
            hasNext={hasNext}
            onPrev={goPrev}
            onNext={goNext}
          />
        </>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Schedule</DialogTitle>
            <DialogDescription>Configure a new durable schedule for a workflow definition.</DialogDescription>
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

function ScheduleCard({ schedule }: { schedule: WorkflowSchedule }) {
  const pause = usePauseSchedule();
  const resume = useResumeSchedule();
  const cancel = useCancelSchedule();
  const trigger = useTriggerScheduleNow();
  const [confirmCancelOpen, setConfirmCancelOpen] = useState(false);

  const isBusy = pause.isPending || resume.isPending || cancel.isPending || trigger.isPending;
  const activeError = pause.error ?? resume.error ?? trigger.error ?? cancel.error;
  const canAct = schedule.status !== "DISABLED" && schedule.status !== "COMPLETED";

  return (
    <>
      <Card role="listitem" aria-label={`Schedule ${schedule.name}`}>
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div className="min-w-0">
              <CardTitle className="text-base">{schedule.name}</CardTitle>
              <CardDescription className="font-mono text-xs">
                {schedule.definitionId} · {schedule.scheduleType}
                {schedule.cronExpression ? ` (${schedule.cronExpression})` : ""}
              </CardDescription>
            </div>
            <ScheduleStatusBadge status={schedule.status} />
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span>Next Run</span>
            <span>{formatDateTime(schedule.nextRunAt)}</span>
            {schedule.lastRunAt !== undefined && (
              <>
                <span>Last Run</span>
                <span>{formatDateTime(schedule.lastRunAt)}</span>
              </>
            )}
            <span>Run Count</span>
            <span>{schedule.runCount}</span>
            <span>Tenant</span>
            <span className="font-mono text-xs">{schedule.tenantId}</span>
          </div>

          {activeError && (
            <p className="text-sm text-destructive" role="alert">
              {activeError instanceof Error ? activeError.message : "Action failed."}
            </p>
          )}

          {canAct && (
            <div className="flex gap-2 flex-wrap pt-1">
              {schedule.status === "ACTIVE" && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={isBusy}
                  onClick={() => {
                    // The rejection is already captured reactively via
                    // `pause.error` (rendered above as `activeError`); this
                    // `.catch` only prevents an unhandled-promise-rejection
                    // report for the same failure, it does not swallow it.
                    void pause.mutateAsync(schedule.id).catch(() => {});
                  }}
                  aria-label={`Pause schedule ${schedule.name}`}
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
                    void resume.mutateAsync(schedule.id).catch(() => {});
                  }}
                  aria-label={`Resume schedule ${schedule.name}`}
                >
                  Resume
                </Button>
              )}
              <Button
                variant="secondary"
                size="sm"
                disabled={isBusy}
                onClick={() => {
                  void trigger.mutateAsync(schedule.id).catch(() => {});
                }}
                aria-label={`Trigger schedule ${schedule.name} now`}
              >
                Trigger Now
              </Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={isBusy}
                onClick={() => setConfirmCancelOpen(true)}
                aria-label={`Cancel schedule ${schedule.name}`}
              >
                Cancel
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={confirmCancelOpen} onOpenChange={setConfirmCancelOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel schedule "{schedule.name}"?</DialogTitle>
            <DialogDescription>
              This permanently disables the schedule. It cannot be resumed afterward — a new schedule
              would need to be created.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setConfirmCancelOpen(false)} disabled={cancel.isPending}>
              Keep Schedule
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={cancel.isPending}
              onClick={() => {
                void cancel.mutateAsync(schedule.id).then(() => setConfirmCancelOpen(false));
              }}
            >
              {cancel.isPending ? "Cancelling…" : "Confirm Cancel"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
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
  const [name, setName] = useState("");
  const [definitionId, setDefinitionId] = useState("");
  const [scheduleType, setScheduleType] = useState<ScheduleType>("INTERVAL");
  const [cron, setCron] = useState("");
  const [intervalSeconds, setIntervalSeconds] = useState("3600");
  const [maxRuns, setMaxRuns] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim() || !definitionId.trim()) {
      setError("Name and Definition ID are required.");
      return;
    }
    if (scheduleType === "CRON" && !cron.trim()) {
      setError("A cron expression is required for CRON schedules.");
      return;
    }
    setError(null);
    const payload: CreateSchedulePayload = {
      name: name.trim(),
      definitionId: definitionId.trim(),
      scheduleType,
      maxRuns: maxRuns ? parseInt(maxRuns, 10) : null,
    };
    if (scheduleType === "CRON") payload.cronExpression = cron.trim();
    if (scheduleType === "INTERVAL") payload.intervalSeconds = parseInt(intervalSeconds, 10);

    try {
      await create.mutateAsync(payload);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create schedule.");
    }
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
      <div>
        <Label htmlFor="sched-name">Schedule Name *</Label>
        <Input
          id="sched-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="daily-invoice-sync"
          disabled={create.isPending}
          aria-required="true"
        />
      </div>
      <div>
        <Label htmlFor="sched-definition-id">Workflow Definition ID *</Label>
        <Input
          id="sched-definition-id"
          value={definitionId}
          onChange={(e) => setDefinitionId(e.target.value)}
          placeholder="definition-id-here"
          disabled={create.isPending}
          aria-required="true"
        />
      </div>
      <div>
        <Label htmlFor="sched-type">Schedule Type *</Label>
        <Select value={scheduleType} onValueChange={(v) => setScheduleType(v as ScheduleType)}>
          <SelectTrigger id="sched-type" disabled={create.isPending}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="INTERVAL">Interval</SelectItem>
            <SelectItem value="CRON">Cron</SelectItem>
            <SelectItem value="ONCE">Once (fires on next tick)</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {scheduleType === "CRON" && (
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
      )}
      {scheduleType === "INTERVAL" && (
        <div>
          <Label htmlFor="sched-interval">Interval (seconds) *</Label>
          <Input
            id="sched-interval"
            type="number"
            min="1"
            value={intervalSeconds}
            onChange={(e) => setIntervalSeconds(e.target.value)}
            disabled={create.isPending}
            aria-required="true"
          />
        </div>
      )}
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
          {create.isPending ? "Creating…" : "Create"}
        </Button>
      </div>
    </form>
  );
}
