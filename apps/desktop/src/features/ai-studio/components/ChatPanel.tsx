/**
 * AI Studio Chat tab (M7.2) — the third tab alongside Providers & Models
 * and Governance in the existing `AiStudioApp`. Owns no state of its own
 * beyond wiring `useConversation` (transcript + send + which conversation
 * is active) to `useAgentStatus` (polling one PAUSED_FOR_APPROVAL task, if
 * any, until it resolves).
 *
 * Layout (AI Studio functional stabilization, Phase B): the `Card` is given
 * a fixed viewport-relative height so the header/description stay put and
 * only `MessageList`'s own transcript region scrolls, using the exact same
 * `flex-1` + `overflow-hidden`-ancestor recipe `MiniChatHost.tsx` already
 * proved out for its floating panel — not a new layout approach. The height
 * constant mirrors the one `WorkflowBuilderTab.tsx` already uses for the
 * same "fixed chrome, scrolling body" shape elsewhere in this app.
 *
 * Recent Conversations (Phase C): `RecentConversationsPanel` is a sidebar
 * inside this same fixed-height `Card`, not a new page/route — selecting a
 * conversation or starting a new one calls straight into
 * `useConversation`'s `switchConversation`/`startNewConversation`, so the
 * transcript column beside it updates in place without this component
 * unmounting or losing any other local state.
 */

import * as React from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Skeleton } from "@kortex/design-system";
import { useAgentStatus } from "../hooks/useAgentStatus";
import { useConversation } from "../hooks/useConversation";
import { TERMINAL_AGENT_STATUSES } from "../chat-types";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { RecentConversationsPanel } from "./RecentConversationsPanel";

export interface ChatPanelProps {
  tenantId: string;
  userId: string;
}

export function ChatPanel({ tenantId, userId }: ChatPanelProps) {
  const {
    conversationId,
    messages,
    isLoadingHistory,
    historyError,
    isSending,
    pendingTaskId,
    sendMessage,
    resolvePendingApproval,
    switchConversation,
    startNewConversation,
  } = useConversation({ tenantId, userId });

  const statusQuery = useAgentStatus(pendingTaskId ?? "", tenantId, pendingTaskId !== null);
  const observedStatus = statusQuery.data?.status;

  React.useEffect(() => {
    if (!pendingTaskId || !observedStatus) return;
    if (!TERMINAL_AGENT_STATUSES.includes(observedStatus)) return;
    void resolvePendingApproval(pendingTaskId, observedStatus);
  }, [pendingTaskId, observedStatus, resolvePendingApproval]);

  return (
    <Card className="flex h-[calc(100vh-8.5rem)] flex-col">
      <CardHeader>
        <CardTitle>Chat</CardTitle>
        <CardDescription>
          Conversational AI Studio. Mutating actions may require approval before they proceed.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-1 gap-4 overflow-hidden">
        <RecentConversationsPanel
          tenantId={tenantId}
          userId={userId}
          activeConversationId={conversationId}
          onSelectConversation={switchConversation}
          onNewChat={startNewConversation}
        />
        <div className="flex flex-1 flex-col gap-4 overflow-hidden">
          {isLoadingHistory ? (
            <div className="flex-1 space-y-3" role="status" aria-label="Loading conversation">
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
              <MessageList messages={messages} className="flex-1" />
            </>
          )}
          <Composer onSend={sendMessage} disabled={pendingTaskId !== null} sendDisabled={isSending} />
        </div>
      </CardContent>
    </Card>
  );
}
