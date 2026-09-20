import { useEffect, useState } from "react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
} from "@kortex/design-system";
import { useTestAiProviderConnection } from "../hooks/useAiProviderConfigs";
import type { AiProvider, AiProviderConfig } from "../types";

interface ProviderConfigDialogProps {
  provider: AiProvider;
  config: AiProviderConfig | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (apiKey: string) => void;
  isSubmitting: boolean;
  submitError: Error | null;
}

/**
 * Collects one provider API key, requires a successful ad-hoc test of that
 * exact key, and only then hands it to `onSubmit`.
 *
 * Flow (AI Studio functional stabilization §4): enter key -> Test key ->
 * only on a successful test does Save Key become enabled. This replaces the
 * previous "enter key -> save immediately -> hope it works" flow. The
 * dialog's own "Test key" button (deliberately not labeled "Test
 * connection" — the card's separate, pre-existing button of that name tests
 * the already-*stored* credential, a different action) runs against the
 * typed key itself (`kortex.ai.provider.test`'s optional `api_key`
 * parameter — see `engine.py::test_provider_connection`), never against
 * whatever is already stored, and never persists that key itself; only an
 * explicit "Save key" click, after a successful test, calls `onSubmit` and
 * thus `kortex.ai.provider.configure`.
 *
 * Editing the key after a successful test immediately invalidates that
 * result (`testedKey` tracks exactly which string was last confirmed) — the
 * user cannot test key A and then silently save a since-edited key B.
 *
 * The key lives in this component's state for exactly as long as the dialog
 * is open and is cleared whenever it closes (see the effect below) — so a
 * dialog reopened after a successful save never presents the previous key,
 * and a key is not left sitting in a mounted component's state behind a
 * closed dialog.
 *
 * `type="password"` is masking for the person at the keyboard; it is not a
 * security control and is not relied on as one. The actual guarantees are
 * elsewhere and are structural: the key is sent once (to the ad-hoc test)
 * and, only after that succeeds and the user separately confirms, a second
 * time (to `kortex.ai.provider.configure`) — both under the parameter name
 * `api_key` that the Kernel's audit sanitizer redacts by exact match; the
 * backend hands the saved value straight to `SecretStore`; and no response
 * shape in this feature has a field a stored key or its handle could come
 * back in.
 */
export function ProviderConfigDialog({
  provider,
  config,
  open,
  onOpenChange,
  onSubmit,
  isSubmitting,
  submitError,
}: ProviderConfigDialogProps) {
  const [apiKey, setApiKey] = useState("");
  const [testedKey, setTestedKey] = useState<string | null>(null);
  const isReplacing = config?.hasCredential === true;
  const test = useTestAiProviderConnection();

  useEffect(() => {
    if (!open) {
      setApiKey("");
      setTestedKey(null);
      test.reset();
    }
    // `test` (a useMutation result) is a stable reference across renders;
    // only `open` should re-run this reset.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const trimmed = apiKey.trim();
  const testIsCurrent = testedKey !== null && testedKey === trimmed;
  const testSucceeded = testIsCurrent && test.data?.connected === true;
  const testFailed = testIsCurrent && test.data?.connected === false;
  const canTest = trimmed.length > 0 && !isSubmitting && !test.isPending;
  const canSubmit = testSucceeded && !isSubmitting;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {isReplacing ? "Replace" : "Configure"} {provider.displayName}
          </DialogTitle>
          <DialogDescription>
            {isReplacing
              ? "Entering a new key replaces the stored one. The existing key cannot be displayed — it is held by the Security Engine and is never returned to this app."
              : `Your ${provider.vendor} API key is stored by the Security Engine and never returned to this app. Requests to ${provider.displayName} are made by the KORTEX backend, never from this window.`}
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (canSubmit) {
              onSubmit(trimmed);
            }
          }}
          className="space-y-4"
        >
          <div className="space-y-2">
            <Label htmlFor={`api-key-${provider.providerId}`}>API key</Label>
            <Input
              id={`api-key-${provider.providerId}`}
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={isReplacing ? "Enter a new key to replace the stored one" : "Paste your API key"}
              disabled={isSubmitting}
            />
          </div>

          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!canTest}
              onClick={() => {
                test.mutate(
                  { providerId: provider.providerId, apiKey: trimmed },
                  { onSuccess: () => setTestedKey(trimmed) },
                );
              }}
            >
              {test.isPending ? "Testing…" : "Test key"}
            </Button>
            {testSucceeded && (
              <p role="status" className="text-caption text-muted-foreground" data-testid="dialog-test-feedback">
                Connection successful
                {test.data && test.data.models.length > 0 ? ` — ${test.data.models.length} models found.` : "."}
              </p>
            )}
            {testFailed && (
              <p role="alert" className="text-caption text-destructive" data-testid="dialog-test-feedback">
                Connection failed{test.data?.detail ? `: ${test.data.detail}` : "."}
              </p>
            )}
            {test.error !== null && (
              <p role="alert" className="text-caption text-destructive" data-testid="dialog-test-feedback">
                {test.error.message}
              </p>
            )}
          </div>

          {submitError !== null && (
            <p role="alert" className="text-caption text-destructive">
              {submitError.message}
            </p>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {isSubmitting ? "Saving…" : "Save key"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
