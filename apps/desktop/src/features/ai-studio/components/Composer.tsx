import * as React from "react";
import { Button, Textarea } from "@kortex/design-system";

export interface ComposerProps {
  onSend: (text: string) => void;
  /** Blocks typing AND sending entirely — reserved for states where a new
   * message would be genuinely rejected, not merely delayed. The one case
   * today is an agent task PAUSED_FOR_APPROVAL (M7.2 §9): a new message
   * during that state would break the single-flight assumption the pending
   * approval relies on, so there must be no way to even compose one until
   * it resolves. */
  disabled: boolean;
  /** Blocks sending only — typing/editing remains available. Used while a
   * message is already in flight (`isSending`): that request genuinely
   * cannot be submitted again yet, but there is no backend reason the user
   * can't keep composing their next message while they wait. Defaults to
   * `false` so existing callers that only ever passed one boolean keep
   * their original (typing-blocked-too) behavior unless they opt in. */
  sendDisabled?: boolean;
  /** Defaults to the AI Studio Chat tab's original copy, so that surface is
   * byte-for-byte unaffected. Callers mounted somewhere other than AI
   * Studio (e.g. Mini Chat) pass their own, since "Message AI Studio..."
   * would be visibly wrong copy outside that specific tab. */
  placeholder?: string;
}

/** Message input, reused by both the AI Studio Chat tab and Mini Chat.
 * Enter submits; Shift+Enter inserts a newline.
 *
 * `disabled` and `sendDisabled` are deliberately separate props (AI Studio
 * functional stabilization, Phase B): the previous single `disabled` prop
 * froze the textarea itself for the entire duration a response was being
 * generated, which made the composer feel locked even though there was no
 * backend reason typing had to stop. Only submission genuinely cannot
 * proceed while a request is already in flight. */
export function Composer({ onSend, disabled, sendDisabled = false, placeholder = "Message AI Studio..." }: ComposerProps) {
  const [value, setValue] = React.useState("");
  const blockSend = disabled || sendDisabled;

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || blockSend) return;
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
      <Button type="submit" disabled={blockSend || value.trim().length === 0}>
        Send
      </Button>
    </form>
  );
}
