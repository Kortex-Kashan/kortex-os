/**
 * M7.2 §2.3 (Option B, and only Option B): a lightweight card shown in the
 * Chat transcript when an agent task is `PAUSED_FOR_APPROVAL` -- goal,
 * pending tool info, and a single "Review & Decide" action that navigates
 * to the EXISTING Workflow Approval Queue. There is no approve/reject/
 * delegate action here, and never will be: this feature must not become a
 * second approval-decision surface. Deciding always happens in Workflow.
 */

import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@kortex/design-system";
import { useApplicationNavigation } from "@/navigation/navigationBridge";
import type { PendingToolCall } from "../chat-types";

export interface ToolCallCardProps {
  goal: string;
  pendingToolCalls: PendingToolCall[];
}

export function ToolCallCard({ goal, pendingToolCalls }: ToolCallCardProps) {
  const { navigateToApplication } = useApplicationNavigation();

  return (
    <Card aria-label="Approval pending">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">Waiting for approval</Badge>
        </div>
        <CardTitle className="text-body">{goal}</CardTitle>
        <CardDescription>
          This request proposes {pendingToolCalls.length === 1 ? "a tool call" : "tool calls"} that require
          human approval before it can continue.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {pendingToolCalls.length > 0 && (
          <ul className="space-y-1" aria-label="Proposed tool calls">
            {pendingToolCalls.map((call) => (
              <li key={call.callId} className="text-caption text-muted-foreground">
                {call.toolName}
              </li>
            ))}
          </ul>
        )}
        <Button
          size="sm"
          onClick={() => navigateToApplication({ applicationId: "workflow-engine", search: "?tab=approvals" })}
        >
          Review & Decide
        </Button>
      </CardContent>
    </Card>
  );
}
