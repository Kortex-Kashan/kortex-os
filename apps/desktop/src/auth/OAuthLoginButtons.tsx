import * as React from "react";
import { Button, Spinner } from "@kortex/design-system";
import { open as openUrl } from "@tauri-apps/plugin-shell";

import { useAuth } from "./AuthProvider";
import { beginOAuthLogin, getOAuthConfig, type OAuthConfig, type OAuthProviderId } from "./oauthCapability";
import { useOAuthDeepLink } from "./useOAuthDeepLink";

const PROVIDER_LABELS: Record<OAuthProviderId, string> = {
  google: "Google",
  microsoft: "Microsoft",
};

/**
 * Phase A: "Continue with Google/Microsoft" — real OAuth, gated on
 * `kortex.security.oauth.get_config`'s report. A provider with no
 * configured client ID/secret renders disabled with a tooltip, never a
 * button that silently fails (per the master brief's "IF a provider is not
 * configured, the UI should say so" requirement).
 */
export function OAuthLoginButtons() {
  const auth = useAuth();
  const [config, setConfig] = React.useState<OAuthConfig | null>(null);
  const [pendingProvider, setPendingProvider] = React.useState<OAuthProviderId | null>(null);
  const pendingStateRef = React.useRef<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    void getOAuthConfig().then((result) => {
      if (!cancelled) {
        setConfig(result);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useOAuthDeepLink(({ code, state }) => {
    if (!pendingProvider || state !== pendingStateRef.current) {
      return;
    }
    const provider = pendingProvider;
    pendingStateRef.current = null;
    setPendingProvider(null);
    void auth.loginWithOAuth(provider, code, state);
  });

  async function handleClick(provider: OAuthProviderId) {
    setPendingProvider(provider);
    const begin = await beginOAuthLogin(provider);
    if (!begin.ok) {
      setPendingProvider(null);
      return;
    }
    pendingStateRef.current = begin.state;
    await openUrl(begin.authorizationUrl);
  }

  if (config === null) {
    return null;
  }
  if (!config.google && !config.microsoft) {
    return null;
  }

  const isBusy = pendingProvider !== null;

  return (
    <div className="flex flex-col gap-2">
      {(["google", "microsoft"] as const).map((provider) => {
        const configured = config[provider];
        const label = `Continue with ${PROVIDER_LABELS[provider]}`;
        return (
          <Button
            key={provider}
            type="button"
            variant="outline"
            disabled={!configured || isBusy}
            aria-busy={pendingProvider === provider}
            onClick={() => void handleClick(provider)}
            title={configured ? undefined : `Ask your administrator to configure ${PROVIDER_LABELS[provider]} sign-in.`}
            className="w-full"
          >
            {pendingProvider === provider ? (
              <>
                <Spinner size={14} />
                Waiting for {PROVIDER_LABELS[provider]}…
              </>
            ) : (
              label
            )}
          </Button>
        );
      })}
    </div>
  );
}
