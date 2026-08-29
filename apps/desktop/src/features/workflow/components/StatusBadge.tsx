/**
 * Status badge variants for workflow domain statuses.
 * Maps backend status strings to design-system Badge variants.
 */

import { Badge } from "@kortex/design-system";
import type { WorkflowStatus, StepStatus, ApprovalStatus, ScheduleStatus, ExternalExecutionStatus } from "../types";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

function statusToVariant(status: string): BadgeVariant {
  switch (status) {
    case "COMPLETED":
    case "ACTIVE":
    case "APPROVED":
      return "default";
    case "RUNNING":
    case "PENDING":
      return "secondary";
    case "FAILED":
    case "REJECTED":
    case "CANCELLED":
    case "TIMEOUT":
    case "TIMED_OUT":
      return "destructive";
    default:
      return "outline";
  }
}

export function WorkflowStatusBadge({ status }: { status: WorkflowStatus }) {
  return <Badge variant={statusToVariant(status)}>{status.replace(/_/g, " ")}</Badge>;
}

export function StepStatusBadge({ status }: { status: StepStatus }) {
  return <Badge variant={statusToVariant(status)}>{status.replace(/_/g, " ")}</Badge>;
}

export function ApprovalStatusBadge({ status }: { status: ApprovalStatus }) {
  return <Badge variant={statusToVariant(status)}>{status.replace(/_/g, " ")}</Badge>;
}

export function ScheduleStatusBadge({ status }: { status: ScheduleStatus }) {
  return <Badge variant={statusToVariant(status)}>{status.replace(/_/g, " ")}</Badge>;
}

export function ExecStatusBadge({ status }: { status: ExternalExecutionStatus }) {
  return <Badge variant={statusToVariant(status)}>{status.replace(/_/g, " ")}</Badge>;
}

/** Format an ISO datetime string as a human-readable relative time or absolute. */
export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
