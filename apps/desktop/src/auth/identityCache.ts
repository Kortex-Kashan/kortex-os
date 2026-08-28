import type { AuthIdentity } from "./authTypes";

const STORAGE_KEY = "kortex.auth.identity.v1";

/**
 * A *display-only* cache of the last known identity — never a credential,
 * never a token, never anything Rust's `TokenStore` custody covers. Its
 * only purpose is UX continuity: session restoration (Phase 6) validates a
 * stored token via an inert capability call that returns no identity data
 * (see `authCapability.ts::checkStoredSession`), so without this cache the
 * TopBar would have nothing to show for who is signed in until the next
 * fresh login. `principal_id`/`principal_type`/`tenant_id`/`roles` are
 * ordinary identity metadata, not secrets — nothing here can authenticate
 * a request on its own (unlike `session/sessionStorage.ts`'s explicit
 * "never tokens/credentials" rule, which this respects; the two documents
 * are deliberately kept separate rather than merged into one, since a
 * corrupt/cleared workspace-preferences document must never be able to
 * accidentally take identity display down with it, or vice versa).
 *
 * Written only right after a successful login; read only to pre-populate
 * display while a restored session is still being validated; cleared on
 * logout and whenever a stored session turns out to be invalid.
 */

function isAuthIdentity(value: unknown): value is AuthIdentity {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.principalId === "string" &&
    typeof candidate.principalType === "string" &&
    typeof candidate.tenantId === "string" &&
    Array.isArray(candidate.roles) &&
    candidate.roles.every((role) => typeof role === "string")
  );
}

export function loadCachedIdentity(): AuthIdentity | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    return isAuthIdentity(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function saveCachedIdentity(identity: AuthIdentity): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(identity));
  } catch {
    // Best-effort — a full/unavailable localStorage must never break login.
  }
}

export function clearCachedIdentity(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Best-effort — see saveCachedIdentity.
  }
}
