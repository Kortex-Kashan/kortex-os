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
 * Collects one provider API key and hands it to `onSubmit`.
 *
 * The key lives in this component's state for exactly as long as the dialog
 * is open and is cleared whenever it closes (see the effect below) — so a
 * dialog reopened after a successful save never presents the previous key,
 * and a key is not left sitting in a mounted component's state behind a
 * closed dialog.
 *
 * `type="password"` is masking for the person at the keyboard; it is not a
 * security control and is not relied on as one. The actual guarantees are
 * elsewhere and are structural: the key is sent once, to
 * `kortex.ai.provider.configure`, under the parameter name `api_key` that
 * the Kernel's audit sanitizer redacts by exact match; the backend hands it
 * straight to `SecretStore`; and no response shape in this feature has a
 * field a stored key or its handle could come back in.
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
  const isReplacing = config?.hasCredential === true;

  useEffect(() => {
    if (!open) {
      setApiKey("");
    }
  }, [open]);

  const trimmed = apiKey.trim();
  const canSubmit = trimmed.length > 0 && !isSubmitting;

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
