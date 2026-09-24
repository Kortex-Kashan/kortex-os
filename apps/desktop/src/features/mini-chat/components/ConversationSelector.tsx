import * as React from "react";
import { Button, DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@kortex/design-system";
import { listConversations } from "@/features/ai-studio/chat-api";
import type { ConversationSummaryDto } from "@/features/ai-studio/chat-types";

export interface ConversationSelectorProps {
  activeConversationId: string;
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
}

export function ConversationSelector({
  activeConversationId,
  onSelectConversation,
  onNewChat,
}: ConversationSelectorProps) {
  const [conversations, setConversations] = React.useState<ConversationSummaryDto[]>([]);
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    listConversations()
      .then((data) => setConversations(data))
      .catch(() => setConversations([]));
  }, [open]);

  const activeTitle =
    conversations.find((c) => c.conversationId === activeConversationId)?.title || "Active Session";

  return (
    <div className="flex items-center gap-1">
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 max-w-[180px] gap-1 px-2 text-xs font-normal text-muted-foreground hover:text-foreground"
            title={activeTitle}
          >
            <span className="truncate">{activeTitle}</span>
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              className="size-3 shrink-0 opacity-60"
            >
              <path d="m6 9 6 6 6-6" />
            </svg>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64 max-h-72 overflow-y-auto">
          <DropdownMenuItem onSelect={onNewChat} className="gap-2 font-medium text-primary">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              className="size-3.5"
            >
              <path d="M12 5v14M5 12h14" />
            </svg>
            Start New Chat
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          {conversations.length === 0 ? (
            <div className="p-2 text-center text-caption text-muted-foreground">No recent conversations</div>
          ) : (
            conversations.map((conv) => (
              <DropdownMenuItem
                key={conv.conversationId}
                onSelect={() => onSelectConversation(conv.conversationId)}
                className={`text-caption truncate ${
                  conv.conversationId === activeConversationId ? "font-semibold bg-accent text-accent-foreground" : ""
                }`}
              >
                {conv.title}
              </DropdownMenuItem>
            ))
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
        onClick={onNewChat}
        title="Start New Chat"
        aria-label="New Chat"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          className="size-3.5"
        >
          <path d="M12 5v14M5 12h14" />
        </svg>
      </Button>
    </div>
  );
}
