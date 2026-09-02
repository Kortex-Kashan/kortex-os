import { useState } from "react";
import type { ReactNode } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
} from "@kortex/design-system";
import { AiStudioAccessDeniedError } from "../api";
import { useAiModels } from "../hooks/useAiModels";
import { useAiProviders } from "../hooks/useAiProviders";
import type { AiModel, AiProvider } from "../types";
import { AiGovernanceTab } from "./AiGovernanceTab";
import { ChatPanel } from "./ChatPanel";
import { useAuth } from "@/auth/AuthProvider";

type AiTab = "registry" | "governance" | "chat";

const TAB_LABEL: Record<AiTab, string> = {
  registry: "Providers & Models",
  governance: "Governance",
  chat: "Chat",
};

/** The AI Studio workspace: tabbed between the provider/model registry
 * (read-only), the AI Governance dashboard (M5.6), and Chat (M7.2).
 * Each tab independently manages its own loading/error states. */
export function AiStudioApp() {
  const [activeTab, setActiveTab] = useState<AiTab>("registry");
  const { state } = useAuth();
  const tenantId =
    state.status === "AUTHENTICATED" && state.identity ? state.identity.tenantId : "";
  const userId =
    state.status === "AUTHENTICATED" && state.identity ? state.identity.principalId : "";

  return (
    <div className="space-y-4">
      <nav
        role="tablist"
        aria-label="AI Studio tabs"
        className="flex gap-1 border-b border-border pb-1"
      >
        {(["registry", "governance", "chat"] as AiTab[]).map((tab) => (
          <button
            key={tab}
            role="tab"
            id={`ai-tab-${tab}`}
            aria-selected={activeTab === tab}
            aria-controls={`ai-panel-${tab}`}
            onClick={() => setActiveTab(tab)}
            className={[
              "px-3 py-1.5 text-sm rounded-md transition-colors capitalize",
              activeTab === tab
                ? "bg-primary text-primary-foreground font-medium"
                : "text-muted-foreground hover:text-foreground hover:bg-muted",
            ].join(" ")}
          >
            {TAB_LABEL[tab]}
          </button>
        ))}
      </nav>

      <div
        role="tabpanel"
        id={`ai-panel-${activeTab}`}
        aria-labelledby={`ai-tab-${activeTab}`}
      >
        {activeTab === "registry" && (
          <Card>
            <CardHeader>
              <CardTitle>AI Studio</CardTitle>
              <CardDescription>
                Provider and model registry — browse only. Generation, agent orchestration, and
                provider configuration are not available yet.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <ProvidersSection />
              <ModelsSection />
            </CardContent>
          </Card>
        )}
        {activeTab === "governance" && <AiGovernanceTab tenantId={tenantId} />}
        {activeTab === "chat" && <ChatPanel tenantId={tenantId} userId={userId} />}
      </div>
    </div>
  );
}

function SectionShell({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-4">
        <h3 className="text-heading leading-none tracking-tight">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function ProvidersSection() {
  const { data, isPending, isError, error, refetch, isFetching } = useAiProviders();

  if (isPending) {
    return (
      <SectionShell title="Providers">
        <LoadingRows label="Loading AI providers" />
      </SectionShell>
    );
  }

  if (isError) {
    return (
      <SectionShell title="Providers">
        <FailureState error={error} onRetry={() => void refetch()} />
      </SectionShell>
    );
  }

  const providers = data ?? [];

  return (
    <SectionShell
      title="Providers"
      action={<RefreshButton onRefresh={() => void refetch()} isRefreshing={isFetching} />}
    >
      {providers.length === 0 ? (
        <p className="text-body text-muted-foreground">No AI providers are currently registered.</p>
      ) : (
        <ul className="space-y-3">
          {providers.map((provider) => (
            <ProviderCard key={provider.providerId} provider={provider} />
          ))}
        </ul>
      )}
    </SectionShell>
  );
}

function ModelsSection() {
  const { data, isPending, isError, error, refetch, isFetching } = useAiModels();

  if (isPending) {
    return (
      <SectionShell title="Models">
        <LoadingRows label="Loading AI models" />
      </SectionShell>
    );
  }

  if (isError) {
    return (
      <SectionShell title="Models">
        <FailureState error={error} onRetry={() => void refetch()} />
      </SectionShell>
    );
  }

  const models = data ?? [];

  return (
    <SectionShell
      title="Models"
      action={<RefreshButton onRefresh={() => void refetch()} isRefreshing={isFetching} />}
    >
      {models.length === 0 ? (
        <p className="text-body text-muted-foreground">No AI models are currently available.</p>
      ) : (
        <ul className="space-y-3">
          {models.map((model) => (
            <ModelCard key={`${model.providerId}:${model.modelId}`} model={model} />
          ))}
        </ul>
      )}
    </SectionShell>
  );
}

function LoadingRows({ label }: { label: string }) {
  return (
    <div className="space-y-3" role="status" aria-label={label}>
      <Skeleton className="h-14 w-full" />
      <Skeleton className="h-14 w-full" />
    </div>
  );
}

function RefreshButton({ onRefresh, isRefreshing }: { onRefresh: () => void; isRefreshing: boolean }) {
  return (
    <Button variant="outline" size="sm" onClick={onRefresh} disabled={isRefreshing}>
      Refresh
    </Button>
  );
}

function FailureState({ error, onRetry }: { error: Error; onRetry: () => void }) {
  if (error instanceof AiStudioAccessDeniedError) {
    return (
      <div className="space-y-2">
        <Badge variant="destructive">Access denied</Badge>
        <p className="text-body text-muted-foreground">You do not have permission to view this registry.</p>
        <p className="text-caption text-muted-foreground">{error.message}</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-body text-muted-foreground">Something went wrong loading this registry.</p>
      <p className="text-caption text-muted-foreground">{error.message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

function ProviderCard({ provider }: { provider: AiProvider }) {
  return (
    <li className="rounded-md border border-border p-4" data-testid="ai-provider-card">
      <div className="flex items-center justify-between gap-2">
        <span className="text-body font-medium text-foreground">{provider.displayName}</span>
        <Badge variant="secondary">{provider.endpointType}</Badge>
      </div>
      <p className="text-caption text-muted-foreground">
        {provider.vendor} · {provider.providerId}
        {provider.credentialRequirement !== "none" ? ` · requires ${provider.credentialRequirement}` : ""}
      </p>
      {provider.supportedModels.length > 0 && (
        <p className="mt-2 text-body text-muted-foreground">Models: {provider.supportedModels.join(", ")}</p>
      )}
    </li>
  );
}

function ModelCard({ model }: { model: AiModel }) {
  return (
    <li className="rounded-md border border-border p-4" data-testid="ai-model-card">
      <div className="flex items-center justify-between gap-2">
        <span className="text-body font-medium text-foreground">{model.modelId}</span>
        <Badge variant="secondary">{model.providerDisplayName}</Badge>
      </div>
      <p className="text-caption text-muted-foreground">{model.providerId}</p>
    </li>
  );
}
