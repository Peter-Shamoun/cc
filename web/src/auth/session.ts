/**
 * The client's one auth seam. Adapters ask an AuthSession for request headers and report 401s to it. Nothing else
 * in the app knows how the user is authenticated, so the real backend swaps the session here, in one place
 * (sessionFromEnv / AppShell).
 *
 * - `demo-token`: `Authorization: Bearer demo:<customer>:<user>`, for the demo API with synthetic data only. The
 *   token is baked into the bundle, so it must have the demo shape (sessionFromEnv and the vite build both check).
 * - `cookie`: the real backend. Its sign-in sets an HttpOnly, Secure, SameSite=Strict `cc_session` cookie that JS
 *   never sees. Requests send `x-cc-csrf: 1`, and the API rejects cookie-authenticated mutations without it.
 *   `onExpired` sends the user to sign in.
 * - `none`: no credentials (the offline mock).
 *
 * Rules: credentials only in headers or cookies, never in a URL, storage or log.
 */
export type AuthKind = 'none' | 'demo-token' | 'cookie';

export interface AuthSession {
  readonly kind: AuthKind;
  /** Extra headers for every /api request. */
  headers(): Record<string, string>;
  /** A request came back 401 / auth_expired. Called at most once per page load. */
  onExpired(): void;
}

export const CSRF_HEADER = 'x-cc-csrf';
/** Same shape the API's DemoAuth accepts (api/backends.py). */
export const DEMO_TOKEN = /^demo:[a-z0-9_]{2,40}:[A-Za-z0-9_.@+-]{1,80}$/;

function once(fn: () => void): () => void {
  let done = false;
  return () => { if (!done) { done = true; fn(); } };
}

export const noSession: AuthSession = { kind: 'none', headers: () => ({}), onExpired: () => {} };

export function demoTokenSession(token: string, onExpired: () => void = () => {}): AuthSession {
  return { kind: 'demo-token', headers: () => ({ authorization: `Bearer ${token}` }), onExpired: once(onExpired) };
}

/**
 * Cross-tab sign-out. Signing out in one tab must not leave a conversation on screen in another tab until that tab
 * happens to make a request. `signOut` posts a constant on a BroadcastChannel (no ids or data). Every cookie
 * session listens and leaves the page, which drops its in-memory answers and drafts. One listener per page.
 */
export const AUTH_CHANNEL = 'cc-auth';
export const SIGNED_OUT = 'signed-out';
let signOutListener: { channel: BroadcastChannel; fns: Set<() => void> } | null = null;
/** This tab is the one signing out: its own navigation wins, the echo on its listener is ignored. */
let signingOut = false;

/** Tests only. */
export function resetSignOutState(): void { signingOut = false; signOutListener?.channel.close(); signOutListener = null; }

export function onSignedOutElsewhere(fn: () => void): () => void {
  if (typeof BroadcastChannel === 'undefined') return () => {};
  if (!signOutListener) {
    const channel = new BroadcastChannel(AUTH_CHANNEL);
    const fns = new Set<() => void>();
    channel.onmessage = (e: MessageEvent) => { if (e.data === SIGNED_OUT && !signingOut) fns.forEach((f) => f()); };
    signOutListener = { channel, fns };
  }
  const l = signOutListener;
  l.fns.add(fn);
  return () => {
    l.fns.delete(fn);
    if (!l.fns.size && signOutListener === l) { l.channel.close(); signOutListener = null; }
  };
}

function announceSignOut(): void {
  signingOut = true;
  if (typeof BroadcastChannel === 'undefined') return;
  try { const c = new BroadcastChannel(AUTH_CHANNEL); c.postMessage(SIGNED_OUT); c.close(); } catch { /* best effort */ }
}

/** `signInUrl` gets `?next=<path>`. Only the pathname: no query or hash, which could hold app state. */
export function cookieSession(signInUrl?: string, nav: (url: string) => void = (u) => window.location.assign(u)): AuthSession {
  const toSignIn = () => (signInUrl ? `${signInUrl}${signInUrl.includes('?') ? '&' : '?'}next=${encodeURIComponent(window.location.pathname)}` : null);
  // Another tab signed out: leave even without a sign-in URL (a reload drops what's on screen; the next load is 401).
  onSignedOutElsewhere(once(() => nav(toSignIn() ?? window.location.pathname)));
  return {
    kind: 'cookie',
    headers: () => ({ [CSRF_HEADER]: '1' }),
    onExpired: once(() => {
      const url = toSignIn();
      if (url) nav(url);
    }),
  };
}

/** Back-compat for adapters that took a bare token string. */
export function asSession(auth: string | AuthSession | undefined): AuthSession {
  if (auth === undefined || auth === '') return noSession;
  return typeof auth === 'string' ? demoTokenSession(auth) : auth;
}

interface AuthEnv { VITE_AUTH?: string; VITE_ANALYST_DEMO_TOKEN?: string; VITE_SIGN_IN_URL?: string }

/**
 * Build-time choice: `VITE_AUTH=cookie` gives the real backend (`VITE_SIGN_IN_URL` optional), a demo token gives
 * the demo API, and anything else gives none. A non-demo-shaped token is refused rather than shipped in the bundle.
 */
export function sessionFromEnv(env: AuthEnv = import.meta.env as AuthEnv): AuthSession {
  if (env.VITE_AUTH === 'cookie') return cookieSession(env.VITE_SIGN_IN_URL || undefined);
  const token = env.VITE_ANALYST_DEMO_TOKEN;
  if (!token) return noSession;
  if (!DEMO_TOKEN.test(token)) throw new Error('VITE_ANALYST_DEMO_TOKEN must be a demo token (demo:<customer>:<user>)');
  return demoTokenSession(token);
}

/**
 * Sign out: `POST /api/auth/sign-out` (204). The server revokes the session (AuthBackend.sign_out) and answers
 * `Clear-Site-Data: "cache", "storage"` plus an expired `cc_session` cookie, so the browser drops the HTTP cache and
 * all storage for this origin. Then it navigates to `signInUrl` (or reloads), which also drops in-memory drafts and
 * answers. A failed request (offline, 5xx) still navigates away: the user asked to leave, and the cookie expires on
 * its own.
 */
export async function signOut(
  base: string,
  session: AuthSession,
  opts: { signInUrl?: string; fetchImpl?: typeof fetch; nav?: (url: string) => void } = {},
): Promise<boolean> {
  const { fetchImpl = fetch, nav = (u: string) => window.location.assign(u) } = opts;
  let ok = false;
  try {
    const res = await fetchImpl(`${base}/api/auth/sign-out`, { method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: session.headers() });
    ok = res.status === 204;
  } catch {
    /* offline: still leave */
  }
  announceSignOut();
  nav(opts.signInUrl || window.location.pathname);
  return ok;
}
