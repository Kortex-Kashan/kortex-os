import * as React from "react";
import { onOpenUrl } from "@tauri-apps/plugin-deep-link";

export interface ConnectorOAuthCallbackParams {
  code: string;
  state: string;
}

/**
 * Integration Hub M2: listens for the `kortex-connector-auth://oauth-callback`
 * deep link while mounted, invoking `onCallback` with the parsed
 * `code`/`state` query params. A deliberate sibling of `useOAuthDeepLink.ts`
 * (Phase A sign-in) on a **distinct** scheme — `kortex-auth:` vs.
 * `kortex-connector-auth:` — rather than the same hook reused, so an
 * in-flight sign-in and an in-flight "Connect GitHub" never race on the same
 * listener. Ignores any URL that isn't this scheme or is missing either
 * param, rather than throwing from a global listener.
 */
export function useConnectorOAuthDeepLink(onCallback: (params: ConnectorOAuthCallbackParams) => void): void {
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
        if (url.protocol !== "kortex-connector-auth:") {
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
