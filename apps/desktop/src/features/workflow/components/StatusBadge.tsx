/**
 * Status badge variants for workflow domain statuses.
 * Maps backend status strings to design-system Badge variants.
 *
 * M5-A7: `WAITING_APPROVAL` — the single most actionable state (blocked on a
 * human decision) — and `PAUSED`/`TRIGGERING` previously fell through to the
 * same generic "outline" treatment as an unrecognized value, visually
 * indistinguishable from each other or from a bug. Each now has its own
 * variant so an operator scanning a list can spot "needs a human" without
 * reading every row's text.
 */

import { Badge } from "@kortex/design-system";
import type {
  WorkflowStatus,
  WorkflowState,
  ApprovalState,
  ScheduleStatus,
  ExternalExecutionStatus,
} from "../types";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

function statusToVariant(status: string): BadgeVariant {
  switch (status) {
    case "COMPLETED":
    case "ACTIVE":
    case "APPROVED":
      return "default";
    case "RUNNING":
    case "PENDING":
    case "PAUSED":
    case "TRIGGERING":
      return "secondary";
    case "WAITING_APPROVAL":
    case "WAITING":
      // Distinct from other in-progress states: this one is blocked on a
      // human, not merely running — an operator should notice it first.
      return "destructive";
    case "FAILED":
    case "REJECTED":
    case "CANCELLED":
    case "TIMEOUT":
    case "TIMED_OUT":
    case "DISABLED":
    case "EXPIRED":
      return "destructive";
    default:
      return "outline";
  }
}

export function WorkflowStatusBadge({ status }: { status: WorkflowStatus }) {
  return <Badge variant={statusToVariant(status)}>{status.replace(/_/g, " ")}</Badge>;
}

export function WorkflowStateBadge({ status }: { status: WorkflowState }) {
  return <Badge variant={statusToVariant(status)}>{status.replace(/_/g, " ")}</Badge>;
}

export function ApprovalStatusBadge({ status }: { status: ApprovalState }) {
  return <Badge variant={statusToVariant(status)}>{status.replace(/_/g, " ")}</Badge>;
}

export function ScheduleStatusBadge({ status }: { status: ScheduleStatus }) {
  return <Badge variant={statusToVariant(status)}>{status.replace(/_/g, " ")}</Badge>;
}

export function ExecStatusBadge({ status }: { status: ExternalExecutionStatus }) {
  return <Badge variant={statusToVariant(status)}>{status.replace(/_/g, " ")}</Badge>;
}

/** Format an ISO datetime string for display: relative-from-now alongside
 * the absolute local time, so an operator on a surface that polls every
 * 10-30s doesn't have to do date math to tell "3 minutes ago" from "3 hours
 * ago" (M5-A7) — while still keeping the exact timestamp visible. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const absolute = date.toLocaleString();
  return `${absolute} (${formatRelative(date)})`;
}

function formatRelative(date: Date): string {
  const diffMs = date.getTime() - Date.now();
  const diffSeconds = Math.round(diffMs / 1000);
  const absSeconds = Math.abs(diffSeconds);

  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 31536000],
    ["month", 2592000],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];

  for (const [unit, secondsInUnit] of units) {
    if (absSeconds >= secondsInUnit) {
      const value = Math.round(diffSeconds / secondsInUnit);
      return new Intl.RelativeTimeFormat("en", { numeric: "auto" }).format(value, unit);
    }
  }
  return new Intl.RelativeTimeFormat("en", { numeric: "auto" }).format(diffSeconds, "second");
}
