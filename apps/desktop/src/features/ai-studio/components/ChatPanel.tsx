/**
 * AI Studio Chat tab (M7.2) — the third tab alongside Providers & Models
 * and Governance in the existing `AiStudioApp`. Owns no state of its own
 * beyond wiring `useConversation` (transcript + send) to `useAgentStatus`
 * (polling one PAUSED_FOR_APPROVAL task, if any, until it resolves).
 */

import * as React from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Skeleton } from "@kortex/design-system";
import { useAgentStatus } from "../hooks/useAgentStatus";
import { useConversation } from "../hooks/useConversation";
import { TERMINAL_AGENT_STATUSES } from "../chat-types";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";

export interface ChatPanelProps {
  tenantId: string;
  userId: string;
}

export function ChatPanel({ tenantId, userId }: ChatPanelProps) {
  const { messages, isLoadingHistory, historyError, isSending, pendingTaskId, sendMessage, resolvePendingApproval } =
    useConversation({ tenantId, userId });

  const statusQuery = useAgentStatus(pendingTaskId ?? "", tenantId, pendingTaskId !== null);
  const observedStatus = statusQuery.data?.status;

  React.useEffect(() => {
    if (!pendingTaskId || !observedStatus) return;
    if (!TERMINAL_AGENT_STATUSES.includes(observedStatus)) return;
    void resolvePendingApproval(pendingTaskId, observedStatus);
  }, [pendingTaskId, observedStatus, resolvePendingApproval]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Chat</CardTitle>
        <CardDescription>
          Conversational AI Studio. Mutating actions may require approval before they proceed.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoadingHistory ? (
          <div className="space-y-3" role="status" aria-label="Loading conversation">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : (
          <>
            {historyError && (
              <p className="text-caption text-muted-foreground">
                Could not load prior conversation history: {historyError.message}
              </p>
            )}
            <MessageList messages={messages} />
          </>
        )}
        <Composer onSend={sendMessage} disabled={isSending || pendingTaskId !== null} />
      </CardContent>
    </Card>
  );
}
