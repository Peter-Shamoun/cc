/**
 * Per-chat composer drafts. Deliberately memory-only (a module Map), NOT localStorage or
 * sessionStorage: a half-typed question can contain PHI (a member id pasted from a claim), and
 * spec §6.2 allows only allowlisted UI prefs in browser storage. Trade-off: drafts survive switching
 * chats and closing the sidebar, but not a reload. ChatGPT/Claude keep drafts across reloads; we
 * accept that gap on purpose. See docs/frontend-shell.md.
 */
const drafts = new Map<string, string>();
export const NEW_CHAT_KEY = '__new__';
export const getDraft = (key: string) => drafts.get(key) ?? '';
export function setDraft(key: string, text: string) {
  if (text) drafts.set(key, text);
  else drafts.delete(key);
}
/** A new chat's draft moves to the chat once it gets an id. */
export function moveDraft(from: string, to: string) {
  const t = drafts.get(from);
  drafts.delete(from);
  if (t) drafts.set(to, t);
}
export const clearDrafts = () => drafts.clear();
