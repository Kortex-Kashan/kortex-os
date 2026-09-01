/**
 * A stable per-tenant/per-user conversation id, so a reload or app relaunch
 * resumes the SAME conversation rather than starting a new, empty one. This
 * is the only piece of chat state kept in `localStorage` -- an opaque
 * pointer, never the transcript itself. The transcript is always
 * reconstructed from the backend's durable conversation history (M7.2 §2.1);
 * this id merely says which conversation to ask for.
 */

const STORAGE_KEY_PREFIX = "kortex.ai-studio.chat.conversation-id";

export function getOrCreateConversationId(tenantId: string, userId: string): string {
  const key = `${STORAGE_KEY_PREFIX}:${tenantId}:${userId}`;
  try {
    const existing = window.localStorage.getItem(key);
    if (existing) return existing;
    const generated = crypto.randomUUID();
    window.localStorage.setItem(key, generated);
    return generated;
  } catch {
    // localStorage unavailable (private mode, disabled site data, etc.) --
    // fall back to a fresh id for this session; history hydration will
    // simply come back empty rather than throwing.
    return crypto.randomUUID();
  }
}
