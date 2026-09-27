import { Badge, Card, CardDescription, CardHeader } from "@kortex/design-system";
import type { ChatMessage } from "../chat-types";
import { ToolCallCard } from "./ToolCallCard";

/** A task paused while this desktop carries out an already-authorized
 * Browser action. Informational only: there is nothing to decide here —
 * any approval the action required was given before it was issued. */
function BrowserExecutionCard({ toolNames }: { toolNames: string[] }) {
  return (
    <Card aria-label="Browser action in progress">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">Browser action in progress</Badge>
        </div>
        <CardDescription>
          {toolNames.length > 0 ? `Running ${toolNames.join(", ")} on this desktop.` : "Running a Browser action on this desktop."}
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

const ROLE_BUBBLE_CLASS: Record<ChatMessage["role"], string> = {
  user: "bg-primary text-primary-foreground",
  assistant: "bg-card text-card-foreground border border-border",
  system: "bg-muted text-muted-foreground italic",
};

export function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.pendingApproval?.kind === "browserExecution") {
    return <BrowserExecutionCard toolNames={message.pendingApproval.pendingToolCalls.map((call) => call.toolName)} />;
  }
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
