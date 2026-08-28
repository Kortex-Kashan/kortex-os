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

/** The AI Studio workspace: read-only visibility into the provider and
 * model registries, and nothing else — no generation, no agent
 * orchestration, no provider/credential configuration. Providers and
 * models are two independent registries (two capabilities), so each
 * renders its own loading/populated/empty/access-denied/error/retry
 * state via `RegistrySection` rather than one combined state hiding
 * a partial failure. */
export function AiStudioApp() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>AI Studio</CardTitle>
        <CardDescription>
          Provider and model registry — browse only. Generation, agent orchestration, and provider
          configuration are not available yet.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <ProvidersSection />
        <ModelsSection />
      </CardContent>
    </Card>
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
