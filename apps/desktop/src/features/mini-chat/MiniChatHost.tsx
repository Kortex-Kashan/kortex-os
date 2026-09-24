import * as React from "react";
import { motion } from "motion/react";
import { Button, Card, CardContent, CardHeader, CardTitle, Skeleton } from "@kortex/design-system";
import { motionTokens } from "@kortex/design-system/tokens";
import { useAuth } from "@/auth/AuthProvider";
import { Composer } from "@/features/ai-studio/components/Composer";
import { MessageList } from "@/features/ai-studio/components/MessageList";
import { TERMINAL_AGENT_STATUSES } from "@/features/ai-studio/chat-types";
import { useAgentStatus } from "@/features/ai-studio/hooks/useAgentStatus";
import { useAiModels } from "@/features/ai-studio/hooks/useAiModels";
import { useConversation } from "@/features/ai-studio/hooks/useConversation";
import { useApplicationNavigation } from "@/navigation/navigationBridge";
import { useUiStore, type CopilotTab } from "@/stores/uiStore";
import { AiStudioIcon, WorkflowIcon } from "@/workspace/icons";
import { AgentsTab } from "./components/AgentsTab";
import { ConversationSelector } from "./components/ConversationSelector";
import { McpTab } from "./components/McpTab";
import { ToolsTab } from "./components/ToolsTab";

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

export function MiniChatHost() {
  const { state } = useAuth();
  const tenantId = state.status === "AUTHENTICATED" && state.identity ? state.identity.tenantId : "";
  const userId = state.status === "AUTHENTICATED" && state.identity ? state.identity.principalId : "";

  const [open, setOpen] = React.useState(false);
  const [activeTab, setActiveTab] = React.useState<CopilotTab>("chat");
  const copilotMode = useUiStore((s) => s.copilotMode);
  const setCopilotMode = useUiStore((s) => s.setCopilotMode);

  React.useEffect(() => {
    const onToggle = () => setOpen((prev) => !prev);
    const onOpen = () => setOpen(true);
    const onClose = () => setOpen(false);
    window.addEventListener("kortex:toggle-copilot", onToggle);
    window.addEventListener("kortex:open-copilot", onOpen);
    window.addEventListener("kortex:close-copilot", onClose);
    return () => {
      window.removeEventListener("kortex:toggle-copilot", onToggle);
      window.removeEventListener("kortex:open-copilot", onOpen);
      window.removeEventListener("kortex:close-copilot", onClose);
    };
  }, []);

  const { navigateToApplication } = useApplicationNavigation();

  const {
    conversationId,
    messages,
    isLoadingHistory,
    historyError,
    isSending,
    pendingTaskId,
    sendMessage,
    resolvePendingApproval,
    switchConversation,
    startNewConversation,
  } = useConversation({ tenantId, userId });

  const modelsQuery = useAiModels();
  const activeModelLabel = React.useMemo(() => {
    if (!modelsQuery.data || modelsQuery.data.length === 0) {
      return "Auto Routing";
    }
    const first = modelsQuery.data[0];
    return first.modelId || first.providerDisplayName || "Auto Routing";
  }, [modelsQuery.data]);

  const statusQuery = useAgentStatus(pendingTaskId ?? "", tenantId, pendingTaskId !== null);
  const observedStatus = statusQuery.data?.status;

  React.useEffect(() => {
    if (!pendingTaskId || !observedStatus) return;
    if (!TERMINAL_AGENT_STATUSES.includes(observedStatus)) return;
    void resolvePendingApproval(pendingTaskId, observedStatus);
  }, [pendingTaskId, observedStatus, resolvePendingApproval]);

  const isDocked = copilotMode === "docked";

  return (
    <div className={`pointer-events-none fixed z-50 transition-all ${isDocked ? "bottom-3 right-3" : "bottom-4 right-4"}`}>
      <div className="relative">
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
            className={`flex flex-col shadow-high border-border/80 bg-background/95 backdrop-blur-xl transition-all ${
              isDocked
                ? "h-[calc(100vh-4.5rem)] w-[28rem] max-w-[calc(100vw-2rem)]"
                : "h-[34rem] w-96 max-w-[calc(100vw-2rem)]"
            }`}
          >
            <CardHeader className="flex flex-col space-y-0 border-b border-border py-2.5 px-3">
              <div className="flex flex-row items-center justify-between">
                <div className="flex items-center gap-2">
                  <CardTitle className="flex items-center gap-2 font-display text-body">
                    <AiStudioIcon className="size-4 text-primary" aria-hidden="true" />
                    KORTEX <span className="text-primary">AI</span>
                  </CardTitle>
                  <span
                    className="hidden rounded-full border border-primary/20 bg-primary/5 px-2 py-0.5 text-[11px] font-mono text-primary sm:inline"
                    title="Active model route"
                  >
                    {activeModelLabel}
                  </span>
                </div>
                <div className="flex items-center gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0"
                    onClick={() => setCopilotMode(isDocked ? "floating" : "docked")}
                    aria-label={isDocked ? "Float Copilot" : "Dock Copilot"}
                    title={isDocked ? "Floating mode" : "Expand to tall panel"}
                    tabIndex={open ? undefined : -1}
                  >
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={2}
                      className="size-3.5"
                    >
                      {isDocked ? (
                        <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />
                      ) : (
                        <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                      )}
                    </svg>
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0"
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
                    className="h-7 w-7 p-0"
                    onClick={() => setOpen(false)}
                    aria-label="Collapse KORTEX AI assistant"
                    tabIndex={open ? undefined : -1}
                  >
                    <CollapseIcon className="size-4" />
                  </Button>
                </div>
              </div>

              {/* Sub-header tabs for Chat, Agents, Tools, MCP */}
              <div className="mt-2 flex items-center justify-between gap-1 border-t border-border/40 pt-1.5">
                <div className="flex gap-1" role="tablist" aria-label="Copilot tabs">
                  {(["chat", "agents", "tools", "mcp"] as const).map((tab) => (
                    <button
                      key={tab}
                      role="tab"
                      aria-selected={activeTab === tab}
                      onClick={() => setActiveTab(tab)}
                      className={`rounded px-2 py-0.5 text-xs font-medium capitalize transition-colors ${
                        activeTab === tab
                          ? "bg-primary text-primary-foreground"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground"
                      }`}
                      tabIndex={open ? 0 : -1}
                    >
                      {tab}
                    </button>
                  ))}
                </div>

                {activeTab === "chat" && (
                  <ConversationSelector
                    activeConversationId={conversationId}
                    onSelectConversation={switchConversation}
                    onNewChat={startNewConversation}
                  />
                )}
              </div>
            </CardHeader>

            <CardContent className="flex flex-1 flex-col gap-3 overflow-hidden p-3 pt-3">
              {activeTab === "chat" && (
                <>
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
                      <MessageList messages={messages} className="flex-1" />
                    </>
                  )}
                  <Composer
                    onSend={sendMessage}
                    disabled={pendingTaskId !== null}
                    sendDisabled={isSending}
                    placeholder="Message KORTEX AI..."
                  />
                </>
              )}

              {activeTab === "agents" && <AgentsTab tenantId={tenantId} />}

              {activeTab === "tools" && <ToolsTab />}

              {activeTab === "mcp" && <McpTab />}
            </CardContent>
          </Card>
        </motion.div>

        {/* Launcher */}
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
            className="gap-2 rounded-full shadow-high border border-primary/30"
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

