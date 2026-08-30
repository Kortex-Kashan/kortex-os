/**
 * Human Governance Approval Queue — pending approval requests with decision UI.
 *
 * M5-A6: `ApprovalRequest` no longer carries `workflowName`,
 * `requesterPrincipalId`, `createdAt`, `expiresAt`, `decidedAt`,
 * `deciderPrincipalId`, or `decisionRationale` — none of those exist on the
 * real `kortex.workflow.approval.*` responses. Decisions are submitted as
 * the current operator's own principal ID (`approverId`), matching what
 * `decide_approval_request` actually requires and enforces (M5-A2: the
 * backend rejects a decision whose `approver_id` doesn't match the
 * dispatcher-verified caller). Delegation is a time-bounded role grant, not
 * a per-ticket action, per the real `delegate_approval_role` signature.
 */

import { useState } from "react";
import type { FormEvent } from "react";
import {
  Badge,
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
  Separator,
  Skeleton,
} from "@kortex/design-system";
import { useAuth } from "@/auth/AuthProvider";
import { useDelegateApproval, usePendingApprovals, useSubmitApprovalDecision } from "../hooks/useApprovals";
import { usePagedList } from "../hooks/usePagedList";
import { WorkflowAccessDeniedError } from "../api";
import type { ApprovalRequest } from "../types";
import { ApprovalStatusBadge, formatDateTime } from "./StatusBadge";
import { PaginationControls } from "./PaginationControls";

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function ApprovalQueue() {
  const { state } = useAuth();
  const currentPrincipalId =
    state.status === "AUTHENTICATED" && state.identity ? state.identity.principalId : "";
  const { data, isPending, isError, error, refetch, isFetching } = usePendingApprovals();
  const { pageItems, page, pageCount, hasPrev, hasNext, goPrev, goNext } = usePagedList(
    data ?? [],
    10,
  );

  if (isPending) {
    return (
      <section aria-label="Pending Approvals">
        <h3 className="text-heading mb-4 leading-none tracking-tight">Approval Queue</h3>
        <div className="space-y-3" role="status" aria-label="Loading approvals">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      </section>
    );
  }

  if (isError) {
    const msg = error instanceof WorkflowAccessDeniedError
      ? `Access denied: ${error.message}`
      : error.message;
    return (
      <section aria-label="Pending Approvals">
        <h3 className="text-heading mb-4 leading-none tracking-tight">Approval Queue</h3>
        <div className="space-y-2" role="alert">
          <p className="text-sm text-destructive">{msg}</p>
          {!(error instanceof WorkflowAccessDeniedError) && (
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          )}
        </div>
      </section>
    );
  }

  const requests = data ?? [];

  return (
    <section aria-label="Pending Approvals">
      <div className="mb-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h3 className="text-heading leading-none tracking-tight">Approval Queue</h3>
          {requests.length > 0 && (
            <Badge variant="destructive" aria-label={`${requests.length} pending approvals`}>
              {requests.length}
            </Badge>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refetch()}
          disabled={isFetching}
          aria-label="Refresh approval queue"
        >
          Refresh
        </Button>
      </div>

      {requests.length === 0 ? (
        <p className="text-body text-muted-foreground" role="status">
          No pending approvals.
        </p>
      ) : (
        <>
          <div className="space-y-3" role="list" aria-label="Pending approval requests">
            {pageItems.map((req) => (
              <ApprovalCard key={req.id} request={req} currentPrincipalId={currentPrincipalId} />
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
    </section>
  );
}

// ---------------------------------------------------------------------------
// Single approval card
// ---------------------------------------------------------------------------

function ApprovalCard({
  request,
  currentPrincipalId,
}: {
  request: ApprovalRequest;
  currentPrincipalId: string;
}) {
  const [dialogMode, setDialogMode] = useState<"decide" | "delegate" | null>(null);
  const submitDecision = useSubmitApprovalDecision();
  const delegateMutation = useDelegateApproval();

  async function handleDecision(decision: "APPROVED" | "REJECTED", reason: string) {
    await submitDecision.mutateAsync({
      requestId: request.id,
      decision,
      approverId: currentPrincipalId,
      reason,
    });
    setDialogMode(null);
  }

  async function handleDelegate(delegateeId: string, hours: number) {
    const now = new Date();
    const until = new Date(now.getTime() + hours * 60 * 60 * 1000);
    await delegateMutation.mutateAsync({
      delegatorId: currentPrincipalId,
      delegateeId,
      role: request.requiredRole,
      validFrom: now.toISOString(),
      validUntil: until.toISOString(),
    });
    setDialogMode(null);
  }

  return (
    <>
      <Card role="listitem" aria-label={`Approval request ${request.id}`}>
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div className="min-w-0">
              <CardTitle className="text-base">Required Role: {request.requiredRole}</CardTitle>
              <CardDescription className="font-mono text-xs truncate">
                {request.id} · tenant {request.tenantId}
              </CardDescription>
            </div>
            <ApprovalStatusBadge status={request.state} />
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-muted-foreground">
            {request.instanceId && (
              <>
                <span>Instance</span>
                <span className="font-mono text-xs truncate">{request.instanceId}</span>
              </>
            )}
            {request.stepId && (
              <>
                <span>Step</span>
                <span className="font-mono text-xs truncate">{request.stepId}</span>
              </>
            )}
            <span>Times Out</span>
            <span>{formatDateTime(request.timeoutAt)}</span>
            <span>Signature Required</span>
            <span>
              <Badge variant={request.signatureRequired ? "default" : "outline"}>
                {request.signatureRequired ? "Yes" : "No"}
              </Badge>
            </span>
          </div>

          {request.contextSnapshot && Object.keys(request.contextSnapshot).length > 0 && (
            <>
              <Separator />
              <div className="text-sm">
                <p className="font-medium mb-1 text-muted-foreground">Context</p>
                <pre className="text-xs bg-muted rounded-md p-2 overflow-auto max-h-32 whitespace-pre-wrap">
                  {JSON.stringify(request.contextSnapshot, null, 2)}
                </pre>
              </div>
            </>
          )}

          {request.state === "PENDING" && (
            <div className="flex gap-2 pt-1 flex-wrap">
              <Button
                size="sm"
                onClick={() => setDialogMode("decide")}
                aria-label={`Decide request ${request.id}`}
              >
                Decide
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setDialogMode("delegate")}
                aria-label={`Delegate role for request ${request.id}`}
              >
                Delegate Role
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Decision Dialog */}
      <Dialog open={dialogMode === "decide"} onOpenChange={(o) => !o && setDialogMode(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Submit Decision</DialogTitle>
            <DialogDescription>
              You are deciding as <span className="font-mono">{currentPrincipalId || "(unknown)"}</span>.
              This decision cannot be reversed.
            </DialogDescription>
          </DialogHeader>
          <DecisionForm
            onSubmit={handleDecision}
            isPending={submitDecision.isPending}
            error={submitDecision.error}
            onCancel={() => setDialogMode(null)}
          />
        </DialogContent>
      </Dialog>

      {/* Delegate Dialog */}
      <Dialog open={dialogMode === "delegate"} onOpenChange={(o) => !o && setDialogMode(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delegate Role: {request.requiredRole}</DialogTitle>
            <DialogDescription>
              Grants another principal the "{request.requiredRole}" role for a time-bounded window,
              on your (<span className="font-mono">{currentPrincipalId || "(unknown)"}</span>) authority.
            </DialogDescription>
          </DialogHeader>
          <DelegateForm
            onSubmit={handleDelegate}
            isPending={delegateMutation.isPending}
            error={delegateMutation.error}
            onCancel={() => setDialogMode(null)}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// Decision Form
// ---------------------------------------------------------------------------

function DecisionForm({
  onSubmit,
  isPending,
  error,
  onCancel,
}: {
  onSubmit: (decision: "APPROVED" | "REJECTED", reason: string) => Promise<void>;
  isPending: boolean;
  error: unknown;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  async function submit(decision: "APPROVED" | "REJECTED") {
    if (!reason.trim()) {
      setValidationError("A reason is required.");
      return;
    }
    setValidationError(null);
    try {
      await onSubmit(decision, reason.trim());
    } catch {
      // Surfaced via `error` prop below; keep the dialog open.
    }
  }

  const errorMessage = validationError ?? (error instanceof Error ? error.message : null);

  return (
    <div className="space-y-4">
      <div>
        <Label htmlFor="decision-reason">Reason *</Label>
        <Input
          id="decision-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Provide the reason for your decision..."
          disabled={isPending}
          aria-required="true"
        />
      </div>
      {errorMessage && (
        <p className="text-xs text-destructive" role="alert">
          {errorMessage}
        </p>
      )}
      <div className="flex gap-2 justify-end">
        <Button variant="outline" size="sm" onClick={onCancel} disabled={isPending}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => void submit("REJECTED")}
          disabled={isPending}
          aria-label="Reject this approval request"
        >
          Reject
        </Button>
        <Button
          size="sm"
          onClick={() => void submit("APPROVED")}
          disabled={isPending}
          aria-label="Approve this approval request"
        >
          Approve
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Delegate Form
// ---------------------------------------------------------------------------

function DelegateForm({
  onSubmit,
  isPending,
  error,
  onCancel,
}: {
  onSubmit: (delegateeId: string, hours: number) => Promise<void>;
  isPending: boolean;
  error: unknown;
  onCancel: () => void;
}) {
  const [delegateeId, setDelegateeId] = useState("");
  const [hours, setHours] = useState("24");
  const [validationError, setValidationError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const hoursNum = parseInt(hours, 10);
    if (!delegateeId.trim() || !Number.isFinite(hoursNum) || hoursNum <= 0) {
      setValidationError("A delegatee principal ID and a positive duration in hours are required.");
      return;
    }
    setValidationError(null);
    try {
      await onSubmit(delegateeId.trim(), hoursNum);
    } catch {
      // Surfaced via `error` prop below.
    }
  }

  const errorMessage = validationError ?? (error instanceof Error ? error.message : null);

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
      <div>
        <Label htmlFor="delegate-to">Delegate to Principal ID *</Label>
        <Input
          id="delegate-to"
          value={delegateeId}
          onChange={(e) => setDelegateeId(e.target.value)}
          placeholder="principal_id"
          disabled={isPending}
          aria-required="true"
        />
      </div>
      <div>
        <Label htmlFor="delegate-hours">Valid for (hours) *</Label>
        <Input
          id="delegate-hours"
          type="number"
          min="1"
          value={hours}
          onChange={(e) => setHours(e.target.value)}
          disabled={isPending}
          aria-required="true"
        />
      </div>
      {errorMessage && (
        <p className="text-xs text-destructive" role="alert">
          {errorMessage}
        </p>
      )}
      <div className="flex gap-2 justify-end">
        <Button variant="outline" size="sm" type="button" onClick={onCancel} disabled={isPending}>
          Cancel
        </Button>
        <Button size="sm" type="submit" disabled={isPending}>
          {isPending ? "Delegating…" : "Delegate"}
        </Button>
      </div>
    </form>
  );
}
