import * as React from "react";
import { Button, Textarea } from "@kortex/design-system";

export interface ComposerProps {
  onSend: (text: string) => void;
  disabled: boolean;
}

/** Message input for the Chat tab. Enter submits; Shift+Enter inserts a
 * newline. Disabled while a message is in flight or an agent task is
 * PAUSED_FOR_APPROVAL (M7.2 §9: sending disabled during a pending approval
 * keeps the transcript single-flight, so there is never any ambiguity
 * about which reply resolves which pending card). */
export function Composer({ onSend, disabled }: ComposerProps) {
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
        placeholder="Message AI Studio..."
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
