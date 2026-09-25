import { useEffect, useState } from "react";
import { Button } from "@kortex/design-system";

/** Accepts a normal https/http URL as typed, or a bare host
 * ("example.com") which is normalized to `https://` — the one, minimal
 * normalization Browser-B2 does. Deliberately does not fall back to a
 * search engine (explicitly deferred) and does not attempt to interpret
 * the input as anything other than a URL — an input that still doesn't
 * parse as an absolute URL after this is rejected, not guessed at. */
export function normalizeAddress(input: string): string | null {
  const trimmed = input.trim();
  if (!trimmed) return null;

  const candidate = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(trimmed) ? trimmed : `https://${trimmed}`;

  try {
    const url = new URL(candidate);
    // Only ever navigate the embedded surface to real web content — never
    // let a typed address resolve to a scheme this app treats specially
    // elsewhere (e.g. `tauri://`, `kortex-auth://`), which would otherwise
    // be a way to interpret address-bar text as something other than a URL
    // a website can be reached at.
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.toString();
  } catch {
    return null;
  }
}

interface BrowserToolbarProps {
  url: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  disabled: boolean;
  onNavigate: (url: string) => void;
  onBack: () => void;
  onForward: () => void;
  onReload: () => void;
}

export function BrowserToolbar({
  url,
  loading,
  canGoBack,
  canGoForward,
  disabled,
  onNavigate,
  onBack,
  onForward,
  onReload,
}: BrowserToolbarProps) {
  const [addressInput, setAddressInput] = useState(url);
  const [addressError, setAddressError] = useState(false);

  // The address bar reflects the active tab's real URL whenever it isn't
  // being actively edited — matches ordinary browser toolbar behavior
  // (typing is never clobbered mid-edit by a state refresh).
  useEffect(() => {
    setAddressInput(url);
    setAddressError(false);
  }, [url]);

  function submit() {
    const normalized = normalizeAddress(addressInput);
    if (!normalized) {
      setAddressError(true);
      return;
    }
    setAddressError(false);
    onNavigate(normalized);
  }

  return (
    <div className="flex items-center gap-2 border-b border-border/70 bg-background/85 px-3 py-2 backdrop-blur-xl">
      <Button
        size="icon"
        variant="ghost"
        aria-label="Back"
        disabled={disabled || !canGoBack}
        onClick={onBack}
      >
        <BackIcon className="size-4" />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        aria-label="Forward"
        disabled={disabled || !canGoForward}
        onClick={onForward}
      >
        <ForwardIcon className="size-4" />
      </Button>
      <Button size="icon" variant="ghost" aria-label="Reload" disabled={disabled} onClick={onReload}>
        {loading ? <LoadingSpinnerIcon className="size-4 animate-spin text-cyan" /> : <ReloadIcon className="size-4" />}
      </Button>
      <div className="flex flex-1 items-center gap-2">
        <input
          type="text"
          value={addressInput}
          onChange={(e) => {
            setAddressInput(e.target.value);
            setAddressError(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          placeholder="Enter an address"
          aria-label="Address"
          aria-invalid={addressError}
          disabled={disabled}
          className={`w-full rounded border bg-background px-3 py-1.5 text-body text-foreground focus:outline-none focus:ring-1 focus:ring-primary ${
            addressError ? "border-destructive" : "border-input"
          }`}
          data-testid="browser-address-input"
        />
        <Button size="sm" variant="outline" disabled={disabled} onClick={submit} data-testid="browser-navigate-button">
          Go
        </Button>
      </div>
    </div>
  );
}

function BackIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden {...props}>
      <path d="M15 18l-6-6 6-6" />
    </svg>
  );
}

function ForwardIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden {...props}>
      <path d="M9 18l6-6-6-6" />
    </svg>
  );
}

function ReloadIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden {...props}>
      <path d="M3 12a9 9 0 1 1 2.6 6.3M3 12V6m0 6h6" />
    </svg>
  );
}

function LoadingSpinnerIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden {...props}>
      <path d="M12 3a9 9 0 1 0 9 9" />
    </svg>
  );
}
