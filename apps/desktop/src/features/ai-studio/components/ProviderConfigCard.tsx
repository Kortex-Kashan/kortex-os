import { useState } from "react";
import {
  Badge,
  Button,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@kortex/design-system";
import { AiStudioAccessDeniedError } from "../api";
import {
  useConfigureAiProvider,
  useRemoveAiProviderConfig,
  useTestAiProviderConnection,
} from "../hooks/useAiProviderConfigs";
import type { AiProvider, AiProviderConfig } from "../types";
import { ProviderConfigDialog } from "./ProviderConfigDialog";

interface ProviderConfigCardProps {
  provider: AiProvider;
  config: AiProviderConfig | undefined;
}

/**
 * One provider's row in the registry: its identity, its configuration
 * state, and the actions available on it.
 *
 * **Local providers get no credential form.** The gate is
 * `credentialRequirement === "none"`, a signal the provider registry
 * already reports — not a hardcoded `"ollama"` check, which would break the
 * moment a second local provider appeared. Ollama's URL and default model
 * live in operator-level `SystemSettings` (Configuration Engine) and are
 * not tenant-scoped, so a tenant configuration row for it would be inert:
 * nothing reads it, and `OllamaProvider` holds no credential resolver. This
 * card therefore says so plainly rather than offering a form that would
 * appear to work.
 *
 * Feedback is inline, inside this card. `<Toaster />` is not mounted in the
 * app shell (only in the dev component gallery), and mounting it is an
 * app-shell change outside B4's boundary — so a toast would silently
 * render nowhere.
 */
export function ProviderConfigCard({ provider, config }: ProviderConfigCardProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const configure = useConfigureAiProvider();
  const test = useTestAiProviderConnection();
  const remove = useRemoveAiProviderConfig();

  const requiresCredential = provider.credentialRequirement !== "none";
  const isConfigured = config !== undefined;
  const hasCredential = config?.hasCredential === true;

  // Live discovery from the most recent successful test wins over the
  // provider's static `supportedModels`: it is what this tenant's own
  // credential can actually reach right now. Falls back to the static list
  // so a default model can still be chosen before any test has run.
  const discoveredModels = test.data?.models.map((model) => model.modelId) ?? [];
  const selectableModels = discoveredModels.length > 0 ? discoveredModels : provider.supportedModels;

  const busy = configure.isPending || test.isPending || remove.isPending;

  return (
    <li className="rounded-md border border-border p-4 space-y-3" data-testid="ai-provider-card">
      <div className="flex items-start justify-between gap-2">
        <div>
          <span className="text-body font-medium text-foreground">{provider.displayName}</span>
          <p className="text-caption text-muted-foreground">
            {provider.vendor} · {provider.providerId}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {requiresCredential ? (
            hasCredential ? (
              <Badge variant="default" data-testid="provider-config-state">
                Configured
              </Badge>
            ) : (
              <Badge variant="outline" data-testid="provider-config-state">
                Not configured
              </Badge>
            )
          ) : (
            <Badge variant="secondary" data-testid="provider-config-state">
              Local
            </Badge>
          )}
          {isConfigured && !config.enabled && <Badge variant="outline">Disabled</Badge>}
          <Badge variant="secondary">{provider.endpointType}</Badge>
        </div>
      </div>

      {config?.defaultModel != null && (
        <p className="text-caption text-muted-foreground">Default model: {config.defaultModel}</p>
      )}

      {!requiresCredential ? (
        <p className="text-body text-muted-foreground">
          This provider runs locally and needs no API key. Its address and default model are set by
          your administrator in system settings, not here.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={() => setDialogOpen(true)} disabled={busy}>
              {hasCredential ? "Replace key" : "Configure"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => test.mutate(provider.providerId)}
              disabled={busy || !hasCredential}
            >
              {test.isPending ? "Testing…" : "Test connection"}
            </Button>
            {isConfigured && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => remove.mutate(provider.providerId)}
                disabled={busy}
              >
                {remove.isPending ? "Removing…" : "Remove configuration"}
              </Button>
            )}
          </div>

          {hasCredential && selectableModels.length > 0 && (
            <div className="space-y-1">
              <label
                className="text-caption text-muted-foreground"
                htmlFor={`default-model-${provider.providerId}`}
              >
                Default model
                {discoveredModels.length > 0 ? " (from this provider, just now)" : ""}
              </label>
              <Select
                value={config?.defaultModel ?? undefined}
                onValueChange={(modelId) =>
                  configure.mutate({ providerId: provider.providerId, defaultModel: modelId })
                }
                disabled={busy}
              >
                <SelectTrigger id={`default-model-${provider.providerId}`} className="w-72">
                  <SelectValue placeholder="Select a default model" />
                </SelectTrigger>
                <SelectContent>
                  {selectableModels.map((modelId) => (
                    <SelectItem key={modelId} value={modelId}>
                      {modelId}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <ProviderFeedback
            testResult={test.data ?? null}
            testError={test.error}
            // While the dialog is open it owns reporting of its own save
            // failure; surfacing the same error here too would show the
            // user one message twice. Once the dialog is closed this is the
            // only place a configure failure can appear -- which matters
            // for the default-model change, a configure call with no dialog.
            configureError={dialogOpen ? null : configure.error}
            removeError={remove.error}
            configureSucceeded={configure.isSuccess}
            removeSucceeded={remove.isSuccess}
          />

          <ProviderConfigDialog
            provider={provider}
            config={config}
            open={dialogOpen}
            onOpenChange={setDialogOpen}
            isSubmitting={configure.isPending}
            submitError={configure.error}
            onSubmit={(apiKey) => {
              configure.mutate(
                { providerId: provider.providerId, apiKey, enabled: true },
                { onSuccess: () => setDialogOpen(false) },
              );
            }}
          />
        </>
      )}
    </li>
  );
}

function messageFor(error: Error): string {
  if (error instanceof AiStudioAccessDeniedError) {
    return `You do not have permission to manage AI providers. ${error.message}`;
  }
  return error.message;
}

/**
 * Inline result banner for the three mutations plus the connection test.
 *
 * A connection test carrying `connected: true` with a `detail` is reported
 * as a *success with a note*, never as a failure: the backend returns that
 * shape when the credential validated but the separate model-discovery
 * round trip failed on its own, and calling it an error would tell the user
 * their working key is broken.
 */
function ProviderFeedback({
  testResult,
  testError,
  configureError,
  removeError,
  configureSucceeded,
  removeSucceeded,
}: {
  testResult: { connected: boolean; detail: string | null; models: { modelId: string }[] } | null;
  testError: Error | null;
  configureError: Error | null;
  removeError: Error | null;
  configureSucceeded: boolean;
  removeSucceeded: boolean;
}) {
  const failure = testError ?? configureError ?? removeError;
  if (failure !== null) {
    return (
      <p role="alert" className="text-caption text-destructive" data-testid="provider-feedback">
        {messageFor(failure)}
      </p>
    );
  }

  if (testResult !== null) {
    if (!testResult.connected) {
      return (
        <p role="alert" className="text-caption text-destructive" data-testid="provider-feedback">
          Connection failed. {testResult.detail ?? ""}
        </p>
      );
    }
    return (
      <p role="status" className="text-caption text-muted-foreground" data-testid="provider-feedback">
        Connection succeeded
        {testResult.models.length > 0
          ? ` — ${testResult.models.length} model${testResult.models.length === 1 ? "" : "s"} available.`
          : "."}
        {testResult.detail != null ? ` Models could not be listed: ${testResult.detail}` : ""}
      </p>
    );
  }

  if (removeSucceeded) {
    return (
      <p role="status" className="text-caption text-muted-foreground" data-testid="provider-feedback">
        Configuration removed.
      </p>
    );
  }

  if (configureSucceeded) {
    return (
      <p role="status" className="text-caption text-muted-foreground" data-testid="provider-feedback">
        Configuration saved.
      </p>
    );
  }

  return null;
}
