import type { ChatMessage } from "../chat-types";
import { ToolCallCard } from "./ToolCallCard";

const ROLE_BUBBLE_CLASS: Record<ChatMessage["role"], string> = {
  user: "bg-primary text-primary-foreground",
  assistant: "bg-card text-card-foreground border border-border",
  system: "bg-muted text-muted-foreground italic",
};

export function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.pendingApproval) {
    return (
      <ToolCallCard goal={message.pendingApproval.goal} pendingToolCalls={message.pendingApproval.pendingToolCalls} />
    );
  }

  const alignment = message.role === "user" ? "justify-end" : "justify-start";

  return (
    <div className={`flex ${alignment}`}>
      <div
        className={`max-w-[80%] rounded-lg px-3 py-2 text-body ${ROLE_BUBBLE_CLASS[message.role]}`}
        data-testid="chat-message"
        data-role={message.role}
      >
        {message.content}
      </div>
    </div>
  );
}
