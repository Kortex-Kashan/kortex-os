import * as React from "react";
import { motion } from "motion/react";
import { Button, Card, CardContent, CardHeader, CardTitle, Skeleton } from "@kortex/design-system";
import { motionTokens } from "@kortex/design-system/tokens";
import { useAuth } from "@/auth/AuthProvider";
import { Composer } from "@/features/ai-studio/components/Composer";
import { MessageList } from "@/features/ai-studio/components/MessageList";
import { TERMINAL_AGENT_STATUSES } from "@/features/ai-studio/chat-types";
import { useAgentStatus } from "@/features/ai-studio/hooks/useAgentStatus";
import { useConversation } from "@/features/ai-studio/hooks/useConversation";
import { useApplicationNavigation } from "@/navigation/navigationBridge";
import { AiStudioIcon, WorkflowIcon } from "@/workspace/icons";

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
 *
 * Panel persistence (AI Studio functional stabilization, Phase D): the
 * panel `Card` -- and therefore `MessageList`'s scroll container and its
 * own `isFollowing`/scroll-position state -- is now ALWAYS mounted, never
 * conditionally rendered behind `open`. The previous implementation used
 * `AnimatePresence mode="wait"` to swap between two conditionally-rendered
 * branches (panel vs. launcher), which meant collapsing Mini Chat genuinely
 * unmounted the panel -- destroying `MessageList`'s local scroll state --
 * and reopening it mounted a brand-new instance that always started
 * scrolled to the top, regardless of where the user had been reading.
 * `open` now only toggles opacity/scale/`pointer-events`/`aria-hidden` on
 * two permanently-mounted `motion.div`s stacked in the same `relative`
 * anchor point, so a viewer who was mid-scroll — or who had deliberately
 * scrolled away from the bottom — sees exactly that same state on reopen.
 * `aria-hidden` is what keeps `getByRole`/`queryByRole("region", ...)`
 * finding nothing while collapsed, matching the previous
 * mount/unmount-based behavior's observable contract exactly, without
 * actually destroying anything.
 *
 * Workflow Builder entry point (AI Workflow Builder milestone; retargeted by
 * AI Studio functional stabilization Phase E): one compact header button
 * deep-links to the Workflow Engine app's "AI Automation" tab via
 * `navigateToApplication({ applicationId: "workflow-engine", search:
 * "?tab=aiAutomation" })` -- the exact same `?tab=` deep-link convention
 * `ToolCallCard.tsx` already uses to jump into the Workflow Approval Queue.
 * Previously targeted AI Studio's own `workflowBuilder` tab; that tab (and
 * the AI Workflow Builder's whole implementation) has since moved into the
 * Workflow Engine app, so this link moved with it. This is a pure
 * navigation action: Mini Chat renders no Workflow Builder UI of its own,
 * holds no builder state, and calls no builder API. The single
 * `WorkflowBuilderPanel` instance Workflow Engine now renders is what the
 * user lands on -- there is no second builder implementation here to keep
 * in sync with the first.
 */
export function MiniChatHost() {
  const { state } = useAuth();
  const tenantId = state.status === "AUTHENTICATED" && state.identity ? state.identity.tenantId : "";
  const userId = state.status === "AUTHENTICATED" && state.identity ? state.identity.principalId : "";

  const [open, setOpen] = React.useState(false);
  const { navigateToApplication } = useApplicationNavigation();

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
    <div className="pointer-events-none fixed bottom-4 right-4 z-50">
      <div className="relative">
        {/* Panel -- ALWAYS mounted (Phase D). Toggling `open` only animates
        opacity/scale/position and flips `pointer-events`/`aria-hidden`; the
        underlying Card, MessageList, and its scroll state are never torn
        down. Absolutely positioned against the `relative` wrapper so it
        overlays the same bottom-right anchor the launcher occupies, rather
        than the two stacking in a visible flex column while both mounted.
        `inert` (closeout code-review fix) removes the ENTIRE subtree from
        the tab order and from hit-testing/AT exposure while collapsed --
        `aria-hidden` + CSS `pointer-events: none` alone do not affect
        keyboard tab order, so a keyboard user could previously Tab into
        the invisible Composer/"Scroll to latest" button even though the
        two header buttons were individually given `tabIndex={-1}`. `inert`
        covers every focusable descendant automatically, including ones
        added to Composer/MessageList in the future, without needing to
        enumerate them here. */}
        <motion.div
          initial={false}
          animate={
            open
              ? { opacity: 1, scale: 1, y: 0, pointerEvents: "auto" }
              : { opacity: 0, scale: 0.96, y: 8, pointerEvents: "none" }
          }
          transition={{ duration: motionTokens.duration.base, ease: motionTokens.easing.standard }}
          className="absolute bottom-0 right-0"
          aria-hidden={!open}
          inert={!open}
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
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    navigateToApplication({ applicationId: "workflow-engine", search: "?tab=aiAutomation" })
                  }
                  aria-label="Open AI Workflow Builder"
                  title="AI Workflow Builder"
                  tabIndex={open ? undefined : -1}
                >
                  <WorkflowIcon className="size-4" aria-hidden="true" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setOpen(false)}
                  aria-label="Collapse KORTEX AI assistant"
                  tabIndex={open ? undefined : -1}
                >
                  <CollapseIcon className="size-4" />
                </Button>
              </div>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-3 overflow-hidden pt-4">
              {isLoadingHistory ? (
                <div className="flex-1 space-y-3" role="status" aria-label="Loading conversation">
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
                  {/* The scroll container and its auto-follow/"Scroll to
                  latest" behavior live in `MessageList` itself (shared
                  with `ChatPanel.tsx`) — this host no longer wraps it in
                  its own `overflow-y-auto` div, so there is exactly one
                  implementation of that behavior, not two kept in sync by
                  hand. `role="log"` on that shared component's own root
                  already conveys the same "announce new content" ARIA
                  live-region semantics the removed `aria-live="polite"`
                  on this wrapper provided. Being permanently mounted
                  (Phase D) is exactly what lets its internal scroll-follow
                  state survive a collapse/reopen cycle. */}
                  <MessageList messages={messages} className="flex-1" />
                </>
              )}
              <Composer
                onSend={sendMessage}
                disabled={pendingTaskId !== null}
                sendDisabled={isSending}
                placeholder="Message KORTEX AI..."
              />
            </CardContent>
          </Card>
        </motion.div>

        {/* Launcher -- also always mounted, inverse visibility. Occupies
        real layout space (not absolutely positioned) so the outer
        container has a non-zero footprint while collapsed; the panel
        overlays it when open. */}
        <motion.div
          initial={false}
          animate={
            open
              ? { opacity: 0, scale: 0.9, pointerEvents: "none" }
              : { opacity: 1, scale: 1, pointerEvents: "auto" }
          }
          transition={{ duration: motionTokens.duration.fast }}
          aria-hidden={open}
          inert={open}
        >
          <Button
            type="button"
            onClick={() => setOpen(true)}
            aria-label="Open KORTEX AI assistant"
            className="gap-2 rounded-full shadow-high"
            tabIndex={open ? -1 : undefined}
          >
            <AiStudioIcon className="size-4" aria-hidden="true" />
            KORTEX AI
          </Button>
        </motion.div>
      </div>
    </div>
  );
}
