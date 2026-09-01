import type { ChatMessage } from "../chat-types";
import { MessageBubble } from "./MessageBubble";

export function MessageList({ messages }: { messages: ChatMessage[] }) {
  if (messages.length === 0) {
    return (
      <p className="text-body text-muted-foreground" role="status">
        No messages yet. Say hello to get started.
      </p>
    );
  }

  return (
    <div className="space-y-3" role="log" aria-label="Chat transcript">
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
    </div>
  );
}
