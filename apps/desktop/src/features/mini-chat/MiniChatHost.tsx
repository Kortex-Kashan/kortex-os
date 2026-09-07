import * as React from "react";
import { AnimatePresence, motion } from "motion/react";
import { Button, Card, CardContent, CardHeader, CardTitle, Skeleton } from "@kortex/design-system";
import { motionTokens } from "@kortex/design-system/tokens";
import { useAuth } from "@/auth/AuthProvider";
import { Composer } from "@/features/ai-studio/components/Composer";
import { MessageList } from "@/features/ai-studio/components/MessageList";
import { TERMINAL_AGENT_STATUSES } from "@/features/ai-studio/chat-types";
import { useAgentStatus } from "@/features/ai-studio/hooks/useAgentStatus";
import { useConversation } from "@/features/ai-studio/hooks/useConversation";
import { AiStudioIcon } from "@/workspace/icons";

function CollapseIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}

/**
 * The persistent KORTEX assistant, mounted once at the authenticated shell
 * level (`DesktopShell.tsx`) — a sibling of `Workspace`'s route outlet, not
 * a descendant of it. This is the one property that makes it "Mini Chat"
 * rather than a second copy of the AI Studio Chat tab: it never unmounts on
 * route navigation, because it is never inside the tree that changes when
 * the route does.
 *
 * Owns exactly two things: the open/collapsed presentation state (plain
 * `useState`, no persistence — not required for this milestone), and the
 * shell-level `useConversation`/`useAgentStatus` calls. It owns no AI
 * execution logic of its own — sending, history, approval-pause handling,
 * and error rendering are the exact same `useConversation` hook and
 * `Composer`/`MessageList` components the AI Studio Chat tab already uses,
 * imported directly rather than copied. `ChatPanel.tsx` (AI Studio's own
 * host for these same pieces) is untouched.
 *
 * `useConversation`/`useAgentStatus` are called unconditionally, not
 * gated behind `open`: history must load exactly once when the
 * authenticated shell mounts, not once per time the panel is opened, and
 * React's rules of hooks forbid calling them only when `open` is true
 * anyway. Toggling `open` only changes what is rendered, never what is
 * fetched.
 *
 * Identity lifecycle (verified against `AuthGate.tsx`/`routes/index.tsx`
 * before this component was written, not assumed): this component lives
 * inside `AuthGate`'s `children`, which React unmounts entirely — along
 * with `DesktopShell` and everything inside it — the moment `AuthProvider`'s
 * state leaves `"AUTHENTICATED"` (logout, a 401, backend loss), and mounts
 * fresh again on the next successful login. There is therefore no code path
 * by which this component's own `messages` state could survive a change of
 * authenticated identity: it does not merely reset, it does not exist
 * during the gap, and a fresh instance is created for whichever identity is
 * authenticated next. The `chat-conversation-id.ts` localStorage pointer
 * this component's `useConversation` call resolves is independently
 * namespaced by `(tenantId, userId)`, so even that survives an identity
 * change safely -- a new identity resolves a different key, never the
 * previous user's.
 */
export function MiniChatHost() {
  const { state } = useAuth();
  const tenantId = state.status === "AUTHENTICATED" && state.identity ? state.identity.tenantId : "";
  const userId = state.status === "AUTHENTICATED" && state.identity ? state.identity.principalId : "";

  const [open, setOpen] = React.useState(false);

  const { messages, isLoadingHistory, historyError, isSending, pendingTaskId, sendMessage, resolvePendingApproval } =
    useConversation({ tenantId, userId });

  const statusQuery = useAgentStatus(pendingTaskId ?? "", tenantId, pendingTaskId !== null);
  const observedStatus = statusQuery.data?.status;

  React.useEffect(() => {
    if (!pendingTaskId || !observedStatus) return;
    if (!TERMINAL_AGENT_STATUSES.includes(observedStatus)) return;
    void resolvePendingApproval(pendingTaskId, observedStatus);
  }, [pendingTaskId, observedStatus, resolvePendingApproval]);

  // Nothing to host until an authenticated identity actually exists —
  // mirrors `AiStudioApp.tsx`'s own `tenantId`/`userId` derivation above
  // rather than inventing a stricter guard: a momentarily empty tenantId
  // resolves to an empty, harmless history query, exactly as it already
  // does for AI Studio's own Chat tab.
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col items-end">
      <AnimatePresence mode="wait" initial={false}>
        {open ? (
          <motion.div
            key="panel"
            initial={{ opacity: 0, scale: 0.96, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 8 }}
            transition={{ duration: motionTokens.duration.base, ease: motionTokens.easing.standard }}
            className="pointer-events-auto"
          >
            <Card
              role="region"
              aria-label="KORTEX AI assistant"
              className="flex h-[32rem] w-96 max-w-[calc(100vw-2rem)] flex-col shadow-high"
            >
              <CardHeader className="flex flex-row items-center justify-between space-y-0 border-b border-border py-3">
                <CardTitle className="flex items-center gap-2 text-body">
                  <AiStudioIcon className="size-4" aria-hidden="true" />
                  KORTEX AI
                </CardTitle>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setOpen(false)}
                  aria-label="Collapse KORTEX AI assistant"
                >
                  <CollapseIcon className="size-4" />
                </Button>
              </CardHeader>
              <CardContent className="flex flex-1 flex-col gap-3 overflow-hidden pt-4">
                <div className="flex-1 overflow-y-auto" aria-live="polite">
                  {isLoadingHistory ? (
                    <div className="space-y-3" role="status" aria-label="Loading conversation">
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
                      <MessageList messages={messages} />
                    </>
                  )}
                </div>
                <Composer
                  onSend={sendMessage}
                  disabled={isSending || pendingTaskId !== null}
                  placeholder="Message KORTEX AI..."
                />
              </CardContent>
            </Card>
          </motion.div>
        ) : (
          <motion.div
            key="launcher"
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.9 }}
            transition={{ duration: motionTokens.duration.fast }}
            className="pointer-events-auto"
          >
            <Button
              type="button"
              onClick={() => setOpen(true)}
              aria-label="Open KORTEX AI assistant"
              className="gap-2 rounded-full shadow-high"
            >
              <AiStudioIcon className="size-4" aria-hidden="true" />
              KORTEX AI
            </Button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
