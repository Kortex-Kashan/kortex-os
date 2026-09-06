import * as React from "react";
import { onOpenUrl } from "@tauri-apps/plugin-deep-link";

export interface OAuthCallbackParams {
  code: string;
  state: string;
}

/**
 * Phase A: listens for the `kortex-auth://oauth-callback` deep link while
 * mounted, invoking `onCallback` with the parsed `code`/`state` query
 * params. Ignores any URL that isn't the `kortex-auth:` scheme or is
 * missing either param, rather than throwing from a global listener.
 *
 * Registered locally by whichever screen is running a pending OAuth flow
 * (`LoginScreen` for sign-in, the Account app's linked-providers section
 * for linking) — each is mounted for exactly the duration of its own flow,
 * so there is no need for a single app-wide listener.
 */
export function useOAuthDeepLink(onCallback: (params: OAuthCallbackParams) => void): void {
  const callbackRef = React.useRef(onCallback);
  callbackRef.current = onCallback;

  React.useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;

    void onOpenUrl((urls) => {
      for (const raw of urls) {
        let url: URL;
        try {
          url = new URL(raw);
        } catch {
          continue;
        }
        if (url.protocol !== "kortex-auth:") {
          continue;
        }
        const code = url.searchParams.get("code");
        const state = url.searchParams.get("state");
        if (code && state) {
          callbackRef.current({ code, state });
        }
      }
    }).then((fn) => {
      if (cancelled) {
        fn();
      } else {
        unlisten = fn;
      }
    });

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);
}
