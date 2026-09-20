import * as React from "react";
import { Button } from "@kortex/design-system";
import type { ChatMessage } from "../chat-types";
import { MessageBubble } from "./MessageBubble";

/** How close to the bottom (in pixels of unscrolled content) still counts
 * as "at the bottom" for auto-follow purposes. Not zero: a message list
 * that has settled exactly at the bottom can read a pixel or two off due
 * to sub-pixel layout/rounding, and treating that as "the user scrolled
 * away" would spuriously stop following on every single message. */
const NEAR_BOTTOM_THRESHOLD_PX = 64;

export interface MessageListProps {
  messages: ChatMessage[];
  /** Merged onto the scrollable transcript's own root element. Callers
   * (`ChatPanel.tsx`, `MiniChatHost.tsx`) are expected to pass sizing
   * classes (typically `flex-1`) so this component fills the remaining
   * space of a fixed-height flex column — the same `flex-1 overflow-y-auto`
   * recipe `MiniChatHost.tsx` already proved out, now centralized here
   * instead of duplicated per host. */
  className?: string;
}

/**
 * Shared scrollable transcript + auto-scroll-to-latest behavior for both
 * the AI Studio Chat tab and Mini Chat (AI Studio functional stabilization,
 * Phase B). Centralizing this here — rather than in `ChatPanel.tsx` and
 * `MiniChatHost.tsx` separately — is what keeps the two hosts from ever
 * needing two copies of the same follow/scroll-to-latest logic to stay in
 * sync.
 *
 * Auto-follow semantics: while the viewport is already at/near the bottom,
 * new content — a newly appended message, or an existing message's content
 * growing in place (the shape a future incremental/streamed update would
 * take; today's backend sends complete messages, but this reacts to
 * content length, not just message count, so it already behaves correctly
 * if that ever changes) — keeps the viewport pinned to the latest content.
 * The moment the user scrolls up to read older messages, this component
 * stops forcing the scroll position and instead shows a "Scroll to latest"
 * affordance; using it jumps to the bottom and resumes auto-follow.
 */
export function MessageList({ messages, className = "" }: MessageListProps) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const [isFollowing, setIsFollowing] = React.useState(true);

  // A single number that changes whenever there is new content to follow —
  // a new message, or the last message's content growing — without caring
  // which. Summing every message's length (not just the last one's) also
  // means an out-of-order content update (e.g. a mid-transcript
  // pending-approval placeholder being replaced) still counts as "new
  // content" here, which is the correct trigger for "is there something
  // fresh to scroll to" even though it isn't literally the newest message.
  const contentSignal = React.useMemo(
    () => messages.reduce((total, message) => total + message.content.length, 0),
    [messages],
  );

  const isNearBottom = React.useCallback(() => {
    const el = containerRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_THRESHOLD_PX;
  }, []);

  const scrollToBottom = React.useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, []);

  // Runs on mount (initial history load), on every new/changed message, and
  // whenever `isFollowing` itself flips back on (the "Scroll to latest"
  // click) — but only actually moves the scroll position while following.
  // `useLayoutEffect`, not `useEffect` (closeout code-review fix): the
  // passive-effect version let the browser paint one frame at the OLD
  // scroll position before this ran, producing a visible jump/flash on
  // every mount, new message, and "Scroll to latest" click. Running
  // synchronously after DOM mutations but before paint eliminates that
  // frame entirely.
  React.useLayoutEffect(() => {
    if (isFollowing) {
      scrollToBottom();
    }
  }, [messages.length, contentSignal, isFollowing, scrollToBottom]);

  function handleScroll() {
    setIsFollowing(isNearBottom());
  }

  function handleScrollToLatest() {
    setIsFollowing(true);
    scrollToBottom();
  }

  if (messages.length === 0) {
    return (
      <p className={`text-body text-muted-foreground ${className}`} role="status">
        No messages yet. Say hello to get started.
      </p>
    );
  }

  return (
    <div className={`relative overflow-hidden ${className}`}>
      <div
        ref={containerRef}
        onScroll={handleScroll}
        role="log"
        aria-label="Chat transcript"
        className="h-full overflow-y-auto"
      >
        <div className="space-y-3">
          {messages.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))}
        </div>
      </div>
      {!isFollowing && (
        <div className="pointer-events-none absolute inset-x-0 bottom-2 flex justify-center">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="pointer-events-auto shadow-high"
            onClick={handleScrollToLatest}
          >
            Scroll to latest
          </Button>
        </div>
      )}
    </div>
  );
}
