/**
 * The ACTIVE conversation pointer for one tenant/user pair, so a reload or
 * app relaunch resumes the SAME conversation rather than starting a new,
 * empty one. This is the only piece of chat state kept in `localStorage` --
 * an opaque pointer, never the transcript itself. The transcript is always
 * reconstructed from the backend's durable conversation history; this id
 * merely says which conversation to ask for.
 *
 * Phase C (durable "Recent Conversations"): a tenant/user now has many
 * conversations, not one fixed id forever -- the authoritative *list* of
 * them comes from `kortex.ai.conversation.list` (`chat-api.ts`), never from
 * anything stored here. This module's only job is remembering which ONE of
 * those the user was last looking at, so "New Chat" and selecting a past
 * conversation from Recent Conversations both work by updating this same
 * pointer -- there is no second, competing notion of "the current
 * conversation" anywhere in the frontend.
 */

const STORAGE_KEY_PREFIX = "kortex.ai-studio.chat.conversation-id";

function storageKey(tenantId: string, userId: string): string {
  return `${STORAGE_KEY_PREFIX}:${tenantId}:${userId}`;
}

/** The currently active conversation id, creating one (and persisting it as
 * the new active pointer) if this tenant/user has never had one -- e.g. a
 * first-ever session. Every subsequent call for the same tenant/user
 * returns whatever is currently active, until `setActiveConversationId` or
 * `startNewConversationId` changes it. */
export function getActiveConversationId(tenantId: string, userId: string): string {
  const key = storageKey(tenantId, userId);
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

/** Makes `conversationId` the active pointer for this tenant/user -- used
 * when the user selects an existing conversation from Recent Conversations.
 * Silently no-ops if `localStorage` is unavailable: the caller's own React
 * state is what actually drives the UI for the rest of that session, this
 * is only what a future reload resumes. */
export function setActiveConversationId(tenantId: string, userId: string, conversationId: string): void {
  try {
    window.localStorage.setItem(storageKey(tenantId, userId), conversationId);
  } catch {
    // Same non-fatal fallback as getActiveConversationId above.
  }
}

/** Generates a brand-new conversation id, makes it the active pointer, and
 * returns it -- the "New Chat" action. A fresh id is never reused: durable
 * history for it starts genuinely empty, exactly like this tenant/user's
 * very first conversation ever did. */
export function startNewConversationId(tenantId: string, userId: string): string {
  const generated = crypto.randomUUID();
  setActiveConversationId(tenantId, userId, generated);
  return generated;
}
