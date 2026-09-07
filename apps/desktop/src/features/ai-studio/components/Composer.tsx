import * as React from "react";
import { Button, Textarea } from "@kortex/design-system";

export interface ComposerProps {
  onSend: (text: string) => void;
  disabled: boolean;
  /** Defaults to the AI Studio Chat tab's original copy, so that surface is
   * byte-for-byte unaffected. Callers mounted somewhere other than AI
   * Studio (e.g. Mini Chat) pass their own, since "Message AI Studio..."
   * would be visibly wrong copy outside that specific tab. */
  placeholder?: string;
}

/** Message input, reused by both the AI Studio Chat tab and Mini Chat.
 * Enter submits; Shift+Enter inserts a newline. Disabled while a message is
 * in flight or an agent task is PAUSED_FOR_APPROVAL (M7.2 §9: sending
 * disabled during a pending approval keeps the transcript single-flight, so
 * there is never any ambiguity about which reply resolves which pending
 * card). */
export function Composer({ onSend, disabled, placeholder = "Message AI Studio..." }: ComposerProps) {
  const [value, setValue] = React.useState("");

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
  }

  return (
    <form
      className="flex gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <Textarea
        aria-label="Message"
        placeholder={placeholder}
        value={value}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        className="min-h-10"
      />
      <Button type="submit" disabled={disabled || value.trim().length === 0}>
        Send
      </Button>
    </form>
  );
}
