/**
 * Share a conversation inside the organization (contract 1.11.0, CONTRACT.md §17).
 *
 * The link is read-only and org-scoped: the viewer must be signed in to the same organization, and there are no
 * public links. `includeData` defaults to false, so tables are shown without rows and charts are left out. The
 * owner can revoke it at any time. Open a `ShareLink.url` (`/shared/<shareId>`) in the app, which loads
 * `GET /api/shared/<shareId>`.
 * Never log the share id or put row data in the URL. The share id is a capability inside the org.
 */
import type { ShareLink, SharedConversation } from './v1.gen';

type Opts = { base?: string; init?: RequestInit; fetchImpl?: typeof fetch };

export const shareUrl = (conversationId: string, base = ''): string =>
  `${base}/api/conversations/${encodeURIComponent(conversationId)}/share`;
export const sharedUrl = (shareId: string, base = ''): string => `${base}/api/shared/${encodeURIComponent(shareId)}`;

/** The share id from an app path like `/shared/sh_abc`, or null. */
export function shareIdFromPath(path: string): string | null {
  const m = /^\/shared\/([A-Za-z0-9_-]{1,128})\/?$/.exec(path);
  return m ? m[1] : null;
}

async function call<T>(url: string, init: RequestInit, fetchImpl: typeof fetch, notFound: T): Promise<T> {
  const res = await fetchImpl(url, init);
  if (res.status === 404) return notFound;
  if (!res.ok) throw new Error(`share request failed: ${res.status}`);
  return (await res.json()) as T;
}

const defaults = (o: Opts) => ({
  base: o.base ?? '', init: o.init ?? {}, fetchImpl: o.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a)),
});
const headers = (init: RequestInit) => ({ ...(init.headers as Record<string, string> | undefined), 'Content-Type': 'application/json' });

/** The active share, or null when the conversation isn't shared. */
export function getShare(conversationId: string, o: Opts = {}): Promise<ShareLink | null> {
  const { base, init, fetchImpl } = defaults(o);
  return call<ShareLink | null>(shareUrl(conversationId, base), { ...init, method: 'GET' }, fetchImpl, null);
}

/** Create the share, or update `includeData` on the existing one. Rejects on 404 (not your conversation). */
export async function setShare(conversationId: string, includeData = false, o: Opts = {}): Promise<ShareLink> {
  const { base, init, fetchImpl } = defaults(o);
  const link = await call<ShareLink | null>(shareUrl(conversationId, base),
    { ...init, method: 'PUT', headers: headers(init), body: JSON.stringify({ includeData }) }, fetchImpl, null);
  if (!link) throw new Error('share request failed: 404');
  return link;
}

/** Revoke. Resolves true when a share was revoked, false when there was none. */
export async function revokeShare(conversationId: string, o: Opts = {}): Promise<boolean> {
  const { base, init, fetchImpl } = defaults(o);
  const r = await call<{ revoked: boolean }>(shareUrl(conversationId, base),
    { ...init, method: 'DELETE', headers: headers(init) }, fetchImpl, { revoked: false });
  return r.revoked;
}

/** The read-only view, or null for unknown, revoked or other-organization links (all of them are a 404). */
export function loadShared(shareId: string, o: Opts = {}): Promise<SharedConversation | null> {
  const { base, init, fetchImpl } = defaults(o);
  return call<SharedConversation | null>(sharedUrl(shareId, base), { ...init, method: 'GET' }, fetchImpl, null);
}
