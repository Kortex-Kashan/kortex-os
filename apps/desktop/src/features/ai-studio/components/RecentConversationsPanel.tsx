import * as React from "react";
import { Button, Skeleton, cn } from "@kortex/design-system";
import { useConversations } from "../hooks/useConversations";
import type { ConversationSummaryDto } from "../chat-types";

export interface RecentConversationsPanelProps {
  tenantId: string;
  userId: string;
  activeConversationId: string;
  onSelectConversation: (conversationId: string) => void;
  onNewChat: () => void;
}

const GROUP_ORDER = ["Today", "Yesterday", "Older"] as const;
type GroupLabel = (typeof GROUP_ORDER)[number];

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** "Today"/"Yesterday"/"Older" bucketing, per the milestone's own suggested
 * grouping — compares calendar days in the viewer's local time, not a
 * rolling 24h window, so a conversation from 11pm yesterday reads as
 * "Yesterday" even if less than 24 hours have elapsed. */
function groupLabelFor(lastActivityAt: string): GroupLabel {
  const diffDays = Math.round((startOfDay(new Date()) - startOfDay(new Date(lastActivityAt))) / 86_400_000);
  if (diffDays <= 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  return "Older";
}

/**
 * "Recent Conversations" for the AI Studio Chat tab (AI Studio functional
 * stabilization, Phase C). Server-derived (`kortex.ai.conversation.list`)
 * and tenant/user-scoped entirely server-side — this component never
 * decides which conversations exist, it only presents what the backend
 * already scoped to the calling identity.
 *
 * Deliberately not rendered by Mini Chat: its floating panel has no room
 * for a conversation list, and it already shares the exact same
 * `useConversation`/active-pointer infrastructure this panel's selections
 * write to — selecting a conversation here changes what a Mini Chat opened
 * later would resume, without Mini Chat needing any UI of its own for it.
 */
export function RecentConversationsPanel({
  tenantId,
  userId,
  activeConversationId,
  onSelectConversation,
  onNewChat,
}: RecentConversationsPanelProps) {
  const { data, isPending, isError, error, refetch, isFetching } = useConversations(tenantId, userId);

  const groups = React.useMemo(() => {
    const byLabel = new Map<GroupLabel, ConversationSummaryDto[]>();
    for (const conversation of data ?? []) {
      const label = groupLabelFor(conversation.lastActivityAt);
      const bucket = byLabel.get(label);
      if (bucket) {
        bucket.push(conversation);
      } else {
        byLabel.set(label, [conversation]);
      }
    }
    return GROUP_ORDER.filter((label) => byLabel.has(label)).map((label) => ({
      label,
      conversations: byLabel.get(label) ?? [],
    }));
  }, [data]);

  return (
    <div
      className="flex w-64 shrink-0 flex-col gap-3 overflow-hidden border-r border-border pr-4"
      data-testid="recent-conversations-panel"
    >
      <Button type="button" size="sm" onClick={onNewChat}>
        New Chat
      </Button>

      <div className="flex-1 overflow-y-auto">
        {isPending ? (
          <div className="space-y-2" role="status" aria-label="Loading conversations">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : isError ? (
          <div className="space-y-2" role="alert">
            <p className="text-caption text-muted-foreground">Could not load Recent Conversations.</p>
            {error instanceof Error && <p className="text-caption text-muted-foreground">{error.message}</p>}
            <Button type="button" size="sm" variant="outline" onClick={() => void refetch()} disabled={isFetching}>
              Retry
            </Button>
          </div>
        ) : (data ?? []).length === 0 ? (
          <p className="text-caption text-muted-foreground" role="status">
            No conversations yet. Say hello to get started.
          </p>
        ) : (
          <nav aria-label="Recent Conversations">
            {groups.map((group) => (
              <div key={group.label} className="mb-3">
                <h4 className="mb-1 px-1 text-caption font-medium text-muted-foreground">{group.label}</h4>
                <ul className="space-y-0.5">
                  {group.conversations.map((conversation) => {
                    const isActive = conversation.conversationId === activeConversationId;
                    return (
                      <li key={conversation.conversationId}>
                        <button
                          type="button"
                          onClick={() => onSelectConversation(conversation.conversationId)}
                          aria-current={isActive ? "true" : undefined}
                          title={conversation.title}
                          className={cn(
                            "w-full truncate rounded-md px-2 py-1.5 text-left text-body transition-colors",
                            isActive
                              ? "bg-primary text-primary-foreground font-medium"
                              : "text-foreground hover:bg-muted",
                          )}
                        >
                          {conversation.title}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>
        )}
      </div>
    </div>
  );
}
