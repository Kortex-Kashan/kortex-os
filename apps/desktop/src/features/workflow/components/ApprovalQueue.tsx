/**
 * Human Governance Approval Queue — pending approval requests with decision UI.
 *
 * Operators can review context, approve/reject with rationale, or delegate
 * to another principal. All decisions are cryptographically recorded on the
 * backend (M5.3 Ed25519 lineage).
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
import { useDelegateApproval, usePendingApprovals, useSubmitApprovalDecision } from "../hooks/useApprovals";
import { WorkflowAccessDeniedError } from "../api";
import type { ApprovalRequest } from "../types";
import { ApprovalStatusBadge, formatDateTime } from "./StatusBadge";

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function ApprovalQueue() {
  const { data, isPending, isError, error, refetch, isFetching } = usePendingApprovals();

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
        <div className="space-y-3" role="list" aria-label="Pending approval requests">
          {requests.map((req) => (
            <ApprovalCard key={req.requestId} request={req} onActionComplete={() => void refetch()} />
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Single approval card
// ---------------------------------------------------------------------------

function ApprovalCard({
  request,
  onActionComplete,
}: {
  request: ApprovalRequest;
  onActionComplete: () => void;
}) {
  const [dialogMode, setDialogMode] = useState<"decide" | "delegate" | null>(null);
  const submitDecision = useSubmitApprovalDecision();
  const delegateMutation = useDelegateApproval();

  async function handleDecision(decision: "APPROVED" | "REJECTED", rationale: string) {
    await submitDecision.mutateAsync({ requestId: request.requestId, decision, rationale });
    setDialogMode(null);
    onActionComplete();
  }

  async function handleDelegate(delegateTo: string, reason: string) {
    await delegateMutation.mutateAsync({
      requestId: request.requestId,
      delegateToPrincipalId: delegateTo,
      reason,
    });
    setDialogMode(null);
    onActionComplete();
  }

  return (
    <>
      <Card role="listitem" aria-label={`Approval request ${request.requestId}`}>
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div className="min-w-0">
              <CardTitle className="text-base">{request.workflowName || "Workflow Approval"}</CardTitle>
              <CardDescription className="font-mono text-xs truncate">
                {request.requestId}
              </CardDescription>
            </div>
            <ApprovalStatusBadge status={request.status} />
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span>Required Role</span>
            <span>
              <Badge variant="outline">{request.requiredRole}</Badge>
            </span>
            <span>Requested</span>
            <span>{formatDateTime(request.createdAt)}</span>
            <span>Expires</span>
            <span>{formatDateTime(request.expiresAt)}</span>
            {request.requesterPrincipalId && (
              <>
                <span>Requester</span>
                <span className="font-mono text-xs">{request.requesterPrincipalId}</span>
              </>
            )}
          </div>

          {Object.keys(request.context).length > 0 && (
            <>
              <Separator />
              <div className="text-sm">
                <p className="font-medium mb-1 text-muted-foreground">Context</p>
                <pre className="text-xs bg-muted rounded-md p-2 overflow-auto max-h-32 whitespace-pre-wrap">
                  {JSON.stringify(request.context, null, 2)}
                </pre>
              </div>
            </>
          )}

          {request.status === "PENDING" && (
            <div className="flex gap-2 pt-1 flex-wrap">
              <Button
                size="sm"
                onClick={() => setDialogMode("decide")}
                aria-label="Approve or reject this request"
              >
                Decide
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setDialogMode("delegate")}
                aria-label="Delegate this request"
              >
                Delegate
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
              Your decision will be cryptographically recorded and cannot be reversed.
            </DialogDescription>
          </DialogHeader>
          <DecisionForm
            onSubmit={handleDecision}
            isPending={submitDecision.isPending}
            onCancel={() => setDialogMode(null)}
          />
        </DialogContent>
      </Dialog>

      {/* Delegate Dialog */}
      <Dialog open={dialogMode === "delegate"} onOpenChange={(o) => !o && setDialogMode(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delegate Approval</DialogTitle>
            <DialogDescription>
              Transfer this approval to another authorized principal.
            </DialogDescription>
          </DialogHeader>
          <DelegateForm
            onSubmit={handleDelegate}
            isPending={delegateMutation.isPending}
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
  onCancel,
}: {
  onSubmit: (decision: "APPROVED" | "REJECTED", rationale: string) => Promise<void>;
  isPending: boolean;
  onCancel: () => void;
}) {
  const [rationale, setRationale] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(decision: "APPROVED" | "REJECTED") {
    if (!rationale.trim()) {
      setError("Rationale is required.");
      return;
    }
    setError(null);
    await onSubmit(decision, rationale.trim());
  }

  return (
    <div className="space-y-4">
      <div>
        <Label htmlFor="decision-rationale">Rationale *</Label>
        <Input
          id="decision-rationale"
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          placeholder="Provide the reason for your decision..."
          disabled={isPending}
          aria-required="true"
        />
        {error && (
          <p className="text-xs text-destructive mt-1" role="alert">
            {error}
          </p>
        )}
      </div>
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
  onCancel,
}: {
  onSubmit: (delegateTo: string, reason: string) => Promise<void>;
  isPending: boolean;
  onCancel: () => void;
}) {
  const [delegateTo, setDelegateTo] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!delegateTo.trim() || !reason.trim()) {
      setError("Both fields are required.");
      return;
    }
    setError(null);
    await onSubmit(delegateTo.trim(), reason.trim());
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
      <div>
        <Label htmlFor="delegate-to">Delegate to Principal ID *</Label>
        <Input
          id="delegate-to"
          value={delegateTo}
          onChange={(e) => setDelegateTo(e.target.value)}
          placeholder="principal_id"
          disabled={isPending}
          aria-required="true"
        />
      </div>
      <div>
        <Label htmlFor="delegate-reason">Reason *</Label>
        <Input
          id="delegate-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason for delegation..."
          disabled={isPending}
          aria-required="true"
        />
      </div>
      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-2 justify-end">
        <Button variant="outline" size="sm" type="button" onClick={onCancel} disabled={isPending}>
          Cancel
        </Button>
        <Button size="sm" type="submit" disabled={isPending}>
          Delegate
        </Button>
      </div>
    </form>
  );
}
